"""Configuration loading.

Two rules:
  - Risk limits come from a file that lives in version control, so a change to
    them is a reviewable diff rather than an argument the agent won.
  - Secrets never appear in that file. They come from the environment only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .risk.gate import RiskLimits

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


@dataclass(frozen=True)
class BrokerConfig:
    kind: str = "paper"                   # paper | alpaca | robinhood_mcp
    paper: bool = True
    base_url: str | None = None
    mcp_url: str = "https://agent.robinhood.com/mcp/trading"
    token_file: str = "~/.tagent/robinhood-tokens.enc"

    # Paper broker only. Prices are synthetic and reproducible from the seed,
    # so two boxes with the same seed see the same market. See brokers/paper.py
    # for why this is a plumbing exercise and not a backtest.
    paper_state_file: str = "~/.tagent/paper-state.json"
    paper_seed: int = 7
    paper_starting_cash: float = 2000.0
    paper_spread_pct: float = 0.0008
    paper_vol: float = 0.02               # rough daily swing, as a fraction
    paper_settle_days: int = 1            # T+1, equities since May 2024
    paper_base_prices: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentConfig:
    model: str = "claude-opus-5"
    max_context_lessons: int = 25
    max_similar_decisions: int = 5
    interval_minutes: int = 20
    universe: tuple[str, ...] = ()
    setups: tuple[str, ...] = ()


@dataclass(frozen=True)
class AlertConfig:
    on_auth_expired: bool = True
    on_kill_switch: bool = True
    on_gate_reject_streak: int = 5
    webhook_env_var: str = "TAGENT_ALERT_WEBHOOK"


@dataclass(frozen=True)
class Config:
    db_path: str = "./data/tagent.db"
    limits: RiskLimits = field(default_factory=RiskLimits)
    broker: BrokerConfig = field(default_factory=BrokerConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    alerts: AlertConfig = field(default_factory=AlertConfig)
    dry_run: bool = True                  # default is ALWAYS no real orders

    @property
    def anthropic_api_key(self) -> str:
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        return key

    @property
    def alert_webhook(self) -> str | None:
        return os.environ.get(self.alerts.webhook_env_var)


def load(path: str | Path) -> Config:
    """Load config from YAML. Unknown keys are an error, not a shrug.

    A typo in `max_position_pct` that silently falls back to a default is
    exactly the kind of quiet failure this system cannot afford.
    """
    if yaml is None:
        raise RuntimeError("pyyaml is required to load config files")
    raw: dict[str, Any] = yaml.safe_load(Path(path).read_text()) or {}

    limits = _build(RiskLimits, raw.pop("limits", {}) or {}, "limits")
    broker = _build(BrokerConfig, raw.pop("broker", {}) or {}, "broker")
    alerts = _build(AlertConfig, raw.pop("alerts", {}) or {}, "alerts")

    agent_raw = raw.pop("agent", {}) or {}
    for seq_key in ("universe", "setups"):
        if seq_key in agent_raw and agent_raw[seq_key] is not None:
            agent_raw[seq_key] = tuple(agent_raw[seq_key])
    agent = _build(AgentConfig, agent_raw, "agent")

    db_path = raw.pop("db_path", "./data/tagent.db")
    dry_run = bool(raw.pop("dry_run", True))
    if raw:
        raise ValueError(f"unknown config keys: {sorted(raw)}")

    return Config(
        db_path=db_path, limits=limits, broker=broker,
        agent=agent, alerts=alerts, dry_run=dry_run,
    )


def _build(cls, data: dict, section: str):
    valid = {f for f in cls.__dataclass_fields__}
    unknown = set(data) - valid
    if unknown:
        raise ValueError(f"unknown keys in '{section}': {sorted(unknown)}")
    return cls(**data)
