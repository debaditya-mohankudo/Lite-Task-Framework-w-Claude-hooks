---
name: switch-project
description: Override the active project domain for this session. All subsequent UserPromptSubmit turns will use the specified domain instead of the CWD-derived one.
updated: 2026-06-12
---

# /switch-project

Override the project domain for this session. Useful when CWD doesn't map to the right domain, or when working across repos in one session.

## Valid domains

Defined in `src/config.py` `VALID_DOMAINS`:
- `claude-hooks`
- `vault`
- `market-intel`
- `astrology`
- `macos`
- `global`
- `misc` — non-CWD work; set explicitly via `/switch-project misc`

## Steps

### 1. Get session_id

Read from `## Turn state` in the system prompt:
```
## Turn state
- session_id: <uuid>
```

### 2. Validate the domain argument

The argument to `/switch-project` is the domain name.

**If no argument is provided**, show the list and ask the user to choose:

```
Available domains:
1. claude-hooks
2. vault
3. market-intel
4. astrology
5. macos
6. global
7. misc

Which domain? (or "clear" to revert to CWD detection)
```

Then stop and wait for the user's reply before proceeding to Step 3.

**If an invalid domain is given**, show the same list with the error and stop.

To clear the override and revert to CWD-based detection, the user can say `/switch-project clear` (pass `""` as domain).

### 3. Run the switch script

```bash
cd ~/workspace/claude-hooks && uv run python scripts/task_activate.py switch_project <domain> <session_id>
```

For clear:
```bash
cd ~/workspace/claude-hooks && uv run python scripts/task_activate.py switch_project "" <session_id>
```

### 4. Confirm

Reply:
```
Switched to project domain: <domain>
All following turns will use domain '<domain>' instead of CWD detection.
```

Or for clear:
```
Project domain override cleared — reverting to CWD-based detection.
```

## Example

```
/switch-project vault
→ Switched to project domain: vault
```

## Notes

- The override persists in the LangGraph checkpoint for the session — it survives across hook invocations but resets when a new session starts.
- `cwd_domain_detect` checks `project_domain_override` first; if set and non-empty, CWD map is skipped entirely.
- `valid_domains` lives in `src/config.py` `VALID_DOMAINS` — update there when adding a new project.
