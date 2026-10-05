from collections import defaultdict
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.db_models import OddsSnapshot, PaperBet, ShadowPick, Signal, BetAudit
from app.services.model import external_probability
from app.services.quant import (
    consensus_market, expected_value, confidence_score, market_quality,
    uncertainty_adjusted_ev, fractional_kelly_stake,
)
from app.services.validation import risk_multiplier

settings = get_settings()
LOCAL_TZ = ZoneInfo('Europe/Amsterdam')


def ingest_rows(db: Session, rows: list[dict]) -> int:
    db.add_all([OddsSnapshot(**r) for r in rows])
    db.commit()
    return len(rows)


def _today_start():
    now_local = datetime.now(LOCAL_TZ)
    local_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    return local_start.astimezone(timezone.utc)


def _today_exposure(db: Session) -> float:
    return float(db.scalar(select(func.coalesce(func.sum(PaperBet.stake), 0.0)).where(PaperBet.placed_at >= _today_start())) or 0.0)


def _today_tier_exposure(db: Session, tier: str) -> float:
    q = (
        select(func.coalesce(func.sum(PaperBet.stake), 0.0))
        .join(BetAudit, BetAudit.paper_bet_id == PaperBet.id)
        .where(PaperBet.placed_at >= _today_start(), BetAudit.tier == tier)
    )
    return float(db.scalar(q) or 0.0)


def _today_league_exposure(db: Session, sport_key: str) -> float:
    return float(db.scalar(
        select(func.coalesce(func.sum(PaperBet.stake), 0.0)).where(
            PaperBet.placed_at >= _today_start(), PaperBet.sport_key == sport_key
        )
    ) or 0.0)


def _event_exposure(db: Session, event_id: str) -> float:
    return float(db.scalar(select(func.coalesce(func.sum(PaperBet.stake), 0.0)).where(PaperBet.event_id == event_id)) or 0.0)


def _line_features(db: Session, group: list[dict], outcome: str, current_best: float) -> dict:
    """Compare current best price with the previous scan for this exact market."""
    if not group:
        return {'previous_best_odds': None, 'odds_move': 0.0, 'minutes_since_previous': None, 'line_support': 0.5}
    current_at = max((r.get('observed_at') for r in group if r.get('observed_at')), default=datetime.now(timezone.utc))
    if current_at.tzinfo is None:
        current_at = current_at.replace(tzinfo=timezone.utc)
    meta = group[0]
    prior = db.scalars(
        select(OddsSnapshot)
        .where(
            OddsSnapshot.event_id == meta['event_id'],
            OddsSnapshot.market == meta['market'],
            OddsSnapshot.outcome == outcome,
            OddsSnapshot.observed_at < current_at - timedelta(seconds=5),
        )
        .order_by(OddsSnapshot.observed_at.desc())
        .limit(100)
    ).all()
    if not prior:
        return {'previous_best_odds': None, 'odds_move': 0.0, 'minutes_since_previous': None, 'line_support': 0.5}
    latest_at = prior[0].observed_at
    if latest_at.tzinfo is None:
        latest_at = latest_at.replace(tzinfo=timezone.utc)
    cutoff = latest_at - timedelta(seconds=90)
    previous_best = max((float(x.price) for x in prior if (x.observed_at if x.observed_at.tzinfo else x.observed_at.replace(tzinfo=timezone.utc)) >= cutoff), default=None)
    if not previous_best or previous_best <= 1.0:
        return {'previous_best_odds': None, 'odds_move': 0.0, 'minutes_since_previous': None, 'line_support': 0.5}
    move = (float(current_best) / previous_best) - 1.0
    # Shortening odds is mild confirmation; drifting odds is mild negative evidence.
    line_support = max(0.0, min(1.0, 0.5 - move / 0.08))
    minutes = max(0.0, (current_at - latest_at).total_seconds() / 60.0)
    return {
        'previous_best_odds': previous_best,
        'odds_move': move,
        'minutes_since_previous': minutes,
        'line_support': line_support,
    }


