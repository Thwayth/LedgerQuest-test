from signalbot.levels import cluster, find_pools, find_swings, find_zone
from signalbot.models import Side
from signalbot.synthetic import make_trb_like


def test_swings_found():
    df = make_trb_like()
    sh, sl = find_swings(df)
    assert sh and sl
    assert all(5 <= i < len(df) - 5 for i in sh + sl)


def test_cluster_groups_close_prices():
    groups = cluster([(1, 100.0), (2, 100.5), (3, 120.0)], tol=0.02)
    assert [len(g) for g in groups] == [2, 1]


def test_zone_above_price_for_long():
    df = make_trb_like()
    zone = find_zone(df, Side.LONG)
    assert zone is not None
    price = df["close"].iloc[-1]
    assert zone.high > price
    assert zone.touches >= 2
    assert (zone.high - zone.low) / price >= 0.079  # минимальная толщина 8%


def test_pools_beyond_zone_sorted_by_distance():
    df = make_trb_like()
    zone = find_zone(df, Side.LONG)
    pools = find_pools(df, zone, Side.LONG, n=2)
    assert len(pools) == 2
    assert all(p.price > zone.high for p in pools)
    assert pools[0].price < pools[1].price


def test_no_zone_when_too_far():
    df = make_trb_like()
    assert find_zone(df, Side.LONG, max_distance_pct=1.0) is None
