import json
import urllib.request

class GitHubReleasesTransceiver:
    @staticmethod
    def fetch_latest_release_packet(owner, repo):
        url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Cortex-Releases", "Accept": "application/vnd.github.v3+json"})
            with urllib.request.urlopen(req, timeout=3.5) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode('utf-8'))
                    # Đọc nội dung gói tin đính kèm trong body release
                    body = data.get("body", "{}")
                    return json.loads(body)
        except Exception: pass
        return None
