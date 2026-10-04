from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

from app.services.validation import binary_log_loss, summarize_rows, calibration, strategy_health_from_summary


def bet(i, result, model=.60, market=.55, odds=2.0, stake=10.0, pnl=None, clv=.02, sport='soccer_test'):
    if pnl is None:
        pnl = stake * (odds - 1) if result == 'WIN' else -stake
    return SimpleNamespace(
        status='SETTLED', result=result, model_prob=model, fair_prob=market,
        odds=odds, stake=stake, pnl=pnl, clv=clv, sport_key=sport,
        bookmaker='book', placed_at=datetime(2026,1,1,tzinfo=timezone.utc)+timedelta(hours=i),
    )


def test_binary_log_loss_prefers_correct_confidence():
    assert binary_log_loss(.8, 1) < binary_log_loss(.55, 1)


def test_summary_contains_calibration_metrics():
    rows = [bet(i, 'WIN' if i % 2 == 0 else 'LOSS') for i in range(10)]
    s = summarize_rows(rows)
    assert s['n'] == 10
    assert s['model_brier'] is not None
    assert s['market_brier'] is not None
    assert s['max_drawdown'] >= 0


def test_calibration_bins_have_counts():
    rows = [bet(0,'WIN',model=.32), bet(1,'LOSS',model=.35), bet(2,'WIN',model=.72)]
    c = calibration(rows)
    assert sum(x['n'] for x in c) == 3


def test_health_requires_sample_before_degrading():
    h = strategy_health_from_summary({'n': 12, 'roi': -.5, 'avg_clv': -.1, 'model_brier': .4, 'market_brier': .2})
    assert h['status'] == 'INSUFFICIENT_DATA'
    assert h['risk_multiplier'] == 1.0


def test_health_degrades_only_with_multiple_bad_signals():
    h = strategy_health_from_summary({'n': 40, 'roi': -.12, 'avg_clv': -.03, 'model_brier': .30, 'market_brier': .24})
    assert h['status'] == 'DEGRADED'
    assert h['risk_multiplier'] == .25
