from collections import Counter
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.config import get_settings
from app.models.db_models import PaperBet, ScanRun, ShadowPick, Signal, TeamRating
from app.providers.odds_api import OddsAPIProvider
from app.services.cycle import bootstrap_model_tick, execute_cycle, latest_scan, latest_successful_scan, next_scan_due_at, recommended_scan_interval_minutes
from app.services.engine import settle_h2h_from_scores
from app.services.model import ingest_completed_scores, model_summary

router = APIRouter(); settings = get_settings(); APP_VERSION = '1.6.2'

def _scan_dict(x):
    if not x: return None
    return {'id':x.id,'trigger':x.trigger,'status':x.status,'started_at':x.started_at,'finished_at':x.finished_at,'duration_ms':x.duration_ms,'sports':x.sports,'snapshots':x.snapshots,'signals':x.signals,'accepted':x.accepted,'paper_bets':x.paper_bets,'reject_counts':x.reject_counts or {},'top_edge':x.top_edge,'top_ev':x.top_ev,'quota_remaining':x.quota_remaining,'quota_used':x.quota_used,'error':x.error}

@router.get('/health')
def health(db: Session=Depends(get_db)):
    x=latest_scan(db); return {'ok':True,'version':APP_VERSION,'paper_only':settings.paper_only,'auto_scan_enabled':settings.auto_scan_enabled,'latest_scan_status':x.status if x else None,'configured_sports':settings.sports,'model':model_summary(db)}

@router.post('/api/run-cycle')
def run_cycle(db: Session=Depends(get_db)):
    if not settings.odds_api_key: raise HTTPException(400,'ODDS_API_KEY missing')
    r=execute_cycle(db,trigger='manual')
    if r.get('status')=='FAILED': raise HTTPException(500,r.get('detail','Scan failed'))
    if r.get('status')=='BUSY': raise HTTPException(409,r.get('detail','Scan already running'))
    return r

@router.post('/api/bootstrap-model')
def bootstrap_model(): return bootstrap_model_tick()

@router.post('/api/settle')
def settle(db: Session=Depends(get_db)):
    p=OddsAPIProvider(settings.odds_api_key,settings.odds_region); total=learned=0; errors=[]
    for sport in settings.sports:
        try:
            scores=p.fetch_scores(sport,3); learned+=ingest_completed_scores(db,sport,scores); total+=settle_h2h_from_scores(db,scores)
        except Exception as exc: errors.append(f'{sport}: {type(exc).__name__}: {exc}')
    return {'settled':total,'new_model_results':learned,'errors':errors}

@router.get('/api/signals')
def signals(limit:int=100,db:Session=Depends(get_db)):
    rows=db.scalars(select(Signal).order_by(Signal.created_at.desc()).limit(min(limit,500))).all()
    return [{'id':x.id,'event_id':x.event_id,'sport':x.sport_key,'market':x.market,'outcome':x.outcome,'odds':x.best_odds,'book':x.best_bookmaker,'fair_prob':x.fair_prob,'model_prob':x.model_prob,'edge':x.edge,'ev':x.ev,'confidence':x.confidence,'books':x.books,'vig':x.market_vig,'dispersion':x.dispersion,'accepted':x.accepted,'reject_reason':x.reject_reason,'created_at':x.created_at,'meta':x.meta} for x in rows]

@router.get('/api/paper-bets')
def paper_bets(limit:int=200,db:Session=Depends(get_db)):
    rows=db.scalars(select(PaperBet).order_by(PaperBet.placed_at.desc()).limit(min(limit,500))).all()
    return [{'id':x.id,'event_id':x.event_id,'sport':x.sport_key,'market':x.market,'outcome':x.outcome,'bookmaker':x.bookmaker,'odds':x.odds,'stake':x.stake,'edge':x.edge,'ev':x.ev,'status':x.status,'pnl':x.pnl,'closing_odds':x.closing_odds,'clv':x.clv,'result':x.result,'placed_at':x.placed_at,'commence_time':x.commence_time} for x in rows]