def _dynamic_gates(con, model_meta, odds: float, quality: float):
    reliability = float(model_meta.get('reliability') or 0.0)
    multiplier = 1.0
    if quality >= settings.elite_market_quality and reliability >= settings.elite_model_reliability:
        multiplier *= settings.elite_gate_multiplier
    if odds >= settings.longshot_odds_2:
        multiplier *= settings.longshot_gate_multiplier_2
    elif odds >= settings.longshot_odds_1:
        multiplier *= settings.longshot_gate_multiplier_1
    min_books = max(3, settings.min_bookmakers - 1) if quality >= settings.elite_market_quality else settings.min_bookmakers
    return min_books, settings.min_edge * multiplier, settings.min_ev * multiplier, multiplier


def build_signals(db: Session, rows: list[dict]) -> list[Signal]:
    grouped = defaultdict(list)
    for r in rows:
        grouped[(r['event_id'], r['market'], r.get('point'))].append(r)

    signals = []
    now = datetime.now(timezone.utc)
    for (event_id, market, point), group in grouped.items():
        con = consensus_market(group)
        meta = group[0]
        for outcome, consensus_prob in con['fair'].items():
            best = con['best'].get(outcome)
            if not best:
                continue
            fair_prob = float(con.get('reference_fair', {}).get(outcome, consensus_prob) or consensus_prob)
            implied_best = 1.0 / best['odds']
            market_edge = fair_prob - implied_best
            market_ev = expected_value(fair_prob, best['odds'])
            model_event = {**meta, 'market': market}
            model_prob, model_meta = external_probability(db, model_event, outcome, fair_prob)
            edge = model_prob - implied_best
            ev = expected_value(model_prob, best['odds'])
            disp = con['dispersion'].get(outcome, 0.0)
            reliability = float(model_meta.get('reliability') or 0.0)
            quality = market_quality(con['books'], con['vig'], disp, con.get('outlier_books', 0), con.get('raw_books', con['books']))
            agreement = float(con.get('agreement', {}).get(outcome, 0.0))
            conf = confidence_score(con['books'], edge, disp, con['vig'], reliability, quality, agreement)
            adj_ev = uncertainty_adjusted_ev(model_prob, best['odds'], conf, disp, reliability)
            line = _line_features(db, group, outcome, best['odds'])
            min_books, min_edge, min_ev, gate_mult = _dynamic_gates(con, model_meta, best['odds'], quality)

            reasons = []
            if con['books'] < min_books: reasons.append('too_few_books')
            if edge < min_edge: reasons.append('edge_below_threshold')
            if ev < min_ev: reasons.append('ev_below_threshold')
            if con['vig'] > settings.max_vig: reasons.append('market_vig_too_high')
            if quality < settings.min_market_quality: reasons.append('market_quality_too_low')
            if conf < settings.min_confidence: reasons.append('confidence_too_low')
            if not (settings.min_core_odds <= float(best['odds']) <= settings.max_core_odds): reasons.append('odds_outside_core_range')
            commence = meta['commence_time'] if meta['commence_time'].tzinfo else meta['commence_time'].replace(tzinfo=timezone.utc)
            if commence <= now: reasons.append('already_started')
            source = model_meta.get('source')
            if source == 'market_only': reasons.append('independent_model_unavailable')
            elif reliability < settings.min_model_reliability_for_paper: reasons.append('model_not_mature_enough')
            if model_meta.get('market_disagreement'): reasons.append('model_market_disagreement')
            if ev > settings.max_paper_ev: reasons.append('ev_too_extreme')
            if adj_ev <= 0: reasons.append('adjusted_ev_not_positive')

            price_premium = float(con.get('price_premium', {}).get(outcome, 0.0) or 0.0)
            opportunity_score = max(0.0, min(1.0,
                0.28 * quality +
                0.23 * conf +
                0.19 * max(0.0, min(1.0, adj_ev / 0.06)) +
                0.12 * max(0.0, min(1.0, market_ev / 0.04)) +
                0.10 * line['line_support'] +
                0.08 * max(0.0, min(1.0, price_premium / 0.04))
            ))

            s = Signal(
                event_id=event_id, sport_key=meta['sport_key'], market=market, outcome=outcome,
                best_bookmaker=best['bookmaker'], best_odds=best['odds'], fair_prob=fair_prob,
                model_prob=model_prob, edge=edge, ev=ev, confidence=conf, books=con['books'],
                market_vig=con['vig'], dispersion=disp, accepted=not reasons,
                reject_reason=','.join(reasons) if reasons else None,
                meta={
                    'home_team': meta['home_team'], 'away_team': meta['away_team'],
                    'commence_time': commence.isoformat(), 'point': point,
                    'model': model_meta,
                    'consensus_prob': consensus_prob,
                    'reference_fair_prob': fair_prob,
                    'reference_books': int(con.get('reference_books', {}).get(outcome, con['books']) or 0),
                    'raw_books': con.get('raw_books', con['books']),
                    'outlier_books': con.get('outlier_books', 0),
                    'market_quality': quality,
                    'market_agreement': agreement,
                    'adjusted_ev': adj_ev,
                    'market_edge': market_edge,
                    'market_ev': market_ev,
                    'consensus_odds': con.get('consensus_odds', {}).get(outcome),
                    'price_premium': price_premium,
                    'line': line,
                    'opportunity_score': opportunity_score,
                    'effective_gates': {'books': min_books, 'edge': min_edge, 'ev': min_ev, 'multiplier': gate_mult},
                },
            )
            db.add(s)
            signals.append(s)
    db.commit()
    return signals


