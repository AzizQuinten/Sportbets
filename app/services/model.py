from __future__ import annotations

from datetime import datetime, timezone
from math import exp, factorial, log1p, sqrt
import re
import unicodedata

from sqlalchemy import func, or_, select
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
    dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _canonical(name: str) -> str:
    value = unicodedata.normalize('NFKD', str(name or '')).encode('ascii', 'ignore').decode('ascii').lower()
    value = value.replace('&', 'and')
    value = re.sub(r'\b(fc|afc|cf|sc|ac|as|sv|fk|bk|sk|club|football|calcio)\b', ' ', value)
    return re.sub(r'[^a-z0-9]+', '', value)


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


def _find_rating(db: Session, sport_key: str, team: str) -> TeamRating | None:
    exact = db.scalar(select(TeamRating).where(TeamRating.sport_key == sport_key, TeamRating.team == team))
    if exact:
        return exact
    key = _canonical(team)
    if not key:
        return None
    rows = db.scalars(select(TeamRating).where(TeamRating.sport_key == sport_key)).all()
    matches = [x for x in rows if _canonical(x.team) == key]
    return matches[0] if len(matches) == 1 else None


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
        experience = 1.0 if min(hr.games, ar.games) < 20 else 0.78
        margin = 1.0 + 0.18 * log1p(abs(hs - as_))
        k = settings.elo_k_factor * experience * margin
        delta = k * (actual_home - expected_home)
        hr.rating += delta
        ar.rating -= delta

        alpha_h = 0.20 if hr.games < 8 else 0.12
        alpha_a = 0.20 if ar.games < 8 else 0.12
        hr.goals_for_ema = (1 - alpha_h) * hr.goals_for_ema + alpha_h * hs
        hr.goals_against_ema = (1 - alpha_h) * hr.goals_against_ema + alpha_h * as_
        ar.goals_for_ema = (1 - alpha_a) * ar.goals_for_ema + alpha_a * as_
        ar.goals_against_ema = (1 - alpha_a) * ar.goals_against_ema + alpha_a * hs

        hr.games += 1
        ar.games += 1
        if hs > as_:
            hr.wins += 1; ar.losses += 1
        elif hs < as_:
            ar.wins += 1; hr.losses += 1
        else:
            hr.draws += 1; ar.draws += 1
        now = datetime.now(timezone.utc)
        hr.updated_at = now; ar.updated_at = now

        db.add(MatchResult(
            event_id=event_id, sport_key=sport_key, commence_time=_parse_time(event.get('commence_time')),
            home_team=home, away_team=away, home_score=hs, away_score=as_,
        ))
        inserted += 1

    db.commit()
    return inserted


def _league_context(db: Session, sport_key: str, limit: int = 500) -> dict:
    rows = db.scalars(
        select(MatchResult).where(MatchResult.sport_key == sport_key).order_by(MatchResult.commence_time.desc()).limit(limit)
    ).all()
    if not rows:
        return {'n': 0, 'home_goals': 1.45, 'away_goals': 1.20, 'draw_rate': 0.26}
    hg = sum(float(x.home_score) for x in rows) / len(rows)
    ag = sum(float(x.away_score) for x in rows) / len(rows)
    draws = sum(1 for x in rows if x.home_score == x.away_score) / len(rows)
    return {
        'n': len(rows),
        'home_goals': _clamp(hg, 0.75, 2.30),
        'away_goals': _clamp(ag, 0.65, 2.10),
        'draw_rate': _clamp(draws, 0.16, 0.34),
    }


