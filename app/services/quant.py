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


def consensus_market(rows: list[dict]) -> dict:
    """Robust no-vig consensus with whole-book outlier rejection.

    A stale or malformed bookmaker can otherwise create a fake best price and a
    fake edge. We first compute each book's no-vig probabilities, then reject only
    severe multivariate deviations. The cutoff is intentionally loose so genuine
    disagreement remains part of the signal.
    """
    by_book = defaultdict(dict)
    for r in rows:
        if r.get('price') and r['price'] > 1.0:
            by_book[r['bookmaker']][r['outcome']] = r['price']

    book_fair = {}
    book_vig = {}
    preliminary = defaultdict(list)
    for book, prices in by_book.items():
        fair, vig = remove_vig(prices)
        if len(fair) >= 2:
            book_fair[book] = fair
            book_vig[book] = vig
            for outcome, p in fair.items():
                preliminary[outcome].append(p)

    med = {o: median(ps) for o, ps in preliminary.items() if ps}
    deviations = {}
    for book, fair in book_fair.items():
        ds = [abs(p - med[o]) for o, p in fair.items() if o in med]
        deviations[book] = max(ds) if ds else 0.0

    dev_values = list(deviations.values())
    med_dev = median(dev_values) if dev_values else 0.0
    mad_dev = _mad(dev_values)
    cutoff = max(0.08, med_dev + 4.0 * max(mad_dev, 0.01))
    kept_books = {b for b, d in deviations.items() if d <= cutoff}
    if len(kept_books) < 2:
        kept_books = set(book_fair)

    fair_by_outcome = defaultdict(list)
    vigs = []
    for book in kept_books:
        fair = book_fair[book]
        vigs.append(book_vig[book])
        for outcome, p in fair.items():
            fair_by_outcome[outcome].append(p)

    fair = {o: median(ps) for o, ps in fair_by_outcome.items() if ps}
    total = sum(fair.values())
    if total:
        fair = {o: p / total for o, p in fair.items()}

    best = {}
    price_lists = defaultdict(list)
    for r in rows:
        if r['bookmaker'] not in kept_books or not r.get('price') or r['price'] <= 1.0:
            continue
        o = r['outcome']
        price_lists[o].append(r['price'])
        if o not in best or r['price'] > best[o]['odds']:
            best[o] = {'odds': r['price'], 'bookmaker': r['bookmaker']}

    dispersion = {
        o: (pstdev([1 / x for x in vals]) if len(vals) > 1 else 0.0)
        for o, vals in price_lists.items()
    }
    return {
        'fair': fair,
        'best': best,
        'books': len(kept_books),
        'raw_books': len(book_fair),
        'outlier_books': max(0, len(book_fair) - len(kept_books)),
        'vig': median(vigs) if vigs else 0.0,
        'dispersion': dispersion,
    }


def shrink_probability(market_prob: float, model_prob: float | None, model_weight: float = 0.30) -> float:
    if model_prob is None:
        return market_prob
    w = min(max(model_weight, 0.0), 0.65)
    return (1 - w) * market_prob + w * model_prob


def expected_value(prob: float, decimal_odds: float) -> float:
    return prob * decimal_odds - 1.0


def kelly_fraction(prob: float, decimal_odds: float) -> float:
    b = decimal_odds - 1.0
    if b <= 0:
        return 0.0
    q = 1.0 - prob
    return max(0.0, (b * prob - q) / b)


def confidence_score(books: int, edge: float, dispersion: float, vig: float) -> float:
    books_score = min(1.0, books / 10.0)
    edge_score = min(1.0, max(0.0, edge) / 0.10)
    dispersion_penalty = min(1.0, dispersion / 0.06)
    vig_penalty = min(1.0, vig / 0.12)
    score = 0.35 * books_score + 0.35 * edge_score + 0.15 * (1 - dispersion_penalty) + 0.15 * (1 - vig_penalty)
    return max(0.0, min(1.0, score))
