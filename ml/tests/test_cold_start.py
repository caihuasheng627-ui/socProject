import pandas as pd

from forecast_contract import (
    UNKNOWN_ITEM,
    assign_price_groups,
    build_training_item_map,
    encode_item_ids,
    route_price_group,
)


def test_lstm_c_map_contains_training_items_and_one_unknown_only():
    train = pd.DataFrame({"market_hash_name": ["A", "B", "A"]})
    item_map = build_training_item_map(train)

    assert set(item_map) == {"A", "B", UNKNOWN_ITEM}
    encoded = encode_item_ids(pd.Series(["A", "NEW"]), item_map)
    assert encoded.tolist() == [item_map["A"], item_map[UNKNOWN_ITEM]]


def test_lstm_d_unseen_item_routes_using_current_price_only():
    train = pd.DataFrame({
        "market_hash_name": ["A", "A", "B", "B", "C", "C"],
        "price": [1.0, 1.0, 10.0, 10.0, 100.0, 100.0],
    })
    boundaries, known_groups = assign_price_groups(train)

    assert route_price_group("NEW", 0.5, known_groups, boundaries) == "low"
    assert route_price_group("NEW", 1000.0, known_groups, boundaries) == "high"
    assert route_price_group("A", 1000.0, known_groups, boundaries) == known_groups["A"]
