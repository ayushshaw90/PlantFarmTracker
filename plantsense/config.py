import os
from pathlib import Path

# Resolve root path and database path
ROOT_DIR = Path(__file__).resolve().parent.parent
DB_PATH = ROOT_DIR / "mqtt_data.db"

# Accent and color palette
ACCENT = "#22c55e"
PALETTE = ["#22c55e", "#3b82f6", "#f59e0b", "#ef4444", "#8b5cf6", "#06b6d4"]

# Static plant location metadata
PLANT_LOCATIONS = {
    "plant-001": {"name": "Greenhouse A", "lat": 12.9352, "lng": 77.6245},
    "plant-002": {"name": "Rooftop Garden", "lat": 12.9378, "lng": 77.6270},
    "plant-003": {"name": "Open Field B", "lat": 12.9340, "lng": 77.6290},
}

# Seeding configurations
PLANTS_SEED_CFG = {
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

# Simulated publisher configurations
PLANTS_PUB_CFG = {
    "plant-001": {
        "moisture_range": (40, 70),
        "temp_range": (22, 32),
    },
    "plant-002": {
        "moisture_range": (30, 55),
        "temp_range": (18, 28),
    },
    "plant-003": {
        "moisture_range": (50, 80),
        "temp_range": (25, 38),
    },
}
