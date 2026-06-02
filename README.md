# claude-hooks

A personal memory and session layer for Claude Code — making conversations feel continuous and context-aware.

---

## The problem it solves

Claude Code conversations are stateless. Every new session starts cold: Claude has no recollection of what you've been working on, your preferences, your past corrections, or the tools you reach for regularly. You end up repeating yourself constantly.

This project adds persistent memory and session awareness to Claude Code, so it can carry context forward across conversations without you having to re-explain your world every time.

---

## What it does

### Long-term memory

You can store facts about yourself, your preferences, and ongoing projects in a persistent memory store. These get scored and injected into every conversation automatically, before Claude sees your message. It knows your role, your corrections, your project context — without you typing it.

Memories are organized by domain (e.g., work, market, Mac tools, astrology) and prioritized so the most relevant ones surface first.

### Session awareness

Every conversation is tracked as a session. Claude can see summaries of your two most recent relevant sessions — so if you were working on something yesterday, today's Claude knows what you explored, what decisions were made, and what was left open.

### Smart tool suggestions

If you regularly use certain tools for certain kinds of tasks, the system learns that pattern. When you ask about something in that domain, it hints Claude toward the right tools — reducing the guesswork and tool-choice overhead at the start of each task.

### Tool guardrails

A pre-tool-use gate lets you define rules about what tools can or can't be called — useful for protecting sensitive operations or preventing accidental side effects.

---

## How context reaches Claude

Every time you submit a prompt:

1. The system reads your message and figures out what domain(s) it touches (work, market, personal tools, etc.)
2. It fetches relevant memories, tool hints, and session summaries in parallel
3. All of this is injected silently into Claude's system context before it responds

Claude never sees the raw databases — it just sees a coherent context block, as if a well-briefed colleague handed it a briefing note before the conversation started.

---

## What you can store

| Type | What it captures |
|---|---|
| **User** | Who you are, your expertise, how you like to work |
| **Feedback** | Corrections and preferences — things to stop or keep doing |
| **Project** | Ongoing work, decisions, deadlines, motivations |
| **Reference** | Where to look things up (Linear boards, dashboards, Slack channels) |

---

## The MCP server

Beyond the automatic injection, this project also exposes all memory and session data as MCP tools — so Claude can read, write, and search memories mid-conversation when needed. This lets Claude update its own memory when it learns something new, or pull up session history on demand.

---

## In short

This is a lightweight personal knowledge layer that wraps Claude Code — turning a stateless tool into something that feels like a continuous working relationship.
