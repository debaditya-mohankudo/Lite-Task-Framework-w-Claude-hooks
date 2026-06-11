---
name: gc
description: Git commit wrapper — stage all changes and commit with a derived message. Works on any git repo. Use when you need to commit code changes.
user-invocable: true
updated: 2026-06-11
wiki: "[[Documentation/Tools/SKILLS_WIKI#Developer Workflow Skills]]"
---

Git commit operations via `git_local.sh`.

## Intent

Designed for use during subtask workflows — commit each subtask without interrupting flow, then push manually once the parent task is closed. `/gc` never pushes; push is a deliberate end-of-task action.

## How to use this skill

When invoked directly (e.g. `/gc`), derive commit messages from session context — do not ask the user for them. Commit immediately without asking.

Always append a task id to the commit message body when one is available. Sources in priority order:
1. Explicitly passed as an argument: `/gc task:abc123` — use this id regardless of active task state
2. Active task visible in `## Active task` in the system prompt

```
feat(area): short description

task:abc123
```

## Grouping changes into multiple commits

When the session produced multiple distinct tasks, prefer splitting into one commit per task rather than one big commit. Use judgment — don't force a split when changes are tightly coupled.

**How to group:**
- Run `git status --short` and `git diff --stat HEAD` to see all changed files
- Infer task boundaries from file paths, layer (e.g. hooks vs server vs MCP tools), and what you know was done this session
- Propose the grouping to the user with one-line commit messages before committing
- Get confirmation, then commit each group with `git add <files> && git commit -m "..."`
- If `git_local.sh` is used, reset after the first commit if it grabs everything, re-commit in groups

**Guideline, not a rule:** If all changes belong to one coherent task, a single commit is fine. The goal is a readable history, not artificial splitting.

## Determining the target repo

Use the repo where the relevant changes were made — this is not always the primary working directory (`claude_for_mac_local`). Determine it from context:
- If the user just edited vault files → use `--repo ~/workspace/claude_documents`
- If changes are in the current project → omit `--repo` (uses CWD)
- If the user specifies a path explicitly → use `--repo <that path>`

## Running tests before commit

Before committing, check if a test suite exists in the repo and run it:

- `tests/` directory exists → run `uv run python -m pytest tests/ -q`
- If tests fail, report the failures and **do not commit** — ask the user how to proceed
- If tests pass, proceed with commit
- If no test suite found, skip and commit directly

## Committing changes (dry-run preview)

```bash
~/workspace/claude_for_mac_local/tools/git_local.sh [--repo <path>] "Your commit message here"
```

Shows: repo root, git status, staged/unstaged changes, commit message.

## Committing changes (confirmed)

```bash
~/workspace/claude_for_mac_local/tools/git_local.sh -y [--repo <path>] "Your commit message here"
```

## After committing

Confirm to the user: `✓ Committed: "Your commit message"`. Include the repo name when it's not the default project.

## Code graph refresh

After every successful commit, check if the repo has `scripts/build_code_graph.py`. If it does, refresh the graph and embeddings to keep them in sync with the new HEAD.

**Always rebuild the code graph fully:**
```bash
uv run python scripts/build_code_graph.py
```

**For embeddings — incremental update using only changed files:**

Use the MCP tool if available (preferred — no subprocess overhead):
```python
changed = [f for f in subprocess.check_output(
    ["git", "diff", "--name-only", "HEAD~1", "HEAD"]
).decode().splitlines() if f.endswith((".py", ".md"))]

if changed:
    mcp__claude-hooks__code_rag__index_files(files=changed)
else:
    pass
```

Or via shell fallback:
```bash
changed=$(git diff --name-only HEAD~1 HEAD | grep -E '\.(py|md)$')
if [ -n "$changed" ]; then
    uv run python scripts/build_code_embeddings.py --files $changed
else
    uv run python scripts/build_code_embeddings.py
fi
```

If `.code_embeddings.tvim` does not exist yet, `--files` automatically falls back to a full rebuild.

Run silently — only surface output if errors. This keeps `meta.commit` current for forensic use and the RAG index fresh for `/explain`.

If the script errors:
- Check the error message (e.g., "not in a git repository", merge conflicts)
- Suggest the user resolve any conflicts or check branch state

## Examples

**Commit in the current project:**
```bash
~/workspace/claude_for_mac_local/tools/git_local.sh -y "Fix authentication bug"
```

**Commit in the vault repo:**
```bash
~/workspace/claude_for_mac_local/tools/git_local.sh -y --repo ~/workspace/claude_documents "Add Index Terms to astrology notes"
```

**Commit in an arbitrary repo:**
```bash
~/workspace/claude_for_mac_local/tools/git_local.sh -y --repo ~/workspace/some-other-repo "Update config"
```
