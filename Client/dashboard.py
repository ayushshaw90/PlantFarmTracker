import os
import sqlite3

import pandas as pd
import panel as pn
import hvplot.pandas  # registers hvplot with pandas

pn.extension()

BASE_DIR = os.path.dirname(__file__)
DB_PATH = os.path.abspath(os.path.join(BASE_DIR, '..', 'Backend-server', 'mqtt_data.db'))

def load_data(limit: int = 500):
    if not os.path.exists(DB_PATH):
        return pd.DataFrame()
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT id, device_id, moisture, temperature, timestamp FROM messages ORDER BY timestamp DESC LIMIT ?", conn, params=(limit,))
    conn.close()
    if not df.empty:
        df['timestamp'] = pd.to_datetime(df['timestamp'])
    return df

def make_plot(df: pd.DataFrame):
    if df.empty:
        return pn.pane.Markdown("No data available yet.")
    df_sorted = df.sort_values('timestamp')
    plot = df_sorted.hvplot(x='timestamp', y='moisture', by='device_id', legend='top', width=900, height=350)
    return pn.panel(plot)

def refresh(event=None):
    df = load_data()
    table.value = df
    plot_pane.clear()
    plot_pane.append(make_plot(df))


df_initial = load_data()
table = pn.widgets.DataFrame(df_initial, name='Recent data', autosize_mode='fit_columns', width=900)
plot_pane = pn.Column()
plot_pane.append(make_plot(df_initial))

refresh_button = pn.widgets.Button(name='Refresh', button_type='primary')
refresh_button.on_click(refresh)

header = pn.pane.Markdown("# MQTT Data Dashboard")
controls = pn.Row(refresh_button)
layout = pn.Column(header, controls, plot_pane, table)

# Auto-refresh every 5 seconds when served with `panel serve`
pn.state.add_periodic_callback(refresh, 5)

layout.servable()
