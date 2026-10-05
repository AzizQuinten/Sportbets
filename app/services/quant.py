from collections import defaultdict
from statistics import median, pstdev


def implied_prob(decimal_odds: float) -> float:
    return 1.0 / decimal_odds


def remove_vig(prices_by_outcome: dict[str, float]) -> tuple[dict[str, float], float]:
    raw = {k: implied_prob(v) for k, v in prices_by_outcome.items() if v and v > 1.0}
    s = sum(raw.values())
    if s <= 0:
        return {}, 0.0
    return {k: p / s for k, p in raw.items()}, max(0.0, s - 1.0)


def _mad(values: list[float]) -> float:
    if not values:
        return 0.0
    m = median(values)
    return median([abs(x - m) for x in values])


def _normalise(probs: dict[str, float]) -> dict[str, float]:
    total = sum(max(0.0, float(x)) for x in probs.values())
    if total <= 0:
        return {}
    return {k: max(0.0, float(v)) / total for k, v in probs.items()}


def consensus_market(rows: list[dict]) -> dict:
    """Build a robust no-vig market consensus.

    V3 separates two probability anchors:
    - fair: robust median of all retained complete bookmaker books.
    - reference_fair: for each best price, rebuild the consensus excluding that
      bookmaker when enough independent books remain. This prevents a bookmaker
      from helping create the probability used to declare its own price +EV.
    """
    by_book = defaultdict(dict)
    for r in rows:
        try:
            price = float(r.get('price') or 0.0)
        except (TypeError, ValueError):
            continue
        if price > 1.0 and r.get('bookmaker') and r.get('outcome'):
            by_book[r['bookmaker']][r['outcome']] = price

    # Complete books only: partial 1X2 books distort no-vig normalization.
    max_outcomes = max((len(v) for v in by_book.values()), default=0)
    book_fair = {}
    book_vig = {}
    preliminary = defaultdict(list)
    for book, prices in by_book.items():
        if max_outcomes >= 3 and len(prices) < 3:
            continue
        fair, vig = remove_vig(prices)
        if len(fair) >= 2 and -0.02 <= vig <= 0.25:
            book_fair[book] = fair
            book_vig[book] = vig
            for outcome, p in fair.items():
                preliminary[outcome].append(p)

    med = {o: median(ps) for o, ps in preliminary.items() if ps}
    deviations = {}
    for book, fair in book_fair.items():
        ds = [abs(p - med[o]) for o, p in fair.items() if o in med]
        deviations[book] = max(ds) if ds else 0.0
    vals = list(deviations.values())
    md = median(vals) if vals else 0.0
    mad = _mad(vals)
    cutoff = max(0.05, md + 4.0 * max(mad, 0.006))
    kept = {b for b, d in deviations.items() if d <= cutoff}
    if len(kept) < 2:
        kept = set(book_fair)

    fair_by_outcome = defaultdict(list)
    vigs = []
    for b in kept:
        vigs.append(book_vig[b])
        for o, p in book_fair[b].items():
            fair_by_outcome[o].append(p)
    fair = _normalise({o: median(ps) for o, ps in fair_by_outcome.items() if ps})

    best = {}
    price_lists = defaultdict(list)
    for r in rows:
        if r.get('bookmaker') not in kept:
            continue
        try:
            price = float(r.get('price') or 0.0)
        except (TypeError, ValueError):
            continue
        if price <= 1.0:
            continue
        o = r['outcome']
        price_lists[o].append(price)
        if o not in best or price > best[o]['odds']:
            best[o] = {'odds': price, 'bookmaker': r['bookmaker']}

    # Leave-one-out reference consensus for the book offering the best price.
    reference_fair = {}
    reference_books = {}
    for outcome, best_row in best.items():
        excluded = best_row['bookmaker']
        candidates = defaultdict(list)
        ref_book_count = 0
        for book in kept:
            if book == excluded:
                continue
            bf = book_fair.get(book) or {}
            if bf:
                ref_book_count += 1
            for o, p in bf.items():
                candidates[o].append(p)
        if ref_book_count >= 2:
            ref = _normalise({o: median(ps) for o, ps in candidates.items() if ps})
            reference_fair[outcome] = ref.get(outcome, fair.get(outcome, 0.0))
            reference_books[outcome] = ref_book_count
        else:
            reference_fair[outcome] = fair.get(outcome, 0.0)
            reference_books[outcome] = len(kept)

    dispersion = {
        o: (pstdev([1.0 / x for x in xs]) if len(xs) > 1 else 0.0)
        for o, xs in price_lists.items()
    }
    agreement = {
        o: max(0.0, 1.0 - min(1.0, dispersion.get(o, 0.0) / 0.055))
        for o in fair
    }
    consensus_odds = {o: (1.0 / p if p > 0 else None) for o, p in fair.items()}
    price_premium = {}
    for o, b in best.items():
        co = consensus_odds.get(o)
        price_premium[o] = ((b['odds'] / co) - 1.0) if co else 0.0

    return {
        'fair': fair,
        'reference_fair': reference_fair,
        'reference_books': reference_books,
        'best': best,
        'books': len(kept),
        'raw_books': len(book_fair),
        'outlier_books': max(0, len(book_fair) - len(kept)),
        'vig': median(vigs) if vigs else 0.0,
        'dispersion': dispersion,
        'agreement': agreement,
        'consensus_odds': consensus_odds,
        'price_premium': price_premium,
    }


