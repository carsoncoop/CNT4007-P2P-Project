#Reads Common.cfg and PeerInfo.cfg so the rest of the program doesn't have
# to deal with parsing text files.
import math
from dataclasses import dataclass


# everything from Common.cfg
@dataclass
class CommonConfig:
    num_preferred_neighbors: int
    unchoking_interval: int
    optimistic_unchoking_interval: int
    file_name: str
    file_size: int
    piece_size: int

    # file size / piece size, rounded up. For the spec example that's
    # 10000232 / 32768 = 306 pieces
    @property
    def num_pieces(self):
        return math.ceil(self.file_size / self.piece_size)

    # Every piece is piece_size bytes except the last one, which is just
    # whatever is left over (5992 bytes in the spec example).
    def piece_length(self, index):
        if not 0 <= index < self.num_pieces:
            raise IndexError(f"piece index {index} out of range")
        if index == self.num_pieces - 1:
            return self.file_size - index * self.piece_size
        return self.piece_size


# one line from PeerInfo.cfg
@dataclass
class PeerInfo:
    peer_id: int
    host: str
    port: int
    has_file: bool


# name in the cfg file -> (our field name, what type to convert it to)
_COMMON_KEYS = {
    "NumberOfPreferredNeighbors": ("num_preferred_neighbors", int),
    "UnchokingInterval": ("unchoking_interval", int),
    "OptimisticUnchokingInterval": ("optimistic_unchoking_interval", int),
    "FileName": ("file_name", str),
    "FileSize": ("file_size", int),
    "PieceSize": ("piece_size", int),
}


# Each line looks like "Name value". Blank lines get skipped. Anything else
# that looks off raises an error right away, so a typo in the cfg shows up at
# startup instead of breaking something later.
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
    # make sure none of the settings got left out
    missing = {name for name, _ in _COMMON_KEYS.values()} - values.keys()
    if missing:
        raise ValueError(f"{path} is missing: {sorted(missing)}")
    return CommonConfig(**values)


# Each line looks like "id host port hasFile", where hasFile is 1 or 0.
# Peers stay in the same order as the file, since that order decides who
# connects to who.
def load_peers(path="PeerInfo.cfg"):
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


# find one peer by its id
def get_peer(peers, peer_id):
    for p in peers:
        if p.peer_id == peer_id:
            return p
    raise KeyError(f"peer {peer_id} not in PeerInfo.cfg")
