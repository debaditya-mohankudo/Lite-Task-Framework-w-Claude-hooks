"""Node registry — maps node names to callable classes."""
from __future__ import annotations

from langchain_learning.nodes.apply_threshold import ApplyThresholdNode
from langchain_learning.nodes.combination_score import CombinationScoreNode
from langchain_learning.nodes.cwd_domain_detect import CwdDomainDetectNode
from langchain_learning.nodes.finalize_session import FinalizeSessionNode
from langchain_learning.nodes.gate_check import GateCheckNode
from langchain_learning.nodes.keyword_score import KeywordScoreNode
from langchain_learning.nodes.load_classifier_config import LoadClassifierConfigNode
from langchain_learning.nodes.load_memories import LoadMemoriesNode
from langchain_learning.nodes.load_session_context import LoadSessionContextNode
from langchain_learning.nodes.load_turn import LoadTurnNode
from langchain_learning.nodes.log_tool_usage import LogToolUsageNode
from langchain_learning.nodes.memory_domain_signal import MemoryDomainSignalNode
from langchain_learning.nodes.noop import NoopNode
from langchain_learning.nodes.persist_session import PersistSessionNode
from langchain_learning.nodes.score_tools import ScoreToolsNode
from langchain_learning.nodes.set_prompt_id import SetPromptIdNode

NODE_REGISTRY: dict[str, type] = {
    # UserPromptSubmit chain
    "load_turn":               LoadTurnNode,
    "load_memories":           LoadMemoriesNode,
    "load_session_context":    LoadSessionContextNode,
    # classify chain (replaces monolithic classify_domain)
    "load_classifier_config":  LoadClassifierConfigNode,
    "cwd_domain_detect":       CwdDomainDetectNode,
    "keyword_score":           KeywordScoreNode,
    "combination_score":       CombinationScoreNode,
    "memory_domain_signal":    MemoryDomainSignalNode,
    "apply_threshold":         ApplyThresholdNode,
    # downstream
    "score_tools":             ScoreToolsNode,
    "set_prompt_id":           SetPromptIdNode,
    "persist_session":         PersistSessionNode,  # used only by finalize_session (Stop chain)
    # PreToolUse chain
    "gate_check":              GateCheckNode,
    # PostToolUse chain
    "log_tool_usage":          LogToolUsageNode,
    # Stop chain
    "finalize_session":        FinalizeSessionNode,
    # Fallback
    "noop":                    NoopNode,
}


def get_node(name: str):
    """Instantiate a node by name. Raises KeyError if not in registry."""
    node_cls = NODE_REGISTRY[name]
    if not isinstance(node_cls, type):
        raise TypeError(f"Registry entry {name!r} must be a class, got {type(node_cls).__name__}")
    return node_cls()
