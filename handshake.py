import struct

# Handshake length
HANDSHAKE_LENGTH = 32
# 18 byte header + 10 zero bytes
HEADER = b"P2PFILESHARINGPROJ" + (b"\x00" * 10)


def build_handshake(peer_id: int) -> bytes:
    return HEADER + struct.pack(">I", peer_id)


def parse_handshake(data: bytes) -> int:
    # make sure it's 32 bytes and has the right header and zero bytes
    if len(data) != HANDSHAKE_LENGTH or data[:28] != HEADER:
        raise ValueError("Invalid handshake received")

    # unpack the last 4 bytes
    peer_id = struct.unpack(">I", data[28:32])[0]
    return peer_id