def _team_form(db: Session, sport_key: str, team: str, league: dict) -> dict:
    rows = db.scalars(
        select(MatchResult)
        .where(MatchResult.sport_key == sport_key, or_(MatchResult.home_team == team, MatchResult.away_team == team))
        .order_by(MatchResult.commence_time.desc())
        .limit(max(6, settings.model_recent_matches))
    ).all()
    if not rows:
        avg = (league['home_goals'] + league['away_goals']) / 2.0
        return {'n': 0, 'gf': avg, 'ga': avg, 'ppg': 1.35, 'effective_n': 0.0, 'last_at': None}

    now = datetime.now(timezone.utc)
    weighted_gf = weighted_ga = weighted_pts = total_w = 0.0
    for x in rows:
        at = x.commence_time if x.commence_time.tzinfo else x.commence_time.replace(tzinfo=timezone.utc)
        age_days = max(0.0, (now - at).total_seconds() / 86400.0)
        w = 0.5 ** (age_days / max(1.0, settings.model_recency_half_life_days))
        is_home = x.home_team == team
        gf = float(x.home_score if is_home else x.away_score)
        ga = float(x.away_score if is_home else x.home_score)
        pts = 3.0 if gf > ga else 1.0 if gf == ga else 0.0
        weighted_gf += w * gf; weighted_ga += w * ga; weighted_pts += w * pts; total_w += w
    avg = (league['home_goals'] + league['away_goals']) / 2.0
    prior = max(1.0, settings.model_team_prior_games)
    gf = (weighted_gf + prior * avg) / (total_w + prior)
    ga = (weighted_ga + prior * avg) / (total_w + prior)
    ppg = (weighted_pts + prior * 1.35) / (total_w + prior)
    last_at = rows[0].commence_time
    return {'n': len(rows), 'gf': gf, 'ga': ga, 'ppg': ppg, 'effective_n': total_w, 'last_at': last_at}


def _elo_probs(home: TeamRating, away: TeamRating, draw_hint: float) -> dict[str, float]:
    diff = home.rating + settings.elo_home_advantage - away.rating
    decisive_home = 1.0 / (1.0 + 10 ** (-diff / 400.0))
    draw = _clamp(draw_hint + 0.04 * exp(-abs(diff) / 250.0), 0.16, 0.34)
    return {
        home.team: (1.0 - draw) * decisive_home,
        'Draw': draw,
        away.team: (1.0 - draw) * (1.0 - decisive_home),
    }


def _poisson_pmf(k: int, lam: float) -> float:
    return exp(-lam) * (lam ** k) / factorial(k)


def _dc_tau(hg: int, ag: int, home_lambda: float, away_lambda: float) -> float:
    rho = _clamp(settings.dixon_coles_rho, -0.20, 0.20)
    if hg == 0 and ag == 0: return max(0.05, 1.0 - home_lambda * away_lambda * rho)
    if hg == 0 and ag == 1: return max(0.05, 1.0 + home_lambda * rho)
    if hg == 1 and ag == 0: return max(0.05, 1.0 + away_lambda * rho)
    if hg == 1 and ag == 1: return max(0.05, 1.0 - rho)
    return 1.0


def _poisson_probs(home_name: str, away_name: str, home_lambda: float, away_lambda: float) -> dict[str, float]:
    hp = dp = ap = 0.0
    for hg in range(9):
        ph = _poisson_pmf(hg, home_lambda)
        for ag in range(9):
            p = ph * _poisson_pmf(ag, away_lambda) * _dc_tau(hg, ag, home_lambda, away_lambda)
            if hg > ag: hp += p
            elif hg == ag: dp += p
            else: ap += p
    total = hp + dp + ap
    if total <= 0:
        return {home_name: 0.38, 'Draw': 0.27, away_name: 0.35}
    return {home_name: hp / total, 'Draw': dp / total, away_name: ap / total}


