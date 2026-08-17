#!/usr/bin/env python3
"""Interactive Panel dashboard for multi-plant MQTT sensor data.

Fetches data from the FastAPI backend and displays interactive charts
with plant selection and date/time range filtering.

Usage:
    panel serve Client/dashboard.py --port 5006 --allow-websocket-origin="*"
"""

import datetime
import os

import pandas as pd
import panel as pn
import requests
import holoviews as hv
import hvplot.pandas  # noqa: F401 – registers .hvplot accessor

hv.extension("bokeh")
pn.extension("tabulator", sizing_mode="stretch_width")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
API_BASE = os.environ.get("API_BASE", "http://localhost:8000")

# Accent colour palette — earthy greens that match a "plant" theme
ACCENT = "#22c55e"
PALETTE = ["#22c55e", "#3b82f6", "#f59e0b", "#ef4444", "#8b5cf6", "#06b6d4"]

# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def fetch_plants():
    """Get list of plant IDs from the backend."""
    try:
        resp = requests.get(f"{API_BASE}/api/plants", timeout=5)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"Error fetching plants: {e}")
        return []


def fetch_data(plant_ids, start_dt, end_dt):
    """Fetch filtered sensor data from the backend."""
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
    """Fetch per-plant summary stats."""
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


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------

all_plants = fetch_plants()

plant_select = pn.widgets.MultiChoice(
    name="Select Plants",
    options=all_plants,
    value=all_plants[:3],  # default: first 3
    solid=True,
    max_width=400,
)

# Default time range: last 24 hours
now = datetime.datetime.utcnow()
start_picker = pn.widgets.DatetimePicker(
    name="Start",
    value=now - datetime.timedelta(days=1),
    max_width=250,
)
end_picker = pn.widgets.DatetimePicker(
    name="End",
    value=now,
    max_width=250,
)

refresh_btn = pn.widgets.Button(name="⟳  Refresh", button_type="success", max_width=140)

# ---------------------------------------------------------------------------
# Reactive display components
# ---------------------------------------------------------------------------

# Summary cards row
summary_pane = pn.Row(sizing_mode="stretch_width")

# Chart panes
moisture_pane = pn.pane.HoloViews(sizing_mode="stretch_both", min_height=350)
temperature_pane = pn.pane.HoloViews(sizing_mode="stretch_both", min_height=350)

# Data table
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


def _make_summary_card(stat):
    """Create an HTML summary card for one plant."""
    device = stat["device_id"]
    idx = all_plants.index(device) if device in all_plants else 0
    color = PALETTE[idx % len(PALETTE)]
    return pn.pane.HTML(
        f"""
        <div style="
            background: linear-gradient(135deg, {color}22, {color}11);
            border-left: 4px solid {color};
            border-radius: 12px;
            padding: 18px 22px;
            min-width: 220px;
            font-family: 'Inter', 'Segoe UI', sans-serif;
        ">
            <div style="font-size:13px; color:#94a3b8; text-transform:uppercase; letter-spacing:1px; margin-bottom:6px;">
                {device}
            </div>
            <div style="display:flex; gap:20px; flex-wrap:wrap;">
                <div>
                    <div style="font-size:28px; font-weight:700; color:{color};">{stat.get('latest_moisture', '—')}%</div>
                    <div style="font-size:11px; color:#64748b;">Moisture (latest)</div>
                </div>
                <div>
                    <div style="font-size:28px; font-weight:700; color:{color};">{stat.get('latest_temperature', '—')}°C</div>
                    <div style="font-size:11px; color:#64748b;">Temp (latest)</div>
                </div>
            </div>
            <div style="margin-top:10px; font-size:12px; color:#64748b;">
                Avg: {stat.get('avg_moisture', '—')}% · {stat.get('avg_temperature', '—')}°C &nbsp;|&nbsp; {stat.get('count', 0)} readings
            </div>
        </div>
        """,
        sizing_mode="stretch_width",
    )


