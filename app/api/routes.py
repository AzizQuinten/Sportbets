from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from app.core.db import get_db
from app.core.config import get_settings
from app.models.db_models import Signal, PaperBet, OddsSnapshot
from app.providers.odds_api import OddsAPIProvider
from app.services.engine import ingest_rows, build_signals, place_paper_bets, settle_h2h_from_scores

router = APIRouter()
settings = get_settings()


@router.get('/health')
def health():
    return {'ok': True, 'paper_only': settings.paper_only}


@router.post('/api/run-cycle')
def run_cycle(db: Session = Depends(get_db)):
    if not settings.odds_api_key:
        raise HTTPException(400, 'ODDS_API_KEY missing')
    provider = OddsAPIProvider(settings.odds_api_key, settings.odds_region)
    total_rows = total_signals = total_bets = 0
    quota = {}
    for sport in settings.sports:
        events, quota = provider.fetch_odds(sport, settings.market_list)
        rows = provider.flatten(events)
        total_rows += ingest_rows(db, rows)
        signals = build_signals(db, rows)
        total_signals += len(signals)
        total_bets += place_paper_bets(db, signals)
    return {'snapshots': total_rows, 'signals': total_signals, 'paper_bets': total_bets, 'quota': quota}


@router.post('/api/settle')
def settle(db: Session = Depends(get_db)):
    if not settings.odds_api_key:
        raise HTTPException(400, 'ODDS_API_KEY missing')
    provider = OddsAPIProvider(settings.odds_api_key, settings.odds_region)
    total = 0
    for sport in settings.sports:
        total += settle_h2h_from_scores(db, provider.fetch_scores(sport))
    return {'settled': total}


@router.get('/api/signals')
def signals(limit: int = 100, db: Session = Depends(get_db)):
    rows = db.scalars(select(Signal).order_by(Signal.created_at.desc()).limit(limit)).all()
    return [{'id': x.id, 'event_id': x.event_id, 'sport': x.sport_key, 'market': x.market, 'outcome': x.outcome,
             'odds': x.best_odds, 'book': x.best_bookmaker, 'fair_prob': x.fair_prob, 'model_prob': x.model_prob,
             'edge': x.edge, 'ev': x.ev, 'confidence': x.confidence, 'accepted': x.accepted,
             'reject_reason': x.reject_reason, 'created_at': x.created_at, 'meta': x.meta} for x in rows]


@router.get('/api/paper-bets')
def paper_bets(limit: int = 200, db: Session = Depends(get_db)):
    rows = db.scalars(select(PaperBet).order_by(PaperBet.placed_at.desc()).limit(limit)).all()
    return [{'id': x.id, 'event_id': x.event_id, 'sport': x.sport_key, 'market': x.market, 'outcome': x.outcome,
             'odds': x.odds, 'stake': x.stake, 'edge': x.edge, 'ev': x.ev, 'status': x.status, 'pnl': x.pnl,
             'clv': x.clv, 'result': x.result, 'placed_at': x.placed_at, 'commence_time': x.commence_time} for x in rows]


@router.get('/api/kpis')
def kpis(db: Session = Depends(get_db)):
    bets = db.scalars(select(PaperBet)).all()
    settled = [b for b in bets if b.status == 'SETTLED']
    pnl = sum(b.pnl for b in settled)
    staked = sum(b.stake for b in settled)
    wins = sum(1 for b in settled if b.result == 'WIN')
    return {
        'bankroll_start': settings.bankroll,
        'equity': settings.bankroll + pnl,
        'pnl': pnl,
        'roi': pnl / staked if staked else 0.0,
        'bets': len(bets),
        'settled': len(settled),
        'winrate': wins / len(settled) if settled else 0.0,
        'open_exposure': sum(b.stake for b in bets if b.status == 'OPEN'),
    }
