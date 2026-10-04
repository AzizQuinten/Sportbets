from __future__ import annotations

from datetime import datetime, timezone
from math import exp, factorial, sqrt

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.db_models import MatchResult, TeamRating

settings = get_settings()
BASE_GOALS = 1.35


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _parse_time(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def _score_map(event: dict) -> dict[str, float]:
    return {x['name']: float(x['score']) for x in (event.get('scores') or []) if x.get('name') is not None}


def _rating(db: Session, sport_key: str, team: str) -> TeamRating:
    row = db.scalar(select(TeamRating).where(TeamRating.sport_key == sport_key, TeamRating.team == team))
    if row:
        return row
    row = TeamRating(sport_key=sport_key, team=team)
    db.add(row)
    db.flush()
    return row


def ingest_completed_scores(db: Session, sport_key: str, scores: list[dict]) -> int:
    completed = [x for x in scores if x.get('completed') and x.get('scores')]
    completed.sort(key=lambda x: _parse_time(x.get('commence_time')))
    inserted = 0

    for event in completed:
        event_id = event.get('id')
        home = event.get('home_team')
        away = event.get('away_team')
        if not event_id or not home or not away:
            continue
        if db.scalar(select(MatchResult.id).where(MatchResult.event_id == event_id)):
            continue

        sm = _score_map(event)
        hs, as_ = sm.get(home), sm.get(away)
        if hs is None or as_ is None:
            continue

        hr = _rating(db, sport_key, home)
        ar = _rating(db, sport_key, away)

        expected_home = 1.0 / (1.0 + 10 ** ((ar.rating - (hr.rating + settings.elo_home_advantage)) / 400.0))
        actual_home = 1.0 if hs > as_ else 0.5 if hs == as_ else 0.0
        k = settings.elo_k_factor * (1.0 if min(hr.games, ar.games) < 20 else 0.75)
        delta = k * (actual_home - expected_home)
        hr.rating += delta
        ar.rating -= delta

        alpha_h = 0.22 if hr.games < 5 else 0.14
        alpha_a = 0.22 if ar.games < 5 else 0.14
        hr.goals_for_ema = (1 - alpha_h) * hr.goals_for_ema + alpha_h * hs
        hr.goals_against_ema = (1 - alpha_h) * hr.goals_against_ema + alpha_h * as_
        ar.goals_for_ema = (1 - alpha_a) * ar.goals_for_ema + alpha_a * as_
        ar.goals_against_ema = (1 - alpha_a) * ar.goals_against_ema + alpha_a * hs

        hr.games += 1
        ar.games += 1
        if hs > as_:
            hr.wins += 1
            ar.losses += 1
        elif hs < as_:
            ar.wins += 1
            hr.losses += 1
        else:
            hr.draws += 1
            ar.draws += 1
        now = datetime.now(timezone.utc)
        hr.updated_at = now
        ar.updated_at = now

        db.add(MatchResult(
            event_id=event_id,
            sport_key=sport_key,
            commence_time=_parse_time(event.get('commence_time')),
            home_team=home,
            away_team=away,
            home_score=hs,
            away_score=as_,
        ))
        inserted += 1

    db.commit()
    return inserted


def _elo_probs(home: TeamRating, away: TeamRating) -> dict[str, float]:
    diff = home.rating + settings.elo_home_advantage - away.rating
    decisive_home = 1.0 / (1.0 + 10 ** (-diff / 400.0))
    draw = _clamp(0.18 + 0.12 * exp(-abs(diff) / 250.0), 0.16, 0.30)
    return {
        home.team: (1.0 - draw) * decisive_home,
        'Draw': draw,
        away.team: (1.0 - draw) * (1.0 - decisive_home),
    }


def _poisson_pmf(k: int, lam: float) -> float:
    return exp(-lam) * (lam ** k) / factorial(k)


def _poisson_probs(home: TeamRating, away: TeamRating) -> dict[str, float]:
    home_lambda = _clamp(sqrt(max(0.25, home.goals_for_ema) * max(0.25, away.goals_against_ema)) * 1.08, 0.35, 3.6)
    away_lambda = _clamp(sqrt(max(0.25, away.goals_for_ema) * max(0.25, home.goals_against_ema)), 0.30, 3.3)
    hp = dp = ap = 0.0
    for hg in range(9):
        ph = _poisson_pmf(hg, home_lambda)
        for ag in range(9):
            p = ph * _poisson_pmf(ag, away_lambda)
            if hg > ag:
                hp += p
            elif hg == ag:
                dp += p
            else:
                ap += p
    total = hp + dp + ap
    if total <= 0:
        return _elo_probs(home, away)
    return {home.team: hp / total, 'Draw': dp / total, away.team: ap / total}


def independent_h2h(db: Session, sport_key: str, home_team: str, away_team: str) -> tuple[dict[str, float] | None, float, dict]:
    home = db.scalar(select(TeamRating).where(TeamRating.sport_key == sport_key, TeamRating.team == home_team))
    away = db.scalar(select(TeamRating).where(TeamRating.sport_key == sport_key, TeamRating.team == away_team))
    if not home or not away:
        return None, 0.0, {'source': 'market_only', 'home_games': home.games if home else 0, 'away_games': away.games if away else 0}

    min_games = min(home.games, away.games)
    reliability = _clamp(min_games / max(1, settings.model_full_strength_games), 0.0, 1.0)
    elo = _elo_probs(home, away)
    pois = _poisson_probs(home, away)
    independent = {k: 0.55 * elo[k] + 0.45 * pois[k] for k in elo}
    total = sum(independent.values()) or 1.0
    independent = {k: v / total for k, v in independent.items()}
    return independent, reliability, {
        'source': 'elo_poisson',
        'reliability': reliability,
        'home_elo': round(home.rating, 1),
        'away_elo': round(away.rating, 1),
        'home_games': home.games,
        'away_games': away.games,
    }


def external_probability(db: Session, event: dict, outcome: str, market_fair_prob: float) -> tuple[float, dict]:
    if event.get('market') != 'h2h':
        return market_fair_prob, {'source': 'market_only', 'reliability': 0.0, 'model_weight': 0.0}

    probs, reliability, meta = independent_h2h(
        db,
        event['sport_key'],
        event['home_team'],
        event['away_team'],
    )
    if not probs or outcome not in probs:
        return market_fair_prob, {**meta, 'reliability': 0.0, 'model_weight': 0.0}

    weight = settings.max_model_weight * reliability
    raw = probs[outcome]
    market_gap = raw - market_fair_prob
    unbounded = (1.0 - weight) * market_fair_prob + weight * raw
    shift = unbounded - market_fair_prob
    bounded_shift = _clamp(shift, -settings.max_blended_model_shift, settings.max_blended_model_shift)
    blended = market_fair_prob + bounded_shift

    return _clamp(blended, 0.01, 0.99), {
        **meta,
        'raw_model_prob': raw,
        'market_anchor_prob': market_fair_prob,
        'model_weight': weight,
        'market_gap': market_gap,
        'unbounded_blended_prob': unbounded,
        'blended_shift': bounded_shift,
        'shift_capped': abs(shift - bounded_shift) > 1e-12,
        'market_disagreement': abs(market_gap) > settings.max_model_market_gap,
    }


def model_summary(db: Session) -> dict:
    ratings = db.scalar(select(func.count(TeamRating.id))) or 0
    results = db.scalar(select(func.count(MatchResult.id))) or 0
    mature = db.scalar(select(func.count(TeamRating.id)).where(TeamRating.games >= settings.model_full_strength_games)) or 0
    avg_games = db.scalar(select(func.avg(TeamRating.games))) or 0.0
    last_result = db.scalar(select(func.max(MatchResult.commence_time)))
    return {
        'ratings': int(ratings),
        'results': int(results),
        'mature_teams': int(mature),
        'avg_games_per_team': float(avg_games),
        'last_result_at': last_result,
        'max_model_weight': settings.max_model_weight,
        'full_strength_games': settings.model_full_strength_games,
        'status': 'LEARNING' if results < 40 or mature == 0 else 'ACTIVE',
        'guardrails': {
            'min_reliability_for_paper': settings.min_model_reliability_for_paper,
            'max_market_gap': settings.max_model_market_gap,
            'max_blended_shift': settings.max_blended_model_shift,
            'max_paper_ev': settings.max_paper_ev,
        },
    }
