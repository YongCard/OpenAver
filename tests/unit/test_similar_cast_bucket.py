from core.similar.cast_bucket import cast_bucket

_VALID = {"none", "solo", "duo", "multi"}


def test_empty_list_is_none():
    assert cast_bucket([]) == "none"


def test_one_actress_is_solo():
    assert cast_bucket(["A"]) == "solo"


def test_two_actresses_is_duo():
    assert cast_bucket(["A", "B"]) == "duo"


def test_three_actresses_is_multi():
    assert cast_bucket(["A", "B", "C"]) == "multi"


def test_ten_actresses_is_multi():
    assert cast_bucket(["A"] * 10) == "multi"


def test_return_value_is_one_of_four_literals():
    for arg in ([], ["A"], ["A", "B"], ["A", "B", "C"], ["A"] * 7):
        assert cast_bucket(arg) in _VALID
