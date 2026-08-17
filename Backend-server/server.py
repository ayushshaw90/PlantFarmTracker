import os
import json
import sqlite3
import threading
import datetime
import time
from typing import List, Optional

import paho.mqtt.client as mqtt
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

BASE_DIR = os.path.dirname(__file__)
DB_PATH = os.path.join(BASE_DIR, "mqtt_data.db")

conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cursor = conn.cursor()
cursor.execute(
    """
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        device_id TEXT,
        moisture REAL,
        temperature REAL,
        timestamp TEXT,
        raw TEXT
    )
    """
)
cursor.execute(
    "CREATE INDEX IF NOT EXISTS idx_device_timestamp ON messages (device_id, timestamp)"
)
conn.commit()
db_lock = threading.Lock()

def insert_message(device_id: str, moisture, temperature, timestamp: str, raw: str):
    with db_lock:
        cursor.execute(
            "INSERT INTO messages (device_id, moisture, temperature, timestamp, raw) VALUES (?, ?, ?, ?, ?)",
            (device_id, moisture, temperature, timestamp, raw),
        )
        conn.commit()

def on_message(client, userdata, msg):
    try:
        payload = msg.payload.decode("utf-8")
        data = json.loads(payload)
        device_id = data.get("device_id") or data.get("id") or "unknown"
        moisture = data.get("soil_moisture") or data.get("moisture")
        temperature = data.get("temperature")
        timestamp = data.get("timestamp") or datetime.datetime.utcnow().isoformat()
        insert_message(device_id, moisture, temperature, timestamp, payload)
        print("Stored message:", device_id, timestamp)
    except Exception as e:
        print("Failed to process MQTT message:", e)

mqtt_client = mqtt.Client()
mqtt_client.on_message = on_message

# Connection control for background reconnect attempts
mqtt_stop_event = threading.Event()
mqtt_loop_started = False

def _connect_loop(broker: str, port: int, topic: str = "plant/+/sensor", retry_interval: int = 5):
    global mqtt_loop_started
    while not mqtt_stop_event.is_set():
        try:
            mqtt_client.connect(broker, port)
            mqtt_client.subscribe(topic)
            if not mqtt_loop_started:
                mqtt_client.loop_start()
                mqtt_loop_started = True
            print(f"MQTT connected to {broker}:{port} and subscribed to {topic}")
            return
        except Exception as e:
            print(f"MQTT connect failed: {e}; retrying in {retry_interval}s")
            time.sleep(retry_interval)

app = FastAPI(title="MQTT to SQLite API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
def startup_event():
    broker = os.environ.get("MQTT_BROKER", "localhost")
    port = int(os.environ.get("MQTT_PORT", "1883"))
    # Start a background thread that will keep attempting to connect to the broker.
    t = threading.Thread(target=_connect_loop, args=(broker, port), daemon=True)
    t.start()
    print("MQTT background connect thread started")


@app.on_event("shutdown")
def shutdown_event():
    mqtt_stop_event.set()
    try:
        if mqtt_loop_started:
            mqtt_client.loop_stop()
    except Exception:
        pass
    try:
        mqtt_client.disconnect()
    except Exception:
        pass
    conn.close()


@app.get("/")
def health():
    return {"status": "ok"}


@app.get("/data")
def get_data(limit: int = 100):
    with db_lock:
        cursor.execute(
            "SELECT id, device_id, moisture, temperature, timestamp FROM messages ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        rows = cursor.fetchall()
    return [
        {"id": r[0], "device_id": r[1], "moisture": r[2], "temperature": r[3], "timestamp": r[4]}
        for r in rows
    ]


# ---------------------------------------------------------------------------
# New API endpoints for the interactive dashboard
# ---------------------------------------------------------------------------

@app.get("/api/plants")
def get_plants():
    """Return a sorted list of distinct plant / device IDs."""
    with db_lock:
        cursor.execute("SELECT DISTINCT device_id FROM messages ORDER BY device_id")
        rows = cursor.fetchall()
    return [r[0] for r in rows]


@app.get("/api/data")
def get_filtered_data(
    plant_id: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    limit: int = 5000,
):
    """Return sensor data filtered by plant ID and/or datetime range.

    Query params:
        plant_id  – comma-separated list of device IDs (optional)
        start     – ISO 8601 start datetime inclusive (optional)
        end       – ISO 8601 end datetime inclusive (optional)
        limit     – max rows to return (default 5000)
    """
    clauses: List[str] = []
    params: list = []

    if plant_id:
        ids = [p.strip() for p in plant_id.split(",")]
        placeholders = ",".join("?" for _ in ids)
        clauses.append(f"device_id IN ({placeholders})")
        params.extend(ids)
    if start:
        clauses.append("timestamp >= ?")
        params.append(start)
    if end:
        clauses.append("timestamp <= ?")
        params.append(end)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"SELECT id, device_id, moisture, temperature, timestamp FROM messages {where} ORDER BY timestamp ASC LIMIT ?"
    params.append(limit)

    with db_lock:
        cursor.execute(sql, params)
        rows = cursor.fetchall()
    return [
        {"id": r[0], "device_id": r[1], "moisture": r[2], "temperature": r[3], "timestamp": r[4]}
        for r in rows
    ]


@app.get("/api/summary")
def get_summary(plant_id: Optional[str] = None):
    """Return per-plant summary statistics."""
    clauses: List[str] = []
    params: list = []
    if plant_id:
        ids = [p.strip() for p in plant_id.split(",")]
        placeholders = ",".join("?" for _ in ids)
        clauses.append(f"device_id IN ({placeholders})")
        params.extend(ids)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"""
        SELECT device_id,
               COUNT(*) as count,
               ROUND(AVG(moisture), 2) as avg_moisture,
               ROUND(AVG(temperature), 2) as avg_temperature,
               MIN(timestamp) as first_reading,
               MAX(timestamp) as last_reading,
               -- latest values via subquery
               (SELECT moisture FROM messages m2
                WHERE m2.device_id = messages.device_id
                ORDER BY m2.timestamp DESC LIMIT 1) as latest_moisture,
               (SELECT temperature FROM messages m2
                WHERE m2.device_id = messages.device_id
                ORDER BY m2.timestamp DESC LIMIT 1) as latest_temperature
        FROM messages
        {where}
        GROUP BY device_id
        ORDER BY device_id
    """
    with db_lock:
        cursor.execute(sql, params)
        rows = cursor.fetchall()
    return [
        {
            "device_id": r[0],
            "count": r[1],
            "avg_moisture": r[2],
            "avg_temperature": r[3],
            "first_reading": r[4],
            "last_reading": r[5],
            "latest_moisture": r[6],
            "latest_temperature": r[7],
        }
        for r in rows
    ]


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    panel_url = os.environ.get("PANEL_URL", "http://localhost:5006")
    html = f"""
    <html>
      <body>
        <h3>Panel dashboard</h3>
        <p>The interactive Panel dashboard runs on a separate Panel server. If you run it locally, open it directly or use the iframe below.</p>
        <iframe src="{panel_url}" width="100%" height="800" frameBorder="0"></iframe>
      </body>
    </html>
    """
    return HTMLResponse(content=html)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)