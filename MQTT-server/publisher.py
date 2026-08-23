#!/usr/bin/env python3
"""Simulate multiple plant sensors publishing to an MQTT broker.

Each plant publishes on topic  plant/<id>/sensor  every few seconds
with randomised moisture and temperature values.

Usage:
    python MQTT-server/publisher.py                 # default 3 plants
    python MQTT-server/publisher.py --interval 2    # publish every 2s
"""

import argparse
import json
import random
import time

import paho.mqtt.client as mqtt

BROKER = "localhost"
PORT = 1883

PLANTS = {
    "plant-001": {"moisture_range": (40, 70), "temp_range": (22, 32)},
    "plant-002": {"moisture_range": (30, 55), "temp_range": (18, 28)},
    "plant-003": {"moisture_range": (50, 80), "temp_range": (25, 38)},
}


def main():
    parser = argparse.ArgumentParser(description="Multi-plant MQTT publisher")
    parser.add_argument("--interval", type=float, default=5, help="Seconds between publishes (default: 5)")
    args = parser.parse_args()

    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    except AttributeError:
        client = mqtt.Client()
    client.connect(BROKER, PORT)

    print(f"Publishing for {len(PLANTS)} plants every {args.interval}s …")

    while True:
        for plant_id, cfg in PLANTS.items():
            data = {
                "device_id": plant_id,
                "soil_moisture": round(random.uniform(*cfg["moisture_range"]), 2),
                "temperature": round(random.uniform(*cfg["temp_range"]), 2),
            }
            topic = f"plant/{plant_id}/sensor"
            client.publish(topic, json.dumps(data))
            print(f"Published to {topic}: {data}")

        time.sleep(args.interval)


if __name__ == "__main__":
    main()