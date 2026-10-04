from types import SimpleNamespace

from app.services.engine import _exploration_eligible, _selection_score


def signal(**kw):
    base = dict(
        accepted=False,
        market='h2h',
        books=4,
        edge=0.012,
        ev=0.020,
        confidence=0.70,
        reject_reason='edge_below_threshold,ev_below_threshold',
        meta={'market_quality': 0.75, 'adjusted_ev': 0.012},
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_high_quality_near_miss_can_enter_paper_exploration():
    assert _exploration_eligible(signal()) is True


def test_exploration_never_bypasses_model_safety_reasons():
    s = signal(reject_reason='edge_below_threshold,model_not_mature_enough')
    assert _exploration_eligible(s) is False


def test_exploration_requires_positive_adjusted_ev():
    s = signal(meta={'market_quality': 0.75, 'adjusted_ev': -0.001})
    assert _exploration_eligible(s) is False


def test_exploration_requires_market_quality_and_confidence():
    assert _exploration_eligible(signal(meta={'market_quality': 0.40, 'adjusted_ev': 0.02})) is False
    assert _exploration_eligible(signal(confidence=0.40)) is False


def test_selection_score_rewards_better_quality_and_adjusted_ev():
    weak = signal(confidence=0.60, meta={'market_quality': 0.62, 'adjusted_ev': 0.005})
    strong = signal(confidence=0.80, meta={'market_quality': 0.82, 'adjusted_ev': 0.03})
    assert _selection_score(strong) > _selection_score(weak)
