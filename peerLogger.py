import os
import threading
from datetime import datetime

"""Writes every log line to log_peer_[id].log."""
class PeerLogger:
    def __init__(self, own_peer_id, folder=".", writer=None, current_time_text=None):
        """Tests can pass their own writer and clock."""
        self._own_peer_id = own_peer_id
        self._lock = threading.Lock()
        self._is_closed = False
        self._current_time_text = current_time_text or (
            lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        if writer is None:
            path = os.path.join(folder, f"log_peer_{own_peer_id}.log")
            self._writer = open(path, "w", encoding="utf-8")   # fresh log each run
            self._we_opened_writer = True
        else:
            self._writer = writer
            self._we_opened_writer = False

    def _write_line(self, sentence):
        with self._lock:
            if self._is_closed:
                return
            self._writer.write(
                f"[{self._current_time_text()}]: Peer {self._own_peer_id} {sentence}\n")
            self._writer.flush()

    def log_made_connection_to(self, other_peer_id):
        self._write_line(f"makes a connection to Peer {other_peer_id}.")

    def log_got_connection_from(self, other_peer_id):
        self._write_line(f"is connected from Peer {other_peer_id}.")

    def log_preferred_neighbors_changed(self, neighbor_ids):
        self._write_line(
            "has the preferred neighbors " + ",".join(str(i) for i in neighbor_ids) + ".")

    def log_optimistic_neighbor_changed(self, neighbor_id):
        self._write_line(f"has the optimistically unchoked neighbor {neighbor_id}.")

    def log_unchoked_by(self, neighbor_id):
        self._write_line(f"is unchoked by {neighbor_id}.")

    def log_choked_by(self, neighbor_id):
        self._write_line(f"is choked by {neighbor_id}.")

    def log_received_have(self, from_peer_id, piece_index):
        self._write_line(
            f"received the 'have' message from {from_peer_id} for the piece {piece_index}.")

    def log_received_interested(self, from_peer_id):
        self._write_line(f"received the 'interested' message from {from_peer_id}.")

    def log_received_not_interested(self, from_peer_id):
        self._write_line(f"received the 'not interested' message from {from_peer_id}.")

    def log_downloaded_piece(self, piece_index, from_peer_id, pieces_we_have_now):
        self._write_line(
            f"has downloaded the piece {piece_index} from {from_peer_id}. "
            f"Now the number of pieces it has is {pieces_we_have_now}.")

    def log_downloaded_whole_file(self):
        self._write_line("has downloaded the complete file.")

    def close(self):
        with self._lock:
            if self._is_closed:
                return
            self._is_closed = True
            self._writer.flush()
            if self._we_opened_writer:
                self._writer.close()