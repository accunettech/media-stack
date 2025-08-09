import os
import time
import json
import ssl
import socket
import requests
from datetime import datetime, timezone
import paho.mqtt.client as mqtt

MQTT_HOST = "localhost"
MQTT_PORT = "1883"
MQTT_TOPIC = "home/media/vpn/status"
MQTT_USER = "some_user"
MQTT_PASS = "some_password"
MQTT_TLS = False
MQTT_TLS_INSECURE = True

HEALTH_URL = os.getenv("HEALTH_URL", "http://gluetun:9999/health")
PUBLICIP_URL = os.getenv("PUBLICIP_URL", "http://gluetun:8000/v1/publicip/ip")
POLL_INTERVAL = 30

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def build_client():
    client = mqtt.Client()
    if MQTT_USER and MQTT_PASS:
        client.username_pw_set(MQTT_USER, MQTT_PASS)
    if MQTT_TLS:
        ctx = ssl.create_default_context()
        if MQTT_TLS_INSECURE:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        client.tls_set_context(ctx)
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    return client

def publish_status(client, status, reason=None, public_ip=None):
    payload = {
        "status": status,                # "up" | "down"
        "reason": reason,               # optional error text
        "public_ip": public_ip,         # if available
        "host": socket.gethostname(),
        "ts": now_iso(),
    }
    # Retain latest status at the topic root; send events to a subtopic
    client.publish(MQTT_TOPIC, json.dumps(payload), qos=1, retain=True)
    client.publish(f"{MQTT_TOPIC}/events", json.dumps(payload), qos=1, retain=False)

def get_public_ip():
    try:
        r = requests.get(PUBLICIP_URL, timeout=3)
        if r.ok:
            return r.text.strip()
    except Exception:
        pass
    return None

def check_health():
    r = requests.get(HEALTH_URL, timeout=3)
    # gluetun returns 200 OK when healthy
    return r.status_code == 200

def main():
    if not os.getenv("VPN_WATCHDOG_ENABLED", "false").lower() == True:
        quit()

    client = None
    last_up = None  # tri-state: None (unknown), True (up), False (down)

    while True:
        try:
            is_up = check_health()
            if client is None:
                client = build_client()

            if last_up is None or is_up != last_up:
                if is_up:
                    publish_status(client, "up", public_ip=get_public_ip())
                else:
                    publish_status(client, "down", reason="gluetun healthcheck failed")
                last_up = is_up

        except requests.RequestException as e:
            # Couldn’t reach health URL → consider down
            try:
                if client is None:
                    client = build_client()
                if last_up is None or last_up is True:
                    publish_status(client, "down", reason=f"health request error: {e}")
                last_up = False
            except Exception:
                # swallow to avoid crash; we’ll retry next loop
                pass

        except (mqtt.WebsocketConnectionError, ConnectionRefusedError, ssl.SSLError, OSError) as e:
            # MQTT hiccup → rebuild client on next loop
            client = None

        except Exception as e:
            # Unknown error — don’t die, just report and continue
            try:
                if client is None:
                    client = build_client()
                publish_status(client, "down", reason=f"watcher error: {e}")
            except Exception:
                pass

        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()

