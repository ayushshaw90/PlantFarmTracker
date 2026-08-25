import datetime
import math
import random
import sqlite3
import threading
from pathlib import Path
from plantsense.config import DB_PATH, PLANTS_SEED_CFG

# Thread lock for SQLite database writes/reads
db_lock = threading.Lock()

# Global connection caches
_conn = None
_cursor = None


def get_db() -> tuple[sqlite3.Connection, sqlite3.Cursor]:
    """Retrieve or initialize the global SQLite connection for this thread/process."""
    global _conn, _cursor
    if _conn is None:
        _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _cursor = _conn.cursor()
    return _conn, _cursor


def close_db():
    """Close the global SQLite connection cleanly."""
    global _conn, _cursor
    if _conn is not None:
        try:
            _conn.close()
        except Exception:
            pass
        _conn = None
        _cursor = None


def init_db():
    """Create database tables and index if they do not exist."""
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


def insert_message(device_id: str, moisture: float, temperature: float, timestamp: str, raw: str):
    """Insert an incoming MQTT message safely into the database."""
    conn, cursor = get_db()
    with db_lock:
        cursor.execute(
            "INSERT INTO messages (device_id, moisture, temperature, timestamp, raw) VALUES (?, ?, ?, ?, ?)",
            (device_id, moisture, temperature, timestamp, raw),
        )
        conn.commit()


def generate_seed_data(days: int = 7):
    """Yield realistic telemetry readings for plant seeding."""
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
    """Clear and/or seed the database with synthetic telemetry records."""
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
