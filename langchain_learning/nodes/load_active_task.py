"""LoadActiveTaskNode — reads active_task_id from checkpoint; filters by project tag."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from langchain_learning.config import config as _cfg
from langchain_learning.nodes._node_log import entry
from langchain_learning.nodes._text_utils import task_project_tag as _task_project_tag
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)


def _project_from_cwd(cwd: str) -> Optional[str]:
    """Walk up from cwd to find pyproject.toml; return project.name or None."""
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore
        except ImportError:
            return None
    p = Path(cwd).resolve()
    for parent in [p, *p.parents]:
        candidate = parent / "pyproject.toml"
        if candidate.exists():
            try:
                data = tomllib.loads(candidate.read_text())
                return (
                    data.get("project", {}).get("name")
                    or data.get("tool", {}).get("poetry", {}).get("name")
                )
            except Exception:
                return None
    return None


class LoadActiveTaskNode:
    """Pass-through node — active_task_id already lives in checkpoint state.

    Adds one filter: if the active task was tagged with project:<name> at
    creation time and the current CWD resolves to a *different* project, the
    task is suppressed for this turn by returning {"active_task_id": "", ...}.

    This only updates the in-flight SessionState for this pipeline run — it
    never writes back to the LangGraph checkpoint. So from Claude's perspective
    this turn, there is no active task (as if the session started fresh). But
    the checkpoint on disk is untouched: the next prompt from the correct
    directory picks the task back up automatically.

    Tasks with no project tag (created without cwd) are always injected
    regardless of CWD — right default for cross-project or generic tasks.

    Tags: task-activation, active-task, checkpoint, project-scoping, cwd
    """

    def __call__(self, state: SessionState) -> dict:
        entry("load_active_task", state)
        task_id = state.get("active_task_id", "")
        if not task_id:
            return {}

        task_project = _task_project_tag(task_id, _cfg.tasks_db)
        if task_project:
            cwd = state.get("cwd", "")
            cwd_project = _project_from_cwd(cwd) if cwd else None
            if cwd_project and cwd_project != task_project:
                _log.info(
                    "[load_active_task] suppressing task=%s (project:%s) — cwd project=%s",
                    task_id, task_project, cwd_project,
                )
                return {"active_task_id": "", "active_task_title": "", "task_body": ""}

        _log.info("[load_active_task] session=%s active_task=%s title=%r project_tag=%s",
                  (state.get("session_id") or "")[:8], task_id,
                  state.get("active_task_title", ""), task_project or "none")
        return {}
