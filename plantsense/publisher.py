import datetime
import json
import random
import threading
import time
import paho.mqtt.client as mqtt
from plantsense.config import PLANTS_PUB_CFG


def publisher_loop(broker: str, port: int, interval: float, stop_event: threading.Event, username: str = None, password: str = None):
    """Loop to simulate multiple plant sensors publishing to MQTT broker in the background."""
    print(f"MQTT Publisher thread started. Connecting to broker {broker}:{port} ...")

    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    except AttributeError:
        client = mqtt.Client()

    connected = False
    while not stop_event.is_set():
        if not connected:
            try:
                if port == 8883:
                    import ssl
                    client.tls_set(tls_version=ssl.PROTOCOL_TLSv1_2)
                if username and password:
                    client.username_pw_set(username, password)
                client.connect(broker, port, keepalive=60)
                connected = True
                print(f"MQTT Publisher successfully connected to {broker}:{port}")
            except Exception as e:
                print(f"MQTT Publisher connect failed: {e}; retrying in 5s")
                time.sleep(5)
                continue

        try:
            for plant_id, cfg in PLANTS_PUB_CFG.items():
                data = {
                    "device_id": plant_id,
                    "soil_moisture": round(random.uniform(*cfg["moisture_range"]), 2),
                    "temperature": round(random.uniform(*cfg["temp_range"]), 2),
                    "timestamp": datetime.datetime.utcnow().isoformat()
                }
                topic = f"plant/{plant_id}/sensor"
                client.publish(topic, json.dumps(data))
                print(f"[Simulated Publisher] Published to {topic}: {data}")
        except Exception as e:
            print(f"MQTT Publisher failed to publish: {e}; will reconnect")
            connected = False

        start_time = time.time()
        while time.time() - start_time < interval:
            if stop_event.is_set():
                break
            time.sleep(0.1)

    try:
        client.disconnect()
    except Exception:
        pass
    print("MQTT Publisher thread stopped.")
