import hashlib

class NostrRelayTransceiver:
    @staticmethod
    def sign_event(pubkey, content):
        seed = f"{pubkey}_{content}".encode()
        event_id = hashlib.shake_256(seed).hexdigest(32)
        return {"id": event_id, "pubkey": pubkey, "content": content, "status": "nostr_signed"}
