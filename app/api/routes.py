from collections import Counter
from datetime import datetime, timezone, timedelta
import threading

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db, SessionLocal
from app.core.config import get_settings
from app.models.db_models import PaperBet, ScanRun, ShadowPick, Signal, BetAudit
from app.providers.odds_api import OddsAPIProvider
from app.services import cycle as cycle_service
from app.services.cycle import (
    bootstrap_model_tick, execute_cycle, latest_scan, latest_successful_scan,
    next_scan_due_at, recommended_scan_interval_minutes, tracked_sports,
)
from app.services.engine import settle_h2h_from_scores
from app.services.model import ingest_completed_scores, model_summary
from app.services.validation import validation_report

router = APIRouter()
settings = get_settings()
APP_VERSION = '3.0.0'
_manual_launch_lock = threading.Lock()


def _scan_dict(x):
    if not x: return None
    return {'id':x.id,'trigger':x.trigger,'status':x.status,'started_at':x.started_at,'finished_at':x.finished_at,'duration_ms':x.duration_ms,'sports':x.sports,'snapshots':x.snapshots,'signals':x.signals,'accepted':x.accepted,'paper_bets':x.paper_bets,'reject_counts':x.reject_counts or {},'top_edge':x.top_edge,'top_ev':x.top_ev,'quota_remaining':x.quota_remaining,'quota_used':x.quota_used,'error':x.error}


def _repair_stale_scans(db: Session):
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=8)
    stale = db.scalars(select(ScanRun).where(ScanRun.status=='RUNNING', ScanRun.started_at<cutoff)).all()
    if not stale: return 0
    now = datetime.now(timezone.utc)
    for x in stale:
        started = x.started_at if x.started_at.tzinfo else x.started_at.replace(tzinfo=timezone.utc)
        x.status='FAILED'; x.finished_at=now; x.duration_ms=int((now-started).total_seconds()*1000)
        x.error='Watchdog: scan exceeded 8 minute runtime and was marked stale.'
    db.commit(); return len(stale)


def _manual_scan_worker():
    try:
        with SessionLocal() as worker_db:
            execute_cycle(worker_db, trigger='manual')
    except Exception:
        pass


def _launch_manual_scan(db: Session):
    with _manual_launch_lock:
        _repair_stale_scans(db)
        if cycle_service._cycle_lock.locked(): return {'status':'BUSY','detail':'A scan is already running.'}
        threading.Thread(target=_manual_scan_worker, name='manual-scan', daemon=True).start()
        return {'status':'STARTED','message':'Scan started in background. Dashboard updates automatically.','version':APP_VERSION}


def _audit_map(db: Session):
    return {x.paper_bet_id:x for x in db.scalars(select(BetAudit)).all()}


@router.get('/health')
def health(db: Session=Depends(get_db)):
    _repair_stale_scans(db); x=latest_scan(db)
    return {'ok':True,'version':APP_VERSION,'paper_only':settings.paper_only,'auto_scan_enabled':settings.auto_scan_enabled,'scanner_running':cycle_service._cycle_lock.locked(),'latest_scan_status':x.status if x else None,'configured_sports':settings.sports,'tracked_sports':tracked_sports(db),'model':model_summary(db),'strategy_health':validation_report(db)['health']}


@router.post('/api/run-cycle')
def run_cycle(db: Session=Depends(get_db)):
    if not settings.odds_api_key: raise HTTPException(400,detail={'message':'ODDS_API_KEY missing','stage':'configuration'})
    r=_launch_manual_scan(db)
    if r.get('status')=='BUSY': raise HTTPException(409,detail={'message':r.get('detail'),'stage':'cycle_lock'})
    return r


@router.post('/api/bootstrap-model')
def bootstrap_model(): return bootstrap_model_tick()


