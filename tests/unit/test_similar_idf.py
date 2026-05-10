import math

from core.similar.idf import IDF_HOT_THRESHOLD, build_idf, idf_jaccard


def test_idf_hot_threshold_constant():
    assert IDF_HOT_THRESHOLD == 0.25


def test_build_idf_empty_corpus():
    assert build_idf([]) == {}


def test_build_idf_single_empty_doc():
    assert build_idf([[]]) == {}


def test_build_idf_multiple_empty_docs():
    assert build_idf([[], [], []]) == {}


def test_build_idf_single_tag_single_doc_is_hot():
    # df/N = 1/1 = 1.0 > 0.25 -> hot
    assert build_idf([["a"]]) == {"a": 0.0}


def test_build_idf_all_unique_tiny_corpus_all_hot():
    # 3 docs, each tag df=1, df/N = 1/3 ≈ 0.333 > 0.25 -> all hot
    result = build_idf([["a"], ["b"], ["c"]])
    assert result == {"a": 0.0, "b": 0.0, "c": 0.0}


def test_build_idf_identical_corpus_all_hot():
    result = build_idf([["a", "b"], ["a", "b"]])
    assert result == {"a": 0.0, "b": 0.0}


def test_build_idf_per_doc_dedup():
    # tag 在同一 doc 重複只算一次
    result = build_idf([["a", "a", "a"]] + [["b"]] * 100)
    # a 只出現在 1/101 部，遠低於門檻
    assert result["a"] > 0
    assert result["a"] == math.log((101 + 1) / (1 + 1)) + 1


def test_build_idf_uneven_dictionary_rare_higher_than_common():
    corpus: list[list[str]] = []
    # 100 docs: "common" in 10, "rare" in 1
    for _ in range(10):
        corpus.append(["common"])
    corpus.append(["rare"])
    for _ in range(89):
        corpus.append([])
    result = build_idf(corpus)
    assert result["common"] > 0
    assert result["rare"] > 0
    assert result["rare"] > result["common"]


def test_build_idf_hot_strict_greater_than_25_percent_boundary_is_not_hot():
    # 100 docs, df=25 -> df/N = 0.25 (NOT strictly > 0.25) -> not hot
    corpus: list[list[str]] = [["x"]] * 25 + [[]] * 75
    result = build_idf(corpus)
    assert result["x"] > 0
    assert result["x"] == math.log((100 + 1) / (25 + 1)) + 1


def test_build_idf_hot_strict_greater_than_25_percent_above_is_hot():
    # 100 docs, df=26 -> df/N = 0.26 > 0.25 -> hot
    corpus: list[list[str]] = [["y"]] * 26 + [[]] * 74
    result = build_idf(corpus)
    assert result["y"] == 0.0


def test_build_idf_small_library_30_docs_hot_above_threshold():
    # 30 docs, tag in 8 -> 8/30 ≈ 0.267 > 0.25 -> hot
    corpus: list[list[str]] = [["h"]] * 8 + [[]] * 22
    result = build_idf(corpus)
    assert result["h"] == 0.0


def test_build_idf_small_library_30_docs_not_hot_below_threshold():
    # 30 docs, tag in 7 -> 7/30 ≈ 0.233 < 0.25 -> not hot
    corpus: list[list[str]] = [["t"]] * 7 + [[]] * 23
    result = build_idf(corpus)
    assert result["t"] > 0
    assert result["t"] == math.log((30 + 1) / (7 + 1)) + 1


def test_build_idf_small_library_rare_higher_than_borderline():
    # 30 docs: rare in 1 (df/N=0.033), borderline non-hot in 7 (df/N=0.233)
    corpus: list[list[str]] = []
    for _ in range(7):
        corpus.append(["border"])
    corpus.append(["rare"])
    for _ in range(22):
        corpus.append([])
    result = build_idf(corpus)
    assert result["rare"] > result["border"] > 0


def test_idf_jaccard_double_empty():
    assert idf_jaccard(set(), set(), {}) == 0.0


def test_idf_jaccard_no_overlap():
    assert idf_jaccard({"a"}, {"b"}, {"a": 1.0, "b": 1.0}) == 0.0


def test_idf_jaccard_identical_non_hot():
    assert idf_jaccard({"a", "b"}, {"a", "b"}, {"a": 1.5, "b": 1.0}) == 1.0


def test_idf_jaccard_partial_overlap():
    result = idf_jaccard({"a", "b"}, {"b", "c"}, {"a": 1.0, "b": 2.0, "c": 1.0})
    assert result == 0.5


def test_idf_jaccard_partial_overlap_uneven_weights():
    result = idf_jaccard({"a", "b"}, {"b", "c"}, {"a": 1.0, "b": 2.0, "c": 3.0})
    assert math.isclose(result, 2.0 / 6.0)


def test_idf_jaccard_hot_tag_does_not_contribute():
    table = {"hot": 0.0, "rare": 5.0}
    assert idf_jaccard({"hot", "rare"}, {"hot"}, table) == 0.0


def test_idf_jaccard_only_shared_hot_returns_zero():
    table = {"hot": 0.0, "x": 2.0}
    # union 加權只剩 x=2.0；intersection={"hot"} 加權 0 -> 0/2 = 0
    assert idf_jaccard({"hot", "x"}, {"hot"}, table) == 0.0


def test_idf_jaccard_identical_hot_only_returns_zero():
    table = {"h1": 0.0, "h2": 0.0}
    assert idf_jaccard({"h1", "h2"}, {"h1", "h2"}, table) == 0.0


def test_idf_jaccard_oov_tag_uses_zero_default():
    # "z" 不在 idf_table，視為 0
    result = idf_jaccard({"a", "z"}, {"a"}, {"a": 1.0})
    assert result == 1.0


def test_idf_jaccard_oov_only_returns_zero():
    assert idf_jaccard({"unknown"}, {"unknown"}, {}) == 0.0


def test_idf_jaccard_identical_with_hot_mix_equals_one():
    table = {"a": 0.0, "b": 2.0}
    assert idf_jaccard({"a", "b"}, {"a", "b"}, table) == 1.0