def _already_bet(db: Session, s: Signal):
    if settings.one_pick_per_event_market:
        return db.scalar(select(PaperBet.id).where(PaperBet.event_id == s.event_id, PaperBet.market == s.market))
    return db.scalar(select(PaperBet.id).where(PaperBet.event_id == s.event_id, PaperBet.market == s.market, PaperBet.outcome == s.outcome))


def _selection_score(s: Signal) -> float:
    meta = s.meta or {}
    return float(meta.get('opportunity_score') or 0.0)


def _scout_score(s: Signal) -> float:
    meta = s.meta or {}
    q = float(meta.get('market_quality') or 0.0)
    me = max(0.0, float(meta.get('market_edge') or 0.0))
    mev = max(0.0, float(meta.get('market_ev') or 0.0))
    premium = max(0.0, float(meta.get('price_premium') or 0.0))
    line_support = float((meta.get('line') or {}).get('line_support') or 0.5)
    breadth = min(1.0, float(s.books or 0) / 8.0)
    return max(0.0, min(1.0, 0.32*q + 0.18*breadth + 0.18*min(1.0,me/0.02) + 0.15*min(1.0,mev/0.04) + 0.10*line_support + 0.07*min(1.0,premium/0.04)))


def _write_bet(db: Session, s: Signal, stake: float, tier: str) -> bool:
    meta = s.meta or {}
    bet = PaperBet(
        event_id=s.event_id, sport_key=s.sport_key, market=s.market, outcome=s.outcome,
        bookmaker=s.best_bookmaker, odds=s.best_odds, stake=round(stake, 2),
        model_prob=s.model_prob, fair_prob=s.fair_prob, edge=s.edge, ev=s.ev,
        commence_time=datetime.fromisoformat(meta['commence_time']),
    )
    db.add(bet)
    try:
        db.flush()
        score = _scout_score(s) if tier == 'SCOUT' else _selection_score(s)
        audit_ev = float(meta.get('market_ev') or 0.0) if tier == 'SCOUT' else float(meta.get('adjusted_ev') if meta.get('adjusted_ev') is not None else s.ev)
        db.add(BetAudit(
            paper_bet_id=bet.id, tier=tier, selection_score=score,
            adjusted_ev=audit_ev, market_quality=float(meta.get('market_quality') or 0.0),
            confidence=float(s.confidence or 0.0), reject_snapshot=s.reject_reason,
        ))
        db.commit()
        return True
    except Exception:
        db.rollback()
        return False


def _exploration_eligible(s: Signal) -> bool:
    if s.accepted or s.market != 'h2h':
        return False
    meta = s.meta or {}
    model = meta.get('model') or {}
    if model.get('source') == 'market_only':
        return False
    quality = float(meta.get('market_quality') or 0.0)
    adjusted = float(meta.get('adjusted_ev') if meta.get('adjusted_ev') is not None else s.ev)
    if s.books < settings.exploration_min_bookmakers: return False
    if s.edge < settings.exploration_min_edge or s.ev < settings.exploration_min_ev: return False
    if quality < settings.exploration_min_market_quality or s.confidence < settings.exploration_min_confidence: return False
    if adjusted <= 0: return False
    reasons = set((s.reject_reason or '').split(',')) - {''}
    allowed = {
        'too_few_books', 'edge_below_threshold', 'ev_below_threshold',
        'confidence_too_low', 'model_not_mature_enough', 'adjusted_ev_not_positive',
    }
    return not (reasons - allowed)


