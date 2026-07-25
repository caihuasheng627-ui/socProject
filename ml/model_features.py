"""Shared volume-free feature contracts for all train and inference paths."""


FEATURE_CONTRACT_VERSION = "volume-free-v1"


def assert_volume_free_feature_contract(feature_columns):
    """Return an immutable contract, rejecting accidental volume dependencies."""
    columns = tuple(feature_columns)
    forbidden = [column for column in columns if "volume" in column.lower()]
    if forbidden:
        raise ValueError(
            "Model feature contract must not contain volume columns: "
            + ", ".join(forbidden)
        )
    return columns


SEQUENCE_FEATURE_COLS = assert_volume_free_feature_contract(
    (
        "log_price",
        "MA_7",
        "MA_30",
        "MA_90",
        "Return_1d",
        "Return_7d",
        "Volatility_30",
        "RSI_14",
        "MACD",
        "is_floor_price",
        "is_stattrak",
        "is_major_active",
        "steam_ccu",
    )
)


TREE_FEATURE_COLS = assert_volume_free_feature_contract(
    (
        "log_price",
        "MA_7",
        "MA_30",
        "MA_90",
        "Return_1d",
        "Return_7d",
        "Volatility_30",
        "RSI_14",
        "MACD",
        "MA_30_dev",
        "BB_position",
        "is_stattrak",
        "is_floor_price",
        "days_to_next_major",
        "days_since_last_major",
        "is_major_active",
        "steam_ccu",
        "days_since_cs2_announce",
        "weapon_type_enc",
        "rarity_enc",
        "wear_enc",
    )
)


# Frozen from models/gru_items.pkl before removing volume from model inputs.
FROZEN_GRU_ITEMS = (
    "CZ75-Auto | Circaetus (Battle-Scarred)",
    "R8 Revolver | Amber Fade (Factory New)",
    "Danger Zone Case",
    "R8 Revolver | Amber Fade (Minimal Wear)",
    "Dreams & Nightmares Case",
    "CZ75-Auto | Framework (Battle-Scarred)",
    "Glock-18 | Blue Fissure (Factory New)",
    "FAMAS | CaliCamo (Factory New)",
    "CZ75-Auto | Army Sheen (Factory New)",
    "AK-47 | Safari Mesh (Minimal Wear)",
)
