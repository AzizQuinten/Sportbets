from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone, timedelta
import threading

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.models.db_models import OddsSnapshot, PaperBet, ScanRun, Signal
from app.providers.odds_api import OddsAPIProvider
from app.services.engine import ingest_rows, build_signals, place_paper_bets, settle_h2h_from_scores

settings = get_settings()
_cycle_lock = threading.Lock()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_int(value):
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def latest_scan(db: Session) -> ScanRun | None:
    return db.scalar(select(ScanRun).order_by(ScanRun.started_at.desc()).limit(1))


def latest_successful_scan(db: Session) -> ScanRun | None:
    return db.scalar(
        select(ScanRun)
        .where(ScanRun.status.in_(['SUCCESS', 'PARTIAL']))
        .order_by(ScanRun.started_at.desc())
        .limit(1)
    )


def recommended_scan_interval_minutes(db: Session) -> int:
    """Choose a scan cadence from time-to-kickoff while protecting small API plans.

    The scheduler wakes every POLL_MINUTES, but a real API call only happens when
    this adaptive interval has elapsed. A ~500-credit plan is therefore kept near
    a sustainable three-hour baseline instead of being exhausted in a day.
    """
    now = _utcnow()
    next_kickoff = db.scalar(
        select(func.min(OddsSnapshot.commence_time)).where(OddsSnapshot.commence_time > now)
    )

    if next_kickoff is None:
        minutes = 180
    else:
        delta = max(0.0, (next_kickoff - now).total_seconds() / 60.0)
        if delta <= 120:
            minutes = 15
        elif delta <= 360:
            minutes = 30
        elif delta <= 1440:
            minutes = 60
        else:
            minutes = 180

    last = latest_successful_scan(db)
    remaining = last.quota_remaining if last else None
    if remaining is not None:
        if remaining <= settings.api_quota_floor:
            minutes = max(minutes, 720)
        elif remaining < 100:
            minutes = max(minutes, 360)
        elif remaining < 250:
            minutes = max(minutes, 240)
        elif remaining < 500:
            minutes = max(minutes, 180)

    return max(settings.poll_minutes, minutes)


def next_scan_due_at(db: Session) -> datetime | None:
    last = latest_successful_scan(db)
    if last is None:
        return _utcnow()
    anchor = last.finished_at or last.started_at
    return anchor + timedelta(minutes=recommended_scan_interval_minutes(db))


def should_auto_scan(db: Session) -> tuple[bool, str]:
    if not settings.auto_scan_enabled:
        return False, 'auto_scan_disabled'
    last = latest_successful_scan(db)
    if last and last.quota_remaining is not None and last.quota_remaining <= settings.api_quota_floor:
        return False, 'quota_guard'
    due = next_scan_due_at(db)
    is_due = due is None or _utcnow() >= due
    return is_due, 'due' if is_due else 'not_due'


def _cleanup_history(db: Session) -> None:
    now = _utcnow()
    snapshot_cutoff = now - timedelta(days=settings.snapshot_retention_days)
    signal_cutoff = now - timedelta(days=settings.signal_retention_days)
    db.execute(delete(OddsSnapshot).where(OddsSnapshot.observed_at < snapshot_cutoff))
    db.execute(delete(Signal).where(Signal.created_at < signal_cutoff))
    db.commit()


