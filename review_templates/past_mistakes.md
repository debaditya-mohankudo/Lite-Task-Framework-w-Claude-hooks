---
name: past-mistakes
description: Recurring bug patterns learned from real bugs in the claude-hooks project — use as a checklist lens when reviewing new code
metadata:
  type: reference
  domain: claude-hooks
---

# Past Mistakes — claude-hooks

Derived from 13 bug tasks (issue_type=bug) across the project history. Grouped by pattern.

---

## 1. Fail-open guards (bugs: 279435fa)

**What happened:** `imessage__send` gate name check was skipped when `recent_prompt_texts` was empty. The guard was defensive but it made the gate fail-open — sends went through without recipient verification.

**Pattern:** Any guard that says "if context is missing, skip enforcement" is fail-open. Security/safety checks must fail-closed or hard-error, never silently allow.

**Lesson:** Never use `if not collection: return ALLOW`. Use `if not collection: return DENY` or raise. The absence of context should increase suspicion, not grant access.

---

## 2. State clobber on conditional writes (bugs: 279435fa)

**What happened:** `run_post_tool` was setting `prompt=""` in checkpoint unconditionally, overwriting the value set by `set_prompt_id` node. Same-turn gate checks saw empty prompt.

**Pattern:** Writing a field back to shared state (checkpoint, session state) without checking if the incoming value is meaningful. `state["x"] = value` when value can be `""` / `None` / `0` silently erases prior good data.

**Lesson:** Conditional writes: `if value: state["x"] = value`. Only overwrite with a non-empty value unless explicit reset is intended.

---

## 3. Split code paths with inconsistent logging/behavior (bugs: d18b3445)

**What happened:** Tests exercised the LangGraph `gate_check` node path (which used `lc.hooks.gates` detailed logging). The live dispatcher called gate logic directly — no detailed logs. Gate bugs in production were invisible.

**Pattern:** Two paths to the same logic (one for tests, one for live) diverge in behavior. The path that looks right in tests isn't the path that runs in production.

**Lesson:** If a function is exercised through a test harness via a different call chain than production, gate/logging behavior differences can hide real bugs. Trace both entry points when changing gate code.

---

## 4. Missing flush in FastAPI vs CLI path (bugs: 66651b8c)

**What happened:** CLI `dispatcher.py` had `flush_logs()` in a `finally` block. FastAPI `server.py` called the same handlers but never flushed — gate internal logs silently dropped in production.

**Pattern:** When porting a handler from CLI to HTTP server (or vice versa), lifecycle calls (flush, close, commit) in the original path don't automatically carry over.

**Lesson:** Every HTTP endpoint that produces logs/DB writes needs its own `finally: flush_logs()` / `conn.commit()`. Don't assume parity with CLI equivalents.

---

## 5. Test data leaking into production stores (bugs: de23d635)

**What happened:** Integration tests wrote tool usage records (including raw iMessage content from fixtures) into production `tool_hints.sqlite`. No guard existed in `LogToolUsageNode`.

**Pattern:** Any node/handler that writes to a shared store can be exercised by tests without a test-mode guard. The production DB gets polluted with synthetic or sensitive fixture data.

**Lesson:** For any node that writes to production stores, add a session_id prefix guard early (`if session_id.startswith(("test-", "api-test-", "pytest-")): return`). Document the naming contract in test fixtures.

---

## 6. Phantom "tracking" — misleading debug logs (bugs: abec2956)

**What happened:** Gate log showed `current=[]` which looked like mail__read hadn't been tracked. It was tracked — just under a prior prompt in session history. `current` only held the current prompt's tools.

**Pattern:** A variable named `current` or `recent` in a debug log that reflects a scoped subset of state can look like the full state to someone debugging. The log becomes a false alarm.

**Lesson:** When logging collections that are intentionally scoped (e.g., `current_prompt_tools`, not `session_tools`), use the scoped name in the log key. Don't name a partial view `current` if the full view is what people will expect.

---

## 7. Import drift — code removed upstream, import left behind (bugs: 388bb888)

**What happened:** `handle_neighbors` was removed/renamed in `claude_for_mac_local/src/tools/tasks.py`. `load_related_tasks.py` in claude-hooks still imported it. Related tasks silently returned empty on every turn for days before discovery.

**Pattern:** Cross-repo dependency where one repo is updated and the other's import isn't. Silent failure (empty result) rather than hard crash meant it went undetected.

**Lesson:** When removing or renaming a public function in a shared library, grep dependents in other repos before committing. If a function is used across repos, deprecation > deletion in one step.

---

## 8. Schema concerns leaking between prod and test (bugs: bd302aad, 92e11e9b)

**What happened:** `run_id` was added to prod schema (`_SCHEMA`) because tests needed it for scoping. Every prod row had `run_id=NULL` — schema noise. The fix (remove from prod) wasn't regression-tested so it could silently re-enter.

**Pattern:** Test-only schema concerns added to shared prod definitions. And: fixes without tests create the next regression.

**Lesson:** Separate prod schema from test schema extensions. When removing a field from prod, add a test that asserts the column is absent from the real schema.

---

## 9. Replay/sampling non-diversity (bugs: fd6e7016)

**What happened:** `replay_harness.py` returned all UPS pairs ordered by time — long active sessions dominated and filled the entire replay limit with one session's events.

**Pattern:** Any sampling that pulls from an ordered log without deduplicating by key will over-represent the most active entities. "Last N events" ≠ "N representative sessions."

**Lesson:** When building coverage/baseline tools, always deduplicate by the unit of diversity (session, user, task) before applying limits. `LIMIT 50` on raw events ≠ 50 unique sessions.

---

## 10. Prompt ID traceability gap — gate decisions orphaned (bugs: 24c101ae)

**What happened:** Gates logged `prompt=?` when no checkpoint existed yet (first turn, new session). Gate ALLOW/DENY decisions couldn't be correlated back to any user prompt — forensic dead end.

**Pattern:** Tracing IDs (prompt_id, session_id, task_id) assumed present in state aren't always there. First-turn and fresh-session edge cases fall through.

**Lesson:** Any log entry that records a decision should have a non-null trace ID even when context is bootstrapping. Generate a fallback (`hash(session_id + timestamp)`) rather than logging `?`.

---

## Summary table

| # | Pattern | Category |
|---|---------|----------|
| 1 | Fail-open security guard | Gates / Safety |
| 2 | State clobber on conditional write | State management |
| 3 | Split prod/test code paths diverge | Architecture |
| 4 | Missing flush/lifecycle in server port | HTTP migration |
| 5 | Test data polluting prod stores | Test isolation |
| 6 | Misleading scoped debug log variable names | Observability |
| 7 | Import drift across repos | Cross-repo coupling |
| 8 | Test schema concerns in prod definition | Schema discipline |
| 9 | Sampling without diversity deduplication | Tooling |
| 10 | Trace ID missing in first-turn edge case | Observability |
