import urllib.request
import json

class GitNativeTransceiver:
    @staticmethod
    def fetch_raw_peer(owner, peer_repo, filename="synapse_forward.json"):
        for branch in ["main", "master", "HEAD"]:
            url = f"https://raw.githubusercontent.com/{owner}/{peer_repo}/{branch}/{filename}"
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Cortex-GitNative"})
                with urllib.request.urlopen(req, timeout=3.0) as resp:
                    if resp.status == 200:
                        return json.loads(resp.read().decode('utf-8'))
            except Exception: continue
        return None
