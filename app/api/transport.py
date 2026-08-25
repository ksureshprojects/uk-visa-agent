"""Abstraction over the messaging channel a message arrives through.

Only WebChatTransport exists in this demo. WhatsAppTransport (Twilio
Programmable Messaging) and EmailTransport (Twilio SendGrid Inbound Parse /
Mail Send) plug in here later by implementing the same two methods — see
MULTICHANNEL.md §4. The orchestrator and agents never change: everything
downstream of parse_inbound only ever sees (channel, address, text) in and
a reply string out, and address is what app.storage.repository normalizes
into an Identity — a raw phone number/email, not yet a verified person.
"""

from abc import ABC, abstractmethod

from app.storage.models import ChannelType


class ChatTransport(ABC):
    @abstractmethod
    def parse_inbound(self, payload: dict) -> tuple[ChannelType, str, str]:
        """Return (channel, address, message_text) from a channel-specific inbound payload."""

    @abstractmethod
    def format_outbound(self, text: str) -> dict:
        """Shape the agent's reply into whatever this channel's send API expects."""


class WebChatTransport(ChatTransport):
    def parse_inbound(self, payload: dict) -> tuple[ChannelType, str, str]:
        return ChannelType.WEB, payload["external_user_id"], payload["text"]

    def format_outbound(self, text: str) -> dict:
        return {"text": text}
