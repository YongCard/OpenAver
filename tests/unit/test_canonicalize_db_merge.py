"""
TASK-58a-A5: canonicalize DB merge + cache invalidate unit tests (TDD-lite)

Tests:
  1. DB alias 生效（happy path）
  2. DB read 失敗 → fallback hardcoded-only，不 raise
  3. 空 DB → 與 hardcoded-only 等價
  4. cache invalidate 計次（區分 cache hit vs miss）
  5. DB 覆蓋 hardcoded（DB 優先）
"""

import pytest
from unittest.mock import patch, MagicMock
from core.similar.canonicalize import canonicalize, _invalidate_cache
from core.database import TagAliasRecord


@pytest.fixture(autouse=True)
def isolate_cache():
    """每 test 前後清 cache，避免 cross-test 污染"""
    _invalidate_cache()
    yield
    _invalidate_cache()


# ---------------------------------------------------------------------------
# Case 1: DB alias 生效（happy path）
# ---------------------------------------------------------------------------

def test_db_alias_applied(caplog):
    """DB 回傳 alias record → canonicalize 應套用 DB 定義"""
    mock_record = TagAliasRecord(primary_name="メイド", aliases=["女僕"])
    mock_repo = MagicMock()
    mock_repo.get_all.return_value = [mock_record]

    with patch("core.database.TagAliasRepository", return_value=mock_repo):
        result = canonicalize(["女僕"])

    assert result == ["メイド"]


# ---------------------------------------------------------------------------
# Case 2: DB read 失敗 → fallback hardcoded-only，不 raise
# ---------------------------------------------------------------------------

def test_db_read_failure_fallback_hardcoded(caplog):
    """DB get_all 拋 Exception → fallback hardcoded，不 raise；logger 輸出 warning"""
    mock_repo = MagicMock()
    mock_repo.get_all.side_effect = Exception("db error")

    with patch("core.database.TagAliasRepository", return_value=mock_repo):
        # 使用 hardcoded alias → 非 stopword target，確認 fallback 生效
        result = canonicalize(["中出"])

    # "中出し" 不在 stopwords，應正常回傳
    assert result == ["中出し"]
    assert "DB load 失敗" in caplog.text


# ---------------------------------------------------------------------------
# Case 3: 空 DB → 與 hardcoded-only 等價（regression protection）
# ---------------------------------------------------------------------------

def test_empty_db_equals_hardcoded_behavior():
    """DB 回傳 [] → 行為完全等價 hardcoded-only"""
    mock_repo = MagicMock()
    mock_repo.get_all.return_value = []

    with patch("core.database.TagAliasRepository", return_value=mock_repo):
        # stopword 生效
        result_stopword = canonicalize(["高畫質"])
        assert result_stopword == []

        # hardcoded alias 生效
        result_alias = canonicalize(["中出"])
        assert result_alias == ["中出し"]


# ---------------------------------------------------------------------------
# Case 4: cache invalidate 計次（區分 cache hit vs miss）
# ---------------------------------------------------------------------------

def test_cache_invalidate_call_count():
    """
    第一次 call → DB 讀 1 次；
    第二次 call → cache hit，仍 1 次；
    invalidate 後再 call → DB 讀 2 次。
    """
    mock_repo = MagicMock()
    mock_repo.get_all.return_value = []

    with patch("core.database.TagAliasRepository", return_value=mock_repo):
        # 第一次呼叫
        canonicalize(["abc"])
        assert mock_repo.get_all.call_count == 1

        # 第二次呼叫 — cache hit
        canonicalize(["abc"])
        assert mock_repo.get_all.call_count == 1

        # invalidate 後再呼叫 — 重新 DB read
        _invalidate_cache()
        canonicalize(["abc"])
        assert mock_repo.get_all.call_count == 2


# ---------------------------------------------------------------------------
# Case 5: DB 覆蓋 hardcoded（DB 優先）
# ---------------------------------------------------------------------------

def test_db_overrides_hardcoded():
    """DB 定義與 hardcoded 衝突時，DB 優先（DB 後 merge）"""
    # hardcoded: "高画質" → "高畫質"；DB 重新定義 "高画質" → "CustomX"
    mock_record = TagAliasRecord(primary_name="CustomX", aliases=["高画質"])
    mock_repo = MagicMock()
    mock_repo.get_all.return_value = [mock_record]

    with patch("core.database.TagAliasRepository", return_value=mock_repo):
        result = canonicalize(["高画質"])

    # "CustomX" 不在 stopwords，應正常回傳
    assert result == ["CustomX"]
