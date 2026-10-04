from __future__ import annotations

from collections import defaultdict
from math import log
from statistics import mean

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


def summarize_rows(rows: list[PaperBet]) -> dict:
    settled = [x for x in rows if x.status == 'SETTLED' and x.result in ('WIN', 'LOSS')]
    if not settled:
        return {
            'n': 0, 'wins': 0, 'winrate': 0.0, 'stake': 0.0, 'pnl': 0.0, 'roi': 0.0,
            'avg_odds': None, 'avg_clv': None, 'positive_clv_rate': None,
            'model_brier': None, 'market_brier': None, 'model_logloss': None,
            'market_logloss': None, 'max_drawdown': 0.0,
        }
    settled = sorted(settled, key=lambda x: x.placed_at)
    ys = [1 if x.result == 'WIN' else 0 for x in settled]
    stake = sum(float(x.stake or 0.0) for x in settled)
    pnl = sum(float(x.pnl or 0.0) for x in settled)
    clvs = [float(x.clv) for x in settled if x.clv is not None]
    model_brier = mean((float(x.model_prob) - y) ** 2 for x, y in zip(settled, ys))
    market_brier = mean((float(x.fair_prob) - y) ** 2 for x, y in zip(settled, ys))
    return {
        'n': len(settled), 'wins': sum(ys), 'winrate': sum(ys) / len(settled),
        'stake': stake, 'pnl': pnl, 'roi': pnl / stake if stake else 0.0,
        'avg_odds': mean(float(x.odds) for x in settled),
        'avg_clv': mean(clvs) if clvs else None,
        'positive_clv_rate': (sum(1 for x in clvs if x > 0) / len(clvs)) if clvs else None,
        'model_brier': model_brier, 'market_brier': market_brier,
        'model_logloss': mean(binary_log_loss(float(x.model_prob), y) for x, y in zip(settled, ys)),
        'market_logloss': mean(binary_log_loss(float(x.fair_prob), y) for x, y in zip(settled, ys)),
        'max_drawdown': _max_drawdown([float(x.pnl or 0.0) for x in settled]),
    }


def calibration(rows: list[PaperBet], bins: int = 5) -> list[dict]:
    settled = [x for x in rows if x.status == 'SETTLED' and x.result in ('WIN', 'LOSS')]
    buckets = [[] for _ in range(bins)]
    for x in settled:
        p = _clip(float(x.model_prob))
        buckets[min(bins - 1, int(p * bins))].append(x)
    out = []
    for i, bucket in enumerate(buckets):
        if not bucket: continue
        preds = [float(x.model_prob) for x in bucket]
        ys = [1.0 if x.result == 'WIN' else 0.0 for x in bucket]
        out.append({'from': i / bins, 'to': (i + 1) / bins, 'n': len(bucket), 'avg_pred': mean(preds), 'actual_rate': mean(ys), 'gap': mean(ys) - mean(preds)})
    return out


def strategy_health_from_summary(s: dict) -> dict:
    n = int(s.get('n') or 0)
    if n < 30:
        return {'status': 'INSUFFICIENT_DATA', 'risk_multiplier': 1.0, 'reasons': [f'{n}/30 settled CORE bets']}
    reasons = []
    avg_clv = s.get('avg_clv')
    if avg_clv is not None and avg_clv < -0.01: reasons.append('negative_clv')
    mb, kb = s.get('model_brier'), s.get('market_brier')
    if mb is not None and kb is not None and mb > kb + 0.02: reasons.append('model_brier_worse_than_market')
    if s.get('roi', 0.0) < -0.08: reasons.append('material_negative_roi')
    if len(reasons) >= 2: return {'status': 'DEGRADED', 'risk_multiplier': 0.25, 'reasons': reasons}
    if reasons: return {'status': 'WATCH', 'risk_multiplier': 0.50, 'reasons': reasons}
    return {'status': 'STABLE', 'risk_multiplier': 1.0, 'reasons': []}


def _tier_map(db: Session) -> dict[int, str]:
    return {int(x.paper_bet_id): x.tier for x in db.scalars(select(BetAudit)).all()}


def validation_report(db: Session) -> dict:
    rows = list(db.scalars(select(PaperBet).order_by(PaperBet.placed_at.asc())).all())
    tiers = _tier_map(db)
    core_rows = [x for x in rows if tiers.get(x.id, 'LEGACY') != 'EXPLORATION']
    exploration_rows = [x for x in rows if tiers.get(x.id) == 'EXPLORATION']
    overall = summarize_rows(rows)
    core = summarize_rows(core_rows)
    exploration = summarize_rows(exploration_rows)

    by_sport_rows = defaultdict(list)
    by_book_rows = defaultdict(list)
    for x in rows:
        by_sport_rows[x.sport_key].append(x)
        by_book_rows[x.bookmaker].append(x)
    by_sport = [dict(key=k, **summarize_rows(v)) for k, v in by_sport_rows.items()]
    by_bookmaker = [dict(key=k, **summarize_rows(v)) for k, v in by_book_rows.items()]
    by_sport.sort(key=lambda x: (-x['n'], x['key']))
    by_bookmaker.sort(key=lambda x: (-x['n'], x['key']))
    health = strategy_health_from_summary(core)
    return {
        'overall': overall,
        'core': core,
        'exploration': exploration,
        'health': health,
        'calibration': calibration(core_rows),
        'exploration_calibration': calibration(exploration_rows),
        'by_sport': by_sport,
        'by_bookmaker': by_bookmaker,
        'tier_counts': {
            'core': sum(1 for x in rows if tiers.get(x.id, 'LEGACY') != 'EXPLORATION'),
            'exploration': sum(1 for x in rows if tiers.get(x.id) == 'EXPLORATION'),
        },
        'methodology': {
            'min_health_sample': 30,
            'health_uses': 'CORE only',
            'metrics': ['ROI', 'CLV', 'Brier', 'log-loss', 'max drawdown'],
            'note': 'Exploration bets are tiny paper-only research samples and never weaken CORE acceptance criteria.',
        },
    }


def risk_multiplier(db: Session) -> float:
    return float(validation_report(db)['health']['risk_multiplier'])
