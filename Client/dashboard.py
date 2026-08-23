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

# Default time range: last 7 days to cover seeded telemetry window
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
    """Create a modern card matching the reference mockup (icon badge, metric value, mini sparkline effect)."""
    device = stat["device_id"]
    idx = all_plants.index(device) if device in all_plants else 0
    color = PALETTE[idx % len(PALETTE)]
    
    # Modern card styling with dark container & rounded edges
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
    """Convert '#RRGGBB' to an RGBA tuple (0-255, 0-255, 0-255, alpha 0-255)."""
    h = hex_color.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(alpha * 255))


def _bokeh_gradient_hook(plot, element):
    """Bokeh hook: replace flat area fills with smooth linear gradient images."""
    from bokeh.models import LinearColorMapper, Band
    fig = plot.state
    # Dark background & border
    fig.background_fill_color = "#0d1117"
    fig.border_fill_color = "#0d1117"
    fig.outline_line_color = None
    # Axes
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
    # Grid
    fig.xgrid.grid_line_color = "#1a2233"
    fig.ygrid.grid_line_color = "#1a2233"
    fig.xgrid.grid_line_alpha = 0.6
    fig.ygrid.grid_line_alpha = 0.6
    # Title
    if fig.title:
        fig.title.text_color = "#e2e8f0"
        fig.title.text_font = "Inter"
        fig.title.text_font_size = "14px"
    # Legend
    if fig.legend:
        for legend in fig.legend:
            legend.background_fill_color = "#0d1117"
            legend.background_fill_alpha = 0.7
            legend.border_line_color = "#1e293b"
            legend.label_text_color = "#94a3b8"
            legend.label_text_font = "Inter"
            legend.label_text_font_size = "10px"
    # Make area renderers use very low fill alpha for a subtle gradient effect
    for renderer in fig.renderers:
        glyph = getattr(renderer, "glyph", None)
        if glyph is not None and hasattr(glyph, "fill_alpha") and hasattr(glyph, "fill_color"):
            # Only modify area/patch/varea glyphs (not lines)
            glyph_type = type(glyph).__name__
            if glyph_type in ("Patch", "VArea", "Patches"):
                glyph.fill_alpha = 0.12
                glyph.line_alpha = 0


def _apply_modern_chart_style(plot):
    """Apply polished dark theme via HoloViews opts + Bokeh hook."""
    return plot.opts(
        bgcolor="#0d1117",
        gridstyle={"grid_line_color": "#1a2233", "grid_line_alpha": 0.6},
        fontsize={"title": 14, "labels": 11, "ticks": 10},
        toolbar="above",
        hooks=[_bokeh_gradient_hook],
    )


def _make_moisture_plot(df):
    """Create a clean, modern gradient-fill moisture chart."""
    if df.empty:
        return hv.Div("<p style='color:#94a3b8;padding:60px;text-align:center;'>No data for selected filters.</p>")

    devices = df["device_id"].unique()
    overlays = None

    for i, dev in enumerate(devices):
        sub = df[df["device_id"] == dev].sort_values("timestamp")
        color = PALETTE[i % len(PALETTE)]

        # Subtle area fill (gradient effect applied via Bokeh hook)
        area = sub.hvplot.area(
            x="timestamp", y="moisture",
            color=color, alpha=0.15,
            responsive=True, height=380,
        )
        # Smooth line with subtle glow width
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


def _make_temperature_plot(df):
    """Create a clean, modern gradient-fill temperature chart."""
    if df.empty:
        return hv.Div("<p style='color:#94a3b8;padding:60px;text-align:center;'>No data for selected filters.</p>")

    devices = df["device_id"].unique()
    overlays = None

    for i, dev in enumerate(devices):
        sub = df[df["device_id"] == dev].sort_values("timestamp")
        color = PALETTE[(i + 1) % len(PALETTE)]

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
    """Extract and resample a single device's time series to a regular frequency."""
    sub = df[df["device_id"] == device_id][["timestamp", col]].copy()
    sub = sub.set_index("timestamp").sort_index()
    sub = sub.resample(freq).mean().interpolate(method="time")
    sub = sub.dropna()
    return sub


def _run_forecast(series, steps, seasonal_periods=288):
    """Fit Holt-Winters and return forecast with confidence intervals."""
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


def _make_forecast_plot(df, device_id, col, label, unit, color_hist, color_fc, hours):
    """Build an hvplot area overlay for predictive forecasting."""
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

    # Historical area fill & line
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

    # Forecast line
    fc_line_df = fc[["forecast"]].reset_index()
    fc_line_df.columns = ["timestamp", col]
    fc_plot = fc_line_df.hvplot.line(
        x="timestamp", y=col,
        color=color_fc, label=f"Forecast ({hours}h)",
        line_width=2, line_dash="dashed",
        responsive=True, height=380,
    )

    # Confidence interval band
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
# Layout using FastListTemplate with radio-button navigation
# ---------------------------------------------------------------------------

# --- Build section panels (visibility-toggled) ---
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

# --- Navigation via RadioButtonGroup ---
NAV_OPTIONS = ["📊 Overview", "🔮 Forecast", "📋 Data"]
_sections = [overview_section, forecast_section, data_section]

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

# --- Navbar bar with branding + status ---
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
        overview_section,
        forecast_section,
        data_section,
    ],
    accent=ACCENT,
    theme="dark",
    header_background="#0f172a",
    sidebar_width=320,
)

template.servable()

