from flask import Flask, render_template, request, jsonify
import time
import json
import ssl
import threading
import paho.mqtt.client as mqtt

app = Flask(__name__)

# ==== KONFIGURACE MQTT ====
BROKER = "10.20.10.174"
PORT = 8883
USERNAME = "bblp"
PASSWORD = "39268046"
CA_CERT = "/home/tron02/printer.cer"
SERIAL = "01P00A432500021"

camera_on = False
camera_topic = f"device/{SERIAL}/camera/control"

CURRENT_BED_TEMP = 0
CURRENT_NOZZLE_TEMP = 0

mqtt_client = mqtt.Client(client_id="flask_mqtt_client", clean_session=True)
mqtt_client.username_pw_set(USERNAME, PASSWORD)
mqtt_client.tls_set(ca_certs=CA_CERT, certfile=None, keyfile=None,
                    cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLSv1_2)
mqtt_client.tls_insecure_set(True)

# MQTT callback
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("[MQTT] Připojeno k brokeru")
        # Přihlášení k topicu se stavem tiskárny
        client.subscribe(f"device/{SERIAL}/report")
    else:
        print(f"[MQTT] Chyba připojení: {rc}")

def on_message(client, userdata, msg):
    global CURRENT_BED_TEMP, CURRENT_NOZZLE_TEMP
    try:
        data = json.loads(msg.payload.decode())
        # Hledání teplot v MQTT payloadu
        if "print" in data and "bed_temper" in data["print"]:
            CURRENT_BED_TEMP = data["print"]["bed_temper"]
        if "print" in data and "nozzle_temper" in data["print"]:
            CURRENT_NOZZLE_TEMP = data["print"]["nozzle_temper"]
    except Exception as e:
        print("[MQTT] Chyba parsování:", e)

mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message
mqtt_client.connect(BROKER, PORT, keepalive=60)

# Spustíme MQTT loop v samostatném vlákně
mqtt_thread = threading.Thread(target=mqtt_client.loop_forever)
mqtt_thread.daemon = True
mqtt_thread.start()

# ===== Funkce pro payloady =====
def payload_home():
    return {"print": {"command": "gcode_line", "param": "G28\n",
                      "sequence_id": str(int(time.time()*1000)), "user_id": "123456789"}}

def payload_gcode(line):
    return {"print": {"command": "gcode_line", "param": line + "\n",
                      "sequence_id": str(int(time.time()*1000)), "user_id": "123456789"}}

def payload_light(mode):
    return {"system": {"sequence_id": str(int(time.time()*1000)), "command": "ledctrl",
                       "led_node": "chamber_light", "led_mode": mode, "led_on_time": 500,
                       "led_off_time": 500, "loop_times": 1, "interval_time": 1000}}

def send(topic, payload):
    mqtt_client.publish(topic, json.dumps(payload))

# ===== Web routes =====
@app.route("/")
def index():
    return render_template("index.html", camera_on=camera_on,
                           camera_stream_url="http://10.20.10.174:8080/stream")

@app.route("/api/command", methods=["POST"])
def api_command():
    global camera_on
    data = request.json
    cmd = data.get("cmd")
    arg = data.get("arg")
    topic = f"device/{SERIAL}/request"

    if cmd == "home":
        send(topic, payload_home())
    elif cmd == "light_on":
        send(topic, payload_light("on"))
    elif cmd == "light_off":
        send(topic, payload_light("off"))
    elif cmd == "move" and arg:
        send(topic, payload_gcode(arg))
    elif cmd == "bed_temp" and arg:
        send(topic, payload_gcode(f"M140 S{arg}"))
    elif cmd == "nozzle_temp" and arg:
        send(topic, payload_gcode(f"M104 S{arg}"))
    elif cmd == "extrude" and arg:
        send(topic, payload_gcode(f"G1 E{arg} F300"))
    elif cmd == "retract" and arg:
        send(topic, payload_gcode(f"G1 E-{arg} F300"))
    elif cmd == "toggle_camera":
        camera_on = not camera_on
        cam_mode = "on" if camera_on else "off"
        send(camera_topic, {"camera": {"mode": cam_mode}})
        return jsonify({"status": "ok", "camera_on": camera_on})
    else:
        return jsonify({"status": "error", "message": "Neplatný příkaz"}), 400

    return jsonify({"status": "ok", "cmd": cmd})

@app.route("/api/temps", methods=["GET"])
def api_temps():
    return jsonify({
        "bed": CURRENT_BED_TEMP,
        "nozzle": CURRENT_NOZZLE_TEMP
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)


# ---- Rozšířené příkazy pro ovládání tiskárny přes MQTT ----

@app.route('/printer/<int:pid>/cmd/<cmd>')
def printer_command(pid, cmd):
    printers = load_printers()
    mqtt_client.send_command(printers, pid, cmd)
    return redirect(url_for('printer_detail', pid=pid))

# Příklady využití:
# /printer/1/cmd/light_on
# /printer/1/cmd/light_off
# /printer/1/cmd/set_bed_temp_60
# /printer/1/cmd/set_nozzle_temp_210
