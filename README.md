# 🌱 PlantSense — MQTT Multi-Plant Sensor Monitoring & Forecasting Dashboard

![PlantSense Dashboard Banner](https://res.cloudinary.com/xxn0d121/image/upload/f_auto,q_auto/Screenshot_2026-08-18_at_12.09.15_PM)

**PlantSense** is a full-stack, real-time IoT monitoring and predictive analytics platform for multi-plant sensor networks. It ingests live soil moisture and temperature telemetry over MQTT, persists data in a lightweight SQLite database via FastAPI, and presents interactive visual insights and **time-series forecasting using `hvplot` and `Holt-Winters Exponential Smoothing`**.

---

## ✨ Features

- **Live Telemetry & Ingestion**: Subscribes to MQTT topics (`plant/+/sensor`) for high-frequency IoT sensor telemetry.
- **RESTful API Backend**: FastAPI server with automatic connection management, DB schema management, and CORS middleware.
- **Interactive Multi-Section Dashboard**:
  - **📊 Overview**: Summary cards showing latest readings, average stats, and real-time interactive line plots.
  - **🔮 Time Series Forecasting**: Predict future soil moisture and temperature values using `statsmodels` Holt-Winters models, visualized with `hvplot` overlay charts (historical line, forecast trend line, and 95% shaded confidence bands).
  - **📋 Raw Data Explorer**: Paginated, sortable, and downloadable raw data table powered by Panel's Tabulator.
- **Date & Multi-Plant Filtering**: Dynamic UI controls to select plant subsets and customize date/time range windows.

---

## 🛠 Tech Stack

| Layer | Technology |
|---|---|
| **Frontend / Dashboard** | [Panel](https://panel.holoviz.org/), [hvPlot](https://hvplot.holoviz.org/), [HoloViews](https://holoviews.org/), [Bokeh](https://bokeh.org/) |
| **Backend & API** | [FastAPI](https://fastapi.tiangolo.com/), [Uvicorn](https://www.uvicorn.org/) |
| **MQTT Protocol** | [paho-mqtt](https://eclipse.dev/paho/) |
| **Forecasting & Analytics** | [statsmodels](https://www.statsmodels.org/) (Holt-Winters), [Pandas](https://pandas.pydata.org/), [NumPy](https://numpy.org/), [SciPy](https://scipy.org/) |
| **Database** | SQLite3 |

---

## 📁 Repository Structure

```
PanelProject/
├── Backend-server/
│   ├── server.py         # FastAPI application & MQTT subscriber
│   ├── seed_data.py      # Script to seed realistic historical data
│   └── mqtt_data.db      # SQLite database file
├── Client/
│   └── dashboard.py      # Interactive Panel & hvPlot dashboard app
├── MQTT-server/
│   └── publisher.py      # Simulated MQTT plant sensor publisher
├── requirements.txt      # Python package dependencies
└── README.md             # Project documentation
```

---

## 🚀 Quick Start (Local Setup)

### 1. Environment Setup

Clone the repository and set up a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. (Optional) Seed Historical Data

Seed 7 days of realistic 5-minute interval telemetry for testing forecasts:

```bash
python Backend-server/seed_data.py --days 7 --clear
```

### 3. Start Services

#### Step 1: Start FastAPI Backend Server
```bash
python Backend-server/server.py
```
*API running at `http://localhost:8000`*

#### Step 2: Start Panel Dashboard
In a separate terminal (with `.venv` activated):
```bash
panel serve Client/dashboard.py --port 5006 --allow-websocket-origin="*"
```
*Dashboard running at `http://localhost:5006/dashboard`*

#### Step 3: (Optional) Run Simulated MQTT Publisher
If you have an active MQTT broker (e.g. Mosquitto) running locally on port 1883:
```bash
python MQTT-server/publisher.py --interval 5
```

---

## 🐳 Deployment Guide

### Option 1: Docker Deployment (Recommended)

Create a `Dockerfile` for containerized execution:

```dockerfile
FROM python:3.9-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000 5006

CMD ["sh", "-c", "python Backend-server/server.py & panel serve Client/dashboard.py --port 5006 --allow-websocket-origin='*'"]
```

Build and run:
```bash
docker build -t plantsense-dashboard .
docker run -d -p 8000:8000 -p 5006:5006 --name plantsense plantsense-dashboard
```

### Option 2: Production Systemd Service

When deploying on Linux VPS (e.g., Ubuntu/Debian):

1. Set up Gunicorn/Uvicorn workers for FastAPI backend behind Nginx reverse proxy.
2. Run Panel dashboard as a persistent background service using systemd:

```ini
[Unit]
Description=PlantSense Panel Dashboard
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/var/www/PlantSense
ExecStart=/var/www/PlantSense/.venv/bin/panel serve Client/dashboard.py --port 5006 --allow-websocket-origin="yourdomain.com"
Restart=always

[Install]
WantedBy=multi-user.target
```

3. Configure Nginx reverse proxy to route `/` to `localhost:5006` and `/api` to `localhost:8000`.

---

## 📝 License

Distributed under the MIT License.