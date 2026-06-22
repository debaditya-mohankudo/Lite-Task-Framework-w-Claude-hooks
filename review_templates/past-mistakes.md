---
name: past-mistakes
domain: claude-hooks
context_prompt: >
  Review the implementation against known recurring bug patterns in this project.
  Before evaluating each item, call think__think to reason through whether this
  pattern could apply to the change. Then respond with pass/fail and a one-line note.
---

## Auto items

- [auto] pm1: Does any guard return ALLOW when its context is missing? (fail-open security — pattern #1)
- [auto] pm2: Are there conditional state writes that could clobber prior good data with empty/None? (pattern #2)
- [auto] pm3: Are there two code paths (test vs prod) to the same logic that could diverge silently? (pattern #3)
- [auto] pm4: Does the HTTP endpoint have flush/commit lifecycle calls that the CLI path has? (pattern #4)
- [auto] pm5: Do all DB/store writers have a test-session prefix guard? (pattern #5)
- [auto] pm6: Are scoped collections logged with scoped names, not misleading "current"/"recent"? (pattern #6)
- [auto] pm7: Were cross-repo dependents grepped before renaming/removing a public function? (pattern #7)
- [auto] pm8: Are test-only schema concerns kept out of the prod table definition? (pattern #8)

## Manual items

- [manual] m1: Sampling/replay tools deduplicate by key before applying limits (pattern #9)
- [manual] m2: Trace IDs (prompt_id, session_id) are present even in first-turn edge cases (pattern #10)