@router.post('/api/settle')
def settle(db: Session=Depends(get_db)):
    p=OddsAPIProvider(settings.odds_api_key,settings.odds_region); total=learned=0; errors=[]; sports=tracked_sports(db)
    for sport in sports:
        try:
            scores=p.fetch_scores(sport,3); learned+=ingest_completed_scores(db,sport,scores); total+=settle_h2h_from_scores(db,scores)
        except Exception as exc: errors.append(f'{sport}: {type(exc).__name__}: {exc}')
    return {'settled':total,'new_model_results':learned,'sports_checked':sports,'errors':errors}


@router.get('/api/signals')
def signals(limit:int=100,db:Session=Depends(get_db)):
    rows=db.scalars(select(Signal).order_by(Signal.created_at.desc()).limit(min(limit,500))).all()
    return [{'id':x.id,'event_id':x.event_id,'sport':x.sport_key,'market':x.market,'outcome':x.outcome,'odds':x.best_odds,'book':x.best_bookmaker,'fair_prob':x.fair_prob,'model_prob':x.model_prob,'edge':x.edge,'ev':x.ev,'confidence':x.confidence,'books':x.books,'vig':x.market_vig,'dispersion':x.dispersion,'accepted':x.accepted,'reject_reason':x.reject_reason,'created_at':x.created_at,'meta':x.meta} for x in rows]


@router.get('/api/paper-bets')
def paper_bets(limit:int=200,db:Session=Depends(get_db)):
    rows=db.scalars(select(PaperBet).order_by(PaperBet.placed_at.desc()).limit(min(limit,500))).all(); audits=_audit_map(db)
    event_ids=list({x.event_id for x in rows})
    signal_rows=db.scalars(select(Signal).where(Signal.event_id.in_(event_ids)).order_by(Signal.created_at.desc())).all() if event_ids else []
    signal_meta={}
    for s in signal_rows:
        signal_meta.setdefault((s.event_id,s.market,s.outcome),s.meta or {})
    out=[]
    for x in rows:
        a=audits.get(x.id); meta=signal_meta.get((x.event_id,x.market,x.outcome),{})
        out.append({'id':x.id,'event_id':x.event_id,'sport':x.sport_key,'market':x.market,'outcome':x.outcome,'bookmaker':x.bookmaker,'odds':x.odds,'stake':x.stake,'edge':x.edge,'ev':x.ev,'model_prob':x.model_prob,'fair_prob':x.fair_prob,'status':x.status,'pnl':x.pnl,'closing_odds':x.closing_odds,'clv':x.clv,'result':x.result,'placed_at':x.placed_at,'commence_time':x.commence_time,'tier':a.tier if a else 'LEGACY','selection_score':a.selection_score if a else None,'adjusted_ev':a.adjusted_ev if a else None,'market_quality':a.market_quality if a else None,'confidence':a.confidence if a else None,'home_team':meta.get('home_team'),'away_team':meta.get('away_team'),'line':meta.get('line'),'market_ev':meta.get('market_ev'),'opportunity_score':meta.get('opportunity_score')})
    return out


@router.get('/api/kpis')
def kpis(db:Session=Depends(get_db)):
    bets=db.scalars(select(PaperBet)).all(); settled=[b for b in bets if b.status=='SETTLED']; pnl=sum(b.pnl for b in settled); staked=sum(b.stake for b in settled); wins=sum(1 for b in settled if b.result=='WIN'); clvs=[b.clv for b in bets if b.clv is not None]; latest=latest_successful_scan(db); audits=_audit_map(db)
    exploration=sum(1 for b in bets if audits.get(b.id) and audits[b.id].tier=='EXPLORATION'); scout=sum(1 for b in bets if audits.get(b.id) and audits[b.id].tier=='SCOUT'); core=len(bets)-exploration-scout
    open_bets=[b for b in bets if b.status=='OPEN']
    return {'bankroll_start':settings.bankroll,'equity':settings.bankroll+pnl,'pnl':pnl,'roi':pnl/staked if staked else 0,'bets':len(bets),'open_bets':len(open_bets),'core_bets':core,'exploration_bets':exploration,'scout_bets':scout,'settled':len(settled),'winrate':wins/len(settled) if settled else 0,'open_exposure':sum(b.stake for b in open_bets),'avg_clv':sum(clvs)/len(clvs) if clvs else None,'last_scan_prices':latest.snapshots if latest else 0,'last_scan_signals':latest.signals if latest else 0,'quota_remaining':latest.quota_remaining if latest else None}


