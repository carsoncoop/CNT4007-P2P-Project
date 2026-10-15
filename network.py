import socket
import threading
import queue
import struct

from message import Message
from listener import MessageListener


def recv_exact(sock: socket.socket, n: int) -> bytes:
    """Read exactly n bytes, looping because recv() may return fewer.

    Raises ConnectionError if the peer closes before n bytes arrive.
    """
    # TODO
    raise NotImplementedError


def read_message(sock: socket.socket) -> Message:
    """Read one length-prefixed message from the socket."""
    # TODO: read 4-byte length, then type byte, then (length - 1) payload bytes
    raise NotImplementedError


class NeighborConnection:
    """One socket to one neighbor, with a reader thread and a writer thread."""

    def __init__(self, neighbor_id: int, sock: socket.socket, listener: MessageListener):
        # TODO: store fields, create the outgoing queue.Queue
        self.neighbor_id = neighbor_id
        raise NotImplementedError

    def start(self) -> None:
        """Start the reader and writer threads."""
        raise NotImplementedError

    def send(self, msg: Message) -> None:
        """Non-blocking: put msg on the writer queue. Silently drop if closed."""
        raise NotImplementedError

    def close(self) -> None:
        """Stop both threads and close the socket."""
        raise NotImplementedError

    def _reader_loop(self) -> None:
        """Read messages and call listener.on_message until the socket closes."""
        raise NotImplementedError

    def _writer_loop(self) -> None:
        """Take messages off the queue and write them to the socket."""
        raise NotImplementedError


class ConnectionManager:
    """Listens for later peers and connects to earlier ones."""

    def __init__(self, my_id: int, peers: list, listener: MessageListener):
        # peers: list of (peer_id, host, port) in PeerInfo.cfg order
        raise NotImplementedError

    def start(self) -> None:
        """Connect to every peer listed before me, and start the accept thread."""
        raise NotImplementedError

    def _connect_to(self, peer_id: int, host: str, port: int) -> None:
        """Outgoing: connect, send handshake, read + verify reply, register."""
        raise NotImplementedError

    def _accept_loop(self, port: int) -> None:
        """Incoming: accept, read handshake to learn who it is, reply, register."""
        raise NotImplementedError

    def _register(self, neighbor_id: int, sock: socket.socket) -> None:
        """Create NeighborConnection, store it by ID, start it, call on_connected."""
        raise NotImplementedError

    def shutdown(self) -> None:
        raise NotImplementedError
