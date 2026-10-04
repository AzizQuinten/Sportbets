from collections import defaultdict
from statistics import median, pstdev
import math


def implied_prob(decimal_odds: float) -> float:
    return 1.0 / decimal_odds


def remove_vig(prices_by_outcome: dict[str, float]) -> tuple[dict[str, float], float]:
    raw = {k: implied_prob(v) for k, v in prices_by_outcome.items() if v and v > 1.0}
    s = sum(raw.values())
    if s <= 0:
        return {}, 0.0
    return {k: p / s for k, p in raw.items()}, max(0.0, s - 1.0)


def consensus_market(rows: list[dict]) -> dict:
    by_book = defaultdict(dict)
    for r in rows:
        by_book[r['bookmaker']][r['outcome']] = r['price']

    fair_by_outcome = defaultdict(list)
    vigs = []
    valid_books = 0
    for book, prices in by_book.items():
        fair, vig = remove_vig(prices)
        if len(fair) >= 2:
            valid_books += 1
            vigs.append(vig)
            for outcome, p in fair.items():
                fair_by_outcome[outcome].append(p)

    fair = {o: median(ps) for o, ps in fair_by_outcome.items() if ps}
    total = sum(fair.values())
    if total:
        fair = {o: p / total for o, p in fair.items()}

    best = {}
    price_lists = defaultdict(list)
    for r in rows:
        o = r['outcome']
        price_lists[o].append(r['price'])
        if o not in best or r['price'] > best[o]['odds']:
            best[o] = {'odds': r['price'], 'bookmaker': r['bookmaker']}

    dispersion = {
        o: (pstdev([1/x for x in vals]) if len(vals) > 1 else 0.0)
        for o, vals in price_lists.items()
    }
    return {
        'fair': fair,
        'best': best,
        'books': valid_books,
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
    score = 0.35 * books_score + 0.35 * edge_score + 0.15 * (1-dispersion_penalty) + 0.15 * (1-vig_penalty)
    return max(0.0, min(1.0, score))
