import paho.mqtt.client as mqtt
import ssl
import os

def check_status(ip, access_code):
    try:
        client = mqtt.Client()
        client.tls_set(certfile=None, keyfile=None, cert_reqs=ssl.CERT_NONE)
        client.tls_insecure_set(True)
        client.username_pw_set("bblp", access_code)
        client.connect(ip, 8883, 5)
        client.disconnect()
        return "online"
    except:
        return "offline"

def send_gcode(ip, access_code, serial, gcode_path):
    if not os.path.isfile(gcode_path):
        return False, "Soubor neexistuje"
    try:
        client = mqtt.Client()
        client.tls_set(certfile=None, keyfile=None, cert_reqs=ssl.CERT_NONE)
        client.tls_insecure_set(True)
        client.username_pw_set("bblp", access_code)
        client.connect(ip, 8883, 60)
        topic = f"device/{serial}/request"
        with open(gcode_path, "rb") as f:
            client.publish(topic, f.read(), qos=1)
        client.disconnect()
        return True, "Soubor odeslán"
    except Exception as e:
        return False, str(e)

def send_command(ip, access_code, serial, command):
    try:
        client = mqtt.Client()
        client.tls_set(certfile=None, keyfile=None, cert_reqs=ssl.CERT_NONE)
        client.tls_insecure_set(True)
        client.username_pw_set("bblp", access_code)
        client.connect(ip, 8883, 60)
        topic = f"device/{serial}/request"
        client.publish(topic, command.encode(), qos=1)
        client.disconnect()
        return True, "Příkaz odeslán"
    except Exception as e:
        return False, str(e)
