"""Node registry — maps node names to callables."""
from __future__ import annotations

from langchain_learning.nodes.apply_threshold import ApplyThresholdNode
from langchain_learning.nodes.combination_score import CombinationScoreNode
from langchain_learning.nodes.cwd_domain_detect import CwdDomainDetectNode
from langchain_learning.nodes.gate_check import GateCheckNode
from langchain_learning.nodes.keyword_score import KeywordScoreNode
from langchain_learning.nodes.load_active_task import LoadActiveTaskNode
from langchain_learning.nodes.load_task_history import LoadTaskHistoryNode
from langchain_learning.nodes.load_task_code import LoadTaskCodeNode
from langchain_learning.nodes.load_related_tasks import LoadRelatedTasksNode
from langchain_learning.nodes.load_memories import LoadMemoriesNode
from langchain_learning.nodes.load_task_memories import LoadTaskMemoriesNode
from langchain_learning.nodes.set_active_task import SetActiveTaskNode
from langchain_learning.nodes.log_task_events import LogTaskEventsNode
from langchain_learning.nodes.load_turn import LoadTurnNode
from langchain_learning.nodes.log_tool_usage import LogToolUsageNode
from langchain_learning.nodes.update_tool_keywords import UpdateToolKeywordsNode
from langchain_learning.nodes.memory_domain_signal import MemoryDomainSignalNode
from langchain_learning.nodes.noop import NoopNode
from langchain_learning.nodes.score_tools import ScoreToolsNode
from langchain_learning.nodes.set_prompt_id import SetPromptIdNode

NODE_REGISTRY: dict[str, object] = {
    # UserPromptSubmit chain
    "load_turn":               LoadTurnNode,
    "load_active_task":        LoadActiveTaskNode,
    "load_task_history":       LoadTaskHistoryNode,
    "load_task_code":          LoadTaskCodeNode,
    "load_related_tasks":      LoadRelatedTasksNode,
    "load_memories":           LoadMemoriesNode,
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
    # task_activate chain (task_graph.py)
    "set_active_task":         SetActiveTaskNode,
    "load_task_memories":      LoadTaskMemoriesNode,
    # Stop chain
    "log_task_events":         LogTaskEventsNode,
    # Fallback
    "noop":                    NoopNode,
}


def get_node(name: str):
    """Return a callable node by name. Classes are instantiated; other callables returned as-is."""
    node = NODE_REGISTRY[name]
    if not callable(node):
        raise TypeError(f"Registry entry {name!r} is not callable, got {type(node).__name__}")
    return node() if isinstance(node, type) else node
