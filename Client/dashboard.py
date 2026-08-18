#!/usr/bin/env python3
"""Interactive Panel dashboard for multi-plant MQTT sensor data.

Fetches data from the FastAPI backend and displays interactive charts
with plant selection and date/time range filtering.

Usage:
    panel serve Client/dashboard.py --port 5006 --allow-websocket-origin="*"
"""

import datetime
import os
import warnings

import numpy as np
import pandas as pd
import panel as pn
import requests
import holoviews as hv
import hvplot.pandas  # noqa: F401 – registers .hvplot accessor
from statsmodels.tsa.holtwinters import ExponentialSmoothing

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

# Forecast widgets
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

# ---------------------------------------------------------------------------
# Reactive display components
# ---------------------------------------------------------------------------

# Summary cards row
summary_pane = pn.Row(sizing_mode="stretch_width")

# Chart panes
moisture_pane = pn.pane.HoloViews(sizing_mode="stretch_both", min_height=350)
temperature_pane = pn.pane.HoloViews(sizing_mode="stretch_both", min_height=350)

# Forecast chart panes
forecast_moisture_pane = pn.pane.HoloViews(sizing_mode="stretch_both", min_height=380)
forecast_temperature_pane = pn.pane.HoloViews(sizing_mode="stretch_both", min_height=380)
forecast_status = pn.pane.HTML(
    "<p style='color:#94a3b8; padding:20px; text-align:center;'>"
    "Select a plant and click <b>🔮 Run Forecast</b> to generate predictions.</p>",
    sizing_mode="stretch_width",
)

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


def _resample_series(df, device_id, col, freq="5min"):
    """Extract and resample a single device's time series to a regular frequency."""
    sub = df[df["device_id"] == device_id][["timestamp", col]].copy()
    sub = sub.set_index("timestamp").sort_index()
    sub = sub.resample(freq).mean().interpolate(method="time")
    sub = sub.dropna()
    return sub


def _run_forecast(series, steps, seasonal_periods=288):
    """Fit Holt-Winters and return forecast with confidence intervals.

    Parameters
    ----------
    series : pd.Series
        Regularly-spaced time series (index = DatetimeIndex).
    steps : int
        Number of future steps to forecast.
    seasonal_periods : int
        Seasonal period length (default 288 = 24h at 5-min intervals).

    Returns
    -------
    forecast_df : pd.DataFrame
        Columns: 'forecast', 'lower', 'upper' with a DatetimeIndex.
    """
    # Need at least 2 full seasonal cycles for Holt-Winters seasonal
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

            # Simple confidence interval: ± 1.96 * residual std
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


def _make_forecast_plot(df, device_id, col, label, unit, color_hist, color_fc, hours):
    """Build an hvplot overlay: historical line + forecast line + confidence band."""
    if df.empty or device_id not in df["device_id"].values:
        return hv.Div(
            "<p style='color:#94a3b8;padding:60px;text-align:center;'>"
            "No data available for this plant.</p>"
        )

    steps = int(hours * 12)  # 12 steps per hour at 5-min intervals
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

    # --- Historical line (hvplot) ---
    hist_df = series.reset_index()
    hist_plot = hist_df.hvplot.line(
        x="timestamp",
        y=col,
        color=color_hist,
        label=f"{device_id} (historical)",
        line_width=2,
        responsive=True,
        height=360,
    )

    # --- Forecast line (hvplot) ---
    fc_line_df = fc[["forecast"]].reset_index()
    fc_line_df.columns = ["timestamp", col]
    fc_plot = fc_line_df.hvplot.line(
        x="timestamp",
        y=col,
        color=color_fc,
        label=f"Forecast ({hours}h)",
        line_width=2,
        line_dash="dashed",
        responsive=True,
        height=360,
    )

    # --- Confidence interval band (hvplot area) ---
    band_df = fc[["lower", "upper"]].reset_index()
    band_plot = band_df.hvplot.area(
        x="timestamp",
        y="lower",
        y2="upper",
        color=color_fc,
        alpha=0.15,
        label="95% Confidence",
        responsive=True,
        height=360,
    )

    # --- Connection line from last historical point to first forecast point ---
    connect_df = pd.DataFrame(
        {
            "timestamp": [hist_df["timestamp"].iloc[-1], fc_line_df["timestamp"].iloc[0]],
            col: [hist_df[col].iloc[-1], fc_line_df[col].iloc[0]],
        }
    )
    connect_plot = connect_df.hvplot.line(
        x="timestamp",
        y=col,
        color=color_fc,
        line_width=1,
        line_dash="dotted",
        responsive=True,
        height=360,
    )

    title = f"{label} Forecast — {device_id} ({hours}h ahead)"
    overlay = (band_plot * hist_plot * connect_plot * fc_plot).opts(
        title=title,
        ylabel=f"{label} ({unit})",
        xlabel="Time",
        fontsize={"title": 14, "labels": 12, "ticks": 10},
        toolbar="above",
        legend_position="top_right",
    )
    return overlay


def run_forecast_handler(event=None):
    """Handle the Run Forecast button click."""
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

    # Fetch a larger window of data for the forecast (up to 10 days)
    end_dt = datetime.datetime.utcnow()
    start_dt = end_dt - datetime.timedelta(days=10)
    df = fetch_data([device_id], start_dt, end_dt)

    forecast_moisture_pane.object = _make_forecast_plot(
        df, device_id, "moisture", "Soil Moisture", "%",
        color_hist="#22c55e", color_fc="#f59e0b", hours=hours,
    )
    forecast_temperature_pane.object = _make_forecast_plot(
        df, device_id, "temperature", "Temperature", "°C",
        color_hist="#3b82f6", color_fc="#ef4444", hours=hours,
    )

    forecast_status.object = (
        f"<p style='color:#22c55e; padding:10px; text-align:center;'>"
        f"✅ Forecast complete for <b>{device_id}</b> — {hours}h ahead "
        f"({int(hours * 12)} steps at 5-min intervals)</p>"
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
forecast_btn.on_click(run_forecast_handler)

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
                "<h2 style='margin:0; color:#e2e8f0; font-weight:700;'>"
                "🔮 Time Series Forecast</h2>"
            ),
        ),
        forecast_status,
        forecast_moisture_pane,
        forecast_temperature_pane,
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
