from datetime import datetime, timezone
from sqlalchemy import String, Float, Integer, DateTime, Boolean, JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.core.db import Base


def utcnow():
    return datetime.now(timezone.utc)


class OddsSnapshot(Base):
    __tablename__ = 'odds_snapshots'
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(String(80), index=True)
    sport_key: Mapped[str] = mapped_column(String(80), index=True)
    commence_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    home_team: Mapped[str] = mapped_column(String(160))
    away_team: Mapped[str] = mapped_column(String(160))
    market: Mapped[str] = mapped_column(String(40))
    bookmaker: Mapped[str] = mapped_column(String(80))
    outcome: Mapped[str] = mapped_column(String(160))
    price: Mapped[float] = mapped_column(Float)
    point: Mapped[float | None] = mapped_column(Float, nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Signal(Base):
    __tablename__ = 'signals'
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(String(80), index=True)
    sport_key: Mapped[str] = mapped_column(String(80), index=True)
    market: Mapped[str] = mapped_column(String(40))
    outcome: Mapped[str] = mapped_column(String(160))
    best_bookmaker: Mapped[str] = mapped_column(String(80))
    best_odds: Mapped[float] = mapped_column(Float)
    fair_prob: Mapped[float] = mapped_column(Float)
    model_prob: Mapped[float] = mapped_column(Float)
    edge: Mapped[float] = mapped_column(Float)
    ev: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    books: Mapped[int] = mapped_column(Integer)
    market_vig: Mapped[float] = mapped_column(Float)
    dispersion: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    accepted: Mapped[bool] = mapped_column(Boolean, default=False)
    reject_reason: Mapped[str | None] = mapped_column(String(220), nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class PaperBet(Base):
    __tablename__ = 'paper_bets'
    __table_args__ = (UniqueConstraint('event_id', 'market', 'outcome', name='uq_paper_pick'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(String(80), index=True)
    sport_key: Mapped[str] = mapped_column(String(80), index=True)
    market: Mapped[str] = mapped_column(String(40))
    outcome: Mapped[str] = mapped_column(String(160))
    bookmaker: Mapped[str] = mapped_column(String(80))
    odds: Mapped[float] = mapped_column(Float)
    stake: Mapped[float] = mapped_column(Float)
    model_prob: Mapped[float] = mapped_column(Float)
    fair_prob: Mapped[float] = mapped_column(Float)
    edge: Mapped[float] = mapped_column(Float)
    ev: Mapped[float] = mapped_column(Float)
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    commence_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(20), default='OPEN', index=True)
    pnl: Mapped[float] = mapped_column(Float, default=0.0)
    closing_odds: Mapped[float | None] = mapped_column(Float, nullable=True)
    clv: Mapped[float | None] = mapped_column(Float, nullable=True)
    result: Mapped[str | None] = mapped_column(String(40), nullable=True)
