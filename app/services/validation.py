from __future__ import annotations

from collections import defaultdict
from math import log, sqrt
from statistics import mean, pstdev

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.db_models import PaperBet, BetAudit


def _clip(p: float) -> float:
    return min(1 - 1e-9, max(1e-9, float(p)))


def binary_log_loss(p: float, y: int) -> float:
    p = _clip(p)
    return -(y * log(p) + (1 - y) * log(1 - p))


def _max_drawdown(pnls: list[float]) -> float:
    equity = peak = 0.0
    max_dd = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return max_dd


def _wilson(wins: int, n: int, z: float = 1.96) -> tuple[float | None, float | None]:
    if n <= 0: return None, None
    p = wins / n
    den = 1 + z*z/n
    centre = (p + z*z/(2*n)) / den
    half = z * sqrt((p*(1-p)/n) + (z*z/(4*n*n))) / den
    return max(0.0, centre-half), min(1.0, centre+half)


def summarize_rows(rows: list[PaperBet]) -> dict:
    settled = [x for x in rows if x.status == 'SETTLED' and x.result in ('WIN', 'LOSS')]
    if not settled:
        return {
            'n':0,'wins':0,'winrate':0.0,'winrate_low':None,'winrate_high':None,
            'stake':0.0,'pnl':0.0,'roi':0.0,'roi_se':None,'profit_factor':None,
            'avg_odds':None,'avg_clv':None,'median_clv':None,'positive_clv_rate':None,
            'model_brier':None,'market_brier':None,'model_logloss':None,'market_logloss':None,
            'max_drawdown':0.0,'expected_pnl':0.0,'edge_realisation':None,
        }
    settled = sorted(settled, key=lambda x: x.placed_at)
    ys = [1 if x.result == 'WIN' else 0 for x in settled]
    stakes = [float(x.stake or 0.0) for x in settled]
    pnls = [float(x.pnl or 0.0) for x in settled]
    stake = sum(stakes); pnl = sum(pnls)
    clvs = sorted(float(x.clv) for x in settled if x.clv is not None)
    model_brier = mean((float(x.model_prob) - y) ** 2 for x, y in zip(settled, ys))
    market_brier = mean((float(x.fair_prob) - y) ** 2 for x, y in zip(settled, ys))
    unit_returns = [(p/s if s else 0.0) for p,s in zip(pnls,stakes)]
    roi_se = (pstdev(unit_returns)/sqrt(len(unit_returns))) if len(unit_returns)>1 else None
    gross_win = sum(max(0.0,p) for p in pnls); gross_loss = abs(sum(min(0.0,p) for p in pnls))
    expected_pnl = sum(float(x.ev or 0.0)*float(x.stake or 0.0) for x in settled)
    low, high = _wilson(sum(ys), len(ys))
    realised_return = pnl/stake if stake else 0.0
    mean_pred_edge = mean(float(x.ev or 0.0) for x in settled)
    return {
        'n':len(settled),'wins':sum(ys),'winrate':sum(ys)/len(settled),
        'winrate_low':low,'winrate_high':high,'stake':stake,'pnl':pnl,
        'roi':realised_return,'roi_se':roi_se,
        'profit_factor':(gross_win/gross_loss if gross_loss>0 else None),
        'avg_odds':mean(float(x.odds) for x in settled),
        'avg_clv':mean(clvs) if clvs else None,
        'median_clv':(clvs[len(clvs)//2] if clvs else None),
        'positive_clv_rate':(sum(1 for x in clvs if x>0)/len(clvs)) if clvs else None,
        'model_brier':model_brier,'market_brier':market_brier,
        'model_logloss':mean(binary_log_loss(float(x.model_prob),y) for x,y in zip(settled,ys)),
        'market_logloss':mean(binary_log_loss(float(x.fair_prob),y) for x,y in zip(settled,ys)),
        'max_drawdown':_max_drawdown(pnls),'expected_pnl':expected_pnl,
        'edge_realisation':(realised_return/mean_pred_edge if abs(mean_pred_edge)>1e-9 else None),
    }


def calibration(rows: list[PaperBet], bins: int = 6) -> list[dict]:
    settled = [x for x in rows if x.status == 'SETTLED' and x.result in ('WIN','LOSS')]
    buckets = [[] for _ in range(bins)]
    for x in settled:
        p = _clip(float(x.model_prob)); buckets[min(bins-1,int(p*bins))].append(x)
    out=[]
    for i,bucket in enumerate(buckets):
        if not bucket: continue
        preds=[float(x.model_prob) for x in bucket]; ys=[1.0 if x.result=='WIN' else 0.0 for x in bucket]
        out.append({'from':i/bins,'to':(i+1)/bins,'n':len(bucket),'avg_pred':mean(preds),'actual_rate':mean(ys),'gap':mean(ys)-mean(preds)})
    return out


def strategy_health_from_summary(core: dict, recent: dict | None = None) -> dict:
    n=int(core.get('n') or 0)
    if n<30:
        return {'status':'INSUFFICIENT_DATA','risk_multiplier':1.0,'reasons':[f'{n}/30 settled CORE bets']}
    reasons=[]
    avg_clv=core.get('avg_clv')
    if avg_clv is not None and avg_clv < -0.008: reasons.append('negative_clv')
    if core.get('positive_clv_rate') is not None and core['positive_clv_rate'] < 0.42: reasons.append('low_positive_clv_rate')
    mb,kb=core.get('model_brier'),core.get('market_brier')
    if mb is not None and kb is not None and mb > kb + 0.015: reasons.append('model_brier_worse_than_market')
    if core.get('roi',0.0)<-0.08: reasons.append('material_negative_roi')
    if recent and recent.get('n',0)>=12 and recent.get('avg_clv') is not None and recent['avg_clv'] < -0.015: reasons.append('recent_negative_clv')
    severe=sum(r in {'negative_clv','model_brier_worse_than_market','recent_negative_clv'} for r in reasons)
    if severe>=2 or len(reasons)>=3: return {'status':'DEGRADED','risk_multiplier':0.25,'reasons':reasons}
    if reasons: return {'status':'WATCH','risk_multiplier':0.50,'reasons':reasons}
    return {'status':'STABLE','risk_multiplier':1.0,'reasons':[]}


def _tier_map(db: Session) -> dict[int,str]:
    return {int(x.paper_bet_id):x.tier for x in db.scalars(select(BetAudit)).all()}


def _odds_band(odds: float) -> str:
    if odds < 1.75: return '<1.75'
    if odds < 2.50: return '1.75-2.49'
    if odds < 4.00: return '2.50-3.99'
    if odds < 6.00: return '4.00-5.99'
    return '6.00+'


def validation_report(db: Session) -> dict:
    rows=list(db.scalars(select(PaperBet).order_by(PaperBet.placed_at.asc())).all())
    tiers=_tier_map(db)
    core_rows=[x for x in rows if tiers.get(x.id,'LEGACY') in ('CORE','LEGACY')]
    exploration_rows=[x for x in rows if tiers.get(x.id)=='EXPLORATION']
    scout_rows=[x for x in rows if tiers.get(x.id)=='SCOUT']
    settled_core=[x for x in core_rows if x.status=='SETTLED' and x.result in ('WIN','LOSS')]
    recent_core=settled_core[-20:]

    overall=summarize_rows(rows); core=summarize_rows(core_rows); exploration=summarize_rows(exploration_rows); scout=summarize_rows(scout_rows); recent=summarize_rows(recent_core)

    by_sport_rows=defaultdict(list); by_book_rows=defaultdict(list); by_odds_rows=defaultdict(list); by_tier_rows=defaultdict(list)
    for x in rows:
        by_sport_rows[x.sport_key].append(x); by_book_rows[x.bookmaker].append(x); by_odds_rows[_odds_band(float(x.odds))].append(x); by_tier_rows[tiers.get(x.id,'LEGACY')].append(x)
    by_sport=[dict(key=k,**summarize_rows(v)) for k,v in by_sport_rows.items()]
    by_bookmaker=[dict(key=k,**summarize_rows(v)) for k,v in by_book_rows.items()]
    by_odds=[dict(key=k,**summarize_rows(v)) for k,v in by_odds_rows.items()]
    by_tier=[dict(key=k,**summarize_rows(v)) for k,v in by_tier_rows.items()]
    by_sport.sort(key=lambda x:(-x['n'],x['key'])); by_bookmaker.sort(key=lambda x:(-x['n'],x['key']))
    odds_order={'<1.75':0,'1.75-2.49':1,'2.50-3.99':2,'4.00-5.99':3,'6.00+':4}; by_odds.sort(key=lambda x:odds_order.get(x['key'],99))
    health=strategy_health_from_summary(core,recent)

    return {
        'overall':overall,'core':core,'recent_core_20':recent,'exploration':exploration,'scout':scout,
        'health':health,'calibration':calibration(core_rows),'exploration_calibration':calibration(exploration_rows),'scout_calibration':calibration(scout_rows),
        'by_sport':by_sport,'by_bookmaker':by_bookmaker,'by_odds_band':by_odds,'by_tier':by_tier,
        'tier_counts':{
            'core':sum(1 for x in rows if tiers.get(x.id,'LEGACY') in ('CORE','LEGACY')),
            'exploration':sum(1 for x in rows if tiers.get(x.id)=='EXPLORATION'),
            'scout':sum(1 for x in rows if tiers.get(x.id)=='SCOUT'),
        },
        'methodology':{
            'min_health_sample':30,'health_uses':'CORE only',
            'metrics':['ROI','ROI standard error','profit factor','CLV','Brier','log-loss','max drawdown','calibration','odds bands','rolling 20'],
            'note':'EXPLORATION and SCOUT remain tiny paper-only research tiers. CORE risk is governed by CORE evidence, especially CLV and calibration rather than raw win rate alone.',
        },
    }


def risk_multiplier(db: Session) -> float:
    return float(validation_report(db)['health']['risk_multiplier'])