@router.get('/api/kpis')
def kpis(db:Session=Depends(get_db)):
    bets=db.scalars(select(PaperBet)).all(); settled=[b for b in bets if b.status=='SETTLED']; pnl=sum(b.pnl for b in settled); staked=sum(b.stake for b in settled); wins=sum(1 for b in settled if b.result=='WIN'); clvs=[b.clv for b in bets if b.clv is not None]; latest=latest_successful_scan(db)
    return {'bankroll_start':settings.bankroll,'equity':settings.bankroll+pnl,'pnl':pnl,'roi':pnl/staked if staked else 0,'bets':len(bets),'settled':len(settled),'winrate':wins/len(settled) if settled else 0,'open_exposure':sum(b.stake for b in bets if b.status=='OPEN'),'avg_clv':sum(clvs)/len(clvs) if clvs else None,'last_scan_prices':latest.snapshots if latest else 0,'last_scan_signals':latest.signals if latest else 0,'quota_remaining':latest.quota_remaining if latest else None}

@router.get('/api/engine-status')
def engine_status(db:Session=Depends(get_db)):
    latest=latest_scan(db); successful=latest_successful_scan(db)
    return {'version':APP_VERSION,'configured_sports':settings.sports,'scan_window':'today+tomorrow Europe/Amsterdam','auto_scan_enabled':settings.auto_scan_enabled,'auto_settle_enabled':settings.auto_settle_enabled,'historical_bootstrap_enabled':settings.historical_bootstrap_enabled,'historical_bootstrap_seasons':settings.historical_bootstrap_seasons,'scheduler_tick_minutes':settings.poll_minutes,'recommended_scan_interval_minutes':recommended_scan_interval_minutes(db),'next_scan_due_at':next_scan_due_at(db),'quota_floor':settings.api_quota_floor,'latest_scan':_scan_dict(latest),'latest_successful_scan':_scan_dict(successful),'model':model_summary(db),'strategy':{'min_bookmakers':settings.min_bookmakers,'min_edge':settings.min_edge,'min_ev':settings.min_ev,'max_vig':settings.max_vig,'kelly_fraction':settings.kelly_fraction,'max_stake_pct':settings.max_stake_pct,'max_event_exposure_pct':settings.max_event_exposure_pct,'max_daily_exposure_pct':settings.max_daily_exposure_pct,'paper_only':settings.paper_only,'max_model_weight':settings.max_model_weight,'min_model_reliability_for_paper':settings.min_model_reliability_for_paper,'max_model_market_gap':settings.max_model_market_gap,'max_blended_model_shift':settings.max_blended_model_shift,'max_paper_ev':settings.max_paper_ev,'one_pick_per_event_market':settings.one_pick_per_event_market,'shadow_min_edge':settings.shadow_min_edge,'shadow_min_ev':settings.shadow_min_ev}}

def _latest_scan_signals(db):
    run=latest_successful_scan(db)
    if not run:return run,[]
    end=run.finished_at or datetime.now(timezone.utc)
    return run,db.scalars(select(Signal).where(Signal.created_at>=run.started_at,Signal.created_at<=end).order_by(Signal.ev.desc(),Signal.edge.desc())).all()