def independent_h2h(db: Session, sport_key: str, home_team: str, away_team: str) -> tuple[dict[str, float] | None, float, dict]:
    home = _find_rating(db, sport_key, home_team)
    away = _find_rating(db, sport_key, away_team)
    if not home or not away:
        return None, 0.0, {
            'source': 'market_only',
            'home_games': home.games if home else 0,
            'away_games': away.games if away else 0,
            'name_resolution': 'partial' if (home or away) else 'none',
        }

    league = _league_context(db, sport_key)
    hf = _team_form(db, sport_key, home.team, league)
    af = _team_form(db, sport_key, away.team, league)
    league_team_avg = max(0.65, (league['home_goals'] + league['away_goals']) / 2.0)

    home_attack = _clamp(hf['gf'] / league_team_avg, 0.55, 1.65)
    home_defence = _clamp(hf['ga'] / league_team_avg, 0.55, 1.65)
    away_attack = _clamp(af['gf'] / league_team_avg, 0.55, 1.65)
    away_defence = _clamp(af['ga'] / league_team_avg, 0.55, 1.65)
    form_delta = _clamp((hf['ppg'] - af['ppg']) / 3.0, -0.30, 0.30)

    home_lambda = league['home_goals'] * sqrt(home_attack * away_defence) * (1.0 + 0.10 * form_delta)
    away_lambda = league['away_goals'] * sqrt(away_attack * home_defence) * (1.0 - 0.08 * form_delta)
    home_lambda = _clamp(home_lambda, 0.30, 3.80)
    away_lambda = _clamp(away_lambda, 0.25, 3.50)

    pois = _poisson_probs(home.team, away.team, home_lambda, away_lambda)
    elo = _elo_probs(home, away, league['draw_rate'])
    independent = {
        home.team: 0.48 * elo[home.team] + 0.52 * pois[home.team],
        'Draw': 0.40 * elo['Draw'] + 0.60 * pois['Draw'],
        away.team: 0.48 * elo[away.team] + 0.52 * pois[away.team],
    }
    total = sum(independent.values()) or 1.0
    independent = {k: v / total for k, v in independent.items()}

    games_factor = _clamp(min(home.games, away.games) / max(1, settings.model_full_strength_games), 0.0, 1.0)
    history_factor = _clamp(min(hf['effective_n'], af['effective_n']) / max(4.0, settings.model_full_strength_games * 0.75), 0.0, 1.0)
    league_factor = _clamp(league['n'] / 120.0, 0.30, 1.0)
    last_dates = [x for x in (hf['last_at'], af['last_at']) if x is not None]
    if last_dates:
        oldest = min(x if x.tzinfo else x.replace(tzinfo=timezone.utc) for x in last_dates)
        age = max(0.0, (datetime.now(timezone.utc) - oldest).total_seconds() / 86400.0)
        freshness = 0.5 ** (age / max(1.0, settings.model_freshness_half_life_days))
    else:
        freshness = 0.35
    reliability = _clamp((0.35 * games_factor + 0.35 * history_factor + 0.20 * league_factor + 0.10 * freshness), 0.0, 1.0)

    # Map canonical rating names back to the live event labels expected by engine.
    mapped = {home_team: independent[home.team], 'Draw': independent['Draw'], away_team: independent[away.team]}
    return mapped, reliability, {
        'source': 'elo_poisson_recency_v3',
        'reliability': reliability,
        'home_elo': round(home.rating, 1),
        'away_elo': round(away.rating, 1),
        'home_games': home.games,
        'away_games': away.games,
        'home_form_n': hf['n'],
        'away_form_n': af['n'],
        'home_ppg': round(hf['ppg'], 3),
        'away_ppg': round(af['ppg'], 3),
        'home_lambda': round(home_lambda, 3),
        'away_lambda': round(away_lambda, 3),
        'league_samples': league['n'],
        'league_home_goals': round(league['home_goals'], 3),
        'league_away_goals': round(league['away_goals'], 3),
        'freshness': round(freshness, 4),
        'name_resolution': 'canonical' if (home.team != home_team or away.team != away_team) else 'exact',
    }


def external_probability(db: Session, event: dict, outcome: str, market_fair_prob: float) -> tuple[float, dict]:
    if event.get('market') != 'h2h':
        return market_fair_prob, {'source': 'market_only', 'reliability': 0.0, 'model_weight': 0.0}

    probs, reliability, meta = independent_h2h(db, event['sport_key'], event['home_team'], event['away_team'])
    if not probs or outcome not in probs:
        return market_fair_prob, {**meta, 'reliability': 0.0, 'model_weight': 0.0}

    # Market remains the anchor. Independent evidence can move it only gradually
    # and the maximum shift is capped to protect against model misspecification.
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
    leagues = db.scalar(select(func.count(func.distinct(MatchResult.sport_key)))) or 0
    return {
        'ratings': int(ratings),
        'results': int(results),
        'mature_teams': int(mature),
        'avg_games_per_team': float(avg_games),
        'leagues_learned': int(leagues),
        'last_result_at': last_result,
        'max_model_weight': settings.max_model_weight,
        'full_strength_games': settings.model_full_strength_games,
        'status': 'LEARNING' if results < 120 or mature < 10 else 'ACTIVE',
        'engine': 'Elo + recency-weighted league/team Poisson + Dixon-Coles + market anchor',
        'guardrails': {
            'min_reliability_for_paper': settings.min_model_reliability_for_paper,
            'max_market_gap': settings.max_model_market_gap,
            'max_blended_shift': settings.max_blended_model_shift,
            'max_paper_ev': settings.max_paper_ev,
            'recency_half_life_days': settings.model_recency_half_life_days,
        },
    }
