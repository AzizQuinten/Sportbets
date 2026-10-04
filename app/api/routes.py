from collections import Counter
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.config import get_settings
from app.models.db_models import Signal, PaperBet, ScanRun
from app.providers.odds_api import OddsAPIProvider
from app.services.cycle import execute_cycle, latest_scan, latest_successful_scan, next_scan_due_at, recommended_scan_interval_minutes
from app.services.engine import settle_h2h_from_scores

router = APIRouter()
settings = get_settings()


def _scan_dict(x: ScanRun | None):
    if not x:
        return None
    return {
        'id': x.id,
        'trigger': x.trigger,
        'status': x.status,
        'started_at': x.started_at,
        'finished_at': x.finished_at,
        'duration_ms': x.duration_ms,
        'sports': x.sports,
        'snapshots': x.snapshots,
        'signals': x.signals,
        'accepted': x.accepted,
        'paper_bets': x.paper_bets,
        'reject_counts': x.reject_counts or {},
        'top_edge': x.top_edge,
        'top_ev': x.top_ev,
        'quota_remaining': x.quota_remaining,
        'quota_used': x.quota_used,
        'error': x.error,
    }


@router.get('/health')
def health(db: Session = Depends(get_db)):
    latest = latest_scan(db)
    return {
        'ok': True,
        'paper_only': settings.paper_only,
        'auto_scan_enabled': settings.auto_scan_enabled,
        'latest_scan_status': latest.status if latest else None,
    }


@router.post('/api/run-cycle')
def run_cycle(db: Session = Depends(get_db)):
    if not settings.odds_api_key:
        raise HTTPException(400, 'ODDS_API_KEY missing')
    result = execute_cycle(db, trigger='manual')
    if result.get('status') == 'FAILED':
        raise HTTPException(500, result.get('detail', 'Scan failed'))
    if result.get('status') == 'BUSY':
        raise HTTPException(409, result.get('detail', 'Scan already running'))
    return result


@router.post('/api/settle')
def settle(db: Session = Depends(get_db)):
    if not settings.odds_api_key:
        raise HTTPException(400, 'ODDS_API_KEY missing')
    provider = OddsAPIProvider(settings.odds_api_key, settings.odds_region)
    total = 0
    errors = []
    for sport in settings.sports:
        try:
            total += settle_h2h_from_scores(db, provider.fetch_scores(sport))
        except Exception as exc:
            errors.append(f'{sport}: {type(exc).__name__}: {exc}')
    return {'settled': total, 'errors': errors}


@router.get('/api/signals')
def signals(limit: int = 100, db: Session = Depends(get_db)):
    rows = db.scalars(select(Signal).order_by(Signal.created_at.desc()).limit(min(limit, 500))).all()
    return [{
        'id': x.id,
        'event_id': x.event_id,
        'sport': x.sport_key,
        'market': x.market,
        'outcome': x.outcome,
        'odds': x.best_odds,
        'book': x.best_bookmaker,
        'fair_prob': x.fair_prob,
        'model_prob': x.model_prob,
        'edge': x.edge,
        'ev': x.ev,
        'confidence': x.confidence,
        'books': x.books,
        'vig': x.market_vig,
        'dispersion': x.dispersion,
        'accepted': x.accepted,
        'reject_reason': x.reject_reason,
        'created_at': x.created_at,
        'meta': x.meta,
    } for x in rows]


@router.get('/api/paper-bets')
def paper_bets(limit: int = 200, db: Session = Depends(get_db)):
    rows = db.scalars(select(PaperBet).order_by(PaperBet.placed_at.desc()).limit(min(limit, 500))).all()
    return [{
        'id': x.id,
        'event_id': x.event_id,
        'sport': x.sport_key,
        'market': x.market,
        'outcome': x.outcome,
        'bookmaker': x.bookmaker,
        'odds': x.odds,
        'stake': x.stake,
        'edge': x.edge,
        'ev': x.ev,
        'status': x.status,
        'pnl': x.pnl,
        'clv': x.clv,
        'result': x.result,
        'placed_at': x.placed_at,
        'commence_time': x.commence_time,
    } for x in rows]


@router.get('/api/kpis')
def kpis(db: Session = Depends(get_db)):
    bets = db.scalars(select(PaperBet)).all()
    settled = [b for b in bets if b.status == 'SETTLED']
    pnl = sum(b.pnl for b in settled)
    staked = sum(b.stake for b in settled)
    wins = sum(1 for b in settled if b.result == 'WIN')
    latest = latest_successful_scan(db)
    return {
        'bankroll_start': settings.bankroll,
        'equity': settings.bankroll + pnl,
        'pnl': pnl,
        'roi': pnl / staked if staked else 0.0,
        'bets': len(bets),
        'settled': len(settled),
        'winrate': wins / len(settled) if settled else 0.0,
        'open_exposure': sum(b.stake for b in bets if b.status == 'OPEN'),
        'last_scan_prices': latest.snapshots if latest else 0,
        'last_scan_signals': latest.signals if latest else 0,
        'quota_remaining': latest.quota_remaining if latest else None,
    }


