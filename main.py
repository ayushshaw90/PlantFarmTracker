#!/usr/bin/env python3
"""Single entry point for launching the PlantSense app stack.

Runs backend, MQTT publisher simulator, and Panel dashboard in a single process.
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import random
import signal
import socket
import sqlite3
import sys
import threading
import time
import warnings
from pathlib import Path
from typing import List, Optional, Tuple

import folium
import holoviews as hv
import hvplot.pandas  # noqa: F401
import numpy as np
import paho.mqtt.client as mqtt
import pandas as pd
import panel as pn
import requests
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from statsmodels.tsa.holtwinters import ExponentialSmoothing

# Initialize HoloViews and Panel
hv.extension("bokeh")
pn.extension("tabulator", sizing_mode="stretch_width")

# Configuration
API_BASE = "http://localhost:8000"
ACCENT = "#22c55e"
PALETTE = ["#22c55e", "#3b82f6", "#f59e0b", "#ef4444", "#8b5cf6", "#06b6d4"]

# Resolve paths
ROOT_DIR = Path(__file__).resolve().parent
DB_PATH = ROOT_DIR / "Backend-server" / "mqtt_data.db"
if not DB_PATH.exists():
    DB_PATH = ROOT_DIR / "mqtt_data.db"

# Database lock
db_lock = threading.Lock()

# Global database connection references
conn = None
cursor = None


def init_db():
    db_conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    cur = db_conn.cursor()
    cur.execute(
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
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_device_timestamp ON messages (device_id, timestamp)"
    )
    db_conn.commit()
    db_conn.close()


# Ensure database table exists
init_db()


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
            print(f"MQTT subscriber connected to {broker}:{port} and subscribed to {topic}")
            return
        except Exception as e:
            print(f"MQTT subscriber connect failed: {e}; retrying in {retry_interval}s")
            time.sleep(retry_interval)


app = FastAPI(title="MQTT to SQLite API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
def startup_event():
    broker = os.environ.get("MQTT_BROKER", "localhost")
    port = int(os.environ.get("MQTT_PORT", "1883"))
    t = threading.Thread(target=_connect_loop, args=(broker, port), daemon=True)
    t.start()
    print("MQTT subscriber background connect thread started")


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


@app.get("/api/plants")
def get_plants():
    """Return a sorted list of distinct plant / device IDs."""
    with db_lock:
        cursor.execute("SELECT DISTINCT device_id FROM messages ORDER BY device_id")
        rows = cursor.fetchall()
    return [r[0] for r in rows]


# Static plant location metadata
PLANT_LOCATIONS = {
    "plant-001": {"name": "Greenhouse A", "lat": 12.9352, "lng": 77.6245},
    "plant-002": {"name": "Rooftop Garden", "lat": 12.9378, "lng": 77.6270},
    "plant-003": {"name": "Open Field B", "lat": 12.9340, "lng": 77.6290},
}


@app.get("/api/plants/locations")
def get_plant_locations():
    """Return location metadata for each known plant."""
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


# Seeding Logic
PLANTS_SEED_CFG = {
    "plant-001": {
        "moisture_base": 55,
        "moisture_amp": 15,
        "temp_base": 26,
        "temp_amp": 5,
    },
    "plant-002": {
        "moisture_base": 40,
        "moisture_amp": 10,
        "temp_base": 22,
        "temp_amp": 4,
    },
    "plant-003": {
        "moisture_base": 65,
        "moisture_amp": 12,
        "temp_base": 30,
        "temp_amp": 6,
    },
}


def generate_seed_data(days: int = 7):
    """Yield (device_id, moisture, temperature, timestamp, raw) tuples."""
    now = datetime.datetime.utcnow()
    start = now - datetime.timedelta(days=days)
    total_minutes = days * 24 * 60
    INTERVAL_MINUTES = 5

    for device_id, cfg in PLANTS_SEED_CFG.items():
        t = start
        for i in range(0, total_minutes, INTERVAL_MINUTES):
            hour_frac = (t.hour + t.minute / 60) / 24.0
            moisture = (
                cfg["moisture_base"]
                + cfg["moisture_amp"] * math.sin(2 * math.pi * (hour_frac - 0.25))
                + random.gauss(0, 2)
            )
            temperature = (
                cfg["temp_base"]
                + cfg["temp_amp"] * math.sin(2 * math.pi * (hour_frac - 0.0))
                + random.gauss(0, 0.8)
            )
            moisture = round(max(0, min(100, moisture)), 2)
            temperature = round(max(-10, min(60, temperature)), 2)
            ts = t.isoformat()
            raw = f'{{"device_id":"{device_id}","soil_moisture":{moisture},"temperature":{temperature}}}'
            yield (device_id, moisture, temperature, ts, raw)
            t += datetime.timedelta(minutes=INTERVAL_MINUTES)


def seed_db(days: int = 7, clear: bool = False):
    db_conn = sqlite3.connect(str(DB_PATH))
    cur = db_conn.cursor()
    cur.execute(
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
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_device_timestamp ON messages (device_id, timestamp)"
    )
    if clear:
        cur.execute("DELETE FROM messages")
        print("Cleared existing database records.")

    rows = list(generate_seed_data(days=days))
    cur.executemany(
        "INSERT INTO messages (device_id, moisture, temperature, timestamp, raw) VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    db_conn.commit()
    db_conn.close()
    print(f"Seeded {len(rows)} rows for {len(PLANTS_SEED_CFG)} plants over {days} days into {DB_PATH}")


# Simulated MQTT Publisher Logic
PLANTS_PUB_CFG = {
    "plant-001": {
        "moisture_range": (40, 70),
        "temp_range": (22, 32),
    },
    "plant-002": {
        "moisture_range": (30, 55),
        "temp_range": (18, 28),
    },
    "plant-003": {
        "moisture_range": (50, 80),
        "temp_range": (25, 38),
    },
}


def publisher_loop(broker: str, port: int, interval: float, stop_event: threading.Event):
    print(f"MQTT Publisher thread started. Connecting to broker {broker}:{port} ...")

    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    except AttributeError:
        client = mqtt.Client()

    connected = False
    while not stop_event.is_set():
        if not connected:
            try:
                client.connect(broker, port, keepalive=60)
                connected = True
                print(f"MQTT Publisher successfully connected to {broker}:{port}")
            except Exception as e:
                print(f"MQTT Publisher connect failed: {e}; retrying in 5s")
                time.sleep(5)
                continue

        try:
            for plant_id, cfg in PLANTS_PUB_CFG.items():
                data = {
                    "device_id": plant_id,
                    "soil_moisture": round(random.uniform(*cfg["moisture_range"]), 2),
                    "temperature": round(random.uniform(*cfg["temp_range"]), 2),
                    "timestamp": datetime.datetime.utcnow().isoformat()
                }
                topic = f"plant/{plant_id}/sensor"
                client.publish(topic, json.dumps(data))
                print(f"[Simulated Publisher] Published to {topic}: {data}")
        except Exception as e:
            print(f"MQTT Publisher failed to publish: {e}; will reconnect")
            connected = False

        start_time = time.time()
        while time.time() - start_time < interval:
            if stop_event.is_set():
                break
            time.sleep(0.1)

    try:
        client.disconnect()
    except Exception:
        pass
    print("MQTT Publisher thread stopped.")


# Dashboard Helper Functions
def fetch_plants():
    global API_BASE
    for _ in range(5):
        try:
            resp = requests.get(f"{API_BASE}/api/plants", timeout=2)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            time.sleep(0.5)
    return []


def fetch_data(plant_ids, start_dt, end_dt):
    global API_BASE
    params = {}
    if plant_ids:
        params["plant_id"] = ",".join(plant_ids)
    if start_dt:
        params["start"] = start_dt.isoformat()
    if end_dt:
        params["end"] = end_dt.isoformat()
    params["limit"] = 10000

    try:
        resp = requests.get(f"{API_BASE}/api/data", params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            return pd.DataFrame(columns=["id", "device_id", "moisture", "temperature", "timestamp"])
        df = pd.DataFrame(data)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        return df
    except Exception as e:
        print(f"Error fetching data: {e}")
        return pd.DataFrame(columns=["id", "device_id", "moisture", "temperature", "timestamp"])


def fetch_summary(plant_ids=None):
    global API_BASE
    params = {}
    if plant_ids:
        params["plant_id"] = ",".join(plant_ids)
    try:
        resp = requests.get(f"{API_BASE}/api/summary", params=params, timeout=5)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"Error fetching summary: {e}")
        return []


def fetch_plant_locations():
    global API_BASE
    try:
        resp = requests.get(f"{API_BASE}/api/plants/locations", timeout=5)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"Error fetching plant locations: {e}")
        return []


def _make_summary_card(stat, all_plants):
    device = stat["device_id"]
    idx = all_plants.index(device) if (all_plants and device in all_plants) else 0
    color = PALETTE[idx % len(PALETTE)]

    return pn.pane.HTML(
        f"""
        <div style="
            background: #181d29;
            border: 1px solid #262c3a;
            border-radius: 16px;
            padding: 20px 22px;
            min-width: 250px;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.35);
            font-family: 'Inter', system-ui, -apple-system, sans-serif;
            position: relative;
            overflow: hidden;
        ">
            <div style="display:flex; justify-content:space-between; align-items:flex-start;">
                <div>
                    <div style="width:38px; height:38px; border-radius:10px; background:{color}22; display:flex; align-items:center; justify-content:center; color:{color}; font-size:18px; font-weight:bold; margin-bottom:12px;">
                        🌱
                    </div>
                    <div style="font-size:11px; font-weight:700; color:#8e99a9; text-transform:uppercase; letter-spacing:1px; margin-bottom:4px;">
                        {device}
                    </div>
                    <div style="font-size:26px; font-weight:800; color:#ffffff; line-height:1.1;">
                        {stat.get('latest_moisture', '—')}<span style="font-size:15px; color:#8e99a9; margin-left:2px;">%</span>
                    </div>
                    <div style="font-size:12px; color:#8e99a9; margin-top:4px;">
                        Temp: <b style="color:#e2e8f0;">{stat.get('latest_temperature', '—')}°C</b>
                    </div>
                </div>
                <div style="text-align:right;">
                    <span style="background:{color}18; color:{color}; padding:4px 8px; border-radius:8px; font-size:11px; font-weight:700;">
                        ACTIVE
                    </span>
                    <div style="margin-top:20px; font-size:11px; color:#64748b;">
                        Avg: {stat.get('avg_moisture', '—')}%
                    </div>
                </div>
            </div>
        </div>
        """,
        sizing_mode="stretch_width",
    )


def _hex_to_rgba(hex_color, alpha):
    h = hex_color.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(alpha * 255))


def _bokeh_gradient_hook(plot, element):
    fig = plot.state
    fig.background_fill_color = "#0d1117"
    fig.border_fill_color = "#0d1117"
    fig.outline_line_color = None
    fig.xaxis.axis_line_color = "#1e293b"
    fig.yaxis.axis_line_color = "#1e293b"
    fig.xaxis.major_tick_line_color = "#334155"
    fig.yaxis.major_tick_line_color = "#334155"
    fig.xaxis.minor_tick_line_color = None
    fig.yaxis.minor_tick_line_color = None
    fig.xaxis.major_label_text_color = "#64748b"
    fig.yaxis.major_label_text_color = "#64748b"
    fig.xaxis.major_label_text_font = "Inter"
    fig.yaxis.major_label_text_font = "Inter"
    fig.xaxis.major_label_text_font_size = "10px"
    fig.yaxis.major_label_text_font_size = "10px"
    fig.xaxis.axis_label_text_color = "#94a3b8"
    fig.yaxis.axis_label_text_color = "#94a3b8"
    fig.xaxis.axis_label_text_font = "Inter"
    fig.yaxis.axis_label_text_font = "Inter"
    fig.xaxis.axis_label_text_font_size = "11px"
    fig.yaxis.axis_label_text_font_size = "11px"
    fig.xgrid.grid_line_color = "#1a2233"
    fig.ygrid.grid_line_color = "#1a2233"
    fig.xgrid.grid_line_alpha = 0.6
    fig.ygrid.grid_line_alpha = 0.6
    if fig.title:
        fig.title.text_color = "#e2e8f0"
        fig.title.text_font = "Inter"
        fig.title.text_font_size = "14px"
    if fig.legend:
        for legend in fig.legend:
            legend.background_fill_color = "#0d1117"
            legend.background_fill_alpha = 0.7
            legend.border_line_color = "#1e293b"
            legend.label_text_color = "#94a3b8"
            legend.label_text_font = "Inter"
            legend.label_text_font_size = "10px"
    for renderer in fig.renderers:
        glyph = getattr(renderer, "glyph", None)
        if glyph is not None and hasattr(glyph, "fill_alpha") and hasattr(glyph, "fill_color"):
            glyph_type = type(glyph).__name__
            if glyph_type in ("Patch", "VArea", "Patches"):
                glyph.fill_alpha = 0.12
                glyph.line_alpha = 0


def _apply_modern_chart_style(plot):
    return plot.opts(
        bgcolor="#0d1117",
        gridstyle={"grid_line_color": "#1a2233", "grid_line_alpha": 0.6},
        fontsize={"title": 14, "labels": 11, "ticks": 10},
        toolbar="above",
        hooks=[_bokeh_gradient_hook],
    )


def _make_moisture_plot(df, all_plants):
    if df.empty:
        return hv.Div("<p style='color:#94a3b8;padding:60px;text-align:center;'>No data for selected filters.</p>")

    devices = df["device_id"].unique()
    overlays = None

    for i, dev in enumerate(devices):
        sub = df[df["device_id"] == dev].sort_values("timestamp")
        idx = list(all_plants).index(dev) if (all_plants and dev in all_plants) else i
        color = PALETTE[idx % len(PALETTE)]

        area = sub.hvplot.area(
            x="timestamp", y="moisture",
            color=color, alpha=0.15,
            responsive=True, height=380,
        )
        line = sub.hvplot.line(
            x="timestamp", y="moisture",
            color=color, label=dev, line_width=2,
            responsive=True, height=380,
            hover_cols=["device_id", "moisture", "temperature", "timestamp"],
        )
        overlay = area * line
        overlays = overlay if overlays is None else overlays * overlay

    overlays = overlays.opts(
        title="💧 Soil Moisture Overview",
        ylabel="Moisture (%)",
        xlabel="",
        legend_position="top_right",
    )
    return _apply_modern_chart_style(overlays)


def _make_temperature_plot(df, all_plants):
    if df.empty:
        return hv.Div("<p style='color:#94a3b8;padding:60px;text-align:center;'>No data for selected filters.</p>")

    devices = df["device_id"].unique()
    overlays = None

    for i, dev in enumerate(devices):
        sub = df[df["device_id"] == dev].sort_values("timestamp")
        idx = list(all_plants).index(dev) if (all_plants and dev in all_plants) else i
        color = PALETTE[(idx + 1) % len(PALETTE)]

        area = sub.hvplot.area(
            x="timestamp", y="temperature",
            color=color, alpha=0.15,
            responsive=True, height=380,
        )
        line = sub.hvplot.line(
            x="timestamp", y="temperature",
            color=color, label=dev, line_width=2,
            responsive=True, height=380,
            hover_cols=["device_id", "moisture", "temperature", "timestamp"],
        )
        overlay = area * line
        overlays = overlay if overlays is None else overlays * overlay

    overlays = overlays.opts(
        title="🌡️ Temperature Telemetry",
        ylabel="Temperature (°C)",
        xlabel="",
        legend_position="top_right",
    )
    return _apply_modern_chart_style(overlays)


def _resample_series(df, device_id, col, freq="5min"):
    sub = df[df["device_id"] == device_id][["timestamp", col]].copy()
    sub = sub.set_index("timestamp").sort_index()
    sub = sub.resample(freq).mean().interpolate(method="time")
    sub = sub.dropna()
    return sub


def _run_forecast(series, steps, seasonal_periods=288):
    min_obs = seasonal_periods * 2
    use_seasonal = len(series) >= min_obs

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            if use_seasonal:
                model = ExponentialSmoothing(
                    series,
                    trend="add",
                    seasonal="add",
                    seasonal_periods=seasonal_periods,
                    initialization_method="estimated",
                )
            else:
                model = ExponentialSmoothing(
                    series,
                    trend="add",
                    seasonal=None,
                    initialization_method="estimated",
                )
            fit = model.fit(optimized=True)
            pred = fit.forecast(steps)

            resid_std = np.std(fit.resid.dropna())
            margin = 1.96 * resid_std
            future_idx = pred.index

            forecast_df = pd.DataFrame(
                {
                    "forecast": pred.values,
                    "lower": pred.values - margin,
                    "upper": pred.values + margin,
                },
                index=future_idx,
            )
            forecast_df.index.name = "timestamp"
            return forecast_df
        except Exception as e:
            print(f"Forecast error: {e}")
            return None


def _make_forecast_plot(df, device_id, col, label, unit, color_hist, color_fc, hours, all_plants):
    if df.empty or device_id not in df["device_id"].values:
        return hv.Div(
            "<p style='color:#94a3b8;padding:60px;text-align:center;'>"
            "No data available for this plant.</p>"
        )

    steps = int(hours * 12)
    series = _resample_series(df, device_id, col)

    if len(series) < 20:
        return hv.Div(
            "<p style='color:#f59e0b;padding:60px;text-align:center;'>"
            "⚠️ Not enough data points for forecasting (need ≥ 20).</p>"
        )

    fc = _run_forecast(series[col], steps)
    if fc is None:
        return hv.Div(
            "<p style='color:#ef4444;padding:60px;text-align:center;'>"
            "❌ Forecast model failed. Try a different horizon or wait for more data.</p>"
        )

    hist_df = series.reset_index()
    hist_area = hist_df.hvplot.area(
        x="timestamp", y=col,
        color=color_hist, alpha=0.15,
        responsive=True, height=380,
    )
    hist_line = hist_df.hvplot.line(
        x="timestamp", y=col,
        color=color_hist, label=f"{device_id} (historical)",
        line_width=2, responsive=True, height=380,
    )

    fc_line_df = fc[["forecast"]].reset_index()
    fc_line_df.columns = ["timestamp", col]
    fc_plot = fc_line_df.hvplot.line(
        x="timestamp", y=col,
        color=color_fc, label=f"Forecast ({hours}h)",
        line_width=2, line_dash="dashed",
        responsive=True, height=380,
    )

    band_df = fc[["lower", "upper"]].reset_index()
    band_plot = band_df.hvplot.area(
        x="timestamp", y="lower", y2="upper",
        color=color_fc, alpha=0.15,
        label="95% Confidence Band",
        responsive=True, height=380,
    )

    title = f"🔮 {label} Predictive Forecast — {device_id} (+{hours}h)"
    overlay = (band_plot * hist_area * hist_line * fc_plot).opts(
        title=title,
        ylabel=f"{label} ({unit})",
        xlabel="",
        legend_position="top_right",
    )
    return _apply_modern_chart_style(overlay)


def _build_map_html(all_plants):
    locations = fetch_plant_locations()
    stats = fetch_summary()
    stat_lookup = {s["device_id"]: s for s in stats}

    if not locations:
        return "<p style='color:#94a3b8; padding:60px; text-align:center;'>No plant location data available.</p>"

    avg_lat = sum(loc["lat"] for loc in locations) / len(locations)
    avg_lng = sum(loc["lng"] for loc in locations) / len(locations)

    m = folium.Map(
        location=[avg_lat, avg_lng],
        zoom_start=16,
        tiles="CartoDB dark_matter",
        attr="CartoDB",
    )

    for i, loc in enumerate(locations):
        device_id = loc["device_id"]
        name = loc.get("name", device_id)
        idx = list(all_plants).index(device_id) if (all_plants and device_id in all_plants) else i
        color = PALETTE[idx % len(PALETTE)]
        stat = stat_lookup.get(device_id, {})

        moisture = stat.get("latest_moisture", "—")
        temp = stat.get("latest_temperature", "—")
        count = stat.get("count", 0)

        popup_html = f"""
        <div style="
            font-family: 'Inter', system-ui, sans-serif;
            background: #1a1f2e;
            color: #e2e8f0;
            border-radius: 12px;
            padding: 16px 18px;
            min-width: 200px;
            box-shadow: 0 8px 24px rgba(0,0,0,0.5);
        ">
            <div style="font-size:15px; font-weight:700; color:{color}; margin-bottom:10px;">
                🌱 {name}
            </div>
            <div style="font-size:11px; color:#64748b; text-transform:uppercase; letter-spacing:1px; margin-bottom:6px;">
                {device_id}
            </div>
            <hr style="border:none; border-top:1px solid #2d3548; margin:8px 0;">
            <div style="display:flex; justify-content:space-between; margin-bottom:4px;">
                <span style="color:#94a3b8;">💧 Moisture</span>
                <span style="font-weight:700; color:#22c55e;">{moisture}%</span>
            </div>
            <div style="display:flex; justify-content:space-between; margin-bottom:4px;">
                <span style="color:#94a3b8;">🌡️ Temp</span>
                <span style="font-weight:700; color:#3b82f6;">{temp}°C</span>
            </div>
            <div style="display:flex; justify-content:space-between;">
                <span style="color:#94a3b8;">📊 Readings</span>
                <span style="font-weight:600; color:#e2e8f0;">{count:,}</span>
            </div>
            <div style="margin-top:8px; font-size:10px; color:#475569;">
                📍 {loc['lat']:.4f}, {loc['lng']:.4f}
            </div>
        </div>
        """

        color_map = {
            "#22c55e": "green",
            "#3b82f6": "blue",
            "#f59e0b": "orange",
            "#ef4444": "red",
            "#8b5cf6": "purple",
            "#06b6d4": "cadetblue",
        }
        marker_color = color_map.get(color, "green")

        folium.Marker(
            location=[loc["lat"], loc["lng"]],
            popup=folium.Popup(popup_html, max_width=280),
            tooltip=f"{name} ({device_id})",
            icon=folium.Icon(color=marker_color, icon="leaf", prefix="fa"),
        ).add_to(m)

    import html as html_mod
    raw_html = m._repr_html_()
    escaped = html_mod.escape(raw_html)
    return f'<iframe srcdoc="{escaped}" style="width:100%; height:520px; border:none; border-radius:12px;"></iframe>'


# Dashboard Layout builder
def make_dashboard_app():
    all_plants = fetch_plants()

    plant_select = pn.widgets.MultiChoice(
        name="Select Plants",
        options=all_plants,
        value=all_plants[:3] if all_plants else [],
        solid=True,
        max_width=400,
    )

    now = datetime.datetime.utcnow()
    start_picker = pn.widgets.DatetimePicker(
        name="Start",
        value=now - datetime.timedelta(days=7),
        max_width=250,
    )
    end_picker = pn.widgets.DatetimePicker(
        name="End",
        value=now,
        max_width=250,
    )

    refresh_btn = pn.widgets.Button(name="⟳  Refresh", button_type="success", max_width=140)

    forecast_plant = pn.widgets.Select(
        name="Forecast Plant",
        options=all_plants,
        value=all_plants[0] if all_plants else None,
        max_width=400,
    )
    forecast_hours = pn.widgets.IntSlider(
        name="Forecast Horizon (hours)",
        start=1,
        end=72,
        value=24,
        step=1,
        max_width=400,
    )
    forecast_btn = pn.widgets.Button(name="🔮  Run Forecast", button_type="primary", max_width=180)

    summary_pane = pn.Row(sizing_mode="stretch_width")
    moisture_pane = pn.pane.HoloViews(sizing_mode="stretch_both", min_height=350)
    temperature_pane = pn.pane.HoloViews(sizing_mode="stretch_both", min_height=350)

    forecast_moisture_pane = pn.pane.HoloViews(sizing_mode="stretch_both", min_height=380)
    forecast_temperature_pane = pn.pane.HoloViews(sizing_mode="stretch_both", min_height=380)
    forecast_status = pn.pane.HTML(
        "<p style='color:#94a3b8; padding:20px; text-align:center;'>"
        "Select a plant and click <b>🔮 Run Forecast</b> to generate predictions.</p>",
        sizing_mode="stretch_width",
    )

    table_widget = pn.widgets.Tabulator(
        pd.DataFrame(),
        sizing_mode="stretch_width",
        height=350,
        theme="midnight",
        page_size=50,
        pagination="remote",
        show_index=False,
        frozen_columns=["device_id"],
    )

    map_pane = pn.pane.HTML(
        _build_map_html(all_plants),
        sizing_mode="stretch_width",
        height=540,
    )

    def run_forecast_handler(event=None):
        device_id = forecast_plant.value
        hours = forecast_hours.value

        if not device_id:
            forecast_status.object = (
                "<p style='color:#f59e0b; padding:10px; text-align:center;'>"
                "⚠️ Please select a plant first.</p>"
            )
            return

        forecast_status.object = (
            "<p style='color:#3b82f6; padding:10px; text-align:center;'>"
            "⏳ Running forecast model…</p>"
        )

        end_dt = datetime.datetime.utcnow()
        start_dt = end_dt - datetime.timedelta(days=10)
        df = fetch_data([device_id], start_dt, end_dt)

        forecast_moisture_pane.object = _make_forecast_plot(
            df, device_id, "moisture", "Soil Moisture", "%",
            color_hist="#22c55e", color_fc="#f59e0b", hours=hours, all_plants=all_plants
        )
        forecast_temperature_pane.object = _make_forecast_plot(
            df, device_id, "temperature", "Temperature", "°C",
            color_hist="#3b82f6", color_fc="#ef4444", hours=hours, all_plants=all_plants
        )

        forecast_status.object = (
            f"<p style='color:#22c55e; padding:10px; text-align:center;'>"
            f"✅ Forecast complete for <b>{device_id}</b> — {hours}h ahead "
            f"({int(hours * 12)} steps at 5-min intervals)</p>"
        )

    def update_dashboard(event=None):
        selected = plant_select.value or all_plants
        start_dt = start_picker.value
        end_dt = end_picker.value

        df = fetch_data(selected, start_dt, end_dt)
        stats = fetch_summary(selected)

        summary_pane.clear()
        for s in stats:
            summary_pane.append(_make_summary_card(s, all_plants))

        moisture_pane.object = _make_moisture_plot(df, all_plants)
        temperature_pane.object = _make_temperature_plot(df, all_plants)

        if not df.empty:
            display_df = df[["device_id", "moisture", "temperature", "timestamp"]].copy()
            display_df = display_df.sort_values("timestamp", ascending=False).reset_index(drop=True)
            table_widget.value = display_df
        else:
            table_widget.value = pd.DataFrame(columns=["device_id", "moisture", "temperature", "timestamp"])

        new_plants = fetch_plants()
        if set(new_plants) != set(plant_select.options):
            plant_select.options = new_plants
            forecast_plant.options = new_plants

        map_pane.object = _build_map_html(new_plants)

    refresh_btn.on_click(update_dashboard)
    plant_select.param.watch(update_dashboard, "value")
    start_picker.param.watch(update_dashboard, "value")
    end_picker.param.watch(update_dashboard, "value")
    forecast_btn.on_click(run_forecast_handler)

    update_dashboard()

    pn.state.add_periodic_callback(update_dashboard, period=15000)

    # Layout build
    overview_section = pn.Column(
        pn.pane.HTML(
            """
            <div style="padding: 12px 0 4px;">
                <h2 style="margin:0; color:#e2e8f0; font-weight:700;">📊 Overview</h2>
                <p style="color:#64748b; font-size:13px; margin-top:4px;">
                    At-a-glance stats and live sensor charts for all selected plants.
                </p>
            </div>
            """
        ),
        summary_pane,
        pn.layout.Divider(),
        pn.pane.HTML(
            "<h3 style='margin:0; color:#22c55e; font-weight:600;'>💧 Soil Moisture Over Time</h3>"
        ),
        moisture_pane,
        pn.layout.Divider(),
        pn.pane.HTML(
            "<h3 style='margin:0; color:#3b82f6; font-weight:600;'>🌡️ Temperature Over Time</h3>"
        ),
        temperature_pane,
        sizing_mode="stretch_width",
        visible=True,
    )

    forecast_section = pn.Column(
        pn.pane.HTML(
            """
            <div style="padding: 12px 0 4px;">
                <h2 style="margin:0; color:#e2e8f0; font-weight:700;">🔮 Time Series Forecast</h2>
                <p style="color:#64748b; font-size:13px; margin-top:4px;">
                    Predict future moisture and temperature using Holt-Winters Exponential Smoothing.
                    Select a plant and horizon in the sidebar, then click <b style="color:#f59e0b;">Run Forecast</b>.
                </p>
            </div>
            """,
        ),
        forecast_status,
        pn.pane.HTML(
            "<h3 style='margin:0; color:#22c55e; font-weight:600;'>💧 Moisture Forecast</h3>"
        ),
        forecast_moisture_pane,
        pn.layout.Divider(),
        pn.pane.HTML(
            "<h3 style='margin:0; color:#3b82f6; font-weight:600;'>🌡️ Temperature Forecast</h3>"
        ),
        forecast_temperature_pane,
        sizing_mode="stretch_width",
        visible=False,
    )

    data_section = pn.Column(
        pn.pane.HTML(
            """
            <div style="padding: 12px 0 4px;">
                <h2 style="margin:0; color:#e2e8f0; font-weight:700;">📋 Raw Data</h2>
                <p style="color:#64748b; font-size:13px; margin-top:4px;">
                    Browse, sort, and paginate through all sensor readings.
                </p>
            </div>
            """
        ),
        table_widget,
        sizing_mode="stretch_width",
        visible=False,
    )

    map_section = pn.Column(
        pn.pane.HTML(
            """
            <div style="padding: 12px 0 4px;">
                <h2 style="margin:0; color:#e2e8f0; font-weight:700;">🗺️ Sensor Location Map</h2>
                <p style="color:#64748b; font-size:13px; margin-top:4px;">
                    Interactive map showing the physical locations of all plant sensors.
                    Click a marker to see the latest readings.
                </p>
            </div>
            """
        ),
        map_pane,
        sizing_mode="stretch_width",
        visible=False,
    )

    NAV_OPTIONS = ["📊 Overview", "🗺️ Map", "🔮 Forecast", "📋 Data"]
    _sections = [overview_section, map_section, forecast_section, data_section]

    nav_buttons = pn.widgets.RadioButtonGroup(
        name="Navigation",
        options=NAV_OPTIONS,
        value=NAV_OPTIONS[0],
        button_type="success",
        sizing_mode="stretch_width",
    )

    def _switch_section(event):
        idx = NAV_OPTIONS.index(event.new)
        for i, sec in enumerate(_sections):
            sec.visible = (i == idx)

    nav_buttons.param.watch(_switch_section, "value")

    navbar_html = pn.pane.HTML(
        """
        <style>
            .nav-header {
                display: flex;
                align-items: center;
                justify-content: space-between;
                padding: 6px 4px;
                font-family: 'Inter', 'Segoe UI', sans-serif;
            }
            .nav-header .brand {
                font-size: 16px;
                font-weight: 700;
                color: #e2e8f0;
                letter-spacing: 0.3px;
            }
            .nav-header .status {
                display: flex;
                align-items: center;
                gap: 6px;
                font-size: 12px;
                color: #64748b;
            }
            .nav-header .status-dot {
                width: 7px;
                height: 7px;
                border-radius: 50%;
                background: #22c55e;
                animation: pulse-dot 2s ease-in-out infinite;
            }
            @keyframes pulse-dot {
                0%, 100% { opacity: 1; box-shadow: 0 0 0 0 rgba(34, 197, 94, 0.5); }
                50% { opacity: 0.7; box-shadow: 0 0 0 4px rgba(34, 197, 94, 0); }
            }
        </style>
        <div class="nav-header">
            <span class="brand">🌱 PlantSense</span>
            <div class="status">
                <div class="status-dot"></div>
                Live · Auto-refresh 15s
            </div>
        </div>
        """,
        sizing_mode="stretch_width",
        height=40,
    )

    template = pn.template.FastListTemplate(
        title="🌱 Plant Sensor Dashboard",
        sidebar=[
            pn.pane.HTML(
                """
                <div style="padding: 10px 0;">
                    <p style="color:#94a3b8; font-size:13px; line-height:1.5;">
                        Monitor real-time sensor data from your plant network.
                        Select plants and a date range to filter the view.
                    </p>
                </div>
                """,
            ),
            plant_select,
            pn.layout.Divider(),
            pn.pane.HTML("<div style='font-size:13px;color:#94a3b8;font-weight:600;'>Date / Time Range</div>"),
            start_picker,
            end_picker,
            pn.layout.Divider(),
            refresh_btn,
            pn.layout.Divider(),
            pn.pane.HTML(
                "<div style='font-size:13px;color:#f59e0b;font-weight:600;'>"
                "🔮 Time Series Forecasting</div>"
            ),
            forecast_plant,
            forecast_hours,
            forecast_btn,
        ],
        main=[
            navbar_html,
            nav_buttons,
            pn.layout.Divider(),
            pn.Column(
                overview_section,
                map_section,
                forecast_section,
                data_section,
                sizing_mode="stretch_width",
                margin=0,
            ),
        ],
        accent=ACCENT,
        theme="dark",
        header_background="#0f172a",
        sidebar_width=320,
    )

    return template


def find_free_port(start_port: int | None = None) -> int:
    """Choose a free port, falling back to an OS-assigned ephemeral port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            if start_port is not None:
                sock.bind(("127.0.0.1", start_port))
                return start_port
        except OSError:
            pass
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main() -> int:
    global API_BASE, conn, cursor

    parser = argparse.ArgumentParser(description="Launch the PlantSense app stack.")
    parser.add_argument("--no-publisher", action="store_true", help="Skip the simulated MQTT publisher.")
    parser.add_argument("--backend-port", type=int, default=8000, help="FastAPI backend port.")
    parser.add_argument("--dashboard-port", type=int, default=5006, help="Panel dashboard port.")
    parser.add_argument("--mqtt-interval", type=float, default=5, help="Seconds between MQTT sensor publishes.")
    parser.add_argument("--seed", action="store_true", help="Clear and re-seed database with historical data.")
    parser.add_argument("--days", type=int, default=7, help="Number of days to seed (default: 7).")
    parser.add_argument("--mqtt-broker", type=str, default="localhost", help="MQTT broker address (default: localhost).")
    parser.add_argument("--mqtt-port", type=int, default=1883, help="MQTT broker port (default: 1883).")
    args = parser.parse_args()

    # Determine free ports
    backend_port = find_free_port(args.backend_port)
    dashboard_port = find_free_port(args.dashboard_port)

    # Set up global base URLs and environments
    API_BASE = f"http://localhost:{backend_port}"
    os.environ["API_BASE"] = API_BASE
    os.environ["PORT"] = str(backend_port)
    os.environ["PANEL_URL"] = f"http://localhost:{dashboard_port}"
    os.environ["MQTT_BROKER"] = args.mqtt_broker
    os.environ["MQTT_PORT"] = str(args.mqtt_port)

    # Database existence and population check
    db_exists = DB_PATH.exists()
    db_empty = True
    if db_exists:
        try:
            test_conn = sqlite3.connect(str(DB_PATH))
            test_cur = test_conn.cursor()
            test_cur.execute("SELECT COUNT(*) FROM messages")
            count = test_cur.fetchone()[0]
            if count > 0:
                db_empty = False
            test_conn.close()
        except Exception:
            pass

    # If the database is missing, empty, or seeding is explicitly forced, run the seeder
    if not db_exists or db_empty or args.seed:
        print("Database is empty or seeding was requested. Generating sensor history...")
        seed_db(days=args.days, clear=args.seed)

    # Re-establish connections to database to ensure we have the correct path
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    cursor = conn.cursor()

    # Start FastAPI / Uvicorn thread
    print(f"Starting FastAPI backend on port {backend_port}...")
    def run_uvicorn():
        uvicorn.run(app, host="0.0.0.0", port=backend_port, log_level="warning")

    backend_thread = threading.Thread(target=run_uvicorn, daemon=True)
    backend_thread.start()

    # Start Simulated MQTT publisher thread if requested
    pub_stop_event = threading.Event()
    if not args.no_publisher:
        pub_thread = threading.Thread(
            target=publisher_loop,
            args=(args.mqtt_broker, args.mqtt_port, args.mqtt_interval, pub_stop_event),
            daemon=True
        )
        pub_thread.start()

    # Start Panel / Bokeh serve
    print(f"Starting Panel dashboard on port {dashboard_port}...")
    print(f"To view the dashboard, open: http://localhost:{dashboard_port}")

    try:
        pn.serve(
            make_dashboard_app,
            port=dashboard_port,
            address="0.0.0.0",
            show=False,
            allow_websocket_origin=["*"]
        )
    except KeyboardInterrupt:
        pass
    finally:
        print("\nStopping PlantSense stack...")
        pub_stop_event.set()
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
        try:
            conn.close()
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
