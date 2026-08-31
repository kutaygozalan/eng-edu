"""Operational telemetry, redacted by default.

This exists so a future session with no shell access can answer "is the agent
alive, and is it learning anything?" by reading a file out of git.

That destination is the whole reason for the redaction. The status branch lives
in a fork of a public repository, and a fork of a public repo is public — there
is no private-fork-of-a-public-repo on GitHub. So the default payload carries no
dollar amounts, no equity, no position sizes and no symbols. What it carries is
the operational skeleton: which commit is running, whether the kill switch is
latched, whether orders are real, how many lots are open, what the gate keeps
rejecting, what broke, and what the agent believes it has learned.

`--include-financials` unlocks P&L and equity. Nothing calls it by default, and
`deploy/publish-status.sh` refuses to publish its output unless the operator has
explicitly declared the repository private.

Redaction is layered on purpose:

  1. Structural - financial values are never collected in the first place
     unless the flag is set. Not filtered afterwards: absent.
  2. Textual    - free text written by the LLM or by an operator (lesson text,
     kill-switch reasons, exception messages) is scrubbed of currency amounts
     and symbols before it goes in.
  3. Validation - `validate()` re-checks the finished payload for forbidden
     keys, so a future field added carelessly fails the publish rather than
     leaking quietly.

Layer 2 is the only one that is heuristic, and it is the one to distrust: it
scrubs what the agent has actually traded (an exact list from the database)
plus anything shaped like a ticker or a price. Treat it as a safety net under
layers 1 and 3, not as the guarantee.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

# Keys that may only appear when financials are explicitly requested. Checked
# recursively by validate(), against every dict in the payload.
FINANCIAL_KEYS = frozenset({
    "buying_power", "cash", "deployed_pct", "drawdown_pct", "entry_price",
    "equity", "equity_end", "equity_start", "fees", "financials", "limit_price",
    "max_loss", "notional", "peak_equity", "pnl", "pnl_pct", "quantity",
    "realized_pnl", "settled_cash", "slippage", "total_pnl",
})

# Keys every payload must carry. A file missing any of these is not "an agent
# with nothing to report", it is a broken or truncated write.
REQUIRED_KEYS = frozenset({
    "schema_version", "generated_at", "redacted", "git", "kill_switch",
    "dry_run", "open_lot_count", "events_by_kind", "gate_rejections_by_reason",
    "recent_errors", "lessons", "setups", "calibration",
})

MAX_TEXT = 240

_MONEY = re.compile(r"[$€£]\s?-?\d[\d,]*(?:\.\d+)?")
# OCC-style option symbols: AAPL260116C00150000. Matched before bare tickers,
# which would otherwise nibble the leading root and leave the strike behind.
_OPTION = re.compile(r"\b[A-Z]{1,6}\d{6}[CP]\d{8}\b")
_TICKERISH = re.compile(r"\b[A-Z]{2,5}\b")

# Uppercase tokens that appear in this system's own error messages and are not
# tickers. Redacting them would be harmless but makes the output unreadable,
# which defeats the point of publishing it.
# "SYM" and "OPT" are in here so the placeholders this function writes are not
# themselves re-scrubbed into "<<SYM>>" on the ticker pass that follows.
_NOT_TICKERS = frozenset({
    "SYM", "OPT",
    "ANTHROPIC", "API", "AUTH", "CPU", "CSV", "DNS", "DTE", "EOF", "ERROR",
    "EST", "EDT", "ET", "FAIL", "FIFO", "GET", "HTTP", "HTTPS", "ID", "IO",
    "JSON", "KEY", "MCP", "NYSE", "OAUTH", "OK", "OS", "PATCH", "POST", "PUT",
    "RAM", "SDK", "SQL", "SSL", "TLS", "URL", "US", "UTC", "VM", "WAL", "WARN",
})


def scrub(text: str | None, symbols: frozenset[str] = frozenset()) -> str | None:
    """Strip money and symbols out of free text.

    `symbols` is the exact set the agent has traded, which is what actually
    matters: a ticker the agent has never touched cannot reveal a position.
    The regex pass afterwards is belt-and-braces for symbols that reached a log
    line before they ever reached the database.
    """
    if text is None:
        return None
    out = _MONEY.sub("$<redacted>", str(text))
    for sym in sorted(symbols, key=len, reverse=True):
        if sym:
            out = re.sub(rf"\b{re.escape(sym)}\b", "<SYM>", out, flags=re.IGNORECASE)
    out = _OPTION.sub("<OPT>", out)
    out = _TICKERISH.sub(
        lambda m: m.group(0) if m.group(0) in _NOT_TICKERS else "<SYM>", out
    )
    if len(out) > MAX_TEXT:
        out = out[: MAX_TEXT - 1] + "…"
    return out


def git_info(repo: Path | None = None) -> dict[str, Any]:
    """Which commit is actually running, as seen from the installed package.

    Deliberately derived from the source tree rather than from a build-time
    constant: an editable install means a `git checkout` changes the running
    code, and telemetry that reported a stale baked-in sha would be worse than
    reporting nothing.
    """
    root = Path(repo) if repo else Path(__file__).resolve().parent
    info: dict[str, Any] = {"sha": None, "branch": None, "dirty": None}

    def run(*args: str) -> str | None:
        try:
            out = subprocess.run(
                ["git", "-C", str(root), *args],
                capture_output=True, text=True, timeout=10, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout.strip() if out.returncode == 0 else None

    info["sha"] = run("rev-parse", "HEAD")
    info["branch"] = run("rev-parse", "--abbrev-ref", "HEAD")
    status = run("status", "--porcelain", "--untracked-files=no")
    if status is not None:
        info["dirty"] = bool(status)
    return info


def collect(cfg, store, *, include_financials: bool = False,
            window_days: int = 7, now: datetime | None = None) -> dict[str, Any]:
    """Build the telemetry payload.

    Financial values are not gathered at all unless asked for, so a bug in the
    serializer cannot leak what was never read.
    """
    from .memory import review as R

    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=window_days)).isoformat()
    symbols = (
        frozenset() if include_financials
        else store.known_symbols() | frozenset(cfg.agent.universe)
    )

    def clean(text):
        return text if include_financials else scrub(text, symbols)

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now.isoformat(),
        "redacted": not include_financials,
        "window_days": window_days,
        "git": git_info(),
        "kill_switch": {
            "engaged": store.kill_switch,
            "reason": clean(store.get_state("kill_switch_reason")),
        },
        "dry_run": bool(cfg.dry_run),
        "broker_kind": cfg.broker.kind,
        "model": cfg.agent.model,
        "last_review": store.get_state("last_review"),
        "open_lot_count": store.open_lot_count(),
        "closed_trade_count": len(store.closed_trades()),
        "events_by_kind": [
            {"level": e["level"], "kind": e["kind"], "n": e["n"]}
            for e in store.events_by_kind(since)
        ],
        "gate_rejections_by_reason": [
            {"reason": reason, "n": n}
            for reason, n in R.rejection_summary(store.rejections(since), top_n=20)
        ],
        "recent_errors": [
            {
                "ts": e["ts"], "level": e["level"], "kind": e["kind"],
                "message": clean(e["message"]),
            }
            for e in store.recent_errors(10)
        ],
        "lessons": {
            "active_count": len(store.active_lessons(500)),
            "items": [
                {
                    "scope": l["scope"],
                    "setup_tag": l["setup_tag"],
                    "evidence_n": l["evidence_n"],
                    "text": clean(l["text"]),
                }
                for l in store.active_lessons(50)
            ],
        },
        # Expectancy is a percentage of capital at risk, not a dollar figure:
        # it says whether the agent has found an edge without saying how much
        # money is behind it. total_pnl from the same table is omitted.
        "setups": [
            {
                "setup_tag": s["setup_tag"],
                "n": s["n"],
                "wins": s["wins"],
                "posterior_mean_pct": s["posterior_mean_pct"],
                "posterior_se_pct": s["posterior_se_pct"],
                "blocked": bool(s["blocked"]),
            }
            for s in store.setup_stats()
        ],
        "calibration": [
            {
                "bucket": c["bucket"], "n": c["n"], "wins": c["wins"],
                "mean_stated": c["mean_stated"], "realized_rate": c["realized_rate"],
            }
            for c in sorted(store.calibration(), key=lambda c: c["bucket"])
        ],
    }

    if include_financials:
        closed = store.closed_trades()
        payload["financials"] = {
            "peak_equity": store.peak_equity(),
            "realized_pnl": sum(float(c["pnl"]) for c in closed),
            "wins": sum(1 for c in closed if c["was_win"]),
            "losses": sum(1 for c in closed if not c["was_win"]),
            "by_setup": [
                {"setup_tag": s["setup_tag"], "n": s["n"], "total_pnl": s["total_pnl"]}
                for s in store.setup_stats()
            ],
        }
    return payload


def _financial_keys_in(node: Any, path: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        for k, v in node.items():
            here = f"{path}.{k}" if path else k
            if k in FINANCIAL_KEYS:
                found.append(here)
            found.extend(_financial_keys_in(v, here))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            found.extend(_financial_keys_in(v, f"{path}[{i}]"))
    return found


def validate(payload: Any, *, allow_financials: bool = False) -> list[str]:
    """Return the reasons this payload must not be published; empty means fine.

    The failure this guards against is not a leak but a lie: a truncated or
    half-written file still parses as "no errors, no open lots, nothing to
    report", which is indistinguishable from a healthy quiet day and is exactly
    the reading a future session would take away while the agent is broken.
    """
    problems: list[str] = []
    if not isinstance(payload, dict):
        return ["payload is not a JSON object"]

    missing = sorted(REQUIRED_KEYS - set(payload))
    if missing:
        problems.append(f"missing required keys: {', '.join(missing)}")

    if payload.get("schema_version") != SCHEMA_VERSION:
        problems.append(
            f"schema_version is {payload.get('schema_version')!r}, "
            f"expected {SCHEMA_VERSION}"
        )

    if not payload.get("generated_at"):
        problems.append("generated_at is empty")

    for key in ("events_by_kind", "recent_errors", "setups", "calibration",
                "gate_rejections_by_reason"):
        if key in payload and not isinstance(payload[key], list):
            problems.append(f"{key} is not a list")

    if "git" in payload and not (payload["git"] or {}).get("sha"):
        # A published status file whose only job is to say which commit is
        # running is worthless without the sha.
        problems.append("git.sha is missing; cannot say which commit is running")

    redacted = payload.get("redacted")
    if not isinstance(redacted, bool):
        problems.append("redacted flag is missing or not a boolean")
    elif redacted is False and not allow_financials:
        problems.append(
            "payload is marked unredacted but financial disclosure was not allowed"
        )

    if not allow_financials:
        leaks = _financial_keys_in(payload)
        if leaks:
            problems.append(f"financial keys present: {', '.join(sorted(leaks))}")

    return problems


def main(argv: list[str] | None = None) -> int:
    """`python -m tagent.telemetry --validate FILE`.

    Lives here rather than in the publish script so the rules above are unit
    tested instead of buried in an untested shell heredoc.
    """
    p = argparse.ArgumentParser(prog="tagent.telemetry")
    p.add_argument("--validate", metavar="FILE", required=True)
    p.add_argument("--allow-financials", action="store_true")
    p.add_argument("--min-bytes", type=int, default=200)
    args = p.parse_args(argv)

    path = Path(args.validate)
    if not path.exists():
        print(f"telemetry file does not exist: {path}", file=sys.stderr)
        return 1
    raw = path.read_bytes()
    if len(raw) < args.min_bytes:
        print(
            f"telemetry file is {len(raw)} bytes, under the {args.min_bytes}-byte "
            "floor; treating as truncated rather than quiet",
            file=sys.stderr,
        )
        return 1
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"telemetry file is not valid JSON: {exc}", file=sys.stderr)
        return 1

    problems = validate(payload, allow_financials=args.allow_financials)
    for problem in problems:
        print(f"REFUSED: {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
