from app.models.db_models import TeamRating
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
