from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone, timedelta
import threading

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.models.db_models import OddsSnapshot, PaperBet, ScanRun, ShadowPick, Signal
from app.providers.odds_api import OddsAPIProvider
from app.services.engine import ingest_rows, build_signals, place_paper_bets, settle_h2h_from_scores, track_shadow_picks, update_closing_lines
from app.services.model import ingest_completed_scores

settings = get_settings()
_cycle_lock = threading.Lock()
_model_lock = threading.Lock()

def _utcnow(): return datetime.now(timezone.utc)
def _to_int(value):
    try: return int(value) if value is not None else None
    except (TypeError, ValueError): return None

def latest_scan(db): return db.scalar(select(ScanRun).order_by(ScanRun.started_at.desc()).limit(1))
def latest_successful_scan(db): return db.scalar(select(ScanRun).where(ScanRun.status.in_(['SUCCESS','PARTIAL'])).order_by(ScanRun.started_at.desc()).limit(1))

def recommended_scan_interval_minutes(db):
    now=_utcnow(); next_kickoff=db.scalar(select(func.min(OddsSnapshot.commence_time)).where(OddsSnapshot.commence_time>now))
    if next_kickoff is None: minutes=180
    else:
        delta=max(0,(next_kickoff-now).total_seconds()/60)
        minutes=15 if delta<=120 else 30 if delta<=360 else 60 if delta<=1440 else 180
    last=latest_successful_scan(db); remaining=last.quota_remaining if last else None
    if remaining is not None:
        if remaining<=settings.api_quota_floor or remaining<100: minutes=max(minutes,720)
        elif remaining<250: minutes=max(minutes,360)
        elif remaining<500: minutes=max(minutes,180)
    return max(settings.poll_minutes,minutes)

def next_scan_due_at(db):
    last=latest_successful_scan(db)
    if last is None: return _utcnow()
    return (last.finished_at or last.started_at)+timedelta(minutes=recommended_scan_interval_minutes(db))

def should_auto_scan(db):
    if not settings.auto_scan_enabled: return False,'auto_scan_disabled'
    last=latest_successful_scan(db)
    if last and last.quota_remaining is not None and last.quota_remaining<=settings.api_quota_floor: return False,'quota_guard'
    due=next_scan_due_at(db); ok=due is None or _utcnow()>=due
    return ok,'due' if ok else 'not_due'

def _cleanup_history(db):
    now=_utcnow(); db.execute(delete(OddsSnapshot).where(OddsSnapshot.observed_at<now-timedelta(days=settings.snapshot_retention_days))); db.execute(delete(Signal).where(Signal.created_at<now-timedelta(days=settings.signal_retention_days))); db.commit()

def execute_cycle(db:Session,trigger='manual'):
    if not _cycle_lock.acquire(blocking=False): return {'status':'BUSY','detail':'A scan is already running.'}
    started=_utcnow(); scan=ScanRun(trigger=trigger,status='RUNNING',started_at=started,sports=len(settings.sports)); db.add(scan); db.commit(); db.refresh(scan)
    total_rows=total_bets=total_shadow=clv_updates=0; all_signals=[]; last_quota={}; errors=[]
    try:
        if not settings.odds_api_key: raise RuntimeError('ODDS_API_KEY missing')
        provider=OddsAPIProvider(settings.odds_api_key,settings.odds_region)
        for sport in settings.sports:
            try:
                events,quota=provider.fetch_odds(sport,settings.market_list); last_quota=quota or last_quota; rows=provider.flatten(events); total_rows+=ingest_rows(db,rows); clv_updates+=update_closing_lines(db,rows); signals=build_signals(db,rows); all_signals.extend(signals); total_shadow+=track_shadow_picks(db,signals); total_bets+=place_paper_bets(db,signals)
            except Exception as exc: errors.append(f'{sport}: {type(exc).__name__}: {exc}')
        reject_counts=Counter()
        for s in all_signals:
            if not s.accepted and s.reject_reason: reject_counts.update(x for x in s.reject_reason.split(',') if x)
        accepted=sum(1 for s in all_signals if s.accepted); finished=_utcnow(); scan.finished_at=finished; scan.duration_ms=int((finished-started).total_seconds()*1000); scan.snapshots=total_rows; scan.signals=len(all_signals); scan.accepted=accepted; scan.paper_bets=total_bets; scan.reject_counts=dict(reject_counts); scan.top_edge=max((s.edge for s in all_signals),default=None); scan.top_ev=max((s.ev for s in all_signals),default=None); scan.quota_remaining=_to_int(last_quota.get('remaining')); scan.quota_used=_to_int(last_quota.get('used')); scan.error=' | '.join(errors)[:500] if errors else None; scan.status='SUCCESS' if not errors else ('PARTIAL' if all_signals or total_rows else 'FAILED'); db.commit(); _cleanup_history(db)
        return {'status':scan.status,'scan_id':scan.id,'snapshots':total_rows,'signals':len(all_signals),'accepted':accepted,'paper_bets':total_bets,'shadow_picks':total_shadow,'clv_updates':clv_updates,'reject_counts':dict(reject_counts),'top_edge':scan.top_edge,'top_ev':scan.top_ev,'quota':{'remaining':scan.quota_remaining,'used':scan.quota_used,'last':last_quota.get('last')},'errors':errors,'duration_ms':scan.duration_ms}
    except Exception as exc:
        db.rollback(); return {'status':'FAILED','detail':str(exc)}
    finally: _cycle_lock.release()

def auto_scan_tick():
    with SessionLocal() as db:
        due,reason=should_auto_scan(db)
        return execute_cycle(db,'auto') if due else {'status':'SKIPPED','reason':reason}

def refresh_model_tick(bootstrap=False):
    if not settings.odds_api_key: return {'status':'SKIPPED','reason':'missing_api_key'}
    if not _model_lock.acquire(blocking=False): return {'status':'BUSY'}
    try:
        with SessionLocal() as db:
            provider=OddsAPIProvider(settings.odds_api_key,settings.odds_region); learned=settled=0; errors=[]
            for sport in settings.sports:
                try:
                    scores=provider.fetch_score_history(sport) if bootstrap else provider.fetch_scores(sport,days_from=3)
                    learned+=ingest_completed_scores(db,sport,scores); settled+=settle_h2h_from_scores(db,scores)
                except Exception as exc: errors.append(f'{sport}: {type(exc).__name__}: {exc}')
            return {'status':'SUCCESS' if not errors else 'PARTIAL','mode':'bootstrap' if bootstrap else 'refresh','new_results':learned,'settled':settled,'errors':errors}
    finally: _model_lock.release()

def bootstrap_model_tick(): return refresh_model_tick(bootstrap=True)

def settle_if_needed():
    if not settings.auto_settle_enabled or not settings.odds_api_key: return {'status':'SKIPPED'}
    with SessionLocal() as db:
        cutoff=_utcnow()-timedelta(minutes=120); p=db.scalar(select(func.count(PaperBet.id)).where(PaperBet.status=='OPEN',PaperBet.commence_time<=cutoff)) or 0; s=db.scalar(select(func.count(ShadowPick.id)).where(ShadowPick.status=='OPEN',ShadowPick.commence_time<=cutoff)) or 0
        if not p and not s: return {'status':'SKIPPED','eligible':0}
    return refresh_model_tick()
