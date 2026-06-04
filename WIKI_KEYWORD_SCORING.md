# Keyword Scoring — How Prompts Map to Domains

Explains the pipeline from raw prompt text to active domains, which control memory injection and tool hint retrieval.

## Pipeline overview

```text
prompt text
  → tokenize
  → keyword weights (strong/weak) per domain
  → combo bonuses
  → memory domain soft signal
  → threshold filter → active domains
```

Nodes run in order: `keyword_score` → `combination_score` → `memory_domain_signal` → `apply_threshold`.

---

## Step 1 — Tokenize (`keyword_score`)

The prompt is lowercased and tokenized with `\b[\w-]+\b` into a set of tokens. Multi-word phrases are matched via substring search on the full lowercased prompt rather than token lookup.

## Step 2 — Keyword scoring (`keyword_score`)

Config (`domain_classifier.json`) defines per-domain `keyword_signals` with two tiers:

```text
strong signals → higher weight (e.g. 3)
weak signals   → lower weight  (e.g. 1)
```

For each domain, if any `negative_signals` word appears in the prompt, the domain is **skipped entirely**. Otherwise, each matching strong/weak signal adds its weight to `classifier_scores[domain]`. Matched tokens are collected into `matched_keywords`.

Node: `langchain_learning/nodes/keyword_score.py`

## Step 3 — Combination bonuses (`combination_score`)

Bigram/trigram combos from `combination_signals` are checked — if **all** words in a combo are present in the token set, a bonus score is added on top. Example: `["dasha", "transit"]` together might score an extra 2 for the `astrology` domain.

Node: `langchain_learning/nodes/combination_score.py`

## Step 4 — Soft signal from memories (`memory_domain_signal`)

The top-3 injected memories (already loaded from `MEMORY.sqlite`) have their `domain` fields checked. Any non-`global` domain found in memories but not yet in state gets appended to `domains`. This is a soft prior — it nudges toward domains active in recent turns without requiring prompt keywords.

Node: `langchain_learning/nodes/memory_domain_signal.py`

## Step 5 — Threshold gate (`apply_threshold`)

`classifier_scores` are filtered: only domains with `score >= classify_threshold` (default: 2) survive. If nothing crosses the threshold, the `default_domain` (usually `macos`) is used as fallback.

Final `domains` = union of:

- CWD-detected domains (from `cwd_domain_detect`)
- Memory-signal domains (from `memory_domain_signal`)
- Scored domains that crossed the threshold

`matched_keywords` are merged into `keywords`, then **stopword-filtered** via `core.stopwords.filter_keywords` before being written to state. This strips noise words (grammar words, path fragments, short tokens, hex IDs) so only meaningful terms reach BM25 scoring in `score_tools`.

Node: `langchain_learning/nodes/apply_threshold.py`

---

## Stopword filtering — two passes

Stopwords are filtered at two points:

1. **During scoring (`apply_threshold`)** — `matched_keywords` are filtered before merging into `keywords`. Prevents noise from reaching BM25 tool scoring in the same turn.

2. **At session end (`finalize_session`)** — accumulated session `keywords` are filtered again before being persisted to `sessions.db`. Keeps session summaries clean for future BM25 retrieval across turns.

Both passes use `core.stopwords.filter_keywords`, which strips grammar words, path fragments, short tokens (<3 chars), pure hex IDs, and tool-call IDs.

Stopwords list: `hooks/core/stopwords.json`