@router.get('/api/engine-status')
def engine_status(db:Session=Depends(get_db)):
    _repair_stale_scans(db); latest=latest_scan(db); successful=latest_successful_scan(db); health=validation_report(db)['health']; model=model_summary(db)
    strategy={'min_bookmakers':settings.min_bookmakers,'min_edge':settings.min_edge,'min_ev':settings.min_ev,'min_market_quality':settings.min_market_quality,'min_confidence':settings.min_confidence,'max_vig':settings.max_vig,'kelly_fraction':settings.kelly_fraction,'max_stake_pct':settings.max_stake_pct,'max_event_exposure_pct':settings.max_event_exposure_pct,'max_daily_exposure_pct':settings.max_daily_exposure_pct,'max_league_daily_exposure_pct':settings.max_league_daily_exposure_pct,'paper_only':settings.paper_only,'max_model_weight':settings.max_model_weight,'min_model_reliability_for_paper':settings.min_model_reliability_for_paper,'max_model_market_gap':settings.max_model_market_gap,'max_blended_model_shift':settings.max_blended_model_shift,'max_paper_ev':settings.max_paper_ev,'one_pick_per_event_market':settings.one_pick_per_event_market,'exploration_enabled':settings.exploration_enabled,'exploration_max_bets_per_scan':settings.exploration_max_bets_per_scan,'exploration_min_bookmakers':settings.exploration_min_bookmakers,'exploration_min_edge':settings.exploration_min_edge,'exploration_min_ev':settings.exploration_min_ev,'exploration_min_market_quality':settings.exploration_min_market_quality,'exploration_min_confidence':settings.exploration_min_confidence,'exploration_max_stake_pct':settings.exploration_max_stake_pct,'scout_enabled':settings.scout_enabled,'scout_max_bets_per_scan':settings.scout_max_bets_per_scan,'scout_min_bookmakers':settings.scout_min_bookmakers,'scout_min_market_edge':settings.scout_min_market_edge,'scout_min_market_ev':settings.scout_min_market_ev,'scout_min_market_quality':settings.scout_min_market_quality,'scout_max_stake_pct':settings.scout_max_stake_pct}
    return {'version':APP_VERSION,'configured_sports':settings.sports,'tracked_sports':tracked_sports(db),'scan_window':'now through tomorrow 23:59 Europe/Amsterdam','auto_scan_enabled':settings.auto_scan_enabled,'auto_settle_enabled':settings.auto_settle_enabled,'historical_bootstrap_enabled':settings.historical_bootstrap_enabled,'historical_bootstrap_seasons':settings.historical_bootstrap_seasons,'scheduler_tick_minutes':settings.poll_minutes,'recommended_scan_interval_minutes':recommended_scan_interval_minutes(db),'next_scan_due_at':next_scan_due_at(db),'quota_floor':settings.api_quota_floor,'latest_scan':_scan_dict(latest),'latest_successful_scan':_scan_dict(successful),'scanner_running':cycle_service._cycle_lock.locked(),'model':model,'strategy_health':health,'strategy':strategy}


@router.get('/api/scan-diagnostics')
def scan_diagnostics(db:Session=Depends(get_db)):
    _repair_stale_scans(db); latest=latest_scan(db); successful=latest_successful_scan(db)
    return {'version':APP_VERSION,'latest':_scan_dict(latest),'last_successful':_scan_dict(successful),'scanner_running':cycle_service._cycle_lock.locked(),'configured_sports':settings.sports,'tracked_sports':tracked_sports(db),'markets':settings.market_list,'region':settings.odds_region,'api_key_present':bool(settings.odds_api_key),'scan_window':'now through tomorrow 23:59 Europe/Amsterdam'}


