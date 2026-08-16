import json
import random
import time
import paho.mqtt.client as mqtt

BROKER = "localhost"
PORT = 1883
TOPIC = "plant/plant-001/sensor"

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
client.connect(BROKER, PORT)

while True:
    data = {
        "device_id": "plant-001",
        "soil_moisture": round(random.uniform(30, 80), 2),
        "temperature": round(random.uniform(20, 35), 2)
    }

    client.publish(TOPIC, json.dumps(data))

    print("Published:", data)

    time.sleep(5)