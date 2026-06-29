from core import nas


def test_build_unc_path_normalizes_subpath():
    assert nas.build_unc_path("192.0.2.12", "Media", "Sample/欧美") == (
        r"\\192.0.2.12\Media\Sample\欧美"
    )


def test_save_credential_non_windows(monkeypatch):
    monkeypatch.setattr(nas, "is_windows", lambda: False)

    result = nas.save_windows_credential("nas", "user", "secret")

    assert result.success is False
    assert result.code == "unsupported_platform"


def test_connect_windows_uses_net_use_without_shell(monkeypatch):
    calls = []

    class Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(nas, "is_windows", lambda: True)
    monkeypatch.setattr(nas.os.path, "isdir", lambda _p: True)

    def fake_run(args):
        calls.append(args)
        return Proc()

    monkeypatch.setattr(nas, "_run_command", fake_run)

    result = nas.ensure_share_connected("nas", "share", "user", "pw")

    assert result.success is True
    assert calls == [["net", "use", r"\\nas\share", "/persistent:no", "/user:user", "pw"]]


def test_preflight_reports_unreadable_gallery_directory(monkeypatch):
    monkeypatch.setattr(nas.os.path, "isdir", lambda p: p == r"\\nas\share")

    result = nas.preflight_config({
        "gallery": {"directories": [r"\\nas\share", r"\\nas\missing"]},
        "nas": {"shares": []},
    })

    assert result["success"] is False
    assert result["issues"][0]["path"] == r"\\nas\missing"
