---
tags: hook events, UserPromptSubmit, PreToolUse, PostToolUse, Stop, hook types, event routing, hook payload, claude code hooks, settings.json, hook registration, event_type, hook lifecycle
---
# Claude Code Hook Events

| Event | When it runs |
|---|---|
| `PreToolUse` | Before tool calls (can block them) |
| `PostToolUse` | After tool calls complete |
| `UserPromptSubmit` | When the user submits a prompt, before Claude processes it |
| `Notification` | When Claude Code sends notifications |
| `Stop` | When Claude Code finishes responding |
| `SubagentStop` | When subagent tasks complete |
| `PreCompact` | Before Claude Code is about to run a compact operation |
| `SessionStart` | When Claude Code starts a new session or resumes an existing session |
| `SessionEnd` | When Claude Code session ends |

## Notes

- `PreToolUse` is the only event that can block execution (exit code 2 or JSON `{"decision": "block"}`).
- Hooks are not guaranteed to fire for the first tool calls after `/compact` restores a session — the session must go through a `UserPromptSubmit` first to re-establish checkpoint state.
- `SessionStart` fires on both new sessions and resume, so do not assume a clean state.
