"""NAS / SMB helpers for OpenAver.

Passwords are intentionally process-local inputs only.  Persisted OpenAver
config stores share metadata; Windows Credential Manager stores secrets.
"""
from __future__ import annotations

import os
import platform
import subprocess
from dataclasses import dataclass
from typing import Any

from core.logger import get_logger

logger = get_logger(__name__)


@dataclass
class NasResult:
    success: bool
    unc_path: str
    code: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "unc_path": self.unc_path,
            "code": self.code,
            "message": self.message,
        }


def is_windows() -> bool:
    return platform.system().lower() == "windows"


def credential_target(host: str) -> str:
    # For SMB, Windows resolves saved credentials by the server name/IP used in
    # \\host\share.  A custom OpenAver prefix would save successfully but would
    # not be picked up by `net use`.
    return _clean_segment(host)


def build_unc_path(host: str, share: str, subpath: str = "") -> str:
    host = _clean_segment(host)
    share = _clean_segment(share)
    subpath = (subpath or "").strip().replace("/", "\\").strip("\\")
    if not host or not share:
        return ""
    root = f"\\\\{host}\\{share}"
    return f"{root}\\{subpath}" if subpath else root


def share_root(host: str, share: str) -> str:
    return build_unc_path(host, share)


def ensure_share_connected(
    host: str,
    share: str,
    username: str = "",
    password: str = "",
) -> NasResult:
    """Connect an SMB share if possible, then verify the share root is readable."""
    root = share_root(host, share)
    if not root:
        return NasResult(False, root, "invalid_share", "NAS host/share is required")

    if not is_windows():
        ok = os.path.isdir(root)
        return NasResult(
            ok,
            root,
            "ok" if ok else "unsupported_platform",
            "UNC path is readable" if ok else "NAS auto-connect is only supported on Windows desktop mode",
        )

    cmd = ["net", "use", root, "/persistent:no"]
    if username:
        cmd.append(f"/user:{username}")
        if password:
            cmd.append(password)

    proc = _run_command(cmd)
    if proc.returncode not in (0, 2):
        return NasResult(False, root, "connect_failed", _safe_error(proc))

    ok = os.path.isdir(root)
    return NasResult(
        ok,
        root,
        "ok" if ok else "path_unreadable",
        "NAS share is readable" if ok else "NAS share connected but the path is not readable",
    )


def test_share(
    host: str,
    share: str,
    subpath: str = "",
    username: str = "",
    password: str = "",
) -> NasResult:
    root_result = ensure_share_connected(host, share, username, password)
    unc = build_unc_path(host, share, subpath)
    if not root_result.success:
        root_result.unc_path = unc or root_result.unc_path
        return root_result
    ok = os.path.isdir(unc)
    return NasResult(
        ok,
        unc,
        "ok" if ok else "path_unreadable",
        "NAS folder is readable" if ok else "NAS share connected, but the configured folder is not readable",
    )


def save_windows_credential(host: str, username: str, password: str) -> NasResult:
    target = credential_target(host)
    root = f"\\\\{_clean_segment(host)}"
    if not is_windows():
        return NasResult(False, root, "unsupported_platform", "Credential Manager is only available on Windows")
    if not username or not password:
        return NasResult(False, root, "credential_required", "Username and password are required")

    proc = _run_command(["cmdkey", f"/add:{target}", f"/user:{username}", f"/pass:{password}"])
    if proc.returncode != 0:
        return NasResult(False, root, "credential_save_failed", _safe_error(proc))
    return NasResult(True, root, "ok", "Credential saved")


def disconnect_share(host: str, share: str) -> NasResult:
    root = share_root(host, share)
    if not root:
        return NasResult(False, root, "invalid_share", "NAS host/share is required")
    if not is_windows():
        return NasResult(False, root, "unsupported_platform", "Disconnect is only supported on Windows")
    proc = _run_command(["net", "use", root, "/delete", "/y"])
    if proc.returncode != 0:
        return NasResult(False, root, "disconnect_failed", _safe_error(proc))
    return NasResult(True, root, "ok", "NAS share disconnected")


def status_for_share(share_cfg: dict[str, Any]) -> dict[str, Any]:
    unc = build_unc_path(
        str(share_cfg.get("host", "")),
        str(share_cfg.get("share", "")),
        str(share_cfg.get("subpath", "")),
    )
    readable = bool(unc and os.path.isdir(unc))
    return {
        "id": share_cfg.get("id", ""),
        "name": share_cfg.get("name", ""),
        "enabled": bool(share_cfg.get("enabled", True)),
        "add_to_gallery": bool(share_cfg.get("add_to_gallery", True)),
        "unc_path": unc,
        "readable": readable,
        "code": "ok" if readable else "path_unreadable",
        "message": "NAS folder is readable" if readable else "NAS folder is not readable",
    }


def preflight_config(config: dict[str, Any]) -> dict[str, Any]:
    gallery = config.get("gallery", {}) if isinstance(config.get("gallery"), dict) else {}
    directories = gallery.get("directories", []) if isinstance(gallery.get("directories"), list) else []
    nas_cfg = config.get("nas", {}) if isinstance(config.get("nas"), dict) else {}
    shares = nas_cfg.get("shares", []) if isinstance(nas_cfg.get("shares"), list) else []

    issues: list[dict[str, Any]] = []
    checked: list[dict[str, Any]] = []

    for directory in directories:
        if not isinstance(directory, str) or not directory:
            continue
        readable = os.path.isdir(directory)
        item = {
            "type": "directory",
            "path": directory,
            "readable": readable,
            "code": "ok" if readable else "path_unreadable",
        }
        checked.append(item)
        if not readable:
            issues.append(item)

    for share_cfg in shares:
        if not isinstance(share_cfg, dict) or not share_cfg.get("enabled", True):
            continue
        unc = build_unc_path(
            str(share_cfg.get("host", "")),
            str(share_cfg.get("share", "")),
            str(share_cfg.get("subpath", "")),
        )
        if not unc or unc not in directories:
            continue
        status = status_for_share(share_cfg)
        status["type"] = "nas"
        checked.append(status)
        if not status["readable"]:
            issues.append(status)

    return {"success": len(issues) == 0, "checked": checked, "issues": issues}


def _clean_segment(value: str) -> str:
    return (value or "").strip().strip("\\/")


def _run_command(args: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(args, 124, "", str(exc))


def _safe_error(proc: subprocess.CompletedProcess) -> str:
    text = (proc.stderr or proc.stdout or "").strip()
    if not text:
        return f"Command failed with exit code {proc.returncode}"
    # Avoid echoing full commands or secrets from system output.
    return text.splitlines()[-1][:500]
