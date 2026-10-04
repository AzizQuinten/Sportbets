from app.services.quant import remove_vig, expected_value, kelly_fraction, consensus_market

def test_remove_vig_sums_one():
    fair, vig = remove_vig({'A': 2.0, 'B': 2.0})
    assert abs(sum(fair.values()) - 1) < 1e-9
    assert vig == 0

def test_ev_and_kelly():
    assert expected_value(.60, 2.0) > 0
    assert kelly_fraction(.60, 2.0) > 0

def test_consensus_best_price():
    rows=[
      {'bookmaker':'a','outcome':'Home','price':2.0}, {'bookmaker':'a','outcome':'Away','price':2.0},
      {'bookmaker':'b','outcome':'Home','price':2.1}, {'bookmaker':'b','outcome':'Away','price':1.9},
    ]
    c=consensus_market(rows)
    assert c['books']==2
    assert c['best']['Home']['odds']==2.1
