from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import DateTime, Enum as SqlEnum, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Role(str, Enum):
    requester = "requester"
    admin = "admin"


class RequestStatus(str, Enum):
    sent = "sent"
    downloading = "downloading"
    completed = "completed"
    failed = "failed"


class AudiobookRequest(Base):
    __tablename__ = "audiobook_requests"
    __table_args__ = (UniqueConstraint("user_name", "source_id", name="uq_user_source"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_name: Mapped[str] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(String(255))
    author: Mapped[str] = mapped_column(String(255), default="")
    cover_url: Mapped[str] = mapped_column(Text, default="")
    source_id: Mapped[str] = mapped_column(String(255), index=True)
    listenarr_id: Mapped[str] = mapped_column(String(255), default="", index=True)
    status: Mapped[RequestStatus] = mapped_column(SqlEnum(RequestStatus), default=RequestStatus.sent, index=True)
    error_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
