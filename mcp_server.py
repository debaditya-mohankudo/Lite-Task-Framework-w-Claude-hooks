"""claude-hooks MCP server — self-contained tool server for task, memory, session, hooks tools."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from mcp.server.fastmcp import FastMCP
from dispatcher import build_dispatcher

mcp = FastMCP("claude-hooks")
build_dispatcher(mcp)


def _bootstrap() -> None:
    """Rebuild task embeddings index on startup if stale or missing."""
    from tools.tasks import _TASKS_TVIM, rebuild_task_index
    from logger import get_logger
    log = get_logger(__name__)
    try:
        if not _TASKS_TVIM.exists():
            log.info("[bootstrap] task index missing — rebuilding")
            result = rebuild_task_index()
            log.info("[bootstrap] task index built: %s", result)
        else:
            log.debug("[bootstrap] task index present, skipping rebuild")
    except Exception as exc:
        log.warning("[bootstrap] task index rebuild failed (Ollama down?): %s", exc)


_bootstrap()

if __name__ == "__main__":
    mcp.run()
