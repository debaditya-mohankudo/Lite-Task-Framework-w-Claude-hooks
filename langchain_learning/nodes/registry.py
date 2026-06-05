"""Node registry — maps node names to callable classes."""
from __future__ import annotations

from langchain_learning.nodes.apply_threshold import ApplyThresholdNode
from langchain_learning.nodes.combination_score import CombinationScoreNode
from langchain_learning.nodes.cwd_domain_detect import CwdDomainDetectNode
from langchain_learning.nodes.gate_check import GateCheckNode
from langchain_learning.nodes.keyword_score import KeywordScoreNode
from langchain_learning.nodes.load_memories import LoadMemoriesNode
from langchain_learning.nodes.load_open_tasks import LoadOpenTasksNode
from langchain_learning.nodes.load_session_context import LoadSessionContextNode as LoadPromptContextNode
from langchain_learning.nodes.log_task_events import LogTaskEventsNode
from langchain_learning.nodes.load_turn import LoadTurnNode
from langchain_learning.nodes.log_tool_usage import LogToolUsageNode
from langchain_learning.nodes.update_tool_keywords import UpdateToolKeywordsNode
from langchain_learning.nodes.memory_domain_signal import MemoryDomainSignalNode
from langchain_learning.nodes.noop import NoopNode
from langchain_learning.nodes.score_tools import ScoreToolsNode
from langchain_learning.nodes.set_prompt_id import SetPromptIdNode

NODE_REGISTRY: dict[str, type] = {
    # UserPromptSubmit chain
    "load_turn":               LoadTurnNode,
    "load_memories":           LoadMemoriesNode,
    "load_open_tasks":         LoadOpenTasksNode,
    "load_prompt_context":     LoadPromptContextNode,
    # classify chain
    "cwd_domain_detect":       CwdDomainDetectNode,
    "keyword_score":           KeywordScoreNode,
    "combination_score":       CombinationScoreNode,
    "memory_domain_signal":    MemoryDomainSignalNode,
    "apply_threshold":         ApplyThresholdNode,
    # downstream
    "score_tools":             ScoreToolsNode,
    "set_prompt_id":           SetPromptIdNode,
    # PreToolUse chain
    "gate_check":              GateCheckNode,
    # PostToolUse chain
    "log_tool_usage":          LogToolUsageNode,
    "update_tool_keywords":    UpdateToolKeywordsNode,
    # Stop chain
    "log_task_events":         LogTaskEventsNode,
    # Fallback
    "noop":                    NoopNode,
}


def get_node(name: str):
    """Instantiate a node by name. Raises KeyError if not in registry."""
    node_cls = NODE_REGISTRY[name]
    if not isinstance(node_cls, type):
        raise TypeError(f"Registry entry {name!r} must be a class, got {type(node_cls).__name__}")
    return node_cls()
