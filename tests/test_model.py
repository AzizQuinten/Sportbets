from app.models.db_models import TeamRating
from app.services import model
from app.services.model import _elo_probs, _poisson_probs


def test_elo_probabilities_sum_to_one():
    home = TeamRating(sport_key='x', team='Home', rating=1600, games=10)
    away = TeamRating(sport_key='x', team='Away', rating=1450, games=10)
    p = _elo_probs(home, away)
    assert abs(sum(p.values()) - 1.0) < 1e-9
    assert p['Home'] > p['Away']
    assert 0.10 < p['Draw'] < 0.40


def test_poisson_probabilities_sum_to_one():
    home = TeamRating(sport_key='x', team='Home', games=10, goals_for_ema=1.8, goals_against_ema=1.0)
    away = TeamRating(sport_key='x', team='Away', games=10, goals_for_ema=1.0, goals_against_ema=1.7)
    p = _poisson_probs(home, away)
    assert abs(sum(p.values()) - 1.0) < 1e-9
    assert p['Home'] > p['Away']


def test_external_probability_caps_extreme_model_shift(monkeypatch):
    def fake_independent(*args, **kwargs):
        return (
            {'Home': 0.90, 'Draw': 0.05, 'Away': 0.05},
            1.0,
            {'source': 'elo_poisson', 'reliability': 1.0},
        )

    monkeypatch.setattr(model, 'independent_h2h', fake_independent)
    event = {'market': 'h2h', 'sport_key': 'x', 'home_team': 'Home', 'away_team': 'Away'}
    market = 0.40
    probability, meta = model.external_probability(None, event, 'Home', market)

    assert probability <= market + model.settings.max_blended_model_shift + 1e-12
    assert meta['shift_capped'] is True
    assert meta['market_disagreement'] is True
    assert abs(meta['market_gap'] - 0.50) < 1e-12
