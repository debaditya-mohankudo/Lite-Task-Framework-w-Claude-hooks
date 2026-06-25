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