def execute_cycle(db: Session, trigger: str = 'manual') -> dict:
    """Run one complete odds -> signals -> paper bets cycle with an audit record."""
    if not _cycle_lock.acquire(blocking=False):
        return {'status': 'BUSY', 'detail': 'A scan is already running.'}

    started = _utcnow()
    scan = ScanRun(trigger=trigger, status='RUNNING', started_at=started, sports=len(settings.sports))
    db.add(scan)
    db.commit()
    db.refresh(scan)

    total_rows = 0
    all_signals: list[Signal] = []
    total_bets = 0
    last_quota = {}
    errors: list[str] = []

    try:
        if not settings.odds_api_key:
            raise RuntimeError('ODDS_API_KEY missing')

        provider = OddsAPIProvider(settings.odds_api_key, settings.odds_region)
        for sport in settings.sports:
            try:
                events, quota = provider.fetch_odds(sport, settings.market_list)
                last_quota = quota or last_quota
                rows = provider.flatten(events)
                total_rows += ingest_rows(db, rows)
                signals = build_signals(db, rows)
                all_signals.extend(signals)
                total_bets += place_paper_bets(db, signals)
            except Exception as exc:
                errors.append(f'{sport}: {type(exc).__name__}: {exc}')

        reject_counts = Counter()
        for signal in all_signals:
            if not signal.accepted and signal.reject_reason:
                for reason in signal.reject_reason.split(','):
                    if reason:
                        reject_counts[reason] += 1

        accepted = sum(1 for signal in all_signals if signal.accepted)
        finished = _utcnow()
        scan.finished_at = finished
        scan.duration_ms = int((finished - started).total_seconds() * 1000)
        scan.snapshots = total_rows
        scan.signals = len(all_signals)
        scan.accepted = accepted
        scan.paper_bets = total_bets
        scan.reject_counts = dict(reject_counts)
        scan.top_edge = max((s.edge for s in all_signals), default=None)
        scan.top_ev = max((s.ev for s in all_signals), default=None)
        scan.quota_remaining = _to_int(last_quota.get('remaining'))
        scan.quota_used = _to_int(last_quota.get('used'))
        scan.error = ' | '.join(errors)[:500] if errors else None
        scan.status = 'SUCCESS' if not errors else ('PARTIAL' if all_signals or total_rows else 'FAILED')
        db.commit()

        _cleanup_history(db)

        return {
            'status': scan.status,
            'scan_id': scan.id,
            'snapshots': total_rows,
            'signals': len(all_signals),
            'accepted': accepted,
            'paper_bets': total_bets,
            'reject_counts': dict(reject_counts),
            'top_edge': scan.top_edge,
            'top_ev': scan.top_ev,
            'quota': {
                'remaining': scan.quota_remaining,
                'used': scan.quota_used,
                'last': last_quota.get('last'),
            },
            'errors': errors,
            'duration_ms': scan.duration_ms,
        }
    except Exception as exc:
        db.rollback()
        try:
            scan = db.get(ScanRun, scan.id)
            if scan:
                finished = _utcnow()
                scan.finished_at = finished
                scan.duration_ms = int((finished - started).total_seconds() * 1000)
                scan.status = 'FAILED'
                scan.error = f'{type(exc).__name__}: {exc}'[:500]
                db.commit()
        except Exception:
            db.rollback()
        return {'status': 'FAILED', 'detail': str(exc)}
    finally:
        _cycle_lock.release()


def auto_scan_tick() -> dict:
    """Cheap scheduler tick. Only calls the odds API when the adaptive cadence is due."""
    with SessionLocal() as db:
        due, reason = should_auto_scan(db)
        if not due:
            return {'status': 'SKIPPED', 'reason': reason}
        return execute_cycle(db, trigger='auto')


def settle_if_needed() -> dict:
    """Settle only when an open paper bet is old enough to plausibly be final."""
    if not settings.auto_settle_enabled or not settings.odds_api_key:
        return {'status': 'SKIPPED'}

    with SessionLocal() as db:
        cutoff = _utcnow() - timedelta(minutes=120)
        eligible = db.scalar(
            select(func.count(PaperBet.id)).where(
                PaperBet.status == 'OPEN',
                PaperBet.commence_time <= cutoff,
            )
        ) or 0
        if not eligible:
            return {'status': 'SKIPPED', 'eligible': 0}

        provider = OddsAPIProvider(settings.odds_api_key, settings.odds_region)
        total = 0
        errors = []
        for sport in settings.sports:
            try:
                total += settle_h2h_from_scores(db, provider.fetch_scores(sport))
            except Exception as exc:
                errors.append(f'{sport}: {type(exc).__name__}: {exc}')
        return {'status': 'SUCCESS' if not errors else 'PARTIAL', 'settled': total, 'errors': errors}
