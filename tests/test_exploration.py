from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

from app.services.engine import _exploration_eligible, _selection_score, _scout_eligible, _scout_score


def signal(**kw):
    base = dict(
        accepted=False,
        market='h2h',
        books=5,
        edge=0.012,
        ev=0.020,
        confidence=0.70,
        market_vig=0.04,
        best_odds=2.10,
        reject_reason='edge_below_threshold,ev_below_threshold',
        meta={
            'market_quality':0.75,
            'adjusted_ev':0.012,
            'market_edge':0.010,
            'market_ev':0.020,
            'price_premium':0.012,
            'reference_books':4,
            'opportunity_score':0.72,
            'line':{'line_support':0.65,'odds_move':-0.01},
            'model':{'source':'elo_poisson_recency_v3','reliability':0.70},
            'commence_time':(datetime.now(timezone.utc)+timedelta(hours=4)).isoformat(),
        },
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_high_quality_near_miss_can_enter_paper_exploration():
    assert _exploration_eligible(signal()) is True


def test_exploration_requires_independent_model():
    meta={**signal().meta,'model':{'source':'market_only','reliability':0.0}}
    assert _exploration_eligible(signal(meta=meta)) is False


def test_exploration_never_bypasses_model_disagreement():
    s=signal(reject_reason='edge_below_threshold,model_market_disagreement')
    assert _exploration_eligible(s) is False


def test_exploration_requires_positive_adjusted_ev():
    meta={**signal().meta,'adjusted_ev':-0.001}
    assert _exploration_eligible(signal(meta=meta)) is False


def test_selection_score_uses_opportunity_score():
    weak=signal(meta={**signal().meta,'opportunity_score':0.40})
    strong=signal(meta={**signal().meta,'opportunity_score':0.82})
    assert _selection_score(strong)>_selection_score(weak)


def test_scout_uses_independent_market_reference_even_when_core_rejects():
    s=signal(edge=0.001,ev=0.001,confidence=0.30,reject_reason='edge_below_threshold,ev_below_threshold,independent_model_unavailable')
    assert _scout_eligible(s) is True


def test_scout_requires_reference_books_and_quality():
    low_q=signal(meta={**signal().meta,'market_quality':0.20})
    assert _scout_eligible(low_q) is False
    no_ref=signal(meta={**signal().meta,'reference_books':1})
    assert _scout_eligible(no_ref) is False
    started=signal(meta={**signal().meta,'commence_time':(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()})
    assert _scout_eligible(started) is False


def test_scout_score_rewards_cleaner_market_price_value():
    weak=signal(books=3,meta={**signal().meta,'market_quality':0.52,'market_edge':0.004,'market_ev':0.006,'price_premium':0.005})
    strong=signal(books=8,meta={**signal().meta,'market_quality':0.85,'market_edge':0.015,'market_ev':0.03,'price_premium':0.025})
    assert _scout_score(strong)>_scout_score(weak)