def _latest_scan_signals(db):
    run=latest_successful_scan(db)
    if not run: return run,[]
    end=run.finished_at or datetime.now(timezone.utc)
    return run,db.scalars(select(Signal).where(Signal.created_at>=run.started_at,Signal.created_at<=end).order_by(Signal.ev.desc(),Signal.edge.desc())).all()


@router.get('/api/analytics')
def analytics(db:Session=Depends(get_db)):
    run,rows=_latest_scan_signals(db)
    if not run: return {'latest_scan':None,'reject_counts':{},'near_misses':[],'accepted':[],'market_health':{},'funnel':{}}
    rejects=Counter(); book_counts=[]; vigs=[]; reliabilities=[]; gaps=[]; caps=0; qualities=[]; adjusted=[]; market_edges=[]; market_evs=[]; opp=[]; line_moves=[]; premiums=[]
    for s in rows:
        book_counts.append(s.books); vigs.append(s.market_vig); meta=s.meta or {}; mm=meta.get('model') or {}; reliabilities.append(float(mm.get('reliability') or 0)); qualities.append(float(meta.get('market_quality') or 0)); adjusted.append(float(meta.get('adjusted_ev') if meta.get('adjusted_ev') is not None else s.ev)); market_edges.append(float(meta.get('market_edge') or 0)); market_evs.append(float(meta.get('market_ev') or 0)); opp.append(float(meta.get('opportunity_score') or 0)); premiums.append(float(meta.get('price_premium') or 0)); line_moves.append(float((meta.get('line') or {}).get('odds_move') or 0))
        if mm.get('market_gap') is not None: gaps.append(abs(float(mm.get('market_gap') or 0)))
        caps += 1 if mm.get('shift_capped') else 0
        if not s.accepted and s.reject_reason: rejects.update(x for x in s.reject_reason.split(',') if x)
    def distance(s):
        gates=(s.meta or {}).get('effective_gates') or {}; e=float(gates.get('edge',settings.min_edge)); v=float(gates.get('ev',settings.min_ev)); b=int(gates.get('books',settings.min_bookmakers))
        return max(0,e-s.edge)/max(e,1e-9)+max(0,v-s.ev)/max(v,1e-9)+max(0,b-s.books)/max(b,1)+max(0,s.market_vig-settings.max_vig)/max(settings.max_vig,1e-9)+(100 if s.reject_reason and 'already_started' in s.reject_reason else 0)
    def sig(s): return {'event_id':s.event_id,'sport':s.sport_key,'market':s.market,'outcome':s.outcome,'odds':s.best_odds,'book':s.best_bookmaker,'fair_prob':s.fair_prob,'model_prob':s.model_prob,'edge':s.edge,'ev':s.ev,'confidence':s.confidence,'books':s.books,'vig':s.market_vig,'dispersion':s.dispersion,'reject_reason':s.reject_reason,'distance_to_accept':distance(s),'meta':s.meta}
    candidates=[s for s in rows if not s.accepted and not(s.reject_reason and 'already_started' in s.reject_reason)]; candidates.sort(key=lambda s:(-float((s.meta or {}).get('opportunity_score') or 0),distance(s))); accepted=[s for s in rows if s.accepted]; accepted.sort(key=lambda s:float((s.meta or {}).get('opportunity_score') or 0),reverse=True)
    model_covered=sum(1 for s in rows if ((s.meta or {}).get('model') or {}).get('source')!='market_only')
    high_quality=sum(1 for s in rows if float((s.meta or {}).get('market_quality') or 0)>=settings.min_market_quality)
    positive_adjusted=sum(1 for s in rows if float((s.meta or {}).get('adjusted_ev') or 0)>0)
    scout_like=sum(1 for s in rows if s.books>=settings.scout_min_bookmakers and float((s.meta or {}).get('market_quality') or 0)>=settings.scout_min_market_quality and float((s.meta or {}).get('market_ev') or 0)>=settings.scout_min_market_ev and float((s.meta or {}).get('market_edge') or 0)>=settings.scout_min_market_edge)
    mh={'avg_books':sum(book_counts)/len(book_counts) if book_counts else 0,'avg_vig':sum(vigs)/len(vigs) if vigs else 0,'avg_market_quality':sum(qualities)/len(qualities) if qualities else 0,'avg_adjusted_ev':sum(adjusted)/len(adjusted) if adjusted else 0,'avg_market_edge':sum(market_edges)/len(market_edges) if market_edges else 0,'avg_market_ev':sum(market_evs)/len(market_evs) if market_evs else 0,'avg_opportunity_score':sum(opp)/len(opp) if opp else 0,'avg_price_premium':sum(premiums)/len(premiums) if premiums else 0,'avg_odds_move':sum(line_moves)/len(line_moves) if line_moves else 0,'positive_market_ev':sum(1 for x in market_evs if x>0),'avg_model_reliability':sum(reliabilities)/len(reliabilities) if reliabilities else 0,'avg_abs_model_market_gap':sum(gaps)/len(gaps) if gaps else 0,'model_shift_caps':caps,'signals':len(rows),'positive_ev':sum(1 for s in rows if s.ev>0),'positive_edge':sum(1 for s in rows if s.edge>0),'model_coverage':model_covered/len(rows) if rows else 0}
    funnel={'signals':len(rows),'model_covered':model_covered,'high_market_quality':high_quality,'positive_adjusted_ev':positive_adjusted,'scout_price_candidates':scout_like,'core_accepted':len(accepted)}
    return {'latest_scan':_scan_dict(run),'reject_counts':dict(rejects),'near_misses':[sig(s) for s in candidates[:24]],'accepted':[sig(s) for s in accepted[:30]],'market_health':mh,'funnel':funnel}


