import socket
import threading
import queue
import struct

from message import Message
from listener import MessageListener


def recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError(f"Socket closed with {n - len(buf)} bytes left to read")
        buf.extend(chunk)
    return bytes(buf)


def read_message(sock: socket.socket) -> Message:
    length = struct.unpack('!I', recv_exact(sock, 4))[0]
    msg_type = recv_exact(sock, 1)[0]
    payload = recv_exact(sock, length - 1)
    return Message(msg_type, payload)


class NeighborConnection:
    def __init__(self, neighbor_id: int, sock: socket.socket, listener: MessageListener):
        self.neighbor_id = neighbor_id
        self.sock = sock
        self.listener = listener
        self.is_closed = False
        self.writer_queue = queue.Queue()

    def start(self) -> None:
        if self.is_closed:
            raise RuntimeError("Cannot start a closed connection")
        self.reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self.writer_thread = threading.Thread(target=self._writer_loop, daemon=True)
        self.reader_thread.start()
        self.writer_thread.start()


    def send(self, msg: Message) -> None:
        if not self.is_closed:
            self.writer_queue.put(msg)

    def close(self) -> None:
        """Stop both threads and close the socket."""
        if self.is_closed:
            return                      
        self.is_closed = True
        self.writer_queue.put(None)     
        try:
            self.sock.close()           
        except OSError:
            pass

    def _reader_loop(self) -> None:
        """Read messages and call listener.on_message until the socket closes."""
        try:
            while not self.is_closed:
                msg = read_message(self.sock)
                self.listener.on_message(self.neighbor_id, msg)
        except OSError:
            pass                        
        finally:
            self.close()
            self.listener.on_disconnected(self.neighbor_id)

    def _writer_loop(self) -> None:
        """Take messages off the queue and write them to the socket."""
        while not self.is_closed:
            msg = self.writer_queue.get()   
            if msg is None:                 
                break
            try:
                self.sock.sendall(msg.encode())
            except OSError:
                self.close()
                break


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
