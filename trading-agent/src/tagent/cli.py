"""Command line interface.

Subcommands are the operational surface:

    tagent auth       one-time OAuth, headless-friendly
    tagent discover   dump the broker's MCP tool surface (pin names from this)
    tagent doctor     check config coherence BEFORE a trading day
    tagent health     auth + broker reachability (run at 08:00 from cron)
    tagent cycle      one trading cycle (run every 20 minutes)
    tagent review     nightly journal (run after the close)
    tagent status     what the agent knows and has done
    tagent kill       engage the kill switch
    tagent resume     release it (deliberately manual)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import clock, config as cfgmod
from .brokers.base import AuthExpired, BrokerError
from .memory.store import Store


def _load(args) -> cfgmod.Config:
    path = Path(args.config)
    if not path.exists():
        sys.exit(f"config not found: {path}\nCopy config/config.example.yaml first.")
    return cfgmod.load(path)


def _store(cfg: cfgmod.Config) -> Store:
    return Store(cfg.db_path)


def _broker(cfg: cfgmod.Config):
    kind = cfg.broker.kind
    if kind == "robinhood_mcp":
        from .brokers.robinhood_mcp import RobinhoodMCPBroker
        from .mcp.client import MCPClient
        from .mcp.tokens import EncryptedFileTokenStore

        tokens = EncryptedFileTokenStore(cfg.broker.token_file)
        client = MCPClient(cfg.broker.mcp_url, tokens, refresher=_make_refresher(cfg))
        return RobinhoodMCPBroker(client)
    if kind == "paper":
        sys.exit("the paper broker is not implemented yet; use robinhood_mcp")
    sys.exit(f"unknown broker kind: {kind}")


def _make_refresher(cfg: cfgmod.Config):
    from .mcp import oauth

    def refresh(tokens):
        meta = oauth.discover(cfg.broker.mcp_url)
        client_id = _read_client_id(cfg)
        return oauth.refresh(meta.token_endpoint, tokens.refresh_token, client_id)

    return refresh


def _client_id_path(cfg: cfgmod.Config) -> Path:
    return Path(cfg.broker.token_file).expanduser().with_suffix(".client.json")


def _read_client_id(cfg: cfgmod.Config) -> str:
    p = _client_id_path(cfg)
    if not p.exists():
        sys.exit(f"no registered client at {p}. Run `tagent auth` first.")
    return json.loads(p.read_text())["client_id"]


# ---------------------------------------------------------------- commands --

def cmd_auth(args) -> int:
    """Headless OAuth. No browser is needed on this machine."""
    from .mcp import oauth
    from .mcp.tokens import EncryptedFileTokenStore

    cfg = _load(args)
    print(f"Discovering OAuth metadata for {cfg.broker.mcp_url} ...")
    meta = oauth.discover(cfg.broker.mcp_url)
    print(f"  authorize: {meta.authorization_endpoint}")
    print(f"  token:     {meta.token_endpoint}")

    cid_path = _client_id_path(cfg)
    if cid_path.exists() and not args.reregister:
        client_id = json.loads(cid_path.read_text())["client_id"]
        print(f"  reusing registered client {client_id}")
    else:
        client_id, secret = oauth.register_client(meta, client_name="tagent")
        cid_path.parent.mkdir(parents=True, exist_ok=True)
        cid_path.write_text(json.dumps({"client_id": client_id, "client_secret": secret}))
        cid_path.chmod(0o600)
        print(f"  registered client {client_id}")

    pending = oauth.begin(meta, client_id)
    print(
        "\n" + "=" * 72
        + "\n1. Open this URL in a browser on ANY machine and approve access:\n\n"
        f"{pending.authorize_url}\n\n"
        "2. Your browser will land on a localhost page that fails to load.\n"
        "   That is expected - nothing is listening there. Copy the FULL URL\n"
        "   from the address bar and paste it below.\n"
        + "=" * 72
    )
    redirect = input("\nRedirect URL: ").strip()

    code = oauth.extract_code(redirect, pending.state)
    tokens = oauth.exchange(pending, code)

    store = EncryptedFileTokenStore(cfg.broker.token_file)
    store.save(tokens)
    refreshable = (
        "refreshable" if tokens.refresh_token
        else "NO refresh token - you will need to re-run this periodically"
    )
    print(
        f"\nAuthorized. Token stored encrypted at {cfg.broker.token_file}\n"
        f"Expires in {tokens.seconds_remaining / 3600:.1f}h ({refreshable})"
    )
    return 0


def cmd_discover(args) -> int:
    """Print the broker's actual MCP tool surface."""
    cfg = _load(args)
    broker = _broker(cfg)
    try:
        tools = broker._c.list_tools()
    except AuthExpired as exc:
        print(f"not authorized: {exc}", file=sys.stderr)
        return 2

    print(f"{len(tools)} tools exposed:\n")
    for t in sorted(tools, key=lambda t: t.name):
        print(f"  {t.summary()}")

    print("\nResolved capability bindings:")
    try:
        for cap, tool in sorted(broker.bind().items()):
            print(f"  {cap:<22} -> {tool}")
        print(f"\noptions supported: {broker.supports_options()}")
    except BrokerError as exc:
        print(f"  RESOLUTION FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_doctor(args) -> int:
    """Check that the configuration can actually place a trade.

    Exists because the most demoralizing failure mode is a system that runs
    perfectly for a week and never trades, because the position limit cannot
    afford one unit of anything in the universe.
    """
    cfg = _load(args)
    problems: list[str] = []
    warnings: list[str] = []

    print(f"broker:   {cfg.broker.kind} (dry_run={cfg.dry_run})")
    print(f"universe: {', '.join(cfg.agent.universe) or '(unrestricted)'}")
    print(f"setups:   {', '.join(cfg.agent.setups) or '(none)'}")

    if not cfg.agent.setups:
        problems.append("no setups configured; every proposal will be unattributable")
    if not cfg.agent.universe:
        warnings.append("universe is unrestricted; the gate cannot enforce scope")

    equity = args.equity
    budget = equity * cfg.limits.max_position_pct
    print(f"\nAt ${equity:,.0f} equity, {cfg.limits.max_position_pct:.1%} per position "
          f"= ${budget:,.2f} max loss per trade")

    OPTION_MIN_LOSS = 70.0   # a $1-wide vertical, typical credit
    if any(s for s in cfg.agent.setups if "spread" in s or "condor" in s or "option" in s):
        if budget < OPTION_MIN_LOSS:
            problems.append(
                f"options setups are configured, but ${budget:,.2f} cannot fund one "
                f"contract of the cheapest defined-risk spread (~${OPTION_MIN_LOSS:.0f} "
                "max loss). Every options proposal will be rejected. Either trade "
                "equities until the account is larger, or raise max_position_pct "
                "(which concentrates risk)."
            )
        elif budget < OPTION_MIN_LOSS * 3:
            warnings.append(
                f"${budget:,.2f} funds only "
                f"{int(budget // OPTION_MIN_LOSS)} contract(s); position sizing will "
                "be lumpy and diversification poor"
            )

    daily_risk = cfg.limits.max_trades_per_day * cfg.limits.max_position_pct
    print(f"Worst case if every trade hits its max loss: {daily_risk:.1%} of equity/day")
    if daily_risk > cfg.limits.daily_loss_limit_pct * 3:
        warnings.append(
            f"{cfg.limits.max_trades_per_day} trades x "
            f"{cfg.limits.max_position_pct:.1%} = {daily_risk:.1%} potential daily "
            f"loss, far above the {cfg.limits.daily_loss_limit_pct:.1%} daily limit. "
            "The limit will halt trading most days it is tested."
        )

    if cfg.limits.max_position_pct > cfg.limits.max_symbol_pct:
        problems.append("max_position_pct exceeds max_symbol_pct; no trade can pass both")
    if cfg.limits.max_symbol_pct > cfg.limits.max_deployed_pct:
        warnings.append("max_symbol_pct exceeds max_deployed_pct")
    if cfg.limits.min_dte < 7:
        warnings.append(
            f"min_dte={cfg.limits.min_dte} permits near-expiry options; retail data "
            "on short-dated options is uniformly bad"
        )
    if not cfg.limits.require_defined_risk:
        warnings.append("require_defined_risk is off; undefined-risk structures allowed")

    print()
    for w in warnings:
        print(f"  WARN  {w}")
    for p in problems:
        print(f"  FAIL  {p}")
    if not problems and not warnings:
        print("  all checks passed")
    print()
    return 1 if problems else 0


def cmd_health(args) -> int:
    cfg = _load(args)
    store = _store(cfg)
    broker = _broker(cfg)
    try:
        broker.health_check()
    except AuthExpired as exc:
        store.log("critical", "auth_expired", str(exc))
        print(f"AUTH EXPIRED: {exc}", file=sys.stderr)
        print("Run `tagent auth` before the open.", file=sys.stderr)
        return 2
    except BrokerError as exc:
        store.log("error", "health_failed", str(exc))
        print(f"BROKER ERROR: {exc}", file=sys.stderr)
        return 1

    acct = broker.account()
    print(f"broker ok: equity ${acct.equity:,.2f}, "
          f"settled ${acct.settled_cash:,.2f}, {len(acct.positions)} positions")
    if store.kill_switch:
        print(f"KILL SWITCH ENGAGED: {store.get_state('kill_switch_reason')}")
        return 1
    return 0


def cmd_cycle(args) -> int:
    from .agent.loop import run_cycle

    cfg = _load(args)
    store = _store(cfg)
    try:
        broker = _broker(cfg)
        report = run_cycle(cfg, store, broker)
    except AuthExpired as exc:
        store.log("critical", "auth_expired", str(exc))
        print(f"AUTH EXPIRED: {exc}", file=sys.stderr)
        return 2
    print(report.summary())
    for e in report.errors:
        print(f"  error: {e}", file=sys.stderr)
    return 1 if report.errors else 0


def cmd_review(args) -> int:
    from .memory import review as R

    cfg = _load(args)
    store = _store(cfg)

    rows = store.closed_trades()
    if not rows:
        print("no closed trades yet; nothing to review")
        return 0

    stats = R.compute_setup_stats(rows)
    store.upsert_setup_stats(stats)
    store.upsert_calibration(R.compute_calibration(rows))

    since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    blocked = [s for s in stats if s.blocked and s.regime_tag == "*"]

    print(f"reviewed {len(rows)} closed trades")
    for s in sorted(stats, key=lambda s: -s.n):
        if s.regime_tag != "*":
            continue
        flag = " BLOCKED" if s.blocked else ("" if s.actionable else " (thin)")
        print(f"  {s.setup_tag:<24} n={s.n:<4} "
              f"posterior {s.posterior_mean_pct:+.2%}{flag}")

    for cal in R.compute_calibration(rows):
        if cal.n >= 5 and abs(cal.gap) > 0.15:
            direction = "over" if cal.gap > 0 else "under"
            print(f"  calibration: {direction}confident at {cal.bucket} "
                  f"(stated {cal.mean_stated:.0%}, realized {cal.realized_rate:.0%})")

    if blocked:
        print(f"  blocked setups: {', '.join(s.setup_tag for s in blocked)}")

    # Retire lessons the evidence no longer supports.
    now = datetime.now(timezone.utc)
    for lesson in store.active_lessons(200):
        retire, why = R.should_retire(lesson, stats, now)
        if retire:
            store.retire_lesson(lesson["id"], why)
            print(f"  retired lesson {lesson['id']}: {why}")

    if not args.no_journal:
        from .agent.journal import write_journal

        equity_rows = store.closed_trades(since=since)
        packet = R.ReviewPacket(
            as_of=now.isoformat(),
            closed_trades=equity_rows,
            setup_stats=stats,
            calibration=R.compute_calibration(rows),
            top_rejections=R.rejection_summary(store.rejections(since)),
            active_lessons=store.active_lessons(50),
            equity_start=store.start_of_day_equity(since) or 0.0,
            equity_end=store.peak_equity(),
        )
        jr = write_journal(
            store=store, model=cfg.agent.model, api_key=cfg.anthropic_api_key,
            packet=packet, stats=stats,
        )
        if jr.error:
            print(f"  journal failed: {jr.error}")
        else:
            print(f"\n  {jr.summary}")
            print(f"  lessons: {jr.written} written, {jr.refused} refused, "
                  f"{jr.retired} retired")
            for r in jr.refusals:
                print(f"    refused: {r}")

    store.set_state("last_review", now.isoformat())
    store.log("info", "review_complete", f"{len(rows)} trades, {len(stats)} setups")
    return 0


def cmd_status(args) -> int:
    cfg = _load(args)
    store = _store(cfg)

    print(f"kill switch: {'ENGAGED - ' + (store.get_state('kill_switch_reason') or '') if store.kill_switch else 'off'}")
    print(f"last review: {store.get_state('last_review') or 'never'}")
    print(f"peak equity: ${store.peak_equity():,.2f}")

    closed = store.closed_trades()
    print(f"\nclosed trades: {len(closed)}")
    if closed:
        wins = sum(1 for c in closed if c["was_win"])
        pnl = sum(float(c["pnl"]) for c in closed)
        print(f"  win rate {wins / len(closed):.1%}, total P&L ${pnl:+,.2f}")

    lessons = store.active_lessons(100)
    print(f"\nactive lessons: {len(lessons)}")
    for l in lessons[:15]:
        print(f"  [{l['scope']}] {l['text']}")

    print("\nrecent events:")
    for e in store.recent_events(12):
        print(f"  {e['ts'][:19]} {e['level']:<8} {e['kind']:<20} {e['message'][:80]}")
    return 0


def cmd_kill(args) -> int:
    cfg = _load(args)
    store = _store(cfg)
    store.engage_kill_switch(args.reason)
    print(f"kill switch engaged: {args.reason}")
    print("New entries are blocked. Closing orders still permitted.")
    return 0


def cmd_resume(args) -> int:
    cfg = _load(args)
    store = _store(cfg)
    if not store.kill_switch:
        print("kill switch is not engaged")
        return 0
    reason = store.get_state("kill_switch_reason") or "unknown"
    print(f"kill switch was engaged because: {reason}")
    if input("Type 'resume trading' to confirm: ").strip() != "resume trading":
        print("aborted")
        return 1
    store.release_kill_switch()
    print("kill switch released")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="tagent", description=__doc__)
    p.add_argument("--config", default="config.yaml")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("auth", help="one-time OAuth authorization")
    a.add_argument("--reregister", action="store_true")
    a.set_defaults(fn=cmd_auth)

    sub.add_parser("discover", help="list broker MCP tools").set_defaults(fn=cmd_discover)

    d = sub.add_parser("doctor", help="check config coherence")
    d.add_argument("--equity", type=float, default=2000.0)
    d.set_defaults(fn=cmd_doctor)

    sub.add_parser("health", help="auth + broker check").set_defaults(fn=cmd_health)
    sub.add_parser("cycle", help="one trading cycle").set_defaults(fn=cmd_cycle)
    rv = sub.add_parser("review", help="nightly journal")
    rv.add_argument("--no-journal", action="store_true",
                    help="statistics only; skip the LLM journal step")
    rv.set_defaults(fn=cmd_review)
    sub.add_parser("status", help="what the agent knows").set_defaults(fn=cmd_status)

    k = sub.add_parser("kill", help="engage the kill switch")
    k.add_argument("reason")
    k.set_defaults(fn=cmd_kill)

    sub.add_parser("resume", help="release the kill switch").set_defaults(fn=cmd_resume)

    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except BrokenPipeError:
        # Someone piped us into `head`. Not a failure; exiting non-zero here
        # would fire a spurious alert from the cron wrapper.
        try:
            sys.stdout.close()
        except Exception:
            pass
        return 0
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
