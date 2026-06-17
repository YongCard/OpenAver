"""
離線單元測試：core/nfo_utils.py sanitize_nfo_bytes
覆蓋 CDATA 路徑（:31-42）使模組覆蓋率達 100%。
"""

from core.nfo_utils import sanitize_nfo_bytes


class TestSanitizeNfoBytesCodePath:
    def test_fast_path_no_cdata(self):
        """無 CDATA：bare & 被 escape，既有 &amp; 不雙重 escape。"""
        raw = b'<t>foo &amp; bar & baz</t>'
        result = sanitize_nfo_bytes(raw)
        # bare & (在 "bar & baz" 之間) 被轉成 &amp;
        assert b'bar &amp; baz' in result
        # 既有 &amp; 不被雙重 escape（不應出現 &amp;amp;）
        assert b'&amp;amp;' not in result
        # 整體結果不含原始 bare &（用簡單字串確認）
        assert b'foo &amp; bar &amp; baz' in result

    def test_cdata_path_preserved(self):
        """CDATA 內 & 原封不動；CDATA 外 bare & 被 escape。"""
        raw = b'<plot><![CDATA[A & B]]> foo & bar</plot>'
        result = sanitize_nfo_bytes(raw)
        # CDATA 區塊完整保留（內部 & 未被動）
        assert b'<![CDATA[A & B]]>' in result
        # CDATA 外的 bare & 被 escape
        assert b'foo &amp; bar' in result
        # CDATA 內的 & 不應被誤 escape（不應出現 "A &amp; B"）
        assert b'A &amp; B' not in result

    def test_cdata_path_multiple_segments(self):
        """3 段：非 CDATA + CDATA + 非 CDATA；只有非 CDATA 段的 & 被 escape。"""
        raw = b'x & y <![CDATA[a & b]]> z & w'
        result = sanitize_nfo_bytes(raw)
        # 前段 bare & 被 escape
        assert b'x &amp; y ' in result
        # CDATA 區塊原封
        assert b'<![CDATA[a & b]]>' in result
        # 後段 bare & 被 escape
        assert b' z &amp; w' in result
        # CDATA 內的 & 未被誤 escape
        assert b'a &amp; b' not in result
