---
tags: architecture overview, claude-hooks, hook server, MCP tools, LangGraph, session graph, memory system, gate framework, task framework, observability, FastAPI, uvicorn, system overview, components
---
# claude-hooks Architecture

> This document describes the system as built — the decisions made, why they were made, and the constraints that shaped the design.

---

## Overview

`claude-hooks` is a Python system that intercepts all four Claude Code hook events and runs a **LangGraph StateGraph pipeline** in response. Its responsibilities are:

1. **Memory injection** — score and inject relevant memories from `MEMORY.sqlite` into every prompt
2. **Tool hint surfacing** — retrieve relevant MCP tools based on prompt intent and domain
3. **Anti-hallucination gating** — hard-block irreversible MCP tool calls unless a prerequisite tool actually ran this prompt
4. **Tool usage tracking** — accumulate latency and keyword signals per MCP tool for future retrieval
5. **Task tracking** — inject persistent work context (history, code chunks, memories) for the active task

---

## Major Components

| Component | Responsibility |
| --- | --- |
| FastAPI Server | Persistent hook endpoint — resident for the lifetime of Claude Code |
| LangGraph StateGraph | Orchestrates hook pipelines (UPS, PreToolUse, PostToolUse, Stop) |
| Memory System | Retrieves and injects relevant long-term memories per prompt |
| Task Framework | Maintains persistent work context across sessions |
| Gate Framework | Prevents unsafe / irreversible tool execution |
| Tool Hint Engine | Recommends relevant MCP tools based on prompt intent and domain |
| Observability | Records tool latency, keywords, and structured logs |

---

## Design Principles

- **Hooks orchestrate; MCP servers own domain logic.** Project databases stay inside MCP servers — hooks never reach across that boundary.
- **All safety decisions are deterministic and explainable.** Gate checks are rule-based, not probabilistic.
- **Session state is the source of truth.** `SessionState` fields in the LangGraph checkpoint carry all cross-hook context — no DB-as-IPC.
- **Modular graph nodes that can evolve independently.** Each node is a callable class; adding behavior means adding a node, not editing existing ones.

---

## Design Constraints

- Low-latency execution on every hook — every millisecond is user-perceived latency
- Persistent session state across prompts without relying on Claude's in-context memory
- No direct access to project databases — hooks only touch their own DBs
- Deterministic gate evaluation — no heuristics that can false-positive on normal prompts
- Modular graph nodes that can evolve independently without coupling

---

## Extensibility

The architecture is designed to support:

- Additional gate policies (new `@prereq` gate classes)
- New memory retrieval strategies (swap `CombinationSignalRetriever` via Protocol)
- Multiple MCP servers and domains
- Richer task graphs and subtask hierarchies
- Improved retrieval algorithms (BM25 → hybrid or vector)
- Additional observability pipelines

---

## System Diagram

```mermaid
flowchart TD
    CC[Claude Code] -->|Hook event| FS[FastAPI Server\nport 8766]

    FS --> UPS[UserPromptSubmit]
    FS --> PTU_pre[PreToolUse]
    FS --> PTU_post[PostToolUse]
    FS --> STOP[Stop]

    subgraph UPS Pipeline
        UPS --> ST[set_prompt_id\nload_turn]
        ST --> PAR[Parallel branch]
        PAR --> LM[load_memories\nCombinationSignalRetriever]
        PAR --> LT[load_related_tasks\ndiff RAG + code RAG]
        PAR --> SC[score_tools\nKeywordOverlapScorer]
        PAR --> CD[cwd_domain_detect]
        LM & LT & SC & CD --> SP[build additionalSystemPrompt]
        SP --> LE[log_task_events]
    end

    subgraph PreToolUse Pipeline
        PTU_pre --> GC[gate_check\nDefaultGatePolicy]
        GC -->|allow| ALLOW[200 proceed]
        GC -->|deny| BLOCK[200 block + reason]
    end

    subgraph PostToolUse Pipeline
        PTU_post --> LU[log_tool_usage\nlatency + keywords]
    end

    SP -->|## Injected memories\n## Suggested tools\n## Task history| CC

    LM -.->|BM25 scoring| MEMDB[(MEMORY.sqlite)]
    SC -.->|keyword overlap| THDB[(tool_hints.sqlite)]
    LT -.->|task graph| TASKDB[(proj_tasks.db)]
    LU -.->|update hints| THDB
    FS -.->|structured logs| LOGDB[(claude_hooks.sqlite)]
```

---

## Sections

- [State Architecture](arch/state.md) — FastAPI persistent server, MemorySaver as session bus, SessionState fields
- [Graph & Pipeline](arch/graph_pipeline.md) — Graph topology, UPS pipeline, domain classification, anti-hallucination gate, tool tracking
- [System Prompt](arch/system_prompt.md) — All `additionalSystemPrompt` sections and what populates them
- [Task Framework](arch/task_framework.md) — Task lifecycle, activation flow, context injection, auto-close
- [Mid-Task Decisions](arch/mid_task_decisions.md) — Explicit decision tracking, checkpoint persistence, session restore via /task-task-log-decision
- [Databases, MCP & Observability](arch/databases.md) — Database files, MCP tool hosting, logging architecture
- [Gates](arch/gates.md) — Gate framework, all current gates, how to add a new one
- [MCP / Hooks Boundary](arch/mcp_hooks_boundary.md) — Ownership rule: MCP owns domain DBs, hooks own checkpoint; PostToolUse bridge nodes
- [Design Decisions](arch/design_decisions.md) — Key choices and rationale; what this system is not
- [New Repo Onboarding](new_repo_onboarding.md) — How to register a new project into `cwd_domains.json` and seed memories
- [Setup Guide](setup.md) — Getting claude-hooks running from scratch; database creation, hook registration, env vars
