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

    # ------------------------------------------------------- lots / recon --
    def pending_orders(self) -> list[dict]:
        """Submitted decisions whose fills may not be fully booked yet.

        Includes orders already marked filled: a partial fill can complete
        later, and the applied_quantity delta is what decides whether there is
        anything new to book.
        """
        return [
            dict(r)
            for r in self._conn.execute(
                """
                SELECT d.id AS decision_id, d.broker_order_id, d.symbol,
                       d.asset_class, d.side, d.quantity, d.limit_price,
                       json_extract(d.features_json, '$.max_loss') AS max_loss,
                       COALESCE(os.applied_quantity, 0) AS applied_quantity
                FROM decisions d
                LEFT JOIN order_state os
                       ON os.broker_order_id = d.broker_order_id
                WHERE d.broker_order_id IS NOT NULL
                  AND d.status IN ('submitted','filled')
                  AND COALESCE(os.status,'') NOT IN
                      ('cancelled','canceled','rejected','failed','expired')
                ORDER BY d.ts
                """
            )
        ]

    def upsert_order_state(
        self, *, broker_order_id: str, decision_id: int, status: str,
        filled_quantity: float, applied_quantity: float,
        filled_price: float | None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO order_state (broker_order_id, decision_id, status,
                                     filled_quantity, applied_quantity,
                                     filled_price, last_seen_ts)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(broker_order_id) DO UPDATE SET
                status=excluded.status,
                filled_quantity=excluded.filled_quantity,
                applied_quantity=excluded.applied_quantity,
                filled_price=excluded.filled_price,
                last_seen_ts=excluded.last_seen_ts
            """,
            (broker_order_id, decision_id, status, filled_quantity,
             applied_quantity, filled_price, _iso()),
        )

    def open_lot(
        self, *, decision_id: int, symbol: str, asset_class: str, direction: int,
        quantity: float, entry_price: float, entry_ts: str, max_loss: float,
    ) -> int:
        cur = self._conn.execute(
            """
            INSERT INTO lots (decision_id, symbol, asset_class, direction,
                              quantity_total, quantity_open, entry_price,
                              entry_ts, max_loss)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (decision_id, symbol.upper(), asset_class, direction, quantity,
             quantity, entry_price, entry_ts, max_loss),
        )
        return int(cur.lastrowid)

    def open_lots(self, symbol: str) -> list:
        from ..reconcile import Lot

        return [
            Lot(
                id=r["id"], decision_id=r["decision_id"], symbol=r["symbol"],
                direction=r["direction"], quantity_open=r["quantity_open"],
                quantity_total=r["quantity_total"], entry_price=r["entry_price"],
                entry_ts=r["entry_ts"], max_loss=r["max_loss"],
                realized_pnl=r["realized_pnl"], fees=r["fees"],
            )
            for r in self._conn.execute(
                "SELECT * FROM lots WHERE status='open' AND symbol=? "
                "ORDER BY entry_ts",
                (symbol.upper(),),
            )
        ]

    def has_open_lots(self, symbol: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM lots WHERE status='open' AND symbol=? LIMIT 1",
            (symbol.upper(),),
        ).fetchone()
        return row is not None

    def symbols_with_open_lots(self) -> list[str]:
        return [
            r["symbol"]
            for r in self._conn.execute(
                "SELECT DISTINCT symbol FROM lots WHERE status='open'"
            )
        ]

    def symbols_closed_today(self, day_start_iso: str) -> frozenset[str]:
        """Symbols whose lots closed today - the gate's no-re-entry check."""
        return frozenset(
            r["symbol"]
            for r in self._conn.execute(
                "SELECT DISTINCT symbol FROM lots WHERE status='closed' "
                "AND closed_ts >= ?",
                (day_start_iso,),
            )
        )

    def apply_close(
        self, *, lot_id: int, quantity: float, pnl: float, closed: bool,
        closed_ts: str, exit_reason: str,
    ) -> None:
        """Reduce a lot and accumulate its realized P&L.

        quantity_open is decremented rather than set, so several partial closes
        compose correctly. The outcome row is written by the caller only once
        `closed` is true.
        """
        self._conn.execute(
            """
            UPDATE lots
               SET quantity_open = MAX(0, quantity_open - ?),
                   realized_pnl  = realized_pnl + ?,
                   status        = CASE WHEN ? THEN 'closed' ELSE status END,
                   closed_ts     = CASE WHEN ? THEN ? ELSE closed_ts END,
                   exit_reason   = CASE WHEN ? THEN ? ELSE exit_reason END
             WHERE id = ?
            """,
            (quantity, pnl, closed, closed, closed_ts, closed, exit_reason, lot_id),
        )

    def open_lot_count(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) c FROM lots WHERE status='open'"
        ).fetchone()
        return int(row["c"])

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

    def recent_errors(self, limit: int = 10) -> list[dict]:
        """Only the levels that mean something is wrong.

        `data` is deliberately not selected: it carries raw model output and
        order payloads, which must never reach a published telemetry file.
        """
        return [
            dict(r)
            for r in self._conn.execute(
                "SELECT ts, level, kind, message FROM events "
                "WHERE level IN ('error','critical') ORDER BY ts DESC LIMIT ?",
                (limit,),
            )
        ]

    def events_by_kind(self, since: str | None = None) -> list[dict]:
        sql = "SELECT level, kind, COUNT(*) n FROM events"
        params: tuple = ()
        if since:
            sql += " WHERE ts >= ?"
            params = (since,)
        sql += " GROUP BY level, kind ORDER BY n DESC, kind"
        return [dict(r) for r in self._conn.execute(sql, params)]

    # --------------------------------------------------------------- symbols --
    def known_symbols(self) -> frozenset[str]:
        """Every symbol this agent has ever touched.

        Used by telemetry redaction: an exact list beats a regex guess at what
        looks like a ticker.
        """
        return frozenset(
            r["symbol"]
            for r in self._conn.execute(
                "SELECT DISTINCT symbol FROM decisions "
                "UNION SELECT DISTINCT symbol FROM lots"
            )
            if r["symbol"]
        )
