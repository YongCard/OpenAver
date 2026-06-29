#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

TEST_ENV_DIR="$ROOT_DIR/.tmp/openaver-test-env"
mkdir -p "$TEST_ENV_DIR/logs" "$TEST_ENV_DIR/tmp"
export OPENAVER_LOG_DIR="$TEST_ENV_DIR/logs"
export TMPDIR="$TEST_ENV_DIR/tmp"
export TMP="$TEST_ENV_DIR/tmp"
export TEMP="$TEST_ENV_DIR/tmp"

PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  if [[ -x ".venv/Scripts/python.exe" ]]; then
    PYTHON_BIN=".venv/Scripts/python.exe"
  elif [[ -x "venv/Scripts/python.exe" ]]; then
    PYTHON_BIN="venv/Scripts/python.exe"
  elif [[ -x ".venv/bin/python" ]]; then
    PYTHON_BIN=".venv/bin/python"
  elif [[ -x "venv/bin/python" ]]; then
    PYTHON_BIN="venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
  elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python)"
  else
    echo "Python 3.12 is required but was not found." >&2
    exit 127
  fi
fi

if [[ "${OPENAVER_SKIP_INSTALL:-0}" != "1" ]]; then
  "$PYTHON_BIN" -m pip install -r requirements-test.txt
fi

echo "==> Enterprise hygiene check"
"$PYTHON_BIN" scripts/check_enterprise_hygiene.py

echo "==> Unit and integration tests"
"$PYTHON_BIN" -m pytest tests/unit tests/integration -v --cache-clear

echo "==> Python lint"
"$PYTHON_BIN" -m ruff check .

if [[ "${OPENAVER_SKIP_NPM_INSTALL:-0}" != "1" ]]; then
  if command -v npm >/dev/null 2>&1; then
    npm ci
  else
    echo "npm is required for frontend lint but was not found." >&2
    exit 127
  fi
fi

echo "==> Frontend lint"
npm run lint

echo "All OpenAver local verification checks passed."