@router.get('/api/engine-status')
def engine_status(db: Session = Depends(get_db)):
    latest = latest_scan(db)
    successful = latest_successful_scan(db)
    due = next_scan_due_at(db)
    return {
        'auto_scan_enabled': settings.auto_scan_enabled,
        'auto_settle_enabled': settings.auto_settle_enabled,
        'scheduler_tick_minutes': settings.poll_minutes,
        'recommended_scan_interval_minutes': recommended_scan_interval_minutes(db),
        'next_scan_due_at': due,
        'quota_floor': settings.api_quota_floor,
        'latest_scan': _scan_dict(latest),
        'latest_successful_scan': _scan_dict(successful),
        'strategy': {
            'min_bookmakers': settings.min_bookmakers,
            'min_edge': settings.min_edge,
            'min_ev': settings.min_ev,
            'max_vig': settings.max_vig,
            'kelly_fraction': settings.kelly_fraction,
            'max_stake_pct': settings.max_stake_pct,
            'max_event_exposure_pct': settings.max_event_exposure_pct,
            'max_daily_exposure_pct': settings.max_daily_exposure_pct,
            'paper_only': settings.paper_only,
        },
    }


def _latest_scan_signals(db: Session):
    run = latest_successful_scan(db)
    if not run:
        return run, []
    end = run.finished_at or datetime.now(timezone.utc)
    rows = db.scalars(
        select(Signal)
        .where(Signal.created_at >= run.started_at, Signal.created_at <= end)
        .order_by(Signal.ev.desc(), Signal.edge.desc())
    ).all()
    return run, rows


@router.get('/api/analytics')
def analytics(db: Session = Depends(get_db)):
    run, rows = _latest_scan_signals(db)
    if not run:
        return {
            'latest_scan': None,
            'reject_counts': {},
            'near_misses': [],
            'accepted': [],
            'market_health': {},
        }

    rejects = Counter()
    sport_counts = Counter()
    book_counts = []
    vigs = []
    for s in rows:
        sport_counts[s.sport_key] += 1
        book_counts.append(s.books)
        vigs.append(s.market_vig)
        if not s.accepted and s.reject_reason:
            rejects.update(x for x in s.reject_reason.split(',') if x)

    def distance(s: Signal):
        edge_gap = max(0.0, settings.min_edge - s.edge) / max(settings.min_edge, 1e-9)
        ev_gap = max(0.0, settings.min_ev - s.ev) / max(settings.min_ev, 1e-9)
        book_gap = max(0, settings.min_bookmakers - s.books) / max(settings.min_bookmakers, 1)
        vig_gap = max(0.0, s.market_vig - settings.max_vig) / max(settings.max_vig, 1e-9)
        started_penalty = 100.0 if (s.reject_reason and 'already_started' in s.reject_reason) else 0.0
        return edge_gap + ev_gap + book_gap + vig_gap + started_penalty

    candidates = [s for s in rows if not s.accepted and not (s.reject_reason and 'already_started' in s.reject_reason)]
    candidates.sort(key=lambda s: (distance(s), -s.ev, -s.edge))

    def sig(s: Signal):
        return {
            'event_id': s.event_id,
            'sport': s.sport_key,
            'market': s.market,
            'outcome': s.outcome,
            'odds': s.best_odds,
            'book': s.best_bookmaker,
            'fair_prob': s.fair_prob,
            'model_prob': s.model_prob,
            'edge': s.edge,
            'ev': s.ev,
            'confidence': s.confidence,
            'books': s.books,
            'vig': s.market_vig,
            'dispersion': s.dispersion,
            'reject_reason': s.reject_reason,
            'distance_to_accept': distance(s),
            'meta': s.meta,
        }

    accepted = [s for s in rows if s.accepted]
    accepted.sort(key=lambda s: (s.ev, s.edge), reverse=True)

    return {
        'latest_scan': _scan_dict(run),
        'reject_counts': dict(rejects),
        'near_misses': [sig(s) for s in candidates[:15]],
        'accepted': [sig(s) for s in accepted[:30]],
        'market_health': {
            'avg_books': sum(book_counts) / len(book_counts) if book_counts else 0.0,
            'avg_vig': sum(vigs) / len(vigs) if vigs else 0.0,
            'sports': dict(sport_counts),
            'signals': len(rows),
            'positive_ev': sum(1 for s in rows if s.ev > 0),
            'positive_edge': sum(1 for s in rows if s.edge > 0),
        },
    }


@router.get('/api/scan-history')
def scan_history(limit: int = 20, db: Session = Depends(get_db)):
    rows = db.scalars(
        select(ScanRun).order_by(ScanRun.started_at.desc()).limit(min(limit, 100))
    ).all()
    return [_scan_dict(x) for x in rows]
