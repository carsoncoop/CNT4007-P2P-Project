# listener.py  (a base class or a typing.Protocol)
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from message import Message
    from network import NeighborConnection


class MessageListener:
    def on_message(self, neighbor_id: int, msg: Message) -> None: ...
    def on_connected(self, conn: NeighborConnection) -> None: ...
    def on_disconnected(self, neighbor_id: int) -> None: ...
