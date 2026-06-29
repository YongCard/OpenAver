# OpenAver Enterprise Readiness Notes

This document maps the external Prompt2Repo acceptance standard onto OpenAver's
existing project shape. The project remains a desktop-first personal media tool;
the original product behavior and repository conventions take precedence over
generic enterprise-template requirements.

## Positioning

OpenAver is intentionally distributed as a Windows/macOS desktop app with a
local FastAPI backend and GUI shell. The public README promise is "no Docker, no
CLI" for normal users. For that reason, this personal branch does not add Docker
or claim full compliance with standards whose hard gate is `docker compose up`.

The enterprise-inspired additions are limited to local verification, documentation
clarity, and tracked-file hygiene. They do not change runtime behavior, API wire
shape, database locations, scraper behavior, organizer behavior, or UI flows.

## Acceptance Mapping

| External standard | OpenAver adaptation |
| --- | --- |
| `docker compose up` hard gate | Not adopted. It conflicts with OpenAver's desktop-first, no-Docker positioning. |
| `unit_tests/` directory | Mapped to existing `tests/unit/`; the directory is not duplicated or renamed. |
| `API_tests/` directory | Mapped to existing `tests/integration/`; FastAPI endpoint tests already live there. |
| One-command test runner | Added `run_tests.sh` and `run_tests.ps1` as local wrappers around existing pytest, ruff, and npm lint commands. |
| No dependency/build artifact pollution | Added `scripts/check_enterprise_hygiene.py`, which scans only tracked files and ignores untracked local worktree artifacts. |
| README must match real behavior | README keeps the no-Docker user story and documents the optional local verification entrypoint. |
| Unit and API coverage | Existing unit/integration tests remain the source of truth; smoke/e2e stay opt-in because they require network, browsers, or external services. |

## Verification Entry Points

Use one of these from the repository root:

```bash
bash run_tests.sh
```

```powershell
.\run_tests.ps1
```

The scripts run:

- `scripts/check_enterprise_hygiene.py`
- `pytest tests/unit tests/integration -v --cache-clear`
- `ruff check .`
- `npm run lint`

During script execution, pytest temp-directory variables and `OPENAVER_LOG_DIR`
are pointed at `.tmp/openaver-test-env/`. This keeps pytest temp files and
OpenAver test logs inside the repository-local temporary area instead of the
developer's real user profile. The runtime application still uses its normal
paths when launched outside these verification scripts.

Set these environment variables when dependencies are already installed and you
want a faster local pass:

- `OPENAVER_SKIP_INSTALL=1`
- `OPENAVER_SKIP_NPM_INSTALL=1`

## Guardrails

- Do not restructure `tests/`, `core/`, `web/`, or `windows/` solely to match an
  external template.
- Do not change API response fields, status-code behavior, config paths, user
  data paths, or install flows for enterprise-template conformity.
- Treat Docker as a future optional verification/development channel only if the
  branch owner explicitly asks for it.
- Keep smoke and e2e tests out of the default verification runner because they
  depend on external network, browsers, Jellyfin, Ollama, Gemini, or live scraper
  availability.
