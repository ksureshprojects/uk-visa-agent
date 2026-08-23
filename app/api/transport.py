"""Abstraction over the messaging channel a conversation arrives through.

Only WebChatTransport exists in this demo. A WhatsApp or Telegram transport
plugs in here later by implementing the same two methods — the orchestrator
and agents never change, since they only ever see (external_user_id, text)
in and a reply string out.
"""

from abc import ABC, abstractmethod


class ChatTransport(ABC):
    @abstractmethod
    def parse_inbound(self, payload: dict) -> tuple[str, str]:
        """Return (external_user_id, message_text) from a channel-specific inbound payload."""

    @abstractmethod
    def format_outbound(self, text: str) -> dict:
        """Shape the agent's reply into whatever this channel's send API expects."""


class WebChatTransport(ChatTransport):
    def parse_inbound(self, payload: dict) -> tuple[str, str]:
        return payload["external_user_id"], payload["text"]

    def format_outbound(self, text: str) -> dict:
        return {"text": text}
