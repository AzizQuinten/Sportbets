from collections import defaultdict
from datetime import datetime, timezone
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.models.db_models import OddsSnapshot, PaperBet, ShadowPick, Signal, BetAudit
from app.services.model import external_probability
from app.services.quant import consensus_market, expected_value, confidence_score, market_quality, uncertainty_adjusted_ev, fractional_kelly_stake
from app.services.validation import risk_multiplier

settings = get_settings()


def ingest_rows(db: Session, rows: list[dict]) -> int:
    db.add_all([OddsSnapshot(**r) for r in rows])
    db.commit()
    return len(rows)


def _today_start():
    now = datetime.now(timezone.utc)
    return datetime(now.year, now.month, now.day, tzinfo=timezone.utc)


def _today_exposure(db: Session) -> float:
    return float(db.scalar(select(func.coalesce(func.sum(PaperBet.stake), 0.0)).where(PaperBet.placed_at >= _today_start())) or 0.0)


def _today_tier_exposure(db: Session, tier: str) -> float:
    q = select(func.coalesce(func.sum(PaperBet.stake), 0.0)).join(BetAudit, BetAudit.paper_bet_id == PaperBet.id).where(PaperBet.placed_at >= _today_start(), BetAudit.tier == tier)
    return float(db.scalar(q) or 0.0)


def _event_exposure(db: Session, event_id: str) -> float:
    return float(db.scalar(select(func.coalesce(func.sum(PaperBet.stake), 0.0)).where(PaperBet.event_id == event_id)) or 0.0)


def _dynamic_gates(con, model_meta):
    reliability = float(model_meta.get('reliability') or 0.0)
    q = market_quality(con['books'], con['vig'], 0.0, con.get('outlier_books', 0), con.get('raw_books', con['books']))
    min_books = max(3, settings.min_bookmakers - 1) if q >= 0.78 else settings.min_bookmakers
    mature = reliability >= 0.90 and q >= 0.75
    min_edge = settings.min_edge * (0.80 if mature else 1.0)
    min_ev = settings.min_ev * (0.80 if mature else 1.0)
    return min_books, min_edge, min_ev


