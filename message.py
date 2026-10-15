# Message type values from the project spec
# assigns each message type with a number
# that number is the number that is sent on the wire
import struct


CHOKE = 0
UNCHOKE = 1
INTERESTED = 2
NOT_INTERESTED = 3
HAVE = 4
BITFIELD = 5
REQUEST = 6
PIECE = 7

# represents one message: type + payload
class Message:
    """Immutable message. payload is always bytes (b"" when there is none)."""

    def __init__(self, msg_type: int, payload: bytes = b""):
        # TODO: store type and payload
        self._type = msg_type
        self._payload = payload

    # factories: let B and C build messages without touching raw bytes
    @staticmethod
    def choke() -> "Message":
        return Message(msg_type=CHOKE)

    @staticmethod
    def unchoke() -> "Message":
        return Message(msg_type=UNCHOKE)

    @staticmethod
    def interested() -> "Message":
        return Message(msg_type=INTERESTED)

    @staticmethod
    def not_interested() -> "Message":
        return Message(msg_type=NOT_INTERESTED)

    @staticmethod
    def have(piece_index: int) -> "Message":
        return Message(msg_type=HAVE, payload=struct.pack(">I", piece_index))

    @staticmethod
    def bitfield(bits: bytes) -> "Message":
        return Message(msg_type=BITFIELD, payload=bits)

    @staticmethod
    def request(piece_index: int) -> "Message":
        return Message(msg_type=REQUEST, payload=struct.pack(">I", piece_index))

    @staticmethod
    def piece(piece_index: int, data: bytes) -> "Message":
        return Message(msg_type=PIECE, payload=struct.pack(">I", piece_index) + data)

    # ---- accessors ----
    @property
    def type(self) -> int:
        return self._type

    @property
    def payload(self) -> bytes:
        return self._payload

    def piece_index(self) -> int:
        payload = self._payload[0:4]
        return struct.unpack(">I", payload)[0]

    def piece_data(self) -> bytes:
        payload = self._payload[4:]
        return payload

    def bitfield_bits(self) -> bytes:
        return self._payload

    # ---- wire format ----
    def encode(self) -> bytes:
        """Return length + type + payload, ready to send on a socket."""
        # [length][type][payload]
        struct_format = ">IB" + str(len(self._payload)) + "s"
        return struct.pack(struct_format, len(self._payload) + 1, self._type, self._payload)
