"""Classifier config loader — reads domain_classifier.json once per process."""
from __future__ import annotations

import json
from pathlib import Path

from src.logger import get_logger

_log = get_logger(__name__)
_cache: dict | None = None


def get_classifier_config() -> dict:
    global _cache
    if _cache is not None:
        return _cache
    try:
        from src.config import config as src_cfg
        path: Path = src_cfg.domain_classifier_json
        with open(path, "r", encoding="utf-8") as f:
            _cache = json.load(f)
        _log.info("[classifier_config] loaded %d domains from %s",
                  len(_cache.get("keyword_signals", {})), path.name)
    except Exception as exc:
        _log.warning("[classifier_config] failed to load JSON: %s — using empty config", exc)
        _cache = {}
    return _cache
