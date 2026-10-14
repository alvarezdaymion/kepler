"""Optional LLM analysis: the model writes the read-out and next steps from numbers Kepler already
computed. It never calculates anything shown on the dashboard.

Off unless `[analysis] enabled = true` and the `llm` extra is installed (`uv sync --extra llm`).
Credentials come from the environment (ANTHROPIC_API_KEY) as the Anthropic SDK resolves them.

Cost: this is the only metered call Kepler makes. It runs at most once per `every_minutes`
(default 1440, once a day) no matter how often the controller ticks; in between, the last
analysis is shown. After a failure it retries after min(every_minutes, 60) minutes.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

SYSTEM = (
    "You write the analysis panel of a small business's revenue dashboard. Every number you need was "
    "computed in code and is in the JSON the user sends. Use only those numbers and quote them as given. "
    "Do not calculate new totals, rates, percentages or dollar amounts. A value of null means the step is "
    "not tracked, which is different from zero. Write plain text under 250 words with three parts: "
    "1) the main constraint on reaching the goal and why, 2) the next three actions in priority order, "
    "3) any numbers that shouldn't be trusted yet (stale or untracked sources, guard holds, too-early steps)."
)


def facts(snap: dict[str, Any]) -> dict[str, Any]:
    """The compact, already-computed slice of the snapshot the model sees."""
    return {
        "goal": snap["goal"],
        "funnel": snap["funnel"],
        "bottlenecks": snap["bottlenecks"][:6],
        "lanes": [{"name": la["name"], "grade": la["grade"],
                   "kpis": [{k: v for k, v in kp.items() if k in ("label", "value", "target", "grade")}
                            for kp in la["kpis"]]} for la in snap["lanes"]],
        "ads": [{k: r[k] for k in ("ad_name", "status", "spend", "leads", "cpl", "verdict")}
                for r in snap["ads"]["rows"][:15]],
        "auto_cut": snap.get("auto_cut"),
        "sources": [{"name": s["name"], "status": s["status"]} for s in snap["sources"]],
    }


def _load(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def maybe_run(cfg: Any, snap: dict[str, Any], now: dt.datetime, state_dir: Path,
              client: Any = None) -> dict[str, Any]:
    a = cfg["analysis"]
    if not a["enabled"]:
        return {"status": "off", "reason": "Analysis is off. Set [analysis] enabled = true to turn it on."}
    path = state_dir / "analysis.json"
    prev = _load(path)
    every = float(a["every_minutes"])
    last_ok = prev.get("generated_at")
    last_try = prev.get("attempted_at")
    if last_ok and (now - dt.datetime.fromisoformat(last_ok)).total_seconds() / 60 < every:
        return {**prev, "status": "ok"}
    if last_try and prev.get("error") and \
            (now - dt.datetime.fromisoformat(last_try)).total_seconds() / 60 < min(every, 60):
        return {**prev, "status": "error"}
    if client is None:
        try:
            import anthropic
        except ImportError:
            return {"status": "off", "reason": "Install the llm extra to use analysis: uv sync --extra llm"}
        client = anthropic.Anthropic()
    record: dict[str, Any] = {k: prev[k] for k in ("text", "generated_at", "model", "usage") if k in prev}
    record["attempted_at"] = now.isoformat(timespec="seconds")
    try:
        kwargs: dict[str, Any] = {
            "model": a["model"], "max_tokens": int(a["max_tokens"]), "system": SYSTEM,
            "output_config": {"effort": a["effort"]},
            "messages": [{"role": "user", "content": json.dumps(facts(snap), default=str)}],
        }
        if a.get("fallbacks"):
            resp = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"],
                                               fallbacks="default", **kwargs)
        else:
            resp = client.messages.create(**kwargs)
        if resp.stop_reason == "refusal":
            raise RuntimeError("the model declined this request")
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        record.update(text=text, generated_at=now.isoformat(timespec="seconds"), model=resp.model,
                      usage={"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens})
        record.pop("error", None)
        status = "ok"
    except Exception as e:
        record["error"] = str(e)[:300]
        status = "error"
    state_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2))
    return {**record, "status": status}
