"""Model layer.

V1 intentionally uses market-consensus as the anchor and only allows a bounded model tilt.
That prevents an untrained model from overpowering information already embedded in prices.
Replace `external_probability` with a trained, time-split calibrated model later.
"""


def external_probability(event: dict, outcome: str, market_fair_prob: float) -> float:
    # Safe baseline: no unsupported predictive claims before historical training data exists.
    # Hook point for Elo/Poisson/XGBoost/calibrated ensemble.
    return market_fair_prob
