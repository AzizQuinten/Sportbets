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


class ScanRun(Base):
    __tablename__ = 'scan_runs'
    id: Mapped[int] = mapped_column(primary_key=True)
    trigger: Mapped[str] = mapped_column(String(20), default='manual', index=True)
    status: Mapped[str] = mapped_column(String(20), default='RUNNING', index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    sports: Mapped[int] = mapped_column(Integer, default=0)
    snapshots: Mapped[int] = mapped_column(Integer, default=0)
    signals: Mapped[int] = mapped_column(Integer, default=0)
    accepted: Mapped[int] = mapped_column(Integer, default=0)
    paper_bets: Mapped[int] = mapped_column(Integer, default=0)
    reject_counts: Mapped[dict] = mapped_column(JSON, default=dict)
    top_edge: Mapped[float | None] = mapped_column(Float, nullable=True)
    top_ev: Mapped[float | None] = mapped_column(Float, nullable=True)
    quota_remaining: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quota_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)


class MatchResult(Base):
    """Completed fixtures used to train the incremental football model."""
    __tablename__ = 'match_results'
    __table_args__ = (UniqueConstraint('event_id', name='uq_match_result_event'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(String(80), index=True)
    sport_key: Mapped[str] = mapped_column(String(80), index=True)
    commence_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    home_team: Mapped[str] = mapped_column(String(160), index=True)
    away_team: Mapped[str] = mapped_column(String(160), index=True)
    home_score: Mapped[float] = mapped_column(Float)
    away_score: Mapped[float] = mapped_column(Float)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class TeamRating(Base):
    """Incremental Elo + rolling goal-strength state per competition/team."""
    __tablename__ = 'team_ratings'
    __table_args__ = (UniqueConstraint('sport_key', 'team', name='uq_team_rating'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    sport_key: Mapped[str] = mapped_column(String(80), index=True)
    team: Mapped[str] = mapped_column(String(160), index=True)
    rating: Mapped[float] = mapped_column(Float, default=1500.0)
    games: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    draws: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    goals_for_ema: Mapped[float] = mapped_column(Float, default=1.35)
    goals_against_ema: Mapped[float] = mapped_column(Float, default=1.35)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class ShadowPick(Base):
    """Near-miss candidate tracked with zero bankroll risk for threshold research."""
    __tablename__ = 'shadow_picks'
    __table_args__ = (UniqueConstraint('event_id', 'market', 'outcome', name='uq_shadow_pick'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(String(80), index=True)
    sport_key: Mapped[str] = mapped_column(String(80), index=True)
    market: Mapped[str] = mapped_column(String(40))
    outcome: Mapped[str] = mapped_column(String(160))
    bookmaker: Mapped[str] = mapped_column(String(80))
    odds: Mapped[float] = mapped_column(Float)
    fair_prob: Mapped[float] = mapped_column(Float)
    model_prob: Mapped[float] = mapped_column(Float)
    edge: Mapped[float] = mapped_column(Float)
    ev: Mapped[float] = mapped_column(Float)
    books: Mapped[int] = mapped_column(Integer)
    model_reliability: Mapped[float] = mapped_column(Float, default=0.0)
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    commence_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(20), default='OPEN', index=True)
    result: Mapped[str | None] = mapped_column(String(40), nullable=True)
    pnl_units: Mapped[float] = mapped_column(Float, default=0.0)
    closing_odds: Mapped[float | None] = mapped_column(Float, nullable=True)
    clv: Mapped[float | None] = mapped_column(Float, nullable=True)
