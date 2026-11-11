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


# Check that a message makes sense before we use it.
#
# This runs every time a Message is created, including messages we read off
# the network. So if a neighbor sends something broken (a type that doesn't
# exist, or a HAVE with the wrong number of bytes), we find out right away
# with a clear error. Without this, a broken message could crash the program
# much later in a confusing spot, like when we try to unpack a piece index
# that isn't there.
#
# What each type's payload should look like:
#   choke, unchoke, interested, not interested -> nothing
#   have, request                              -> 4 byte piece index
#   bitfield                                   -> any number of bytes
#   piece                                      -> 4 byte piece index + the data
def check_message(msg_type, payload):
    # the types are numbered 0 through 7, so anything else is made up
    if msg_type < CHOKE or msg_type > PIECE:
        raise ValueError("Unknown message type")
    # types 0-3 are just signals, they carry no data
    if msg_type <= NOT_INTERESTED and len(payload) != 0:
        raise ValueError("This message type should have no payload")
    if (msg_type == HAVE or msg_type == REQUEST) and len(payload) != 4:
        raise ValueError("Payload should be 4 bytes")
    # a piece needs at least the 4 byte index; the data after it can be any
    # size (the last piece of the file is usually smaller than the others)
    if msg_type == PIECE and len(payload) < 4:
        raise ValueError("Piece payload is too short")


# represents one message: type + payload
class Message:
    """Immutable message. payload is always bytes (b"" when there is none)."""

    def __init__(self, msg_type: int, payload: bytes = b""):
        # bytes() makes a copy we own, so if someone passes in a bytearray and
        # changes it later, our message doesn't change with it
        payload = bytes(payload)
        check_message(msg_type, payload)
        self._type = msg_type
        self._payload = payload

    # Two messages are equal if the type and payload match. Without this, ==
    # would only be True if both sides were the exact same object, which makes
    # it hard to check in tests that the message we received is the one we sent.
    def __eq__(self, other):
        if not isinstance(other, Message):
            return False
        return self._type == other._type and self._payload == other._payload

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

    # The opposite of encode(): turns the bytes of one full message back into
    # a Message. Message.decode(msg.encode()) gives back a message equal to msg.
    #
    # The real program reads from sockets with read_message() in network.py
    # instead, since it has to pull the bytes off the socket a bit at a time.
    # This one is for when you already have all the bytes, like in the tests.
    @staticmethod
    def decode(data: bytes) -> "Message":
        # need at least the 4 byte length and the 1 byte type
        if len(data) < 5:
            raise ValueError("Message is too short")
        length = struct.unpack(">I", data[0:4])[0]
        # the length field should count everything after itself
        if length != len(data) - 4:
            raise ValueError("Length field doesn't match the message")
        # byte 4 is the type, everything after it is the payload
        return Message(data[4], data[5:])
