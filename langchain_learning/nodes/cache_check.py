"""CacheCheckNode — looks up the incoming prompt against the prompt_cache table."""
from __future__ import annotations

from langchain_learning.nodes._node_log import entry
from langchain_learning.session_state import SessionState
from src.logger import get_logger
from src.tools.prompt_cache import lookup_cache

_log = get_logger(__name__)


class CacheCheckNode:
    """Sequential node, runs right after load_turn.

    Never serves the cached answer itself — only surfaces the hit into state so
    _format_system_prompt can inject a confirmation note. Epic c0f3037f: a cache
    hit must never silently override a live response.

    Tags: prompt-cache, cache-check, session-graph
    """

    def __call__(self, state: SessionState) -> dict:
        entry("cache_check", state)
        prompt = state.get("prompt", "")
        if not prompt:
            return {"cache_hit": {}}
        try:
            row = lookup_cache(prompt)
        except Exception as exc:
            _log.error("[cache_check] lookup_cache error: %s", exc)
            return {"cache_hit": {}}
        if row:
            _log.info("[cache_check] hit match_type=%s source=%s", row.get("match_type"), row.get("source"))
        return {"cache_hit": row or {}}
