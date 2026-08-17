#!/usr/bin/env python3
"""Seed the SQLite database with realistic historical data for testing.

Generates ~2016 readings per plant (one every 5 minutes for 7 days)
for three plants with different characteristic ranges.

Usage:
    python Backend-server/seed_data.py          # from workspace root
    python Backend-server/seed_data.py --days 3 # customise window
"""

import argparse
import math
import os
import random
import sqlite3
import datetime

BASE_DIR = os.path.dirname(__file__)
DB_PATH = os.path.join(BASE_DIR, "mqtt_data.db")

PLANTS = {
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

INTERVAL_MINUTES = 5


def generate_data(days: int = 7):
    """Yield (device_id, moisture, temperature, timestamp, raw) tuples."""
    now = datetime.datetime.utcnow()
    start = now - datetime.timedelta(days=days)
    total_minutes = days * 24 * 60

    for device_id, cfg in PLANTS.items():
        t = start
        for i in range(0, total_minutes, INTERVAL_MINUTES):
            # Time-of-day factor (0..1 over 24h)
            hour_frac = (t.hour + t.minute / 60) / 24.0
            # Daily sine cycle — moisture peaks at night, temp peaks at noon
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


def main():
    parser = argparse.ArgumentParser(description="Seed MQTT database with test data")
    parser.add_argument("--days", type=int, default=7, help="Days of history to generate (default: 7)")
    parser.add_argument("--clear", action="store_true", help="Delete existing data before seeding")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
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

    if args.clear:
        cur.execute("DELETE FROM messages")
        print("Cleared existing data.")

    rows = list(generate_data(days=args.days))
    cur.executemany(
        "INSERT INTO messages (device_id, moisture, temperature, timestamp, raw) VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()

    print(f"Seeded {len(rows)} rows for {len(PLANTS)} plants over {args.days} days into {DB_PATH}")


if __name__ == "__main__":
    main()
