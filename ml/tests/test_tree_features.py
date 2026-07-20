import pytest

from tree_features import assert_held_out


def test_shap_rejects_split_used_to_fit_model():
    with pytest.raises(ValueError, match="in-sample SHAP"):
        assert_held_out("val", "train+val")


def test_shap_accepts_held_out_test():
    assert_held_out("test", "train+val")