@router.get('/api/validation')
def validation(db:Session=Depends(get_db)): return validation_report(db)


@router.get('/api/model-status')
def model_status(db:Session=Depends(get_db)):
    summary=model_summary(db); summary['bootstrap']={'enabled':settings.historical_bootstrap_enabled,'seasons':settings.historical_bootstrap_seasons,'source':'openfootball_public_domain','all_supported_leagues':settings.bootstrap_all_supported_leagues}; return summary


@router.get('/api/shadow')
def shadow(limit:int=100,db:Session=Depends(get_db)):
    all_rows=db.scalars(select(ShadowPick)).all(); settled=[x for x in all_rows if x.status=='SETTLED']; wins=sum(1 for x in settled if x.result=='WIN'); pnl=sum(x.pnl_units for x in settled); clvs=[x.clv for x in all_rows if x.clv is not None]; recent=db.scalars(select(ShadowPick).order_by(ShadowPick.placed_at.desc()).limit(min(limit,300))).all()
    return {'total':len(all_rows),'open':sum(1 for x in all_rows if x.status=='OPEN'),'settled':len(settled),'wins':wins,'winrate':wins/len(settled) if settled else 0,'pnl_units':pnl,'roi_units':pnl/len(settled) if settled else 0,'avg_clv':sum(clvs)/len(clvs) if clvs else None,'picks':[{'sport':x.sport_key,'outcome':x.outcome,'odds':x.odds,'edge':x.edge,'ev':x.ev,'books':x.books,'model_reliability':x.model_reliability,'status':x.status,'result':x.result,'pnl_units':x.pnl_units,'clv':x.clv,'placed_at':x.placed_at,'commence_time':x.commence_time} for x in recent]}


@router.get('/api/scan-history')
def scan_history(limit:int=20,db:Session=Depends(get_db)):
    return [_scan_dict(x) for x in db.scalars(select(ScanRun).order_by(ScanRun.started_at.desc()).limit(min(limit,100))).all()]
