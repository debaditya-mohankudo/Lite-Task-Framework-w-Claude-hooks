"""Node registry — maps node names to callable classes."""
from __future__ import annotations

from langchain_learning.nodes.classify_domain import ClassifyDomainNode
from langchain_learning.nodes.finalize_session import FinalizeSessionNode
from langchain_learning.nodes.gate_check import GateCheckNode
from langchain_learning.nodes.load_memories import LoadMemoriesNode
from langchain_learning.nodes.load_session_context import LoadSessionContextNode
from langchain_learning.nodes.load_turn import LoadTurnNode
from langchain_learning.nodes.log_tool_usage import LogToolUsageNode
from langchain_learning.nodes.noop import NoopNode
from langchain_learning.nodes.persist_session import PersistSessionNode
from langchain_learning.nodes.score_tools import ScoreToolsNode

NODE_REGISTRY: dict[str, type] = {
    # UserPromptSubmit chain
    "load_turn":            LoadTurnNode,
    "load_memories":        LoadMemoriesNode,
    "load_session_context": LoadSessionContextNode,
    "classify_domain":      ClassifyDomainNode,
    "score_tools":          ScoreToolsNode,
    "persist_session":      PersistSessionNode,
    # PreToolUse chain
    "gate_check":           GateCheckNode,
    # PostToolUse chain
    "log_tool_usage":       LogToolUsageNode,
    # Stop chain
    "finalize_session":     FinalizeSessionNode,
    # Fallback
    "noop":                 NoopNode,
}


def get_node(name: str):
    """Instantiate a node by name. Raises KeyError if not in registry."""
    node_cls = NODE_REGISTRY[name]
    if not isinstance(node_cls, type):
        raise TypeError(f"Registry entry {name!r} must be a class, got {type(node_cls).__name__}")
    return node_cls()
