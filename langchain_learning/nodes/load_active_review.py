"""LoadActiveReviewNode — load open review child checklist into session state."""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from langchain_learning.config import config as _cfg
from langchain_learning.nodes._node_log import entry
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)

_REVIEW_TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "review_templates"

# Matches "- [auto] c1: label" or "- [manual] m1: label"
_ITEM_RE = re.compile(r"-\s+\[(auto|manual)\]\s+(\w+):\s+(.+)")


def _parse_template_items(template_name: str) -> list[dict]:
    """Read template MD from disk and extract checklist items."""
    path = _REVIEW_TEMPLATES_DIR / f"{template_name}.md"
    if not path.exists():
        _log.warning("[load_active_review] template not found: %s", path)
        return []
    try:
        content = path.read_text(encoding="utf-8")
    except Exception as exc:
        _log.error("[load_active_review] failed to read template %s: %s", path, exc)
        return []
    items = []
    for line in content.splitlines():
        m = _ITEM_RE.match(line.strip())
        if m:
            items.append({"id": m.group(2), "type": m.group(1), "label": m.group(3).strip(), "status": "pending"})
    return items


class LoadActiveReviewNode:
    """Load open review run checklist into active_review session state.

    Queries review_runs for task_id=active_task_id, status in (open, blocked) — a
    blocked review (failure found) must stay pinned so the checklist keeps the
    development/review tension until the failure is fixed.
    If found, reads result JSON for existing item statuses, merges with fresh
    template items from disk (template always wins for labels — only status is persisted).

    Skipped when no active task or no open review run found.

    Tags: review, checklist, session-state, load-active-review
    """

    def __call__(self, state: SessionState) -> dict:
        entry("load_active_review", state)

        active_id = state.get("active_task_id", "")
        if not active_id:
            _log.info("[load_active_review] no active task — skipped")
            return {"active_review": {}}

        if not _cfg.tasks_db.exists():
            return {"active_review": {}}

        try:
            conn = sqlite3.connect(f"file:{_cfg.tasks_db}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    """SELECT id, template_name, result
                       FROM review_runs
                       WHERE task_id=? AND status IN ('open', 'blocked')
                       LIMIT 1""",
                    (active_id,),
                ).fetchone()
            finally:
                conn.close()
        except Exception as exc:
            _log.error("[load_active_review] DB error: %s", exc)
            return {"active_review": {}}

        if row is None:
            _log.info("[load_active_review] no open review run for task=%s — skipped", active_id)
            return {"active_review": {}}

        template_name = row["template_name"] or ""
        review_task_id = row["id"]

        # Load items from template (always fresh from disk)
        items = _parse_template_items(template_name)

        # Overlay persisted statuses from result if present
        if row["result"]:
            try:
                persisted = {r["id"]: r for r in json.loads(row["result"])}
                for item in items:
                    if item["id"] in persisted:
                        item["status"] = "pass" if persisted[item["id"]].get("passed") else (
                            "fail" if persisted[item["id"]].get("passed") is False else "pending"
                        )
                        if persisted[item["id"]].get("note"):
                            item["note"] = persisted[item["id"]]["note"]
            except Exception as exc:
                _log.warning("[load_active_review] could not parse result: %s", exc)

        _log.info(
            "[load_active_review] loaded review_task=%s template=%s items=%d",
            review_task_id, template_name, len(items),
        )
        return {"active_review": {"review_task_id": review_task_id, "template": template_name, "items": items}}
