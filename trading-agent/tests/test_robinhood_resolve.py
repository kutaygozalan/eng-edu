"""Tool resolution tests.

Robinhood does not publish its MCP tool surface and has changed it twice since
May. These tests assert the resolver copes with several plausible naming
conventions and, crucially, FAILS LOUDLY rather than binding the wrong tool.

Binding `cancel_order` to something that places orders is the failure mode worth
paying tests for.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tagent.brokers.base import BrokerError  # noqa: E402
from tagent.brokers.robinhood_mcp import (  # noqa: E402
    _num, _parse_ts, _rows, resolve_tools, score_tool, CAPABILITIES,
)
from tagent.mcp.client import ToolSpec  # noqa: E402


def tools(*names: str) -> list[ToolSpec]:
    return [ToolSpec(name=n, description="", input_schema={}) for n in names]


SNAKE = tools(
    "get_account", "get_positions", "get_quote", "place_order",
    "cancel_order", "get_orders",
)
PREFIXED = tools(
    "robinhood_get_account", "robinhood_get_positions", "robinhood_get_quote",
    "robinhood_place_order", "robinhood_cancel_order", "robinhood_get_orders",
    "robinhood_get_options", "robinhood_place_option_order",
)
TERSE = tools("accounts", "positions", "quotes", "orders", "order", "cancel")


@pytest.mark.parametrize("available", [SNAKE, PREFIXED])
def test_resolves_common_naming_schemes(available):
    bindings, missing = resolve_tools(available)
    assert missing == []
    assert bindings["get_account"] in {"get_account", "robinhood_get_account"}
    assert bindings["place_order"] in {"place_order", "robinhood_place_order"}
    assert bindings["cancel_order"] in {"cancel_order", "robinhood_cancel_order"}


def test_terse_naming_still_resolves():
    bindings, missing = resolve_tools(TERSE)
    assert missing == []
    assert bindings["place_order"] == "order"
    assert bindings["list_orders"] == "orders"


def test_options_detected_when_present():
    bindings, _ = resolve_tools(PREFIXED)
    assert bindings["place_option_order"] == "robinhood_place_option_order"


def test_options_absent_is_not_fatal():
    """The options rollout is staged; equities must still work without it."""
    bindings, missing = resolve_tools(SNAKE)
    assert missing == []
    assert "place_option_order" not in bindings


def test_missing_required_capability_is_reported():
    bindings, missing = resolve_tools(tools("get_account", "get_positions"))
    assert "place_order" in missing
    assert "get_quote" in missing


# ---------------------------------------------- the dangerous mis-bindings --

def test_place_order_never_binds_to_a_read_tool():
    bindings, _ = resolve_tools(PREFIXED)
    assert "get" not in bindings["place_order"].replace("robinhood_", "")


def test_place_order_never_binds_to_cancel():
    bindings, _ = resolve_tools(SNAKE)
    assert bindings["place_order"] != bindings["cancel_order"]


def test_equity_order_never_binds_to_an_options_tool():
    """Routing an equity order to an options endpoint would be catastrophic."""
    available = tools(
        "get_account", "get_positions", "get_quote", "get_orders",
        "cancel_order", "place_option_order", "place_equity_order",
    )
    bindings, missing = resolve_tools(available)
    assert missing == []
    assert bindings["place_order"] == "place_equity_order"


def test_crypto_tools_are_not_mistaken_for_equity():
    available = tools(
        "get_account", "get_positions", "get_quote", "get_orders", "cancel_order",
        "place_crypto_order", "place_order",
    )
    bindings, _ = resolve_tools(available)
    assert bindings["place_order"] == "place_order"


def test_positions_does_not_bind_to_option_positions():
    available = tools(
        "get_account", "get_option_positions", "get_positions", "get_quote",
        "place_order", "cancel_order", "get_orders",
    )
    bindings, _ = resolve_tools(available)
    assert bindings["get_positions"] == "get_positions"


# ------------------------------------------------------------- overrides ---

def test_override_pins_exact_name():
    bindings, _ = resolve_tools(SNAKE, overrides={"place_order": "cancel_order"})
    assert bindings["place_order"] == "cancel_order"   # operator's choice, honored


def test_override_for_absent_tool_fails_loudly():
    with pytest.raises(BrokerError, match="does not expose"):
        resolve_tools(SNAKE, overrides={"place_order": "nonexistent_tool"})


# --------------------------------------------------------------- parsing ---

def test_num_tries_multiple_field_names():
    assert _num({"buying_power": "1234.5"}, "cash", "buying_power") == 1234.5
    assert _num({"cash": None}, "cash", default=7.0) == 7.0
    assert _num({"cash": "not-a-number"}, "cash", default=3.0) == 3.0


def test_rows_unwraps_nested_and_bare_lists():
    assert _rows([{"symbol": "A"}]) == [{"symbol": "A"}]
    assert _rows({"positions": [{"symbol": "A"}]}, "positions") == [{"symbol": "A"}]
    assert _rows({"symbol": "A"}) == [{"symbol": "A"}]
    assert _rows({"nothing": 1}, "positions") == []


def test_missing_timestamp_reads_as_stale_not_fresh():
    """A quote we cannot date must fail the gate's staleness check, not pass it."""
    from datetime import datetime, timezone
    ts = _parse_ts(None)
    assert ts.year == 1970
    age = (datetime.now(timezone.utc) - ts).total_seconds()
    assert age > 120     # exceeds the default max_data_age_seconds


def test_iso_timestamp_parsed():
    assert _parse_ts("2026-09-15T14:30:00Z").year == 2026
    assert _parse_ts("2026-09-15T14:30:00+00:00").hour == 14


def test_capability_patterns_are_valid_regex():
    import re
    for cap in CAPABILITIES:
        for pat in cap.patterns + cap.negative:
            re.compile(pat)
