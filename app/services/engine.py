from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.db_models import OddsSnapshot, PaperBet, ShadowPick, Signal
from app.services.model import external_probability
from app.services.quant import consensus_market, expected_value, kelly_fraction, confidence_score

settings = get_settings()


def ingest_rows(db: Session, rows: list[dict]) -> int:
    db.add_all([OddsSnapshot(**r) for r in rows])
    db.commit()
    return len(rows)


def _today_exposure(db: Session) -> float:
    now = datetime.now(timezone.utc)
    start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    return float(db.scalar(select(func.coalesce(func.sum(PaperBet.stake), 0.0)).where(PaperBet.placed_at >= start)) or 0.0)


def _event_exposure(db: Session, event_id: str) -> float:
    return float(db.scalar(select(func.coalesce(func.sum(PaperBet.stake), 0.0)).where(PaperBet.event_id == event_id)) or 0.0)


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
            conf = confidence_score(con['books'], edge, disp, con['vig'])

            reasons = []
            if con['books'] < settings.min_bookmakers:
                reasons.append('too_few_books')
            if edge < settings.min_edge:
                reasons.append('edge_below_threshold')
            if ev < settings.min_ev:
                reasons.append('ev_below_threshold')
            if con['vig'] > settings.max_vig:
                reasons.append('market_vig_too_high')
            if meta['commence_time'] <= datetime.now(timezone.utc):
                reasons.append('already_started')

            source = model_meta.get('source')
            reliability = float(model_meta.get('reliability') or 0.0)
            if source != 'market_only' and reliability < settings.min_model_reliability_for_paper:
                reasons.append('model_not_mature_enough')
            if model_meta.get('market_disagreement'):
                reasons.append('model_market_disagreement')
            if ev > settings.max_paper_ev:
                reasons.append('ev_too_extreme')

            s = Signal(
                event_id=event_id,
                sport_key=meta['sport_key'],
                market=market,
                outcome=outcome,
                best_bookmaker=best['bookmaker'],
                best_odds=best['odds'],
                fair_prob=fair_prob,
                model_prob=model_prob,
                edge=edge,
                ev=ev,
                confidence=conf,
                books=con['books'],
                market_vig=con['vig'],
                dispersion=disp,
                accepted=(len(reasons) == 0),
                reject_reason=','.join(reasons) if reasons else None,
                meta={
                    'home_team': meta['home_team'],
                    'away_team': meta['away_team'],
                    'commence_time': meta['commence_time'].isoformat(),
                    'point': point,
                    'model': model_meta,
                    'raw_books': con.get('raw_books', con['books']),
                    'outlier_books': con.get('outlier_books', 0),
                },
            )
            db.add(s)
            signals.append(s)
    db.commit()
    return signals


def place_paper_bets(db: Session, signals: list[Signal]) -> int:
    count = 0
    daily_cap = settings.bankroll * settings.max_daily_exposure_pct
    event_cap = settings.bankroll * settings.max_event_exposure_pct

    accepted = sorted((x for x in signals if x.accepted), key=lambda x: (x.ev, x.edge), reverse=True)
    if settings.one_pick_per_event_market:
        seen = set()
        filtered = []
        for s in accepted:
            key = (s.event_id, s.market)
            if key in seen:
                continue
            seen.add(key)
            filtered.append(s)
        accepted = filtered

    for s in accepted:
        exists = db.scalar(select(PaperBet).where(
            PaperBet.event_id == s.event_id,
            PaperBet.market == s.market,
        )) if settings.one_pick_per_event_market else db.scalar(select(PaperBet).where(
            PaperBet.event_id == s.event_id,
            PaperBet.market == s.market,
            PaperBet.outcome == s.outcome,
        ))
        if exists:
            continue
        if _today_exposure(db) >= daily_cap:
            break
        remaining_event = max(0.0, event_cap - _event_exposure(db, s.event_id))
        remaining_day = max(0.0, daily_cap - _today_exposure(db))
        raw_kelly = kelly_fraction(s.model_prob, s.best_odds) * settings.kelly_fraction
        stake = min(
            settings.bankroll * raw_kelly,
            settings.bankroll * settings.max_stake_pct,
            remaining_event,
            remaining_day,
        )
        if stake < 1.0:
            continue
        commence = datetime.fromisoformat(s.meta['commence_time'])
        bet = PaperBet(
            event_id=s.event_id,
            sport_key=s.sport_key,
            market=s.market,
            outcome=s.outcome,
            bookmaker=s.best_bookmaker,
            odds=s.best_odds,
            stake=round(stake, 2),
            model_prob=s.model_prob,
            fair_prob=s.fair_prob,
            edge=s.edge,
            ev=s.ev,
            commence_time=commence,
        )
        db.add(bet)
        try:
            db.commit()
            count += 1
        except Exception:
            db.rollback()
    return count


