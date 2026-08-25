#!/usr/bin/env python3
"""Single entry point for launching the PlantSense app stack.

Orchestrates the modular components (FastAPI, MQTT publisher/subscriber, Panel UI).
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
from pathlib import Path

# Add project root directory to path to ensure clean package imports
ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR))

import sqlite3
import uvicorn
import panel as pn

from plantsense.config import DB_PATH
from plantsense.database import init_db, seed_db, close_db
from plantsense.backend import app, mqtt_stop_event
from plantsense.publisher import publisher_loop
from plantsense.dashboard import make_dashboard_app


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
    parser = argparse.ArgumentParser(description="Launch the modularized PlantSense app stack.")
    parser.add_argument("--no-publisher", action="store_true", help="Skip the simulated MQTT publisher.")
    parser.add_argument("--backend-port", type=int, default=8000, help="FastAPI backend port.")
    parser.add_argument("--dashboard-port", type=int, default=5006, help="Panel dashboard port.")
    parser.add_argument("--mqtt-interval", type=float, default=5, help="Seconds between MQTT sensor publishes.")
    parser.add_argument("--seed", action="store_true", help="Clear and re-seed database with historical data.")
    parser.add_argument("--days", type=int, default=7, help="Number of days to seed (default: 7).")
    parser.add_argument("--mqtt-broker", type=str, default="bdcc8a3a07274263b22ab1ee2247182e.s1.eu.hivemq.cloud", help="MQTT broker address (default: HiveMQ Cloud).")
    parser.add_argument("--mqtt-port", type=int, default=8883, help="MQTT broker port (default: 8883).")
    parser.add_argument("--mqtt-user", type=str, default=None, help="MQTT username (optional).")
    parser.add_argument("--mqtt-password", type=str, default=None, help="MQTT password (optional).")
    args = parser.parse_args()

    # Find open ports, adapting if running in a cloud environment like Render where PORT is assigned
    env_port = os.environ.get("PORT")
    if env_port:
        # On Render, bind the publicly accessible Panel dashboard to the assigned PORT
        dashboard_port = int(env_port)
        # Run FastAPI on an internal port (e.g. 8000 or 8001 if 8000 is occupied by the dashboard)
        start_backend_port = 8000 if dashboard_port != 8000 else 8001
        backend_port = find_free_port(start_backend_port)
    else:
        # Local development
        backend_port = find_free_port(args.backend_port)
        dashboard_port = find_free_port(args.dashboard_port)

    # Set up global base URLs and environments (read by backend & dashboard)
    os.environ["API_BASE"] = f"http://localhost:{backend_port}"
    # Ensure PORT points to the Panel dashboard port so Render knows where to route traffic
    os.environ["PORT"] = str(dashboard_port)
    os.environ["PANEL_URL"] = f"http://localhost:{dashboard_port}"
    os.environ["MQTT_BROKER"] = args.mqtt_broker
    os.environ["MQTT_PORT"] = str(args.mqtt_port)
    if args.mqtt_user:
        os.environ["MQTT_USER"] = args.mqtt_user
    if args.mqtt_password:
        os.environ["MQTT_PASSWORD"] = args.mqtt_password

    # Ensure DB tables exist
    init_db()

    # Check if database is populated
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

    # Seeding database if requested or if it's completely new/empty
    if not db_exists or db_empty or args.seed:
        print("Database is empty or seeding was requested. Generating sensor history...")
        seed_db(days=args.days, clear=args.seed)

    # Start FastAPI / Uvicorn server thread
    print(f"Starting FastAPI backend on port {backend_port}...")
    def run_uvicorn():
        uvicorn.run(app, host="0.0.0.0", port=backend_port, log_level="warning")

    backend_thread = threading.Thread(target=run_uvicorn, daemon=True)
    backend_thread.start()

    # Start simulated MQTT publisher thread
    pub_stop_event = threading.Event()
    if not args.no_publisher:
        pub_thread = threading.Thread(
            target=publisher_loop,
            args=(args.mqtt_broker, args.mqtt_port, args.mqtt_interval, pub_stop_event),
            kwargs={"username": args.mqtt_user, "password": args.mqtt_password},
            daemon=True
        )
        pub_thread.start()

    # Serve Panel Dashboard
    print(f"Starting Panel dashboard on port {dashboard_port}...")
    print(f"To view the dashboard, open: http://localhost:{dashboard_port}")

    import asyncio
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

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
        close_db()

    return 0


if __name__ == "__main__":
    sys.exit(main())
