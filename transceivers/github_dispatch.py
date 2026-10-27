import json
import urllib.request

class GitHubDispatchTransceiver:
    @staticmethod
    def send_dispatch(owner, target_repo, event_type, token):
        if not token: return False
        url = f"https://api.github.com/repos/{owner}/{target_repo}/dispatches"
        payload = json.dumps({"event_type": event_type, "client_payload": {"sender": "cortex"}}).encode('utf-8')
        try:
            req = urllib.request.Request(
                url, data=payload,
                headers={
                    "Authorization": f"token {token}",
                    "Accept": "application/vnd.github.v3+json",
                    "User-Agent": "Cortex-Dispatcher"
                }
            )
            with urllib.request.urlopen(req, timeout=4) as resp:
                return resp.status == 204
        except Exception:
            return False
