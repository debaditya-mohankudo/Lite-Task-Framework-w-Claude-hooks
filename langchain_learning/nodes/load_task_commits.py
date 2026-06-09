"""LoadTaskCommitsNode — fetch last 5 git commits referencing the active task."""
from __future__ import annotations

import subprocess
from pathlib import Path

from langchain_learning.nodes._node_log import entry
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)

_COMMIT_LIMIT = 5
_SEARCH_WINDOW = 1000
# Search the repo containing the hooks project
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _repo_commit_count() -> int:
    try:
        out = subprocess.check_output(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=_REPO_ROOT, text=True, stderr=subprocess.DEVNULL,
        )
        return int(out.strip())
    except Exception:
        return 0


def _git_log(task_id: str, task_title: str) -> list[dict]:
    """Return last _COMMIT_LIMIT commits whose message mentions task_id or title words."""
    # Build grep pattern: task_id short prefix OR significant title words (≥5 chars)
    title_words = [w for w in task_title.split() if len(w) >= 5]
    patterns = [task_id[:8]] + title_words[:3]  # cap to avoid huge regex
    grep_arg = "|".join(patterns)

    try:
        out = subprocess.check_output(
            [
                "git", "log",
                f"--grep={grep_arg}",
                "--extended-regexp",
                "--regexp-ignore-case",
                f"-{_COMMIT_LIMIT}",
                "--pretty=format:%h|%as|%s",
                *(["HEAD~1000..HEAD"] if _repo_commit_count() >= _SEARCH_WINDOW else []),
            ],
            cwd=_REPO_ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception as exc:
        _log.warning("[load_task_commits] git log failed: %s", exc)
        return []

    if not out:
        return []

    commits = []
    for line in out.splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3:
            commits.append({"sha": parts[0], "date": parts[1], "subject": parts[2]})
    return commits


class LoadTaskCommitsNode:
    """Fetch last 5 git commits whose message references the active task_id or title.

    Runs git log with --grep on the task id (short SHA prefix) and significant
    title words. Returns commits as task_commits list — always injected as a
    separate ## Task commits block regardless of session turn count.

    Tags: task-commits, git-log, task-context, cross-session, development-history
    """

    def __call__(self, state: SessionState) -> dict:
        entry("load_task_commits", state)

        task_id    = state.get("active_task_id", "")
        task_title = state.get("active_task_title", "")

        if not task_id:
            _log.info("[load_task_commits] no active task — skipped")
            return {"task_commits": []}

        commits = _git_log(task_id, task_title)
        _log.info("[load_task_commits] task=%s commits=%d shas=%s title=%r",
                  task_id, len(commits), [c["sha"] for c in commits], task_title[:40])
        return {"task_commits": commits}
