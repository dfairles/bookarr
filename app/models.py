# Copyright (C) 2024-2026 Bookarr Contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

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
    pending_approval = "pending_approval"
    sent = "sent"
    downloading = "downloading"
    completed = "completed"
    failed = "failed"
    denied = "denied"


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
    denied_reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(SqlEnum(Role), default=Role.requester)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
