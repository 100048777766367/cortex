import hashlib

class NostrRelayTransceiver:
    @staticmethod
    def sign_event(pubkey, content):
        # Mơ phỏng ký điện tử sự kiện thần kinh lên trạm phát thanh phi tập trung
        event_id = hashlib.shake_256(f"{pubkey}_{content}".encode()).hexdigest(32)
        return {
            "id": event_id,
            "pubkey": pubkey,
            "content": content,
            "sig": "verified_by_bodhi_cortex"
        }