def _scout_eligible(s: Signal) -> bool:
    if s.accepted or s.market != 'h2h':
        return False
    meta = s.meta or {}
    commence = datetime.fromisoformat(meta['commence_time'])
    if commence <= datetime.now(timezone.utc): return False
    quality = float(meta.get('market_quality') or 0.0)
    market_edge = float(meta.get('market_edge') or 0.0)
    market_ev = float(meta.get('market_ev') or 0.0)
    if s.books < settings.scout_min_bookmakers: return False
    if int(meta.get('reference_books') or 0) < 2: return False
    if quality < settings.scout_min_market_quality: return False
    if market_edge < settings.scout_min_market_edge or market_ev < settings.scout_min_market_ev: return False
    if s.market_vig > settings.max_vig: return False
    if not (settings.scout_min_odds <= float(s.best_odds) <= settings.scout_max_odds): return False
    return True


def _remaining_caps(db: Session, s: Signal, daily_cap: float, event_cap: float):
    league_cap = settings.bankroll * settings.max_league_daily_exposure_pct
    return (
        max(0.0, daily_cap - _today_exposure(db)),
        max(0.0, event_cap - _event_exposure(db, s.event_id)),
        max(0.0, league_cap - _today_league_exposure(db, s.sport_key)),
    )


def place_paper_bets(db: Session, signals: list[Signal]) -> int:
    count = 0
    health_mult = risk_multiplier(db)
    daily_cap = settings.bankroll * settings.max_daily_exposure_pct
    event_cap = settings.bankroll * settings.max_event_exposure_pct

    core = sorted((x for x in signals if x.accepted), key=lambda x: (_selection_score(x), x.confidence, x.edge), reverse=True)
    if settings.one_pick_per_event_market:
        seen = set(); filtered = []
        for s in core:
            key = (s.event_id, s.market)
            if key not in seen:
                seen.add(key); filtered.append(s)
        core = filtered

    for s in core:
        if _already_bet(db, s): continue
        remaining_day, remaining_event, remaining_league = _remaining_caps(db, s, daily_cap, event_cap)
        if min(remaining_day, remaining_event, remaining_league) <= 0: continue
        reliability = float(((s.meta or {}).get('model') or {}).get('reliability') or 0.0)
        base = fractional_kelly_stake(
            settings.bankroll, s.model_prob, s.best_odds, s.confidence,
            settings.kelly_fraction, settings.max_stake_pct, reliability,
        )
        stake = min(base * health_mult, remaining_event, remaining_day, remaining_league)
        if stake >= 1.0 and _write_bet(db, s, stake, 'CORE'):
            count += 1

    if settings.paper_only and settings.exploration_enabled:
        slots = max(0, int(settings.exploration_max_bets_per_scan))
        exploration_daily_cap = settings.bankroll * settings.exploration_max_daily_exposure_pct
        candidates = sorted((s for s in signals if _exploration_eligible(s)), key=lambda x: (_selection_score(x), x.confidence), reverse=True)
        used_events = set()
        for s in candidates:
            if slots <= 0: break
            key = (s.event_id, s.market)
            if key in used_events or _already_bet(db, s): continue
            remaining_day, remaining_event, remaining_league = _remaining_caps(db, s, daily_cap, event_cap)
            remaining_explore = max(0.0, exploration_daily_cap - _today_tier_exposure(db, 'EXPLORATION'))
            stake = min(settings.bankroll * settings.exploration_max_stake_pct * health_mult, remaining_event, remaining_day, remaining_league, remaining_explore)
            if stake >= 1.0 and _write_bet(db, s, stake, 'EXPLORATION'):
                count += 1; slots -= 1; used_events.add(key)

    if settings.paper_only and settings.scout_enabled:
        slots = max(0, int(settings.scout_max_bets_per_scan))
        scout_daily_cap = settings.bankroll * settings.scout_max_daily_exposure_pct
        candidates = sorted((s for s in signals if _scout_eligible(s)), key=_scout_score, reverse=True)
        used_events = set()
        for s in candidates:
            if slots <= 0: break
            key = (s.event_id, s.market)
            if key in used_events or _already_bet(db, s): continue
            remaining_day, remaining_event, remaining_league = _remaining_caps(db, s, daily_cap, event_cap)
            remaining_scout = max(0.0, scout_daily_cap - _today_tier_exposure(db, 'SCOUT'))
            stake = min(settings.bankroll * settings.scout_max_stake_pct, remaining_event, remaining_day, remaining_league, remaining_scout)
            if stake >= 1.0 and _write_bet(db, s, stake, 'SCOUT'):
                count += 1; slots -= 1; used_events.add(key)
    return count


