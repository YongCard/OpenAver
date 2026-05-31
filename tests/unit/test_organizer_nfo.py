"""63c-5: generate_nfo summary→<plot> + rating→<rating> + <mpaa>JP-18+（US7 / CD-63c-10）。"""
from core.organizer import generate_nfo
from core.scrapers.models import Video


def _gen(tmp_path, **kw):
    out = str(tmp_path / "x.nfo")
    assert generate_nfo(number="ABC-123", title="T", output_path=out, **kw) is True
    return (tmp_path / "x.nfo").read_text(encoding="utf-8")


def test_summary_fills_plot(tmp_path):
    nfo = _gen(tmp_path, summary="test plot")
    assert "<plot>test plot</plot>" in nfo


def test_empty_summary_empty_plot(tmp_path):
    nfo = _gen(tmp_path, summary="")
    assert "<plot></plot>" in nfo


def test_rating_doubled(tmp_path):
    nfo = _gen(tmp_path, rating=4.5)
    assert "<rating>9.0</rating>" in nfo


def test_rating_none_no_tag(tmp_path):
    nfo = _gen(tmp_path, rating=None)
    assert "<rating>" not in nfo


def test_rating_zero_no_tag(tmp_path):
    """rating=0 → 不寫 <rating>（mapper 已轉 None，generate_nfo double guard）。"""
    nfo = _gen(tmp_path, rating=0)
    assert "<rating>" not in nfo


def test_mpaa_always_present(tmp_path):
    """<mpaa>JP-18+ 對所有 NFO 生效（含 builtin），無論 rating/summary。"""
    assert "<mpaa>JP-18+</mpaa>" in _gen(tmp_path)
    assert "<mpaa>JP-18+</mpaa>" in _gen(tmp_path, summary="x", rating=5.0)


def test_summary_html_escaped(tmp_path):
    nfo = _gen(tmp_path, summary="<script>evil</script>")
    assert "&lt;script&gt;evil&lt;/script&gt;" in nfo
    assert "<script>evil" not in nfo


def test_builtin_path_defaults(tmp_path):
    """既有 builtin 呼叫（無 summary/rating）→ 空 plot + 無 rating + 有 mpaa（向後相容）。"""
    nfo = _gen(tmp_path)
    assert "<plot></plot>" in nfo
    assert "<rating>" not in nfo
    assert "<mpaa>JP-18+</mpaa>" in nfo


def test_premiered_rating_plot_mpaa_runtime_order(tmp_path):
    """欄位序：<premiered> → <rating> → <plot> → <mpaa> → <runtime>（Jellyfin 標準）。"""
    nfo = _gen(tmp_path, date="2024-01-01", rating=4.0, summary="s", duration=120)
    i_prem = nfo.index("<premiered>")
    i_rat = nfo.index("<rating>")
    i_plot = nfo.index("<plot>")
    i_mpaa = nfo.index("<mpaa>")
    i_run = nfo.index("<runtime>")
    assert i_prem < i_rat < i_plot < i_mpaa < i_run


def test_to_legacy_dict_no_summary_key():
    """regression（US7 硬契約）：to_legacy_dict 不含 summary（不入 DB）。"""
    d = Video(number="ABC-123", summary="should not leak", rating=4.0).to_legacy_dict()
    assert "summary" not in d
    assert "rating" not in d
