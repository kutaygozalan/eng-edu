"""Tests for `tagent order` - the one command that places a trade by hand.

It exists to answer "does a real order actually reach the broker" before
anyone trusts the agent to do it unattended. Because a human runs it and it
bypasses the risk gate on purpose, the guards it DOES keep matter: dry_run, the
kill switch, and a typed confirmation.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tagent.cli import main  # noqa: E402
from tagent.memory.store import Store  # noqa: E402

CONFIG = """
db_path: {db}
dry_run: {dry_run}
broker:
  kind: paper
  paper_state_file: {state}
  paper_starting_cash: 2000.0
  paper_base_prices: {{AAPL: 200.0, F: 11.0}}
agent:
  universe: [AAPL, F]
  setups: [wheel]
"""


@pytest.fixture
def env(tmp_path):
    def build(dry_run="false"):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(CONFIG.format(
            db=tmp_path / "t.db", state=tmp_path / "state.json", dry_run=dry_run,
        ))
        return ["--config", str(cfg)]
    build.db = tmp_path / "t.db"
    return build


def test_dry_run_sends_nothing(env, capsys):
    rc = main(env(dry_run="true") + ["order", "F", "--dollars", "50", "--yes"])
    assert rc == 0
    assert "nothing was sent" in capsys.readouterr().out
    assert not env.db.exists() or Store(env.db).recent_events(5) == []


def test_a_real_order_reaches_the_broker_and_the_ledger(env):
    assert main(env() + ["order", "F", "--dollars", "50", "--yes"]) == 0

    store = Store(env.db)
    decisions = store._conn.execute("SELECT * FROM decisions").fetchall()
    assert len(decisions) == 1
    row = dict(decisions[0])
    assert row["symbol"] == "F"
    assert row["status"] == "submitted", "must be submitted so reconcile picks up the fill"
    assert row["broker_order_id"], "without a broker id the fill can never be booked"
    store.close()


def test_a_manual_order_is_tagged_so_it_cannot_contaminate_a_setup(env):
    """Its outcome still lands in the statistics - under `manual`, not under a
    strategy whose measured expectancy would otherwise absorb it."""
    main(env() + ["order", "F", "--dollars", "50", "--yes"])
    store = Store(env.db)
    row = dict(store._conn.execute("SELECT * FROM decisions").fetchone())
    assert row["setup_tag"] == "manual"
    assert row["gate_verdict"] == "manual"
    store.close()


def test_dollars_below_one_share_is_a_clear_error(env, capsys):
    rc = main(env() + ["order", "AAPL", "--dollars", "10", "--yes"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "does not buy one share" in err
    assert "200" in err, "the error has to say what it would actually cost"


def test_the_kill_switch_blocks_a_new_entry(env, capsys):
    args = env()
    main(args + ["kill", "testing"])
    rc = main(args + ["order", "F", "--dollars", "50", "--yes"])
    assert rc == 1
    assert "kill switch is ENGAGED" in capsys.readouterr().err


def test_the_kill_switch_still_permits_closing(env, capsys):
    """Same asymmetry as the gate: refusing to let risk be reduced is worse
    than allowing it.

    Asserts the guard does not fire, not that the sell succeeds - whether it
    fills depends on the market being open, and a test that only passes on a
    weekday is a test that lies on Sunday.
    """
    args = env()
    main(args + ["kill", "testing"])
    capsys.readouterr()
    main(args + ["order", "F", "--quantity", "1", "--side", "sell", "--yes"])
    assert "kill switch is ENGAGED" not in capsys.readouterr().err


def test_confirmation_is_required_without_yes(env, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda _: "no thanks")
    rc = main(env() + ["order", "F", "--dollars", "50"])
    assert rc == 1
    assert "aborted" in capsys.readouterr().out


def test_the_typed_confirmation_names_the_actual_order(env, monkeypatch):
    seen = {}
    monkeypatch.setattr("builtins.input", lambda p: seen.setdefault("prompt", p) and "")
    main(env() + ["order", "F", "--quantity", "3"])
    assert "buy 3 F" in seen["prompt"], "a blind y/n is not a confirmation"


def test_it_states_that_the_gate_does_not_apply(env, capsys):
    main(env(dry_run="true") + ["order", "F", "--dollars", "50", "--yes"])
    assert "risk gate does NOT apply" in capsys.readouterr().out


def test_size_is_required(env):
    with pytest.raises(SystemExit):
        main(env() + ["order", "F", "--yes"])
