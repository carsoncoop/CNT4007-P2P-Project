"""This file does the parsing for Common.cfg and PeerInfo.cfg."""
import math
from dataclasses import dataclass


@dataclass
class CommonConfig:
    num_preferred_neighbors: int
    unchoking_interval: int
    optimistic_unchoking_interval: int
    file_name: str
    file_size: int
    piece_size: int

    @property
    def num_pieces(self):
        return math.ceil(self.file_size / self.piece_size)

    def piece_length(self, index):
        """Size in bytes of piece `index`; the last piece may be shorter."""
        if not 0 <= index < self.num_pieces:
            raise IndexError(f"piece index {index} out of range")
        if index == self.num_pieces - 1:
            return self.file_size - index * self.piece_size
        return self.piece_size


@dataclass
class PeerInfo:
    peer_id: int
    host: str
    port: int
    has_file: bool


_COMMON_KEYS = {
    "NumberOfPreferredNeighbors": ("num_preferred_neighbors", int),
    "UnchokingInterval": ("unchoking_interval", int),
    "OptimisticUnchokingInterval": ("optimistic_unchoking_interval", int),
    "FileName": ("file_name", str),
    "FileSize": ("file_size", int),
    "PieceSize": ("piece_size", int),
}


def load_common(path="Common.cfg"):
    values = {}
    with open(path) as f:
        for line in f:
            parts = line.split()
            if not parts:
                continue
            if len(parts) != 2 or parts[0] not in _COMMON_KEYS:
                raise ValueError(f"bad line in {path}: {line.strip()!r}")
            field, convert = _COMMON_KEYS[parts[0]]
            values[field] = convert(parts[1])
    missing = {name for name, _ in _COMMON_KEYS.values()} - values.keys()
    if missing:
        raise ValueError(f"{path} is missing: {sorted(missing)}")
    return CommonConfig(**values)


def load_peers(path="PeerInfo.cfg"):
    """Return peers in file order (order decides who connects to whom)."""
    peers = []
    with open(path) as f:
        for line in f:
            parts = line.split()
            if not parts:
                continue
            if len(parts) != 4 or parts[3] not in ("0", "1"):
                raise ValueError(f"bad line in {path}: {line.strip()!r}")
            peers.append(PeerInfo(int(parts[0]), parts[1], int(parts[2]),
                                  parts[3] == "1"))
    return peers


def get_peer(peers, peer_id):
    for p in peers:
        if p.peer_id == peer_id:
            return p
    raise KeyError(f"peer {peer_id} not in PeerInfo.cfg")