def _make_moisture_plot(df):
    """Create a moisture-over-time line plot."""
    if df.empty:
        return hv.Div("<p style='color:#94a3b8;padding:60px;text-align:center;'>No data for selected filters.</p>")
    return df.hvplot.line(
        x="timestamp",
        y="moisture",
        by="device_id",
        color=PALETTE,
        title="Soil Moisture Over Time",
        ylabel="Moisture (%)",
        xlabel="Time",
        legend="top_right",
        responsive=True,
        height=340,
        hover_cols=["device_id", "moisture", "temperature", "timestamp"],
        line_width=2,
    ).opts(
        fontsize={"title": 14, "labels": 12, "ticks": 10},
        toolbar="above",
    )


def _make_temperature_plot(df):
    """Create a temperature-over-time line plot."""
    if df.empty:
        return hv.Div("<p style='color:#94a3b8;padding:60px;text-align:center;'>No data for selected filters.</p>")
    return df.hvplot.line(
        x="timestamp",
        y="temperature",
        by="device_id",
        color=PALETTE,
        title="Temperature Over Time",
        ylabel="Temperature (°C)",
        xlabel="Time",
        legend="top_right",
        responsive=True,
        height=340,
        hover_cols=["device_id", "moisture", "temperature", "timestamp"],
        line_width=2,
    ).opts(
        fontsize={"title": 14, "labels": 12, "ticks": 10},
        toolbar="above",
    )


def update_dashboard(event=None):
    """Refresh all dashboard components with current filter values."""
    selected = plant_select.value or all_plants
    start_dt = start_picker.value
    end_dt = end_picker.value

    # Fetch data
    df = fetch_data(selected, start_dt, end_dt)
    stats = fetch_summary(selected)

    # Update summary cards
    summary_pane.clear()
    for s in stats:
        summary_pane.append(_make_summary_card(s))

    # Update charts
    moisture_pane.object = _make_moisture_plot(df)
    temperature_pane.object = _make_temperature_plot(df)

    # Update table
    if not df.empty:
        display_df = df[["device_id", "moisture", "temperature", "timestamp"]].copy()
        display_df = display_df.sort_values("timestamp", ascending=False).reset_index(drop=True)
        table_widget.value = display_df
    else:
        table_widget.value = pd.DataFrame(columns=["device_id", "moisture", "temperature", "timestamp"])

    # Refresh plant list in case new plants appeared
    new_plants = fetch_plants()
    if set(new_plants) != set(plant_select.options):
        plant_select.options = new_plants


# Wire up event handlers
refresh_btn.on_click(update_dashboard)
plant_select.param.watch(update_dashboard, "value")
start_picker.param.watch(update_dashboard, "value")
end_picker.param.watch(update_dashboard, "value")

# Initial load
update_dashboard()

# Auto-refresh every 15 seconds
pn.state.add_periodic_callback(update_dashboard, period=15000)

# ---------------------------------------------------------------------------
# Layout using FastListTemplate for a polished dark-themed dashboard
# ---------------------------------------------------------------------------

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
    ],
    main=[
        pn.Row(
            pn.pane.HTML(
                "<h2 style='margin:0; color:#e2e8f0; font-weight:700;'>📊 Overview</h2>"
            ),
        ),
        summary_pane,
        pn.layout.Divider(),
        pn.Row(
            pn.pane.HTML(
                "<h2 style='margin:0; color:#e2e8f0; font-weight:700;'>💧 Soil Moisture</h2>"
            ),
        ),
        moisture_pane,
        pn.layout.Divider(),
        pn.Row(
            pn.pane.HTML(
                "<h2 style='margin:0; color:#e2e8f0; font-weight:700;'>🌡️ Temperature</h2>"
            ),
        ),
        temperature_pane,
        pn.layout.Divider(),
        pn.Row(
            pn.pane.HTML(
                "<h2 style='margin:0; color:#e2e8f0; font-weight:700;'>📋 Raw Data</h2>"
            ),
        ),
        table_widget,
    ],
    accent=ACCENT,
    theme="dark",
    header_background="#0f172a",
    sidebar_width=320,
)

template.servable()
