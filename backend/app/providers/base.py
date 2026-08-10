"""
Abstract base classes for SMS and Notification providers.
All providers must implement these interfaces.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass
class SMSResult:
    """Result of an SMS send operation."""
    success: bool
    provider_message_id: Optional[str] = None
    status: str = "unknown"
    error: Optional[str] = None
    segments: int = 1
    raw_response: Optional[dict] = None


@dataclass
class DeliveryStatus:
    """Delivery status from the provider."""
    provider_message_id: str
    status: str  # delivered, failed, unknown
    delivered_at: Optional[datetime] = None
    error: Optional[str] = None
    raw: Optional[dict] = None


@dataclass
class InboundMessage:
    """Parsed inbound SMS from provider."""
    provider_message_id: str
    from_number: str
    to_number: str
    body: str
    received_at: datetime
    raw: Optional[dict] = None


@dataclass
class GatewayHealth:
    """Gateway health check result."""
    is_healthy: bool
    status: str = "unknown"
    error: Optional[str] = None
    last_checked: Optional[datetime] = None


class SMSProvider(ABC):
    """Abstract interface for SMS gateways."""

    @abstractmethod
    async def test_connection(self) -> bool:
        """Test the connection to the SMS gateway."""
        ...

    @abstractmethod
    async def send_sms(
        self,
        to_number: str,
        message: str,
        sender_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> SMSResult:
        """Send an SMS message via the gateway."""
        ...

    @abstractmethod
    async def get_message_status(self, provider_message_id: str) -> DeliveryStatus:
        """Get the delivery status of a sent message."""
        ...

    @abstractmethod
    async def health_check(self) -> GatewayHealth:
        """Check gateway health/connectivity."""
        ...

    @abstractmethod
    def parse_inbound_message(self, raw_data: dict) -> InboundMessage:
        """Parse an inbound message from webhook/poll data."""
        ...

    @abstractmethod
    def parse_delivery_status(self, raw_data: dict) -> DeliveryStatus:
        """Parse a delivery status update from webhook/poll data."""
        ...

    @abstractmethod
    def supports_webhooks(self) -> bool:
        """Whether this provider supports webhooks."""
        ...

    @abstractmethod
    def supports_polling(self) -> bool:
        """Whether this provider requires polling."""
        ...


class NotificationProvider(ABC):
    """Abstract interface for push notification providers."""

    @abstractmethod
    async def send_notification(self, title: str, body: str, **kwargs) -> bool:
        """Send a push notification."""
        ...

    @abstractmethod
    async def test_notification(self) -> bool:
        """Send a test notification."""
        ...

    @abstractmethod
    def is_configured(self) -> bool:
        """Check if the provider has valid configuration."""
        ...
