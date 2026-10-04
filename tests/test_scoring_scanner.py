from signalbot.config import StrategyParams
from signalbot.models import Side
from signalbot.scanner import analyze
from signalbot.scoring import score_components, structure_score, total_score
from signalbot.synthetic import make_random_walk, make_trb_like


def test_components_within_weights():
    df = make_trb_like()
    c = score_components(df, Side.LONG, dist_pct=3.0, max_dist_pct=10, touches=3, rr=3.0)
    assert 0 <= total_score(c) <= 100
    assert c["proximity"] <= 25 and c["squeeze"] <= 20 and c["rr"] <= 15


def test_closer_price_scores_higher():
    df = make_trb_like()
    near = score_components(df, Side.LONG, 1.0, 10, 2, 2.0)["proximity"]
    far = score_components(df, Side.LONG, 9.0, 10, 2, 2.0)["proximity"]
    assert near > far


def test_structure_score_range():
    df = make_trb_like()
    assert 0.0 <= structure_score(df, Side.LONG) <= 1.0


def test_analyze_trb_like_gives_long_idea():
    df = make_trb_like()
    idea = analyze(df, "TRB/USDT:USDT", "binance", "1d", StrategyParams(min_score=0))
    assert idea is not None and idea.side is Side.LONG
    assert idea.invalidation < idea.price < idea.entry_conservative
    assert idea.pools[-1].price > idea.entry_conservative
    assert idea.extra["rr"] >= 2.0


def test_analyze_respects_min_score():
    df = make_trb_like()
    assert analyze(df, "X", "binance", "1d", StrategyParams(min_score=99.9)) is None


def test_analyze_short_history_returns_none():
    assert analyze(make_random_walk(bars=60), "X", "b", "1d", StrategyParams()) is None
