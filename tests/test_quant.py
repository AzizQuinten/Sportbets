from app.services.quant import (
    remove_vig, expected_value, kelly_fraction, consensus_market,
    market_quality, confidence_score, uncertainty_adjusted_ev,
    fractional_kelly_stake,
)


def test_remove_vig_sums_one():
    fair, vig = remove_vig({'A':2.0,'B':2.0})
    assert abs(sum(fair.values())-1)<1e-9
    assert vig==0


def test_ev_and_kelly():
    assert expected_value(.60,2.0)>0
    assert kelly_fraction(.60,2.0)>0


def test_consensus_best_price():
    rows=[
        {'bookmaker':'a','outcome':'Home','price':2.0},{'bookmaker':'a','outcome':'Away','price':2.0},
        {'bookmaker':'b','outcome':'Home','price':2.1},{'bookmaker':'b','outcome':'Away','price':1.9},
    ]
    c=consensus_market(rows)
    assert c['books']==2
    assert c['best']['Home']['odds']==2.1
    assert 'reference_fair' in c


def test_leave_one_out_reference_excludes_best_price_book():
    rows=[]
    for book,home,draw,away in [
        ('sharp1',2.00,3.40,3.80),('sharp2',2.02,3.38,3.78),('value',2.20,3.35,3.75),('sharp3',1.99,3.42,3.82),
    ]:
        rows += [
            {'bookmaker':book,'outcome':'Home','price':home},
            {'bookmaker':book,'outcome':'Draw','price':draw},
            {'bookmaker':book,'outcome':'Away','price':away},
        ]
    c=consensus_market(rows)
    assert c['best']['Home']['bookmaker']=='value'
    assert c['reference_books']['Home']>=2
    assert c['reference_fair']['Home']>0
    assert c['price_premium']['Home']>0


def test_extreme_book_is_filtered_from_best_price():
    rows=[]
    for book,home,draw,away in [
        ('a',2.0,3.4,3.8),('b',2.05,3.3,3.7),('c',1.98,3.5,3.9),('broken',20.0,2.0,2.0),
    ]:
        rows += [
            {'bookmaker':book,'outcome':'Home','price':home},
            {'bookmaker':book,'outcome':'Draw','price':draw},
            {'bookmaker':book,'outcome':'Away','price':away},
        ]
    c=consensus_market(rows)
    assert c['outlier_books']>=1
    assert c['best']['Home']['odds']<5.0


def test_market_quality_rewards_clean_deep_markets():
    good=market_quality(10,0.04,0.01,0,10)
    poor=market_quality(3,0.11,0.05,1,4)
    assert good>poor
    assert 0<=good<=1


def test_confidence_scaled_kelly_reduces_stake():
    high=fractional_kelly_stake(10000,.58,2.0,.90,.20,.01,.90)
    low=fractional_kelly_stake(10000,.58,2.0,.40,.20,.01,.40)
    assert high>low>=0


def test_adjusted_ev_is_more_conservative_with_noise():
    raw=expected_value(.58,2.0)
    adj=uncertainty_adjusted_ev(.58,2.0,.55,.04,.50)
    assert adj<raw
