import paho.mqtt.client as mqtt
import ssl
import time
import json
import sys

# ==== KONFIGURACE ====
BROKER = "10.20.10.174"
PORT = 8883
USERNAME = "bblp"
PASSWORD = "39268046"
CA_CERT = "/home/tron02/printer.cer"
SERIAL = "01P00A432500021"

# ---- PAYLOADY ----
def payload_home():
    return {
        "print": {
            "command": "gcode_line",
            "param": "G28 \n",
            "sequence_id": str(int(time.time() * 1000)),
            "user_id": "123456789"
        }
    }

def payload_gcode_line(line):
    return {
        "print": {
            "command": "gcode_line",
            "param": line + "\n",
            "sequence_id": str(int(time.time() * 1000)),
            "user_id": "123456789"
        }
    }

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

def payload_bed_temp(temp):
    return payload_gcode_line(f"M140 S{temp}")

def payload_nozzle_temp(temp):
    return payload_gcode_line(f"M104 S{temp}")

# ---- MQTT CALLBACKY ----
def on_connect(client, userdata, flags, rc):
    if rc != 0:
        print(f"[!] Chyba připojení: {rc}")
        return

    print("[OK] Připojeno k brokeru")
    topic_pub = f"device/{SERIAL}/request"

    if CMD == "home":
        payload = payload_home()

    elif CMD == "light_on":
        payload = payload_light("on")

    elif CMD == "light_off":
        payload = payload_light("off")

    elif CMD == "move":
        # vždy nejdřív home
        print("[*] Posílám HOME před pohybem...")
        client.publish(topic_pub, json.dumps(payload_home()))
        time.sleep(5)
        print(f"[*] Posílám pohybový G-code: {MOVE_CMD}")
        payload = payload_gcode_line(MOVE_CMD)

    elif CMD == "bed_temp":
        payload = payload_bed_temp(TEMP)

    elif CMD == "nozzle_temp":
        payload = payload_nozzle_temp(TEMP)

    else:
        print(f"[!] Neznámý příkaz: {CMD}")
        client.disconnect()
        return

    client.publish(topic_pub, json.dumps(payload))
    print(f"[>] Odeslán příkaz '{CMD}': {json.dumps(payload)}")

def on_message(client, userdata, msg):
    try:
        payload_str = msg.payload.decode(errors='ignore')
    except:
        payload_str = str(msg.payload)
    print(f"[REPORT] {msg.topic}: {payload_str}")

# ---- HLAVNÍ PROGRAM ----
if len(sys.argv) < 2:
    print("Použití:")
    print(" python printer_cmd.py home")
    print(" python printer_cmd.py light_on")
    print(" python printer_cmd.py light_off")
    print(" python printer_cmd.py move \"G1 X50 Y50 Z10 F3000\"")
    print(" python printer_cmd.py bed_temp 60")
    print(" python printer_cmd.py nozzle_temp 210")
    sys.exit(1)

CMD = sys.argv[1]
MOVE_CMD = None
TEMP = None

if CMD == "move" and len(sys.argv) >= 3:
    MOVE_CMD = sys.argv[2]
elif CMD in ("bed_temp", "nozzle_temp") and len(sys.argv) >= 3:
    try:
        TEMP = int(sys.argv[2])
    except ValueError:
        print("[!] Teplota musí být celé číslo")
        sys.exit(1)

client = mqtt.Client(client_id="mqtt_printer_cmd_tron02", clean_session=True)
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

print(f"[*] Připojuji se a odesílám příkaz '{CMD}'...")
client.connect(BROKER, PORT, keepalive=60)

client.loop_start()
time.sleep(6)
client.loop_stop()
client.disconnect()
