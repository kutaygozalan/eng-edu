"""Tests for published telemetry.

Two questions, and only two:

  1. Can this payload leak money or positions into a public repository?
  2. Can a broken agent produce a payload that reads as a quiet day?

Everything below is one of those. The first is why `redacted` is the default;
the second is why `validate()` refuses rather than shrugging.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tagent.config import AgentConfig, Config  # noqa: E402
from tagent.memory.store import Store  # noqa: E402
from tagent.telemetry import (  # noqa: E402
    FINANCIAL_KEYS, REQUIRED_KEYS, SCHEMA_VERSION, _financial_keys_in, collect,
    scrub, validate,
)


@pytest.fixture
def cfg():
    return Config(
        db_path=":memory:",
        agent=AgentConfig(universe=("AAPL", "MSFT"), setups=("wheel",)),
        dry_run=True,
    )


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "t.db")
    yield s
    s.close()


def seed(store):
    """A database with something in every corner telemetry reads."""
    did = store.record_decision(
        cycle_id="c1", symbol="NVDA", asset_class="equity", side="buy",
        quantity=4, order_type="limit", notional=812.50, setup_tag="wheel",
        confidence=0.7, thesis="cheap", gate_verdict="reject",
        gate_reasons=["symbol_exposure_exceeded", "spread_too_wide"],
        status="rejected",
    )
    store.record_decision(
        cycle_id="c2", symbol="NVDA", asset_class="equity", side="buy",
        quantity=1, order_type="limit", notional=200.0, setup_tag="wheel",
        confidence=0.6, thesis="again", gate_verdict="reject",
        gate_reasons=["spread_too_wide"], status="rejected",
    )
    store.open_lot(
        decision_id=did, symbol="NVDA", asset_class="equity", direction=1,
        quantity=4, entry_price=203.12, entry_ts="2026-08-01T14:00:00+00:00",
        max_loss=400.0,
    )
    store.record_equity(4210.55, 1200.0)
    store.log("error", "quote_failed", "NVDA: connection reset by peer")
    store.log("info", "cycle_complete", "cycle ab12: proposed=1 rejected=1")
    store.add_lesson(
        scope="process",
        text="Stop adding to NVDA after it has already lost $412.50 intraday.",
    )
    return did


# ------------------------------------------------------------------ scrub ---

def test_scrub_removes_dollar_amounts():
    assert "$412.50" not in scrub("down $412.50 today")
    assert "1,240" not in scrub("lost $1,240.50 on the day")


def test_scrub_removes_symbols_the_agent_has_traded():
    out = scrub("NVDA gapped down", frozenset({"NVDA"}))
    assert "NVDA" not in out and "<SYM>" in out


def test_scrub_is_case_insensitive_about_known_symbols():
    assert scrub("nvda gapped down", frozenset({"NVDA"})) == "<SYM> gapped down"


def test_scrub_catches_tickers_the_database_has_never_seen():
    """A symbol can reach a log line before it ever reaches a decision row."""
    out = scrub("quote for TSLA failed", frozenset())
    assert "TSLA" not in out


def test_scrub_removes_option_symbols_whole():
    out = scrub("leg AAPL260116C00150000 rejected")
    assert "AAPL" not in out and "00150000" not in out


def test_scrub_keeps_the_words_that_make_an_error_readable():
    """Redaction that destroys the diagnosis is not worth having."""
    out = scrub("HTTP 529 from the API: overloaded")
    assert "HTTP" in out and "API" in out and "overloaded" in out


def test_scrub_does_not_double_wrap_its_own_placeholders():
    assert "<<SYM>>" not in scrub("AAPL260116C00150000 expired")


def test_scrub_bounds_length():
    assert len(scrub("word " * 500)) <= 240


def test_scrub_passes_through_none():
    assert scrub(None) is None


# ---------------------------------------------------------------- collect ---

def test_default_payload_has_no_financial_keys_anywhere(cfg, store):
    seed(store)
    payload = collect(cfg, store)
    assert _financial_keys_in(payload) == []
    assert payload["redacted"] is True


def test_default_payload_mentions_no_symbol_it_has_traded(cfg, store):
    seed(store)
    blob = json.dumps(collect(cfg, store))
    assert "NVDA" not in blob
    # From the configured universe, which the agent has not traded yet.
    assert "MSFT" not in blob


def test_default_payload_carries_no_dollar_figure(cfg, store):
    seed(store)
    blob = json.dumps(collect(cfg, store))
    for leak in ("412.50", "812.50", "4210.55", "203.12"):
        assert leak not in blob


def test_lesson_text_survives_redaction_usefully(cfg, store):
    """The lesson still has to be readable, or publishing it is pointless."""
    seed(store)
    text = collect(cfg, store)["lessons"]["items"][0]["text"]
    assert "NVDA" not in text and "412.50" not in text
    assert "Stop adding to" in text and "intraday" in text


def test_operational_shape_is_present(cfg, store):
    seed(store)
    p = collect(cfg, store)
    assert p["open_lot_count"] == 1
    assert p["dry_run"] is True
    assert p["schema_version"] == SCHEMA_VERSION
    kinds = {e["kind"]: e["n"] for e in p["events_by_kind"]}
    assert kinds["quote_failed"] == 1 and kinds["cycle_complete"] == 1
    reasons = {r["reason"]: r["n"] for r in p["gate_rejections_by_reason"]}
    assert reasons == {"spread_too_wide": 2, "symbol_exposure_exceeded": 1}


def test_recent_errors_exclude_info_events_and_raw_data(cfg, store):
    seed(store)
    errors = collect(cfg, store)["recent_errors"]
    assert [e["kind"] for e in errors] == ["quote_failed"]
    # `data` holds raw model output and order payloads. It must never appear.
    assert all("data" not in e for e in errors)


def test_kill_switch_reason_is_scrubbed(cfg, store):
    store.engage_kill_switch("halted after NVDA lost $900")
    p = collect(cfg, store)
    assert p["kill_switch"]["engaged"] is True
    assert "NVDA" not in p["kill_switch"]["reason"]
    assert "900" not in p["kill_switch"]["reason"]


def test_include_financials_adds_equity_and_pnl(cfg, store):
    seed(store)
    p = collect(cfg, store, include_financials=True)
    assert p["redacted"] is False
    assert p["financials"]["peak_equity"] == pytest.approx(4210.55)
    assert "realized_pnl" in p["financials"]


def test_financials_are_absent_not_filtered(cfg, store):
    """Structural, not cosmetic: the key does not exist by default."""
    seed(store)
    assert "financials" not in collect(cfg, store)


def test_setup_expectancy_is_a_percentage_never_a_dollar_total(cfg, store):
    from tagent.memory.review import SetupStat

    store.upsert_setup_stats([SetupStat(
        setup_tag="wheel", regime_tag="*", n=40, wins=25,
        observed_mean_pct=0.03, observed_sd_pct=0.1, posterior_mean_pct=0.01,
        posterior_se_pct=0.005, total_pnl=1830.25, blocked=False,
    )])
    setup = collect(cfg, store)["setups"][0]
    assert setup["posterior_mean_pct"] == pytest.approx(0.01)
    assert "total_pnl" not in setup
    assert "1830" not in json.dumps(setup)


def test_every_required_key_is_actually_produced(cfg, store):
    assert REQUIRED_KEYS <= set(collect(cfg, store))


# --------------------------------------------------------------- validate ---

def good(cfg, store):
    return collect(cfg, store)


def test_validate_accepts_a_real_redacted_payload(cfg, store):
    seed(store)
    assert validate(good(cfg, store)) == []


def test_validate_rejects_an_empty_object():
    assert validate({}) != []


def test_validate_rejects_a_non_object():
    assert validate([1, 2, 3]) == ["payload is not a JSON object"]


def test_validate_rejects_a_missing_key(cfg, store):
    p = good(cfg, store)
    del p["open_lot_count"]
    assert any("open_lot_count" in m for m in validate(p))


def test_validate_rejects_financial_keys_when_not_allowed(cfg, store):
    p = good(cfg, store)
    p["financials"] = {"equity": 4210.55}
    problems = validate(p)
    assert any("financial keys present" in m for m in problems)


def test_validate_finds_financial_keys_nested_inside_lists(cfg, store):
    """A leak added to a list element is the one a shallow check would miss."""
    p = good(cfg, store)
    p["setups"].append({"setup_tag": "wheel", "total_pnl": 1830.25})
    assert any("total_pnl" in m for m in validate(p))


def test_validate_allows_financials_when_explicitly_permitted(cfg, store):
    p = collect(cfg, store, include_financials=True)
    assert validate(p, allow_financials=True) == []


def test_validate_rejects_unredacted_payload_without_permission(cfg, store):
    p = collect(cfg, store, include_financials=True)
    assert validate(p) != []


def test_validate_rejects_a_wrong_schema_version(cfg, store):
    p = good(cfg, store)
    p["schema_version"] = 99
    assert any("schema_version" in m for m in validate(p))


def test_validate_rejects_a_payload_with_no_commit(cfg, store):
    """A status file whose job is to name the running commit needs the sha."""
    p = good(cfg, store)
    p["git"] = {"sha": None, "branch": None, "dirty": None}
    assert any("git.sha" in m for m in validate(p))


def test_per_setup_dollar_totals_are_caught_by_the_nested_scan(cfg, store):
    """`financials.by_setup[i].total_pnl` is two levels down inside a list."""
    p = collect(cfg, store, include_financials=True)
    p["financials"]["by_setup"] = [{"setup_tag": "wheel", "total_pnl": 1830.25}]
    assert any("by_setup[0].total_pnl" in m for m in validate(p))
    assert "total_pnl" in FINANCIAL_KEYS


# ------------------------------------------------------ the --validate CLI ---

def run_validator(path, *args):
    return subprocess.run(
        [sys.executable, "-m", "tagent.telemetry", "--validate", str(path), *args],
        capture_output=True, text=True,
        cwd=str(Path(__file__).resolve().parents[1] / "src"),
    )


def test_cli_accepts_a_good_file(tmp_path, cfg, store):
    seed(store)
    f = tmp_path / "t.json"
    f.write_text(json.dumps(collect(cfg, store)))
    assert run_validator(f).returncode == 0


def test_cli_rejects_a_truncated_file(tmp_path, cfg, store):
    """The failure this exists for: half a file still reads as 'all quiet'."""
    seed(store)
    full = json.dumps(collect(cfg, store), indent=2)
    f = tmp_path / "t.json"
    f.write_text(full[: len(full) // 2])
    r = run_validator(f)
    assert r.returncode == 1 and "not valid JSON" in r.stderr


def test_cli_rejects_an_empty_file(tmp_path):
    f = tmp_path / "t.json"
    f.write_text("")
    r = run_validator(f)
    assert r.returncode == 1 and "truncated" in r.stderr


def test_cli_rejects_a_short_but_valid_json_file(tmp_path):
    """`{}` parses cleanly and says nothing. That is the dangerous case."""
    f = tmp_path / "t.json"
    f.write_text("{}")
    assert run_validator(f).returncode == 1


def test_cli_rejects_a_missing_file(tmp_path):
    assert run_validator(tmp_path / "nope.json").returncode == 1


def test_cli_refuses_financials_without_the_flag(tmp_path, cfg, store):
    f = tmp_path / "t.json"
    f.write_text(json.dumps(collect(cfg, store, include_financials=True)))
    assert run_validator(f).returncode == 1
    assert run_validator(f, "--allow-financials").returncode == 0
