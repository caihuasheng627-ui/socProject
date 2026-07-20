"""Compatibility entrypoint for ml/data imports."""
import sys
from pathlib import Path

ML_DIR = Path(__file__).resolve().parents[1]
if str(ML_DIR) not in sys.path:
    sys.path.insert(0, str(ML_DIR))

from feature_engineering import (  # noqa: E402,F401
    CATEGORICAL_COLS,
    build_features,
    fit_categoricals,
    transform_categoricals,
)
