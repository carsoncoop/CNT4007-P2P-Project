"""Handshake message: 18-byte header + 10 zero bytes + 4-byte peer ID = 32 bytes. (Owner: Person A)"""

HEADER = b"P2PFILESHARINGPROJ"   # 18 bytes
HANDSHAKE_LENGTH = 32


def build_handshake(peer_id: int) -> bytes:
    """Return the 32-byte handshake for this peer."""
    # TODO
    raise NotImplementedError


def parse_handshake(data: bytes) -> int:
    """Check length and header; return the sender's peer ID.

    Raises ValueError if the data is not a valid handshake.
    The caller compares the returned ID against the expected one.
    """
    # TODO
    raise NotImplementedError
