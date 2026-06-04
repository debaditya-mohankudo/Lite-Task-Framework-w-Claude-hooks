"""LoadClassifierConfigNode — loads domain_classifier.json into state once per invocation."""
from __future__ import annotations

import json
from pathlib import Path

from langchain_learning.nodes._node_log import entry
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)


class LoadClassifierConfigNode:
    """Load domain_classifier.json from iCloud into state.

    Placing this in a dedicated node keeps all file I/O explicit in the graph
    topology (ACME principle 10: all network/IO effects named as nodes).
    Downstream nodes read from state["classifier_config"] — no file access.
    """

    def __call__(self, state: SessionState) -> dict:
        entry("load_classifier_config", state)
        try:
            from src.config import config as src_cfg
            path: Path = src_cfg.domain_classifier_json
            with open(path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            _log.info("[load_classifier_config] loaded %d domains from %s",
                      len(cfg.get("keyword_signals", {})), path.name)
            return {"classifier_config": cfg}
        except Exception as exc:
            _log.warning("[load_classifier_config] failed to load JSON: %s — using empty config", exc)
            return {"classifier_config": {}}
