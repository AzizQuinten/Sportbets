from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

from app.services.engine import _exploration_eligible, _selection_score, _scout_eligible, _scout_score


def signal(**kw):
    base = dict(
        accepted=False,
        market='h2h',
        books=4,
        edge=0.012,
        ev=0.020,
        confidence=0.70,
        market_vig=0.04,
        best_odds=2.10,
        reject_reason='edge_below_threshold,ev_below_threshold',
        meta={
            'market_quality': 0.75,
            'adjusted_ev': 0.012,
            'market_edge': 0.010,
            'market_ev': 0.020,
            'commence_time': (datetime.now(timezone.utc)+timedelta(hours=4)).isoformat(),
        },
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_high_quality_near_miss_can_enter_paper_exploration():
    assert _exploration_eligible(signal()) is True


def test_exploration_never_bypasses_model_safety_reasons():
    s = signal(reject_reason='edge_below_threshold,model_not_mature_enough')
    assert _exploration_eligible(s) is False


def test_exploration_requires_positive_adjusted_ev():
    meta=signal().meta.copy();meta['adjusted_ev']=-0.001
    assert _exploration_eligible(signal(meta=meta)) is False


def test_selection_score_rewards_better_quality_and_adjusted_ev():
    weak_meta=signal().meta.copy();weak_meta.update({'market_quality':0.62,'adjusted_ev':0.005})
    strong_meta=signal().meta.copy();strong_meta.update({'market_quality':0.82,'adjusted_ev':0.03})
    weak = signal(confidence=0.60, meta=weak_meta)
    strong = signal(confidence=0.80, meta=strong_meta)
    assert _selection_score(strong) > _selection_score(weak)


def test_scout_uses_positive_market_price_edge_even_when_core_rejects():
    s = signal(edge=0.001, ev=0.001, confidence=0.30, reject_reason='edge_below_threshold,ev_below_threshold,model_not_mature_enough')
    assert _scout_eligible(s) is True


def test_scout_rejects_bad_market_or_started_event():
    low_q=signal(meta={**signal().meta,'market_quality':0.20})
    assert _scout_eligible(low_q) is False
    started=signal(meta={**signal().meta,'commence_time':(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()})
    assert _scout_eligible(started) is False


def test_scout_score_rewards_cleaner_market_price_value():
    weak=signal(books=2,meta={**signal().meta,'market_quality':0.50,'market_edge':0.001,'market_ev':0.002})
    strong=signal(books=7,meta={**signal().meta,'market_quality':0.85,'market_edge':0.015,'market_ev':0.03})
    assert _scout_score(strong) > _scout_score(weak)
