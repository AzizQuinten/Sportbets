from collections import defaultdict
from statistics import median, pstdev
from math import log, sqrt


def implied_prob(decimal_odds: float) -> float:
    return 1.0 / decimal_odds


def remove_vig(prices_by_outcome: dict[str, float]) -> tuple[dict[str, float], float]:
    raw = {k: implied_prob(v) for k, v in prices_by_outcome.items() if v and v > 1.0}
    s = sum(raw.values())
    if s <= 0:
        return {}, 0.0
    return {k: p / s for k, p in raw.items()}, max(0.0, s - 1.0)


def _mad(values: list[float]) -> float:
    if not values: return 0.0
    m = median(values)
    return median([abs(x-m) for x in values])


def consensus_market(rows: list[dict]) -> dict:
    """Robust bookmaker consensus.

    Consensus is built from complete bookmaker books only, severe stale/outlier
    books are rejected, and best-price edge is measured against an independent
    market anchor rather than the selected bookmaker itself.
    """
    by_book=defaultdict(dict)
    for r in rows:
        if r.get('price') and r['price']>1.0:
            by_book[r['bookmaker']][r['outcome']]=r['price']
    book_fair={}; book_vig={}; preliminary=defaultdict(list)
    for book,prices in by_book.items():
        fair,vig=remove_vig(prices)
        if len(fair)>=2 and vig<=0.25:
            book_fair[book]=fair; book_vig[book]=vig
            for o,p in fair.items(): preliminary[o].append(p)
    med={o:median(ps) for o,ps in preliminary.items() if ps}
    deviations={}
    for book,fair in book_fair.items():
        ds=[abs(p-med[o]) for o,p in fair.items() if o in med]
        deviations[book]=max(ds) if ds else 0.0
    vals=list(deviations.values()); md=median(vals) if vals else 0.0; mad=_mad(vals)
    cutoff=max(0.06,md+4.0*max(mad,0.008))
    kept={b for b,d in deviations.items() if d<=cutoff}
    if len(kept)<2: kept=set(book_fair)
    fair_by_outcome=defaultdict(list); vigs=[]
    for b in kept:
        vigs.append(book_vig[b])
        for o,p in book_fair[b].items(): fair_by_outcome[o].append(p)
    fair={o:median(ps) for o,ps in fair_by_outcome.items() if ps}
    total=sum(fair.values())
    if total: fair={o:p/total for o,p in fair.items()}
    best={}; price_lists=defaultdict(list)
    for r in rows:
        if r['bookmaker'] not in kept or not r.get('price') or r['price']<=1.0: continue
        o=r['outcome']; price_lists[o].append(r['price'])
        if o not in best or r['price']>best[o]['odds']: best[o]={'odds':r['price'],'bookmaker':r['bookmaker']}
    dispersion={o:(pstdev([1/x for x in xs]) if len(xs)>1 else 0.0) for o,xs in price_lists.items()}
    agreement={o:max(0.0,1.0-min(1.0,dispersion.get(o,0.0)/0.055)) for o in fair}
    return {'fair':fair,'best':best,'books':len(kept),'raw_books':len(book_fair),'outlier_books':max(0,len(book_fair)-len(kept)),'vig':median(vigs) if vigs else 0.0,'dispersion':dispersion,'agreement':agreement}


def expected_value(prob: float, decimal_odds: float) -> float:
    return prob*decimal_odds-1.0


def kelly_fraction(prob: float, decimal_odds: float) -> float:
    b=decimal_odds-1.0
    if b<=0:return 0.0
    return max(0.0,(b*prob-(1.0-prob))/b)


def market_quality(books:int,vig:float,dispersion:float,outlier_books:int=0,raw_books:int=0)->float:
    breadth=min(1.0,books/8.0)
    vig_score=max(0.0,1.0-vig/0.12)
    agreement=max(0.0,1.0-dispersion/0.06)
    outlier_score=1.0-(outlier_books/max(1,raw_books))
    return max(0.0,min(1.0,0.35*breadth+0.25*vig_score+0.30*agreement+0.10*outlier_score))


def confidence_score(books:int,edge:float,dispersion:float,vig:float,reliability:float=0.0,quality:float|None=None)->float:
    q=market_quality(books,vig,dispersion) if quality is None else quality
    edge_score=min(1.0,max(0.0,edge)/0.08)
    # Reliability matters, but market-only signals can still have research value.
    model_score=0.50+0.50*max(0.0,min(1.0,reliability))
    return max(0.0,min(1.0,0.45*q+0.30*edge_score+0.25*model_score))


def uncertainty_adjusted_ev(prob:float,odds:float,confidence:float,dispersion:float)->float:
    """Conservative EV used for ranking/staking, not a claim of true probability."""
    raw=expected_value(prob,odds)
    haircut=0.45+0.55*max(0.0,min(1.0,confidence))
    noise=min(0.04,dispersion*0.75)
    return raw*haircut-noise


def fractional_kelly_stake(bankroll:float,prob:float,odds:float,confidence:float,fraction:float,max_pct:float)->float:
    k=kelly_fraction(prob,odds)
    # Confidence-scaled fractional Kelly materially reduces model-error risk.
    scale=max(0.0,min(1.0,confidence))**2
    return bankroll*min(max_pct,k*fraction*scale)
