"""Node registry — maps node names to callable classes."""
from __future__ import annotations

from langchain_learning.nodes.prompt_nodes import (
    ClassifyDomainNode,
    LoadMemoriesNode,
    LoadSessionContextNode,
    LoadTurnNode,
    PersistSessionNode,
    ScoreToolsNode,
)
from langchain_learning.nodes.stop_nodes import FinalizeSessionNode, NoopNode
from langchain_learning.nodes.tool_nodes import GateCheckNode, LogToolUsageNode

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
