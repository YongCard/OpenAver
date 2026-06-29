"""Lightweight tracked-file hygiene checks for OpenAver.

This script intentionally checks only files tracked by git. It must not report
local developer artifacts such as .venv, node_modules, output, or .tmp-pytest
when they are present but untracked in a personal worktree.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

SKIP_DIR_PARTS = {
    ".git",
    ".github",
    "docs",
    "tests",
    "core/README.md",
    "core/path_utils.py",
    "web/templates/design-system.html",
    "web/templates/design_system",
    "web/static/vendor",
}

TEXT_SUFFIXES = {
    ".cfg",
    ".css",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".mjs",
    ".ps1",
    ".py",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}

FORBIDDEN_TRACKED_PARTS = {
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "build",
    "dist",
    "output",
    ".tmp",
    ".tmp-pytest",
}

SENSITIVE_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"][^'\"\s]{12,}['\"]"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"),
]

ABSOLUTE_PATH_PATTERNS = [
    re.compile(r"(?i)\b[A-Z]:\\Users\\(?!<|username|user)[^\\\s]+\\"),
    re.compile(r"/Users/(?!<|username|user)[^/\s]+/"),
    re.compile(r"/home/(?!<|username|user)[^/\s]+/"),
]

DEBUG_PATTERNS = [
    re.compile(r"print\((?:['\"]here['\"]|['\"]111['\"])\)"),
    re.compile(r"console\.log\((?:['\"]here['\"]|['\"]111['\"])\)"),
]


def _run_git_ls_files() -> list[Path]:
    proc = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return [ROOT / line for line in proc.stdout.splitlines() if line.strip()]


def _display(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _is_text_candidate(path: Path) -> bool:
    rel = _display(path)
    if any(rel == part or rel.startswith(f"{part}/") for part in SKIP_DIR_PARTS):
        return False
    return path.suffix.lower() in TEXT_SUFFIXES


def _scan_text(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(encoding="utf-8", errors="ignore")
    findings: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if any(pattern.search(line) for pattern in SENSITIVE_PATTERNS):
            findings.append(f"{_display(path)}:{lineno}: possible hardcoded secret")
        if any(pattern.search(line) for pattern in ABSOLUTE_PATH_PATTERNS):
            findings.append(f"{_display(path)}:{lineno}: local absolute path")
        if any(pattern.search(line) for pattern in DEBUG_PATTERNS):
            findings.append(f"{_display(path)}:{lineno}: placeholder debug output")
    return findings


def main() -> int:
    findings: list[str] = []
    tracked_files = _run_git_ls_files()

    for path in tracked_files:
        rel_parts = set(path.relative_to(ROOT).parts)
        polluted_parts = rel_parts & FORBIDDEN_TRACKED_PARTS
        if polluted_parts:
            findings.append(
                f"{_display(path)}: tracked artifact directory part {sorted(polluted_parts)}"
            )
        if path.is_file() and _is_text_candidate(path):
            findings.extend(_scan_text(path))

    if findings:
        sys.stdout.write("Enterprise hygiene check failed:\n")
        for finding in findings:
            sys.stdout.write(f"- {finding}\n")
        return 1

    sys.stdout.write(
        "Enterprise hygiene check passed: tracked files contain no obvious delivery pollution.\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
