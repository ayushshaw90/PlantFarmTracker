# 🌱 PlantSense — MQTT Multi-Plant Sensor Monitoring & Forecasting Dashboard

![PlantSense Dashboard Banner](https://res.cloudinary.com/xxn0d121/image/upload/f_auto,q_auto/Screenshot_2026-08-18_at_12.09.15_PM)

🔗 **Live Demo**: [https://plantfarmtracker.onrender.com/](https://plantfarmtracker.onrender.com/)

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
├── main.py              # Orchestrator entrypoint to start backend, publisher, and serving panel UI
├── plantsense/          # Core application package folder
│   ├── config.py        # Centralized configurations, colors, location and seed data
│   ├── database.py      # SQLite connectionpool, schema creation, seeding and insertion methods
│   ├── backend.py       # FastAPI web endpoints, and background MQTT subscriber thread
│   ├── publisher.py     # Background thread simulating sensor MQTT publishes
│   └── dashboard.py     # Panel interactive widgets, forecasting models, folium map, and layouts
├── mqtt_data.db         # SQLite database file containing telemetry data
├── requirements.txt     # Python package dependencies
└── README.md            # Project documentation
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

### 2. Start all services in a single command

Run the unified application stack.

If your MQTT broker requires authentication credentials (like the default HiveMQ Cloud broker):

```bash
python main.py --mqtt-user <your_username> --mqtt-password <your_password>
```

If you are running a local unsecured broker (e.g. localhost on port 1883):

```bash
python main.py --mqtt-broker localhost --mqtt-port 1883
```

This single command starts the entire project:
- **FastAPI backend** (API running at `http://localhost:8000`)
- **Simulated MQTT Plant Publisher** (publishing sensor updates to broker)
- **Panel Dashboard** (served at `http://localhost:5006`)
- **Automated Seeding**: If the database is missing or empty, it automatically seeds 7 days of historical sensor data so charts render immediately.

#### Advanced CLI Options:
- `--seed`: Clear the database and force a re-seed of historical data.
- `--days <int>`: Set the number of days of history to seed (default: 7).
- `--no-publisher`: Run the backend and dashboard without starting the simulated MQTT sensor publisher.
- `--backend-port <int>`: Specify a custom FastAPI backend port (default: 8000).
- `--dashboard-port <int>`: Specify a custom Panel dashboard port (default: 5006).
- `--mqtt-broker <str>`: MQTT broker URL (default: `bdcc8a3a07274263b22ab1ee2247182e.s1.eu.hivemq.cloud`).
- `--mqtt-port <int>`: MQTT broker port (default: `8883` which automatically activates SSL/TLS encryption).
- `--mqtt-user <str>`: MQTT broker authentication username.
- `--mqtt-password <str>`: MQTT broker authentication password.

---

## 🚀 Deployment Guide

### Option 1: Cloud Platforms (Render, Heroku, etc.) — Recommended

You can easily deploy the application directly to cloud platforms like Render without using Docker:

1. **Create a Web Service** on Render and link your GitHub repository.
2. **Configure the Environment**:
   - **Environment/Runtime**: `Python`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: 
     - **With simulated publisher**: `python main.py`
     - **Without simulated publisher (recommended for production)**: `python main.py --no-publisher`
3. **Environment Variables** (Optional, configure under Render Dashboard -> Environment):
   - `PYTHON_VERSION`: `3.11` (or rely on the auto-detected `.python-version` file).
   - `MQTT_BROKER`: Address of your MQTT broker.
   - `MQTT_PORT`: Port of your MQTT broker.
   - `MQTT_USER`: Username for your MQTT broker.
   - `MQTT_PASSWORD`: Password for your MQTT broker.

> [!NOTE]
> When running on Render, the system automatically detects the assigned `$PORT` environment variable and routes public traffic directly to the Panel dashboard. The FastAPI backend runs on an internal port (e.g. `8000`), allowing the Panel server to query it locally within the container.

---

### Option 2: Production Systemd Service (Linux VPS)

If you are deploying on a Linux VPS (e.g., Ubuntu/Debian) without Docker:

1. **Create a Systemd Service File**:
   Create a service file at `/etc/systemd/system/plantsense.service` to keep the application running persistently in the background:

   ```ini
   [Unit]
   Description=PlantSense Unified Application Stack
   After=network.target

   [Service]
   User=ubuntu
   WorkingDirectory=/var/www/PlantSense
   # To include simulated publisher:
   # ExecStart=/var/www/PlantSense/.venv/bin/python main.py
   # To run without simulated publisher:
   ExecStart=/var/www/PlantSense/.venv/bin/python main.py --no-publisher
   Restart=always

   [Install]
   WantedBy=multi-user.target
   ```

2. **Enable and Start the Service**:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable plantsense.service
   sudo systemctl start plantsense.service
   ```

3. **Configure Nginx Reverse Proxy**:
   Configure Nginx to proxy external web traffic directly to your Panel dashboard port (default: `5006`).

---

### Option 3: Docker Deployment

If you prefer to deploy containerized:

1. **Create a `Dockerfile`** in the root directory:
   ```dockerfile
   FROM python:3.11-slim
   WORKDIR /app
   COPY requirements.txt .
   RUN pip install --no-cache-dir -r requirements.txt
   COPY . .
   EXPOSE 8000 5006
   CMD ["python", "main.py", "--no-publisher"]
   ```

2. **Build and Run the Container**:
   ```bash
   docker build -t plantsense-dashboard .
   docker run -d -p 8000:8000 -p 5006:5006 --name plantsense plantsense-dashboard
   ```

---

## 📝 License

Distributed under the MIT License.