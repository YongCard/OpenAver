"""Pure decision-core for the 8-source health canary (zero network, zero scraper deps).

DESIGN: every function here is a PURE function and operates on duck-typed inputs.
`search_result` is read for `.number` / `.title` / `.cover_url` only — we never
import the Video model, the requests library, or any scraper module. This keeps
the canary's judgement logic deterministic and unit-testable without a real
network (spec §US1). The purity is enforced by a static guard test in
`tests/unit/test_source_canary_logic.py`; do not pull in the requests library or
any scraper module here.

Shared by:
- `tests/unit/test_source_canary_logic.py` (deterministic unit tests, runs in CI)
- `tests/smoke/test_source_canary.py` (T3 live shell — feeds real results in)
"""

# Source grouping (plan-73b Canonical Decision 1):
#   Group A — smoke-side reachability probe distinguishes state-1 (down) vs
#             state-2 (200 but parser empty).
#   Group B — quorum-only (no probe); None -> skip, never false state-2 fail.
GROUP_A = {"javbus", "jav321", "heyzo", "d2pass", "dmm", "avsox"}
GROUP_B = {"javdb", "fc2"}

# Source classes for numbers_match canonical rules.
_CENSORED = {"javbus", "jav321", "javdb", "dmm"}


def _is_timeout(search_result) -> bool:
    """True if search_result signals a timeout (disconnect, not parser broken)."""
    return isinstance(search_result, TimeoutError)


def numbers_match(actual, expected, source) -> bool:
    """Canonical number comparison per source-class — NOT a plain string compare.

    Each source emits `.number` in a different shape; a plain `==` would falsely
    flag healthy results. Returns False (rather than raising) on None/empty input.
    Must never let genuinely different numbers through (e.g. SONE-205 vs SONE-206).
    """
    if not actual or not expected:
        return False

    a = str(actual).strip()
    e = str(expected).strip()
    if not a or not e:
        return False

    if source in _CENSORED:
        # upper() then drop hyphen/underscore separators.
        na = a.upper().replace("-", "").replace("_", "")
        ne = e.upper().replace("-", "").replace("_", "")
        return na == ne

    if source == "fc2":
        # Strip FC2-PPV- / FC2- prefix (case-insensitive) -> compare bare digits.
        return _strip_fc2(a) == _strip_fc2(e)

    if source == "heyzo":
        # Strip HEYZO- prefix; if both pure digits compare int() (absorbs leading
        # zeros), else fall back to string compare.
        sa = _strip_prefix(a, "HEYZO-")
        se = _strip_prefix(e, "HEYZO-")
        if sa.isdigit() and se.isdigit():
            return int(sa) == int(se)
        return sa.upper() == se.upper()

    if source == "d2pass":
        # Separators are semantically significant — exact string compare,
        # do NOT normalize '_' vs '-'.
        return a == e

    if source == "avsox":
        # upper() then exact compare.
        return a.upper() == e.upper()

    # Unknown source: safest is exact upper compare.
    return a.upper() == e.upper()


def _strip_prefix(value: str, prefix: str) -> str:
    if value.upper().startswith(prefix.upper()):
        return value[len(prefix):]
    return value


def _strip_fc2(value: str) -> str:
    v = _strip_prefix(value, "FC2-PPV-")
    if v == value:  # FC2-PPV- not present; try FC2-
        v = _strip_prefix(value, "FC2-")
    return v


def classify_one(search_result, probe_reachable, expected_number, source) -> str:
    """Classify one (source, number) probe into "pass" | "fail" | "skip".

    6-state table (plan-73b §技術方案, Codex P2-1):
      1. TimeoutError signal                                    -> skip
      2. Video + number match + title + cover                  -> pass
      3. Video but (mismatch | empty title | empty cover)       -> fail
      4. None + Group A + probe_reachable True                  -> fail
      5. None + Group A + probe_reachable False                 -> skip
      6. None + Group B (or probe_reachable None)               -> skip
    """
    # Row 1: timeout = pure disconnect, not a parser failure.
    if _is_timeout(search_result):
        return "skip"

    # Rows 2 & 3: a returned Video proves the site responded (all non-200 paths
    # return None across the 8 scrapers), so content errors are state-2 fails.
    if search_result is not None:
        number = getattr(search_result, "number", None)
        title = getattr(search_result, "title", None)
        cover_url = getattr(search_result, "cover_url", None)
        if numbers_match(number, expected_number, source) and title and cover_url:
            return "pass"
        return "fail"

    # Video is None below this point.
    # Row 4/5: Group A has a reachability probe to distinguish state-1 vs state-2.
    if source in GROUP_A and probe_reachable is not None:
        return "fail" if probe_reachable else "skip"

    # Row 6: Group B (no probe) or no probe signal -> defer to quorum.
    return "skip"


def quorum_verdict(results):
    """Aggregate per-number results into a source verdict + human-readable reason.

    Returns (verdict, reason) where verdict is "green" | "red" | "skip".
      - any "pass"               -> green (pass wins, even alongside fails)
      - 0 pass and >=1 "fail"    -> red
      - 0 pass and all "skip"    -> skip
    """
    results = list(results)
    n_pass = results.count("pass")
    n_fail = results.count("fail")
    n_skip = results.count("skip")

    if n_pass:
        return "green", f"{n_pass} number(s) healthy (pass wins over {n_fail} fail / {n_skip} skip)"
    if n_fail:
        return "red", f"0 healthy, {n_fail} returned bad/empty content (possible site/parser change)"
    return "skip", f"all {n_skip} number(s) skipped (unreachable / no probe)"
