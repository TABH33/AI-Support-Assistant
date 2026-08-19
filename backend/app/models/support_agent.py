"""SupportAgent model.

ER relationships owned by this entity (ASS3 Sec 3):
  SupportAgent 1 -> Many SupportTicket
"""

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.enums import AccessLevel

if TYPE_CHECKING:
    from app.models.chat import SupportTicket


class SupportAgent(Base):
    """A human support agent who can be assigned SupportTicket records.

    AccessLevel tiers per ASS3 Sec 3.6.
    """

    __tablename__ = "support_agents"

    support_agent_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    access_level: Mapped[AccessLevel] = mapped_column(
        Enum(
            AccessLevel,
            name="access_level",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=AccessLevel.TIER_1,
    )
    # Present now (rather than added in the Task 6 auth task) because Task 6's own
    # brief says it "Uses Customer/SupportAgent models from Task 4" for login.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    assigned_tickets: Mapped[list["SupportTicket"]] = relationship(
        "SupportTicket", back_populates="assigned_support_agent"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"SupportAgent(support_agent_id={self.support_agent_id!r}, full_name={self.full_name!r})"
