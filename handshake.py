import struct

HEADER = b"P2PFILESHARINGPROJ"   # 18 bytes
HANDSHAKE_LENGTH = 32


def build_handshake(peer_id: int) -> bytes:
    return HEADER + (b"\x00" * 10) + struct.pack(">I", peer_id)


def parse_handshake(data: bytes) -> int:
    if len(data) != HANDSHAKE_LENGTH:
        raise ValueError(f"Handshake must be {HANDSHAKE_LENGTH} bytes, got {len(data)}")
    if not data.startswith(HEADER):
        raise ValueError(f"Handshake must start with {HEADER}, got {data[:18]}")
    peer_id_bytes = data[28:32]
    peer_id = struct.unpack(">I", peer_id_bytes)[0]
    return peer_id