def build_signals(db: Session, rows: list[dict]) -> list[Signal]:
    grouped = defaultdict(list)
    for r in rows:
        grouped[(r['event_id'], r['market'], r.get('point'))].append(r)
    signals = []
    for (event_id, market, point), group in grouped.items():
        con = consensus_market(group)
        meta = group[0]
        for outcome, fair_prob in con['fair'].items():
            best = con['best'].get(outcome)
            if not best:
                continue
            model_prob, model_meta = external_probability(db, meta, outcome, fair_prob)
            edge = model_prob - (1.0 / best['odds'])
            ev = expected_value(model_prob, best['odds'])
            disp = con['dispersion'].get(outcome, 0.0)
            reliability = float(model_meta.get('reliability') or 0.0)
            quality = market_quality(con['books'], con['vig'], disp, con.get('outlier_books', 0), con.get('raw_books', con['books']))
            conf = confidence_score(con['books'], edge, disp, con['vig'], reliability, quality)
            adj_ev = uncertainty_adjusted_ev(model_prob, best['odds'], conf, disp)
            min_books, min_edge, min_ev = _dynamic_gates(con, model_meta)
            reasons = []
            if con['books'] < min_books: reasons.append('too_few_books')
            if edge < min_edge: reasons.append('edge_below_threshold')
            if ev < min_ev: reasons.append('ev_below_threshold')
            if con['vig'] > settings.max_vig: reasons.append('market_vig_too_high')
            if quality < 0.45: reasons.append('market_quality_too_low')
            if conf < 0.50: reasons.append('confidence_too_low')
            if meta['commence_time'] <= datetime.now(timezone.utc): reasons.append('already_started')
            source = model_meta.get('source')
            if source != 'market_only' and reliability < settings.min_model_reliability_for_paper: reasons.append('model_not_mature_enough')
            if model_meta.get('market_disagreement'): reasons.append('model_market_disagreement')
            if ev > settings.max_paper_ev: reasons.append('ev_too_extreme')
            s = Signal(
                event_id=event_id, sport_key=meta['sport_key'], market=market, outcome=outcome,
                best_bookmaker=best['bookmaker'], best_odds=best['odds'], fair_prob=fair_prob,
                model_prob=model_prob, edge=edge, ev=ev, confidence=conf, books=con['books'],
                market_vig=con['vig'], dispersion=disp, accepted=not reasons,
                reject_reason=','.join(reasons) if reasons else None,
                meta={
                    'home_team': meta['home_team'], 'away_team': meta['away_team'],
                    'commence_time': meta['commence_time'].isoformat(), 'point': point,
                    'model': model_meta, 'raw_books': con.get('raw_books', con['books']),
                    'outlier_books': con.get('outlier_books', 0), 'market_quality': quality,
                    'adjusted_ev': adj_ev,
                    'effective_gates': {'books': min_books, 'edge': min_edge, 'ev': min_ev},
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
    q = float(meta.get('market_quality') or 0.0)
    ae = float(meta.get('adjusted_ev') if meta.get('adjusted_ev') is not None else s.ev)
    return 0.45 * q + 0.30 * float(s.confidence or 0.0) + 0.25 * max(0.0, min(1.0, ae / 0.08))


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
        db.add(BetAudit(
            paper_bet_id=bet.id, tier=tier, selection_score=_selection_score(s),
            adjusted_ev=float(meta.get('adjusted_ev') if meta.get('adjusted_ev') is not None else s.ev),
            market_quality=float(meta.get('market_quality') or 0.0),
            confidence=float(s.confidence or 0.0),
            reject_snapshot=s.reject_reason,
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
    quality = float(meta.get('market_quality') or 0.0)
    adjusted = float(meta.get('adjusted_ev') if meta.get('adjusted_ev') is not None else s.ev)
    if s.books < settings.exploration_min_bookmakers:
        return False
    if s.edge < settings.exploration_min_edge or s.ev < settings.exploration_min_ev:
        return False
    if quality < settings.exploration_min_market_quality or s.confidence < settings.exploration_min_confidence:
        return False
    if adjusted <= 0:
        return False
    reasons = set((s.reject_reason or '').split(',')) - {''}
    # Exploration may relax only the core breadth/edge/EV gates. It never
    # bypasses timing, bad vig, weak market quality, low confidence, model
    # disagreement/maturity or extreme-EV guardrails.
    allowed = {'too_few_books', 'edge_below_threshold', 'ev_below_threshold'}
    return not (reasons - allowed)


def place_paper_bets(db: Session, signals: list[Signal]) -> int:
    count = 0
    health_mult = risk_multiplier(db)
    daily_cap = settings.bankroll * settings.max_daily_exposure_pct
    event_cap = settings.bankroll * settings.max_event_exposure_pct

    def rank(x):
        return (_selection_score(x), float((x.meta or {}).get('adjusted_ev', x.ev)), x.confidence, x.edge)

    core = sorted((x for x in signals if x.accepted), key=rank, reverse=True)
    if settings.one_pick_per_event_market:
        seen = set(); filtered = []
        for s in core:
            key = (s.event_id, s.market)
            if key not in seen:
                seen.add(key); filtered.append(s)
        core = filtered

    for s in core:
        if _already_bet(db, s):
            continue
        day = _today_exposure(db)
        if day >= daily_cap:
            break
        remaining_event = max(0.0, event_cap - _event_exposure(db, s.event_id))
        remaining_day = max(0.0, daily_cap - day)
        base = fractional_kelly_stake(settings.bankroll, s.model_prob, s.best_odds, s.confidence, settings.kelly_fraction, settings.max_stake_pct)
        stake = min(base * health_mult, remaining_event, remaining_day)
        if stake >= 1.0 and _write_bet(db, s, stake, 'CORE'):
            count += 1

    # Controlled research sampling: produces real paper-ledger observations while
    # keeping the strict CORE gate untouched. A scan can have 0 core bets and
    # still gather a few high-quality near-miss observations for validation.
    if settings.paper_only and settings.exploration_enabled:
        slots = max(0, int(settings.exploration_max_bets_per_scan) - count)
        exploration_daily_cap = settings.bankroll * settings.exploration_max_daily_exposure_pct
        candidates = sorted((s for s in signals if _exploration_eligible(s)), key=rank, reverse=True)
        used_events = set()
        for s in candidates:
            if slots <= 0:
                break
            key = (s.event_id, s.market)
            if key in used_events or _already_bet(db, s):
                continue
            if _today_exposure(db) >= daily_cap or _today_tier_exposure(db, 'EXPLORATION') >= exploration_daily_cap:
                break
            remaining_event = max(0.0, event_cap - _event_exposure(db, s.event_id))
            remaining_day = max(0.0, daily_cap - _today_exposure(db))
            remaining_explore = max(0.0, exploration_daily_cap - _today_tier_exposure(db, 'EXPLORATION'))
            # Fixed tiny cap for research samples; confidence affects ranking, not
            # an exaggerated Kelly stake on a signal that did not clear CORE.
            stake = min(settings.bankroll * settings.exploration_max_stake_pct * health_mult, remaining_event, remaining_day, remaining_explore)
            if stake < 1.0:
                continue
            if _write_bet(db, s, stake, 'EXPLORATION'):
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
        db.add(ShadowPick(event_id=s.event_id, sport_key=s.sport_key, market=s.market, outcome=s.outcome, bookmaker=s.best_bookmaker, odds=s.best_odds, fair_prob=s.fair_prob, model_prob=s.model_prob, edge=s.edge, ev=s.ev, books=s.books, model_reliability=float(mm.get('reliability') or 0.0), commence_time=datetime.fromisoformat(s.meta['commence_time'])))
        count += 1
    if count: db.commit()
    return count


def update_closing_lines(db: Session, rows: list[dict]) -> int:
    best = {}; now = datetime.now(timezone.utc)
    for r in rows:
        if r['commence_time'] <= now: continue
        key = (r['event_id'], r['market'], r['outcome'])
        if key not in best or r['price'] > best[key]: best[key] = r['price']
    updated = 0
    objects = list(db.scalars(select(PaperBet).where(PaperBet.status == 'OPEN')).all()) + list(db.scalars(select(ShadowPick).where(ShadowPick.status == 'OPEN')).all())
    for obj in objects:
        price = best.get((obj.event_id, obj.market, obj.outcome))
        if price:
            obj.closing_odds = price; obj.clv = (obj.odds / price) - 1.0; updated += 1
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
