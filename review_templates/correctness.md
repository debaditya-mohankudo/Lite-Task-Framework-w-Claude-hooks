---
name: correctness
domain: claude-hooks
context_prompt: >
  Review the task implementation for correctness. Check the code changes, task body,
  and any subtasks for the issues below. For each item respond with pass/fail and a
  one-line note explaining your reasoning.
---

## Auto items

- [auto] c1: Are all session state keys written only by their owning node (no cross-node key mutation)?
- [auto] c2: In parallel fan-in nodes, are state keys read before being written in the same node call?
- [auto] c3: Are exceptions caught and logged with context, not silently swallowed?
- [auto] c4: Do all early-return paths return a valid dict with the expected keys?
- [auto] c5: Are DB connections closed or used as context managers (no connection leaks)?

## Manual items

- [manual] m1: Tested against a real session with an active task set
- [manual] m2: Edge case verified: no active task (node skips cleanly)
- [manual] m3: Log messages include enough context to debug without reading source