@router.get('/api/analytics')
def analytics(db:Session=Depends(get_db)):
    run,rows=_latest_scan_signals(db)
    if not run:return {'latest_scan':None,'reject_counts':{},'near_misses':[],'accepted':[],'market_health':{}}
    rejects=Counter(); book_counts=[]; vigs=[]; reliabilities=[]; gaps=[]; caps=0
    for s in rows:
        book_counts.append(s.books); vigs.append(s.market_vig); mm=(s.meta or {}).get('model') or {}; reliabilities.append(float(mm.get('reliability') or 0));
        if mm.get('market_gap') is not None:gaps.append(abs(float(mm.get('market_gap') or 0)))
        caps+=1 if mm.get('shift_capped') else 0
        if not s.accepted and s.reject_reason:rejects.update(x for x in s.reject_reason.split(',') if x)
    def distance(s): return max(0,settings.min_edge-s.edge)/max(settings.min_edge,1e-9)+max(0,settings.min_ev-s.ev)/max(settings.min_ev,1e-9)+max(0,settings.min_bookmakers-s.books)/max(settings.min_bookmakers,1)+max(0,s.market_vig-settings.max_vig)/max(settings.max_vig,1e-9)+(100 if s.reject_reason and 'already_started' in s.reject_reason else 0)
    def sig(s): return {'event_id':s.event_id,'sport':s.sport_key,'market':s.market,'outcome':s.outcome,'odds':s.best_odds,'book':s.best_bookmaker,'fair_prob':s.fair_prob,'model_prob':s.model_prob,'edge':s.edge,'ev':s.ev,'confidence':s.confidence,'books':s.books,'vig':s.market_vig,'dispersion':s.dispersion,'reject_reason':s.reject_reason,'distance_to_accept':distance(s),'meta':s.meta}
    candidates=[s for s in rows if not s.accepted and not(s.reject_reason and 'already_started' in s.reject_reason)]; candidates.sort(key=lambda s:(distance(s),-s.ev,-s.edge)); accepted=[s for s in rows if s.accepted]; accepted.sort(key=lambda s:(s.ev,s.edge),reverse=True)
    return {'latest_scan':_scan_dict(run),'reject_counts':dict(rejects),'near_misses':[sig(s) for s in candidates[:15]],'accepted':[sig(s) for s in accepted[:30]],'market_health':{'avg_books':sum(book_counts)/len(book_counts) if book_counts else 0,'avg_vig':sum(vigs)/len(vigs) if vigs else 0,'avg_model_reliability':sum(reliabilities)/len(reliabilities) if reliabilities else 0,'avg_abs_model_market_gap':sum(gaps)/len(gaps) if gaps else 0,'model_shift_caps':caps,'signals':len(rows),'positive_ev':sum(1 for s in rows if s.ev>0),'positive_edge':sum(1 for s in rows if s.edge>0)}}

@router.get('/api/model-status')
def model_status(db:Session=Depends(get_db)):
    summary=model_summary(db); summary['bootstrap']={'enabled':settings.historical_bootstrap_enabled,'seasons':settings.historical_bootstrap_seasons,'source':'openfootball_public_domain'}; return summary

@router.get('/api/shadow')
def shadow(limit:int=100,db:Session=Depends(get_db)):
    all_rows=db.scalars(select(ShadowPick)).all(); settled=[x for x in all_rows if x.status=='SETTLED']; wins=sum(1 for x in settled if x.result=='WIN'); pnl=sum(x.pnl_units for x in settled); clvs=[x.clv for x in all_rows if x.clv is not None]; recent=db.scalars(select(ShadowPick).order_by(ShadowPick.placed_at.desc()).limit(min(limit,300))).all()
    return {'total':len(all_rows),'open':sum(1 for x in all_rows if x.status=='OPEN'),'settled':len(settled),'wins':wins,'winrate':wins/len(settled) if settled else 0,'pnl_units':pnl,'roi_units':pnl/len(settled) if settled else 0,'avg_clv':sum(clvs)/len(clvs) if clvs else None,'picks':[{'sport':x.sport_key,'outcome':x.outcome,'odds':x.odds,'edge':x.edge,'ev':x.ev,'books':x.books,'model_reliability':x.model_reliability,'status':x.status,'result':x.result,'pnl_units':x.pnl_units,'clv':x.clv,'placed_at':x.placed_at,'commence_time':x.commence_time} for x in recent]}

@router.get('/api/scan-history')
def scan_history(limit:int=20,db:Session=Depends(get_db)):
    return [_scan_dict(x) for x in db.scalars(select(ScanRun).order_by(ScanRun.started_at.desc()).limit(min(limit,100))).all()]
