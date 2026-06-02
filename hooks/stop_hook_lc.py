#!/usr/bin/env python3
"""
Stop hook — LangChain variant (no HTTP).

Replaces: stop_hook.py → POST /hook/stop → server/core/handlers/stop_handler.py

Inlines StopHandler logic: reads session from sessions.db, writes keyword/domain
aggregates back, no FastAPI dependency.
"""
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from hooks_config import cfg as _cfg
_SESSIONS_DB = _cfg.sessions_db
from sqlite_log_handler import setup
from utils import read_stdin, write_json_to_stdout

from core.db.session_db import SessionDB
from core.stopwords import filter_keywords

log = setup("stop_hook_lc")


def main():
    try:
        hook_input = read_stdin()
        session_id = hook_input.get("session_id", "")

        if not session_id:
            write_json_to_stdout()
            return

        if not _SESSIONS_DB.exists():
            write_json_to_stdout()
            return

        db = SessionDB.open(_SESSIONS_DB)
        saved = db.get(session_id)
        if not saved or saved.get("turn", 0) == 0:
            write_json_to_stdout()
            return

        # filter stopwords from accumulated keywords before persisting
        raw_keywords = set(saved.get("keywords", []))
        clean_keywords = filter_keywords(raw_keywords)

        db.upsert(session_id, {
            **saved,
            "keywords": clean_keywords,
            "current_state": "stop",
        })

        log.info("stop_hook_lc: persisted session %s (%d keywords, %d clean)",
                 session_id, len(raw_keywords), len(clean_keywords))

    except Exception as e:
        log.error("stop_hook_lc failed: %s", e)
        write_json_to_stdout(error=f"stop_hook_lc failed: {e}")
    else:
        write_json_to_stdout()


if __name__ == "__main__":
    main()
