import paho.mqtt.client as mqtt
import ssl
import time
import json

# ==== KONFIGURACE ====
BROKER = "10.20.10.174"                 # IP tiskárny (LAN)
PORT = 8883
USERNAME = "bblp"                       # MQTT uživatel (LAN Developer Mode)
PASSWORD = "39268046"                   # LAN Access Code
CA_CERT = "/home/tron02/printer.cer"    # cesta k certifikátu
SERIAL = "01P00A432500021"              # sériové číslo tiskárny

# ==== MQTT CALLBACKY ====
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("[OK] Připojeno k brokeru")
        topic_pub = f"device/{SERIAL}/request"

        # přesný payload podle Bambu Studia pro příkaz G28 (home)
        payload = {
            "print": {
                "command": "gcode_line",
                "param": "G28 \n",
                "sequence_id": str(int(time.time() * 1000)),
                "user_id": "123456789"  # může být libovolné, Studio posílá ID účtu
            }
        }

        client.publish(topic_pub, json.dumps(payload))
        print(f"[>] Odeslán příkaz Home na {topic_pub}:\n    {json.dumps(payload)}")
    else:
        print(f"[!] Chyba při připojení: {rc}")

def on_message(client, userdata, msg):
    try:
        payload_str = msg.payload.decode(errors='ignore')
    except:
        payload_str = str(msg.payload)
    print(f"[REPORT] {msg.topic}: {payload_str}")

# ==== MQTT KLIENT ====
client = mqtt.Client(client_id="mqtt_home_command_tron02", clean_session=True)
client.username_pw_set(USERNAME, PASSWORD)
client.on_connect = on_connect
client.on_message = on_message

client.tls_set(
    ca_certs=CA_CERT,
    certfile=None,
    keyfile=None,
    cert_reqs=ssl.CERT_REQUIRED,
    tls_version=ssl.PROTOCOL_TLSv1_2
)
client.tls_insecure_set(True)

print("[*] Připojuji se a odesílám příkaz Home (G28)...")
client.connect(BROKER, PORT, keepalive=60)

# necháme klienta ještě pár sekund poslouchat odpověď
client.loop_start()
time.sleep(5)
client.loop_stop()
client.disconnect()
