from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Torrent(Base):
    __tablename__ = "torrents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    info_hash_v1: Mapped[str | None] = mapped_column(String(40), unique=True, nullable=True, index=True)
    info_hash_v2: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True, index=True)
    name: Mapped[str] = mapped_column(Text)
    total_size: Mapped[int] = mapped_column(BigInteger, default=0)
    file_count: Mapped[int] = mapped_column(Integer, default=0)
    model_file_count: Mapped[int] = mapped_column(Integer, default=0, index=True)
    formats: Mapped[list[str]] = mapped_column(JSON, default=list)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    magnet_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_source: Mapped[str] = mapped_column(String(32), default="unknown")
    license: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    files: Mapped[list["TorrentFile"]] = relationship(
        back_populates="torrent", cascade="all, delete-orphan", lazy="selectin"
    )


class TorrentFile(Base):
    __tablename__ = "torrent_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    torrent_id: Mapped[int] = mapped_column(ForeignKey("torrents.id", ondelete="CASCADE"), index=True)
    path: Mapped[str] = mapped_column(Text)
    extension: Mapped[str] = mapped_column(String(20), default="", index=True)
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    is_model: Mapped[bool] = mapped_column(default=False, index=True)

    torrent: Mapped[Torrent] = relationship(back_populates="files")


Index("ix_torrent_status_discovered", Torrent.status, Torrent.discovered_at)
