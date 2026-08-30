"""SQLite-backed persistent memory.

One-shot scheduled invocations mean nothing survives in RAM between cycles.
That is deliberate: it forces every piece of accumulated knowledge through this
module and onto disk, which is the only way "carries on tomorrow with what it
learned today" is true rather than aspirational.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def _iso(dt: datetime | None = None) -> str:
    return (dt or datetime.now(timezone.utc)).isoformat()


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        self.migrate()

    def migrate(self) -> None:
        self._conn.executescript(SCHEMA_PATH.read_text())

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        self._conn.execute("BEGIN")
        try:
            yield self._conn
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        else:
            self._conn.execute("COMMIT")

    # ------------------------------------------------------------ decisions --
    def record_decision(
        self,
        *,
        cycle_id: str,
        symbol: str,
        asset_class: str,
        side: str,
        quantity: float,
        order_type: str,
        notional: float,
        setup_tag: str,
        confidence: float,
        thesis: str,
        gate_verdict: str,
        gate_reasons: list[str],
        regime_tag: str | None = None,
        limit_price: float | None = None,
        features: dict[str, Any] | None = None,
        status: str = "proposed",
    ) -> int:
        cur = self._conn.execute(
            """
            INSERT INTO decisions (
                ts, cycle_id, symbol, asset_class, side, quantity, order_type,
                limit_price, notional, setup_tag, regime_tag, confidence, thesis,
                features_json, gate_verdict, gate_reasons, status
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                _iso(), cycle_id, symbol, asset_class, side, quantity, order_type,
                limit_price, notional, setup_tag, regime_tag, confidence, thesis,
                json.dumps(features or {}), gate_verdict, json.dumps(gate_reasons),
                status,
            ),
        )
        return int(cur.lastrowid)

    def mark_submitted(self, decision_id: int, broker_order_id: str) -> None:
        self._conn.execute(
            "UPDATE decisions SET broker_order_id=?, status='submitted' WHERE id=?",
            (broker_order_id, decision_id),
        )

    def set_status(self, decision_id: int, status: str) -> None:
        self._conn.execute(
            "UPDATE decisions SET status=? WHERE id=?", (status, decision_id)
        )

    def record_outcome(
        self,
        *,
        decision_id: int,
        pnl: float,
        pnl_pct: float,
        holding_days: float,
        exit_reason: str,
        fees: float = 0.0,
        slippage: float = 0.0,
    ) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO outcomes (
                decision_id, closed_ts, pnl, pnl_pct, fees, slippage,
                holding_days, exit_reason, was_win
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                decision_id, _iso(), pnl, pnl_pct, fees, slippage,
                holding_days, exit_reason, 1 if pnl > 0 else 0,
            ),
        )

    def closed_trades(self, since: str | None = None) -> list[dict]:
        sql = """
            SELECT d.*, o.pnl, o.pnl_pct, o.holding_days, o.exit_reason,
                   o.was_win, o.slippage, o.fees, o.closed_ts
            FROM decisions d JOIN outcomes o ON o.decision_id = d.id
        """
        params: tuple = ()
        if since:
            sql += " WHERE o.closed_ts >= ?"
            params = (since,)
        sql += " ORDER BY o.closed_ts"
        return [dict(r) for r in self._conn.execute(sql, params)]

    def rejections(self, since: str) -> list[dict]:
        return [
            dict(r)
            for r in self._conn.execute(
                "SELECT * FROM decisions WHERE gate_verdict='reject' AND ts >= ?",
                (since,),
            )
        ]

    def trades_today(self, day_start_iso: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) c FROM decisions WHERE ts >= ? AND status IN "
            "('submitted','filled')",
            (day_start_iso,),
        ).fetchone()
        return int(row["c"])

    def similar_decisions(
        self, setup_tag: str, regime_tag: str | None, limit: int = 5
    ) -> list[dict]:
        """Closed trades from the same setup, most recent first.

        Deliberately simple retrieval: setup + regime match beats embedding
        similarity here, because the agent needs 'how did THIS play work out'
        and setups are already a hand-curated taxonomy.
        """
        return [
            dict(r)
            for r in self._conn.execute(
                """
                SELECT d.symbol, d.setup_tag, d.regime_tag, d.confidence, d.thesis,
                       o.pnl_pct, o.exit_reason, o.closed_ts
                FROM decisions d JOIN outcomes o ON o.decision_id = d.id
                WHERE d.setup_tag = ?
                  AND (? IS NULL OR d.regime_tag = ?)
                ORDER BY o.closed_ts DESC LIMIT ?
                """,
                (setup_tag, regime_tag, regime_tag, limit),
            )
        ]

    # -------------------------------------------------------------- lessons --
    def active_lessons(self, limit: int = 40) -> list[dict]:
        return [
            dict(r)
            for r in self._conn.execute(
                """
                SELECT * FROM lessons WHERE status='active'
                ORDER BY (scope='process') DESC, evidence_n DESC, updated_ts DESC
                LIMIT ?
                """,
                (limit,),
            )
        ]

    def add_lesson(
        self,
        *,
        scope: str,
        text: str,
        setup_tag: str | None = None,
        regime_tag: str | None = None,
        evidence_ids: list[int] | None = None,
    ) -> int:
        ids = evidence_ids or []
        now = _iso()
        cur = self._conn.execute(
            """
            INSERT INTO lessons (created_ts, updated_ts, scope, setup_tag,
                                 regime_tag, text, evidence_ids, evidence_n)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (now, now, scope, setup_tag, regime_tag, text, json.dumps(ids), len(ids)),
        )
        return int(cur.lastrowid)

    def retire_lesson(self, lesson_id: int, reason: str) -> None:
        self._conn.execute(
            "UPDATE lessons SET status='retired', retired_ts=?, retired_reason=? "
            "WHERE id=?",
            (_iso(), reason, lesson_id),
        )

    # ---------------------------------------------------------- setup stats --
    def upsert_setup_stats(self, stats: list) -> None:
        now = _iso()
        with self.tx() as c:
            for s in stats:
                c.execute(
                    """
                    INSERT INTO setup_stats (
                        setup_tag, regime_tag, n, wins, observed_mean_pct,
                        observed_sd_pct, posterior_mean_pct, posterior_se_pct,
                        total_pnl, blocked, updated_ts
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(setup_tag, regime_tag) DO UPDATE SET
                        n=excluded.n, wins=excluded.wins,
                        observed_mean_pct=excluded.observed_mean_pct,
                        observed_sd_pct=excluded.observed_sd_pct,
                        posterior_mean_pct=excluded.posterior_mean_pct,
                        posterior_se_pct=excluded.posterior_se_pct,
                        total_pnl=excluded.total_pnl, blocked=excluded.blocked,
                        updated_ts=excluded.updated_ts
                    """,
                    (
                        s.setup_tag, s.regime_tag, s.n, s.wins, s.observed_mean_pct,
                        s.observed_sd_pct, s.posterior_mean_pct, s.posterior_se_pct,
                        s.total_pnl, 1 if s.blocked else 0, now,
                    ),
                )

    def blocked_setups(self) -> frozenset[str]:
        return frozenset(
            r["setup_tag"]
            for r in self._conn.execute(
                "SELECT DISTINCT setup_tag FROM setup_stats "
                "WHERE blocked=1 AND regime_tag='*'"
            )
        )

    def setup_stats(self) -> list[dict]:
        return [
            dict(r)
            for r in self._conn.execute(
                "SELECT * FROM setup_stats WHERE regime_tag='*' ORDER BY setup_tag"
            )
        ]

    def upsert_calibration(self, buckets: list) -> None:
        now = _iso()
        with self.tx() as c:
            for b in buckets:
                c.execute(
                    """
                    INSERT INTO calibration (bucket, n, wins, mean_stated,
                                             realized_rate, updated_ts)
                    VALUES (?,?,?,?,?,?)
                    ON CONFLICT(bucket) DO UPDATE SET
                        n=excluded.n, wins=excluded.wins,
                        mean_stated=excluded.mean_stated,
                        realized_rate=excluded.realized_rate,
                        updated_ts=excluded.updated_ts
                    """,
                    (b.bucket, b.n, b.wins, b.mean_stated, b.realized_rate, now),
                )

    def calibration(self) -> list[dict]:
        return [dict(r) for r in self._conn.execute("SELECT * FROM calibration")]

    # --------------------------------------------------------- equity/state --
    def record_equity(
        self, equity: float, cash: float, deployed_pct: float = 0.0
    ) -> tuple[float, float]:
        row = self._conn.execute(
            "SELECT MAX(peak_equity) p FROM equity_curve"
        ).fetchone()
        peak = max(float(row["p"] or 0.0), equity)
        dd = (peak - equity) / peak if peak > 0 else 0.0
        self._conn.execute(
            "INSERT OR REPLACE INTO equity_curve "
            "(ts, equity, cash, deployed_pct, peak_equity, drawdown_pct) "
            "VALUES (?,?,?,?,?,?)",
            (_iso(), equity, cash, deployed_pct, peak, dd),
        )
        return peak, dd

    def peak_equity(self) -> float:
        row = self._conn.execute("SELECT MAX(peak_equity) p FROM equity_curve").fetchone()
        return float(row["p"] or 0.0)

    def start_of_day_equity(self, day_start_iso: str) -> float | None:
        row = self._conn.execute(
            "SELECT equity FROM equity_curve WHERE ts >= ? ORDER BY ts LIMIT 1",
            (day_start_iso,),
        ).fetchone()
        return float(row["equity"]) if row else None

    def get_state(self, key: str, default: str | None = None) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM state WHERE key=?", (key,)
        ).fetchone()
        return row["value"] if row else default

    def set_state(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO state (key, value, updated_ts) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
            "updated_ts=excluded.updated_ts",
            (key, value, _iso()),
        )

    @property
    def kill_switch(self) -> bool:
        return self.get_state("kill_switch", "0") == "1"

    def engage_kill_switch(self, reason: str) -> None:
        self.set_state("kill_switch", "1")
        self.set_state("kill_switch_reason", reason)
        self.log("critical", "kill_switch", reason)

    def release_kill_switch(self) -> None:
        """Deliberately manual. Nothing in the agent may call this."""
        self.set_state("kill_switch", "0")
        self.log("warn", "kill_switch", "released by operator")

    # ---------------------------------------------------------------- events --
    def log(self, level: str, kind: str, message: str, **data: Any) -> None:
        self._conn.execute(
            "INSERT INTO events (ts, level, kind, message, data) VALUES (?,?,?,?,?)",
            (_iso(), level, kind, message, json.dumps(data)),
        )

    def recent_events(self, limit: int = 50) -> list[dict]:
        return [
            dict(r)
            for r in self._conn.execute(
                "SELECT * FROM events ORDER BY ts DESC LIMIT ?", (limit,)
            )
        ]
