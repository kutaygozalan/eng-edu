"""The journal-writing step of the nightly review.

Statistics are computed deterministically elsewhere; this is where the agent
gets to write prose about what it learned. That is exactly where an LLM will
talk itself into things, so nothing it proposes is trusted:

  * Every lesson is validated against the evidence bar before it is stored. An
    edge claim without 30 closed trades behind it is rejected with a reason,
    and the rejection is logged so the pattern is visible.
  * Retirements are checked against the active lesson set - the model cannot
    retire a lesson that does not exist, or one it merely dislikes today.
  * The model never sees raw sample means, only shrunk posteriors, so the
    numbers it reasons about are already honest.

The result is a journal that grows slowly and mostly with process rules, which
is what a real trader's journal looks like.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import anthropic

from ..memory import review as R
from ..memory.store import Store
from .prompts import REVIEW_SCHEMA, REVIEW_SYSTEM_PROMPT

MAX_TOKENS = 8000


@dataclass
class JournalReport:
    summary: str = ""
    written: int = 0
    refused: int = 0
    retired: int = 0
    refusals: list[str] = None
    error: str | None = None

    def __post_init__(self) -> None:
        self.refusals = self.refusals or []


def write_journal(
    *,
    store: Store,
    model: str,
    api_key: str,
    packet: R.ReviewPacket,
    stats: list[R.SetupStat],
) -> JournalReport:
    report = JournalReport()
    client = anthropic.Anthropic(api_key=api_key)

    try:
        response = client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            thinking={"type": "adaptive"},
            output_config={
                "effort": "high",
                "format": {"type": "json_schema", "schema": REVIEW_SCHEMA},
            },
            system=REVIEW_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": json.dumps(packet.to_prompt_dict(), indent=2),
                }
            ],
        )
    except anthropic.APIStatusError as exc:
        report.error = f"{exc.status_code}: {exc.message}"
        store.log("error", "journal_model_error", report.error)
        return report
    except anthropic.APIConnectionError as exc:
        report.error = str(exc)
        store.log("error", "journal_unreachable", report.error)
        return report

    if response.stop_reason == "refusal":
        report.error = "model declined to write the journal"
        store.log("warn", "journal_refusal", report.error)
        return report

    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        report.error = f"unparseable journal output: {exc}"
        store.log("warn", "journal_unparseable", report.error, raw=text[:500])
        return report

    report.summary = str(payload.get("summary") or "").strip()

    for proposed in payload.get("new_lessons") or []:
        ok, why = R.validate_proposed_lesson(proposed, stats)
        if not ok:
            report.refused += 1
            report.refusals.append(f"{proposed.get('text', '')[:80]}: {why}")
            store.log(
                "info", "lesson_refused", why,
                scope=proposed.get("scope"), text=proposed.get("text", "")[:200],
            )
            continue
        if _is_duplicate(store, proposed["text"]):
            report.refused += 1
            report.refusals.append("duplicate of an active lesson")
            continue

        store.add_lesson(
            scope=proposed["scope"],
            text=proposed["text"].strip(),
            setup_tag=proposed.get("setup_tag"),
            regime_tag=proposed.get("regime_tag"),
            evidence_ids=proposed.get("evidence_ids") or [],
        )
        report.written += 1

    active_ids = {l["id"] for l in store.active_lessons(500)}
    for lesson_id in payload.get("retire_lesson_ids") or []:
        if lesson_id not in active_ids:
            continue
        store.retire_lesson(int(lesson_id), "retired by nightly review")
        report.retired += 1

    store.log(
        "info", "journal_written",
        f"{report.written} written, {report.refused} refused, "
        f"{report.retired} retired",
    )
    return report


def _is_duplicate(store: Store, text: str) -> bool:
    """Cheap near-duplicate check on normalized text.

    Without this the journal slowly fills with the same rule in ten phrasings,
    crowding out the context budget that real lessons need.
    """
    norm = " ".join(text.lower().split())
    for existing in store.active_lessons(500):
        other = " ".join(existing["text"].lower().split())
        if norm == other:
            return True
        # Substring containment catches "Never trade 0DTE" vs
        # "Never trade 0DTE options on any underlying".
        if len(norm) > 20 and (norm in other or other in norm):
            return True
    return False