def track_shadow_picks(db: Session, signals: list[Signal]) -> int:
    count = 0
    for s in sorted(signals, key=lambda x: x.ev, reverse=True):
        if s.accepted or s.market != 'h2h':
            continue
        if s.reject_reason and 'already_started' in s.reject_reason:
            continue
        if s.books < settings.shadow_min_bookmakers or s.edge < settings.shadow_min_edge or s.ev < settings.shadow_min_ev:
            continue
        exists = db.scalar(select(ShadowPick.id).where(
            ShadowPick.event_id == s.event_id,
            ShadowPick.market == s.market,
            ShadowPick.outcome == s.outcome,
        ))
        if exists:
            continue
        model_meta = (s.meta or {}).get('model') or {}
        db.add(ShadowPick(
            event_id=s.event_id,
            sport_key=s.sport_key,
            market=s.market,
            outcome=s.outcome,
            bookmaker=s.best_bookmaker,
            odds=s.best_odds,
            fair_prob=s.fair_prob,
            model_prob=s.model_prob,
            edge=s.edge,
            ev=s.ev,
            books=s.books,
            model_reliability=float(model_meta.get('reliability') or 0.0),
            commence_time=datetime.fromisoformat(s.meta['commence_time']),
        ))
        count += 1
    if count:
        db.commit()
    return count


def update_closing_lines(db: Session, rows: list[dict]) -> int:
    best = {}
    now = datetime.now(timezone.utc)
    for r in rows:
        if r['commence_time'] <= now:
            continue
        key = (r['event_id'], r['market'], r['outcome'])
        if key not in best or r['price'] > best[key]:
            best[key] = r['price']

    updated = 0
    for bet in db.scalars(select(PaperBet).where(PaperBet.status == 'OPEN')).all():
        price = best.get((bet.event_id, bet.market, bet.outcome))
        if price:
            bet.closing_odds = price
            bet.clv = (bet.odds / price) - 1.0
            updated += 1
    for pick in db.scalars(select(ShadowPick).where(ShadowPick.status == 'OPEN')).all():
        price = best.get((pick.event_id, pick.market, pick.outcome))
        if price:
            pick.closing_odds = price
            pick.clv = (pick.odds / price) - 1.0
            updated += 1
    if updated:
        db.commit()
    return updated


def settle_h2h_from_scores(db: Session, scores: list[dict]) -> int:
    settled = 0
    for event in scores:
        if not event.get('completed') or not event.get('scores'):
            continue
        scoremap = {x['name']: float(x['score']) for x in event['scores']}
        if len(scoremap) < 2:
            continue
        home, away = event.get('home_team'), event.get('away_team')
        hs, as_ = scoremap.get(home), scoremap.get(away)
        if hs is None or as_ is None:
            continue
        winner = home if hs > as_ else away if as_ > hs else 'Draw'

        bets = db.scalars(select(PaperBet).where(
            PaperBet.event_id == event['id'],
            PaperBet.status == 'OPEN',
            PaperBet.market == 'h2h',
        )).all()
        for b in bets:
            won = b.outcome == winner
            b.pnl = round(b.stake * (b.odds - 1.0), 2) if won else -b.stake
            b.status = 'SETTLED'
            b.result = 'WIN' if won else 'LOSS'
            settled += 1

        shadows = db.scalars(select(ShadowPick).where(
            ShadowPick.event_id == event['id'],
            ShadowPick.status == 'OPEN',
            ShadowPick.market == 'h2h',
        )).all()
        for s in shadows:
            won = s.outcome == winner
            s.pnl_units = (s.odds - 1.0) if won else -1.0
            s.status = 'SETTLED'
            s.result = 'WIN' if won else 'LOSS'
    db.commit()
    return settled
