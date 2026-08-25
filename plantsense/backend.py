import datetime
import json
import os
import threading
import time
from typing import List, Optional
import paho.mqtt.client as mqtt
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from plantsense.config import PLANT_LOCATIONS
from plantsense.database import get_db, db_lock, insert_message

# FastAPI app instance
app = FastAPI(title="MQTT to SQLite API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# MQTT connection control for background reconnect attempts
try:
    mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
except AttributeError:
    mqtt_client = mqtt.Client()
mqtt_stop_event = threading.Event()
mqtt_loop_started = False


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


mqtt_client.on_message = on_message


def _connect_loop(broker: str, port: int, topic: str = "plant/+/sensor", retry_interval: int = 5, username: str = None, password: str = None):
    global mqtt_loop_started
    while not mqtt_stop_event.is_set():
        try:
            if port == 8883:
                import ssl
                mqtt_client.tls_set(tls_version=ssl.PROTOCOL_TLSv1_2)
            if username and password:
                mqtt_client.username_pw_set(username, password)

            mqtt_client.connect(broker, port)
            mqtt_client.subscribe(topic)
            if not mqtt_loop_started:
                mqtt_client.loop_start()
                mqtt_loop_started = True
            print(f"MQTT subscriber connected to {broker}:{port} and subscribed to {topic}")
            return
        except Exception as e:
            print(f"MQTT subscriber connect failed: {e}; retrying in {retry_interval}s")
            time.sleep(retry_interval)


@app.on_event("startup")
def startup_event():
    broker = os.environ.get("MQTT_BROKER", "localhost")
    port = int(os.environ.get("MQTT_PORT", "1883"))
    username = os.environ.get("MQTT_USER")
    password = os.environ.get("MQTT_PASSWORD")
    t = threading.Thread(
        target=_connect_loop,
        args=(broker, port),
        kwargs={"username": username, "password": password},
        daemon=True
    )
    t.start()
    print("MQTT subscriber background connect thread started")


@app.on_event("shutdown")
def shutdown_event():
    mqtt_stop_event.set()
    try:
        global mqtt_loop_started
        if mqtt_loop_started:
            mqtt_client.loop_stop()
    except Exception:
        pass
    try:
        mqtt_client.disconnect()
    except Exception:
        pass


@app.get("/")
def health():
    return {"status": "ok"}


@app.get("/data")
def get_data(limit: int = 100):
    conn, cursor = get_db()
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


@app.get("/api/plants")
def get_plants():
    """Return a sorted list of distinct plant / device IDs."""
    conn, cursor = get_db()
    with db_lock:
        cursor.execute("SELECT DISTINCT device_id FROM messages ORDER BY device_id")
        rows = cursor.fetchall()
    return [r[0] for r in rows]


@app.get("/api/plants/locations")
def get_plant_locations():
    """Return location metadata for each known plant."""
    conn, cursor = get_db()
    with db_lock:
        cursor.execute("SELECT DISTINCT device_id FROM messages ORDER BY device_id")
        device_ids = [r[0] for r in cursor.fetchall()]

    result = []
    for did in device_ids:
        loc = PLANT_LOCATIONS.get(did, {"name": did, "lat": 0, "lng": 0})
        result.append({
            "device_id": did,
            "name": loc["name"],
            "lat": loc["lat"],
            "lng": loc["lng"],
        })
    return result


@app.get("/api/data")
def get_filtered_data(
    plant_id: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    limit: int = 5000,
):
    conn, cursor = get_db()
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
    conn, cursor = get_db()
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
    panel_port = os.environ.get("PANEL_PORT", os.environ.get("PANEL_URL", "http://localhost:5006").split(":")[-1])
    panel_url = os.environ.get("PANEL_URL", f"http://localhost:{panel_port}")
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
