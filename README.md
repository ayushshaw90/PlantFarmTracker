# MQTT → SQLite → Panel Dashboard

This workspace provides a FastAPI-based web server that subscribes to an MQTT topic, stores incoming messages in a local SQLite database, and an interactive Panel dashboard that reads the database and displays recent data.

Files added/updated
- `Backend-server/server.py` — FastAPI app that runs an MQTT client and writes messages to `mqtt_data.db`.
- `Client/dashboard.py` — Panel dashboard (served with `panel serve`) that reads the SQLite DB and displays a table and moisture plot.
- `requirements.txt` — Python dependencies.

Quick start

1. Create a virtual environment and install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Start the backend FastAPI server (it starts an MQTT client on startup):

```bash
# from workspace root
python Backend-server/server.py
# or using uvicorn
uvicorn Backend-server.server:app --reload --port 8000
```

3. Start the Panel dashboard in a separate terminal:

```bash
panel serve Client/dashboard.py --port 5006 --allow-websocket-origin=localhost:8000
```

4. Open the dashboard directly at http://localhost:5006 or visit http://localhost:8000/dashboard to see an iframe embedding the Panel server.

Notes
- The backend writes messages into `Backend-server/mqtt_data.db`.
- By default the MQTT broker is `localhost:1883`. Override with `MQTT_BROKER` and `MQTT_PORT` environment variables.
- The Panel server reads the same SQLite file directly; make sure both processes run on the same machine (or mount the DB file).