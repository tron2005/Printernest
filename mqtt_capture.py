import paho.mqtt.client as mqtt
import ssl
import time
import json

# ==== KONFIGURACE ====
BROKER = "10.20.10.174"                 # IP tiskárny
PORT = 8883
USERNAME = "bblp"                       # MQTT uživatel
PASSWORD = "39268046"                   # LAN Access Code
CA_CERT = "/home/tron02/printer.cer"    # cesta k certifikátu
SERIAL = "01P00A432500021"              # sériové číslo tiskárny

LOGFILE = "light_capture.log"

# ==== PAYLOAD pro rozsvícení světla ====
def payload_light(mode):
    return {
        "system": {
            "sequence_id": str(int(time.time() * 1000)),
            "command": "ledctrl",
            "led_node": "chamber_light",
            "led_mode": mode,
            "led_on_time": 500,
            "led_off_time": 500,
            "loop_times": 1,
            "interval_time": 1000
        }
    }

# ==== CALLBACKY ====
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("[OK] Připojeno k brokeru")
        topic_sub = f"device/{SERIAL}/report"
        client.subscribe(topic_sub)
        print(f"[*] Přihlášeno k tématu {topic_sub}")
        # Po připojení hned pošleme příkaz rozsvítit
        payload = payload_light("on")
        topic_pub = f"device/{SERIAL}/request"
        client.publish(topic_pub, json.dumps(payload))
        print(f"[>] Odeslán příkaz světla na {topic_pub}: {json.dumps(payload)}")
    else:
        print(f"[!] Chyba připojení: {rc}")

def on_disconnect(client, userdata, rc):
    print(f"[DISC] Odpojeno, rc={rc}")

def on_message(client, userdata, msg):
    try:
        payload_str = msg.payload.decode(errors='ignore')
    except:
        payload_str = str(msg.payload)

    log_line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg.topic}: {payload_str}"
    print(f"[REPORT] {log_line}")
    with open(LOGFILE, "a", encoding="utf-8") as f:
        f.write(log_line + "\n")

# ==== MQTT KLIENT ====
client = mqtt.Client(client_id="mqtt_light_sniffer_tron02", clean_session=True)
client.username_pw_set(USERNAME, PASSWORD)
client.on_connect = on_connect
client.on_message = on_message
client.on_disconnect = on_disconnect

client.tls_set(
    ca_certs=CA_CERT,
    certfile=None,
    keyfile=None,
    cert_reqs=ssl.CERT_REQUIRED,
    tls_version=ssl.PROTOCOL_TLSv1_2
)
client.tls_insecure_set(True)

print("[*] Připojuji se k brokeru...")
client.connect(BROKER, PORT, keepalive=300)
client.loop_forever()