def track_shadow_picks(db: Session, signals: list[Signal]) -> int:
    count = 0
    for s in sorted(signals, key=lambda x: (_selection_score(x), x.ev), reverse=True):
        if s.accepted or s.market != 'h2h': continue
        if s.reject_reason and 'already_started' in s.reject_reason: continue
        if s.books < settings.shadow_min_bookmakers or s.edge < settings.shadow_min_edge or s.ev < settings.shadow_min_ev: continue
        if db.scalar(select(ShadowPick.id).where(ShadowPick.event_id == s.event_id, ShadowPick.market == s.market, ShadowPick.outcome == s.outcome)): continue
        mm = (s.meta or {}).get('model') or {}
        db.add(ShadowPick(
            event_id=s.event_id, sport_key=s.sport_key, market=s.market, outcome=s.outcome,
            bookmaker=s.best_bookmaker, odds=s.best_odds, fair_prob=s.fair_prob,
            model_prob=s.model_prob, edge=s.edge, ev=s.ev, books=s.books,
            model_reliability=float(mm.get('reliability') or 0.0),
            commence_time=datetime.fromisoformat(s.meta['commence_time']),
        ))
        count += 1
    if count: db.commit()
    return count


def update_closing_lines(db: Session, rows: list[dict]) -> int:
    """Use robust consensus fair odds as the CLV benchmark, not one noisy book."""
    now = datetime.now(timezone.utc)
    grouped = defaultdict(list)
    for r in rows:
        commence = r['commence_time'] if r['commence_time'].tzinfo else r['commence_time'].replace(tzinfo=timezone.utc)
        if commence > now:
            grouped[(r['event_id'], r['market'], r.get('point'))].append(r)
    closing = {}
    for (event_id, market, _), group in grouped.items():
        con = consensus_market(group)
        for outcome, prob in con.get('fair', {}).items():
            if prob > 0:
                closing[(event_id, market, outcome)] = 1.0 / prob
    updated = 0
    objects = list(db.scalars(select(PaperBet).where(PaperBet.status == 'OPEN')).all()) + list(db.scalars(select(ShadowPick).where(ShadowPick.status == 'OPEN')).all())
    for obj in objects:
        price = closing.get((obj.event_id, obj.market, obj.outcome))
        if price and price > 1.0:
            obj.closing_odds = price
            obj.clv = (obj.odds / price) - 1.0
            updated += 1
    if updated: db.commit()
    return updated


def settle_h2h_from_scores(db: Session, scores: list[dict]) -> int:
    settled = 0
    for event in scores:
        if not event.get('completed') or not event.get('scores'): continue
        scoremap = {x['name']: float(x['score']) for x in event['scores']}
        home, away = event.get('home_team'), event.get('away_team')
        hs, as_ = scoremap.get(home), scoremap.get(away)
        if hs is None or as_ is None: continue
        winner = home if hs > as_ else away if as_ > hs else 'Draw'
        for b in db.scalars(select(PaperBet).where(PaperBet.event_id == event['id'], PaperBet.status == 'OPEN', PaperBet.market == 'h2h')).all():
            won = b.outcome == winner
            b.pnl = round(b.stake * (b.odds - 1.0), 2) if won else -b.stake
            b.status = 'SETTLED'; b.result = 'WIN' if won else 'LOSS'; settled += 1
        for s in db.scalars(select(ShadowPick).where(ShadowPick.event_id == event['id'], ShadowPick.status == 'OPEN', ShadowPick.market == 'h2h')).all():
            won = s.outcome == winner
            s.pnl_units = (s.odds - 1.0) if won else -1.0
            s.status = 'SETTLED'; s.result = 'WIN' if won else 'LOSS'
    db.commit()
    return settled
