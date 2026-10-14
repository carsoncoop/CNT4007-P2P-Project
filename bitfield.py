"""Bitfield: which pieces a peer has. Piece 0 is the HIGH bit of byte 0."""
import math


class Bitfield:
    def __init__(self, num_pieces, full=False):
        self.num_pieces = num_pieces
        self._bits = bytearray(math.ceil(num_pieces / 8))
        if full:
            for i in range(num_pieces):  # sets real bits only; spare stay 0
                self.set(i)

    def _check(self, index):
        if not 0 <= index < self.num_pieces:
            raise IndexError(f"piece index {index} out of range "
                             f"(0..{self.num_pieces - 1})")

    def set(self, index):
        self._check(index)
        self._bits[index // 8] |= 0x80 >> (index % 8)

    def has(self, index):
        self._check(index)
        return bool(self._bits[index // 8] & (0x80 >> (index % 8)))

    def count(self):
        return sum(bin(b).count("1") for b in self._bits)

    def is_complete(self):
        return self.count() == self.num_pieces

    def to_bytes(self):
        """Payload for a 'bitfield' message."""
        return bytes(self._bits)

    @classmethod
    def from_bytes(cls, data, num_pieces):
        """Build from a received 'bitfield' payload; reject malformed data."""
        expected = math.ceil(num_pieces / 8)
        if len(data) != expected:
            raise ValueError(f"bitfield is {len(data)} bytes, expected {expected}")
        spare = expected * 8 - num_pieces
        if spare and data[-1] & ((1 << spare) - 1):
            raise ValueError("spare bits in bitfield must be zero")
        bf = cls(num_pieces)
        bf._bits = bytearray(data)
        return bf

    def missing_from(self, other):
        """Indices that `other` has and I don't (what I could request)."""
        if other.num_pieces != self.num_pieces:
            raise ValueError("bitfields have different sizes")
        return [i for i in range(self.num_pieces)
                if other.has(i) and not self.has(i)]