def expected_value(prob: float, decimal_odds: float) -> float:
    return prob * decimal_odds - 1.0


def kelly_fraction(prob: float, decimal_odds: float) -> float:
    b = decimal_odds - 1.0
    if b <= 0:
        return 0.0
    return max(0.0, (b * prob - (1.0 - prob)) / b)


def market_quality(books: int, vig: float, dispersion: float, outlier_books: int = 0, raw_books: int = 0) -> float:
    breadth = min(1.0, books / 8.0)
    vig_score = max(0.0, 1.0 - max(0.0, vig) / 0.12)
    agreement = max(0.0, 1.0 - dispersion / 0.06)
    outlier_score = 1.0 - (outlier_books / max(1, raw_books))
    return max(0.0, min(1.0, 0.35 * breadth + 0.25 * vig_score + 0.30 * agreement + 0.10 * outlier_score))


def confidence_score(
    books: int,
    edge: float,
    dispersion: float,
    vig: float,
    reliability: float = 0.0,
    quality: float | None = None,
    market_agreement: float | None = None,
) -> float:
    q = market_quality(books, vig, dispersion) if quality is None else quality
    edge_score = min(1.0, max(0.0, edge) / 0.08)
    model_score = 0.45 + 0.55 * max(0.0, min(1.0, reliability))
    agree = max(0.0, min(1.0, market_agreement if market_agreement is not None else (1.0 - min(1.0, dispersion / 0.055))))
    return max(0.0, min(1.0, 0.36 * q + 0.22 * edge_score + 0.25 * model_score + 0.17 * agree))


def uncertainty_adjusted_ev(prob: float, odds: float, confidence: float, dispersion: float, reliability: float = 0.0) -> float:
    """Conservative EV for ranking/staking, not a promise of realised edge."""
    raw = expected_value(prob, odds)
    evidence = 0.38 + 0.42 * max(0.0, min(1.0, confidence)) + 0.20 * max(0.0, min(1.0, reliability))
    noise = min(0.05, dispersion * 0.85)
    return raw * evidence - noise


def fractional_kelly_stake(bankroll: float, prob: float, odds: float, confidence: float, fraction: float, max_pct: float, reliability: float = 1.0) -> float:
    k = kelly_fraction(prob, odds)
    confidence_scale = max(0.0, min(1.0, confidence)) ** 2
    reliability_scale = 0.50 + 0.50 * max(0.0, min(1.0, reliability))
    return bankroll * min(max_pct, k * fraction * confidence_scale * reliability_scale)
