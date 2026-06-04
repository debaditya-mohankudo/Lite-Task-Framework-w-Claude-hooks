"""CwdDomainDetectNode — deterministic domain detection from CWD map."""
from __future__ import annotations

from langchain_learning.nodes._node_log import entry
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)


class CwdDomainDetectNode:
    """Map state["cwd"] to a domain using cwd_domain_map from classifier_config.

    Deterministic, zero-cost, runs before any scoring. Adds to domains if matched.
    CWD comes from SessionState (threaded from hook input) — never os.getcwd().
    """

    def __call__(self, state: SessionState) -> dict:
        entry("cwd_domain_detect", state, cwd=state.get("cwd", "")[:40])

        cwd = state.get("cwd", "")
        cfg = state.get("classifier_config", {})
        cwd_map: dict = cfg.get("cwd_domain_map", {})

        detected: list[str] = list(state.get("domains", []))
        for key, domain in cwd_map.items():
            if key.lower() in cwd.lower():
                if domain not in detected:
                    detected.append(domain)
                _log.info("[cwd_domain_detect] cwd=%r → domain=%s", key, domain)
                break

        return {"domains": detected}
