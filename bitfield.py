# bitfield.py
#
# Keeps track of which pieces a peer has, 1 bit per piece.
# The spec says piece 0 is the HIGHEST bit of the first byte, so piece 0 is
# 0x80, piece 1 is 0x40, and so on. Piece 8 starts over in the next byte.
# Any leftover bits at the end of the last byte have to stay 0.
import math


class Bitfield:
    # full=True is for a peer that starts with the whole file
    def __init__(self, num_pieces, full=False):
        self.num_pieces = num_pieces
        self._bits = bytearray(math.ceil(num_pieces / 8))
        if full:
            for i in range(num_pieces):  # only the real pieces, leftover bits stay 0
                self.set(i)

    # just makes sure the index is actually a piece in the file
    def _check(self, index):
        if not 0 <= index < self.num_pieces:
            raise IndexError(f"piece index {index} out of range "
                             f"(0..{self.num_pieces - 1})")

    # index // 8 picks the byte, 0x80 >> (index % 8) picks the bit inside it
    def set(self, index):
        self._check(index)
        self._bits[index // 8] |= 0x80 >> (index % 8)

    def has(self, index):
        self._check(index)
        return bool(self._bits[index // 8] & (0x80 >> (index % 8)))

    # counts up all the 1 bits, which is how many pieces we have
    def count(self):
        return sum(bin(b).count("1") for b in self._bits)

    def is_complete(self):
        return self.count() == self.num_pieces

    # this is what gets sent as the payload of a bitfield message
    def to_bytes(self):
        return bytes(self._bits)

    # Turns a bitfield message we got back into a Bitfield.
    # Also checks it's the right length and the leftover bits are 0. If not,
    # the other peer sent us garbage, so raise an error instead of using it.
    @classmethod
    def from_bytes(cls, data, num_pieces):
        expected = math.ceil(num_pieces / 8)
        if len(data) != expected:
            raise ValueError(f"bitfield is {len(data)} bytes, expected {expected}")
        spare = expected * 8 - num_pieces  # how many extra bits are at the end
        if spare and data[-1] & ((1 << spare) - 1):
            raise ValueError("spare bits in bitfield must be zero")
        bf = cls(num_pieces)
        bf._bits = bytearray(data)
        return bf

    # Pieces the other peer has that we don't. Basically the list of stuff we
    # could ask them for.
    def missing_from(self, other):
        if other.num_pieces != self.num_pieces:
            raise ValueError("bitfields have different sizes")
        return [i for i in range(self.num_pieces)
                if other.has(i) and not self.has(i)]
