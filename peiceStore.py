"""Piece storage on disk and tracking of outstanding requests."""
import os
import random
import threading

from bitfield import Bitfield


class PieceStore:
    """Owns peer_<id>/<file_name> and this peer's own bitfield."""

    def __init__(self, peer_id, common, has_file, base_dir="."):
        self.common = common
        self.dir = os.path.join(base_dir, f"peer_{peer_id}")
        os.makedirs(self.dir, exist_ok=True)
        self.path = os.path.join(self.dir, common.file_name)
        self._lock = threading.Lock()

        if has_file:
            if (not os.path.isfile(self.path)
                    or os.path.getsize(self.path) != common.file_size):
                raise FileNotFoundError(
                    f"{self.path} must exist with {common.file_size} bytes "
                    f"before starting peer {peer_id}")
            self.bitfield = Bitfield(common.num_pieces, full=True)
        else:
            with open(self.path, "wb") as f:
                f.truncate(common.file_size)  # preallocate; pieces land by offset
            self.bitfield = Bitfield(common.num_pieces)

    # ----- TODO (Carson): stubs that peerProcess.py already calls -----
    # These are on Carson's task list. peerProcess.py is wired up to use them,
    # so fill them in with these names (or update peerProcess.py to match).

    # Pick a random piece that their_bitfield has, we don't have, and we
    # haven't already requested from anyone. Mark it as requested and return
    # its index, or return None if there's nothing to ask for.
    def pick_request(self, their_bitfield):
        raise NotImplementedError("TODO (Carson): random piece selection")

    # Forget a pending request so the piece can be asked for again
    # (called when the neighbor chokes us or disconnects before sending it).
    def release_request(self, index):
        raise NotImplementedError("TODO (Carson): pending request tracking")

    # True if we have every piece of the file.
    def is_complete(self):
        raise NotImplementedError("TODO (Carson): do I have the whole file?")

    # ----- small helpers used by peerProcess.py -----

    # how many pieces we have (used to decide whether to send a bitfield)
    def piece_count(self):
        with self._lock:
            return self.bitfield.count()

    # our bitfield as bytes, ready to put in a BITFIELD message
    def bitfield_bytes(self):
        with self._lock:
            return self.bitfield.to_bytes()

    def has_piece(self, index):
        with self._lock:
            return self.bitfield.has(index)

    def read_piece(self, index):
        with self._lock:
            if not self.bitfield.has(index):
                raise KeyError(f"piece {index} not available")
            with open(self.path, "rb") as f:
                f.seek(index * self.common.piece_size)
                return f.read(self.common.piece_length(index))

    def write_piece(self, index, data):
        """Store a piece. Returns (pieces_held, file_complete), or None if
        the piece was already held (duplicate)."""
        if len(data) != self.common.piece_length(index):
            raise ValueError(f"piece {index} is {len(data)} bytes, "
                             f"expected {self.common.piece_length(index)}")
        with self._lock:
            if self.bitfield.has(index):
                return None
            with open(self.path, "r+b") as f:
                f.seek(index * self.common.piece_size)
                f.write(data)
            self.bitfield.set(index)
            return self.bitfield.count(), self.bitfield.is_complete()
