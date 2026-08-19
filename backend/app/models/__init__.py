"""SQLAlchemy models package.

Import every model module here so that (a) `Base.metadata` is fully populated
for Alembic autogeneration, and (b) all string-based `relationship()` targets
resolve when mappers are configured.
"""

from app.models.base import Base
from app.models.chat import ChatMessage, ChatSession, Notification, SupportTicket
from app.models.customer import Customer
from app.models.device import Device
from app.models.knowledge import KnowledgeBaseArticle
from app.models.support_agent import SupportAgent
from app.models.telematics import DrivingEvent, Driver, Trip, Vehicle

__all__ = [
    "Base",
    "Customer",
    "Device",
    "Driver",
    "Vehicle",
    "Trip",
    "DrivingEvent",
    "ChatSession",
    "ChatMessage",
    "SupportTicket",
    "Notification",
    "SupportAgent",
    "KnowledgeBaseArticle",
]
