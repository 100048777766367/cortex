import urllib.request
import json

class MqttMeshTransceiver:
    @staticmethod
    def broadcast_signal(topic, payload_dict):
        try:
            data = json.dumps({"topic": topic, "message": payload_dict}).encode('utf-8')
            req = urllib.request.Request("https://httpbin.org/post", data=data, headers={"User-Agent": "Cortex-MQTT"})
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                return resp.status == 200
        except Exception: return False
