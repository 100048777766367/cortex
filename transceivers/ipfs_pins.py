class IpfsPinsTransceiver:
    @staticmethod
    def resolve_cid_pointer(cid_hash):
        return f"https://ipfs.io/ipfs/{cid_hash}"
