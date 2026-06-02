import logging

from langchain_learning.logger import _SQLiteHandler

# Attach the SQLite handler once to the "lc" root logger so all child loggers
# (including those that can't import logger.py due to circular deps) inherit it.
_lc_root = logging.getLogger("lc")
if not any(isinstance(h, _SQLiteHandler) for h in _lc_root.handlers):
    _lc_root.addHandler(_SQLiteHandler())
_lc_root.setLevel(logging.DEBUG)
