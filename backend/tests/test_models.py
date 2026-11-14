"""Limits on the user-supplied profile: it is stored in Mongo and will feed the LLM prompt."""

import pytest
from pydantic import ValidationError

from app.models import CATEGORIES, UserProfile


def test_defaults_are_valid_and_cover_every_category():
    profile = UserProfile()
    assert set(profile.weights) == set(CATEGORIES)
    assert all(w == 3 for w in profile.weights.values())


@pytest.mark.parametrize("weight", [0, 3, 5])
def test_weights_inside_range_are_accepted(weight):
    assert UserProfile(weights={"labor": weight}).weights == {"labor": weight}


@pytest.mark.parametrize("weight", [-1, 6, 10**9])
def test_weights_outside_range_are_rejected(weight):
    with pytest.raises(ValidationError, match="between 0 and 5"):
        UserProfile(weights={"labor": weight})


@pytest.mark.parametrize("key", ["not_a_category", "$where", "a.b", ""])
def test_unknown_weight_keys_are_rejected(key):
    with pytest.raises(ValidationError, match="unknown categories"):
        UserProfile(weights={key: 3})


def test_text_fields_are_length_limited():
    with pytest.raises(ValidationError):
        UserProfile(name="n" * 101)
    with pytest.raises(ValidationError):
        UserProfile(stances="s" * 2001)
    assert UserProfile(name="n" * 100, stances="s" * 2000)
