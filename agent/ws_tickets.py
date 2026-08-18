"""Backward-compatible export. Implementation lives in agent.stores.tickets."""

from agent.stores.tickets import WebSocketTicket, WebSocketTicketStore

__all__ = ["WebSocketTicket", "WebSocketTicketStore"]
