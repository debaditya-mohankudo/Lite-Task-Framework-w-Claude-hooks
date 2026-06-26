# Jinja2 — Features and How We Use Them

A reference for Jinja2 patterns that matter in this project.
Sections marked **✓ used here** are actively practiced in `hooks/templates/ui/`.

---

## Template Inheritance

Jinja2's most important feature. A base template defines the page skeleton and named `{% block %}` slots. Child templates extend it and fill in only what changes.

```jinja2
{# base.html — defines the skeleton #}
<html>
  <body>
    {% block sidebar %}{% endblock %}
    {% block detail %}{% endblock %}
  </body>
</html>

{# tasks/list.html — fills in the slots #}
{% extends "ui/base.html" %}

{% block sidebar %}
  ... task-specific sidebar ...
{% endblock %}

{% block detail %}
  ... task list content ...
{% endblock %}
```

**Why it matters:** every page shares one HTML skeleton. Nav, CSS links, JS — change once in `base.html`, all pages update. No copy-paste drift.

**✓ used here:** `tasks/list.html`, `memory/list.html`, `docs/list.html` all extend `ui/base.html` and override `{% block sidebar %}` and `{% block detail %}`.

---

## Includes

Pull a reusable chunk of HTML into any template without inheritance.

```jinja2
{% include "ui/partials/sidebar.html" %}
```

Unlike blocks, includes have no parent/child relationship — they just paste the file in at that point. Variables from the current context are available inside the included file.

**When to use over blocks:** when the same chunk appears in multiple unrelated places, not just as a slot override.

**✓ used here:** `base.html` includes `sidebar.html` and `task_body_fields.html`.

---

## Macros

Macros are Jinja2's equivalent of functions — define once, call anywhere. They can accept arguments and return rendered HTML.

```jinja2
{# Define in icons.html #}
{% macro icon_docs() %}≡{% endmacro %}
{% macro icon_tasks() %}⊞{% endmacro %}

{# Import and call in any template #}
{% from "ui/partials/icons.html" import icon_tasks, icon_docs %}

<span class="mem-nav-icon">{{ icon_docs() }}</span>
```

Macros can take arguments too:

```jinja2
{% macro status_dot(status) %}
  <span class="dot dot-{{ status }}"></span>
{% endmacro %}

{{ status_dot('wip') }}
```

**Why it matters:** one change in the macro file propagates everywhere. Without this, we were editing the same icon character in 4 files and missing some (which is exactly the bug we hit).

**✓ used here:** `partials/icons.html` — all five nav icons (`icon_tasks`, `icon_search`, `icon_memories`, `icon_docs`, `icon_sub`) are defined here and imported in every page template.

---

## Namespace for Loop Variables

A common Jinja2 gotcha: you cannot assign to a variable inside a loop and read it outside — Jinja2 scoping prevents it. The fix is `namespace`.

```jinja2
{# This does NOT work — current_epic stays None outside the loop #}
{% set current_epic = None %}
{% for task in tasks %}
  {% set current_epic = task.id %}  {# scoped to the loop, lost after #}
{% endfor %}

{# This works — namespace object persists across loop iterations #}
{% set ns = namespace(current_epic=None) %}
{% for task in tasks %}
  {% set ns.current_epic = task.id %}
{% endfor %}
{{ ns.current_epic }}  {# readable here #}
```

**✓ used here:** `sidebar.html` uses `{% set ns = namespace(current_epic=None) %}` to track the current epic while iterating the task tree, so child tasks can be grouped under their parent.

---

## Filters

Filters transform a value using the `|` pipe syntax. Jinja2 ships many built-in filters; you can also register custom ones in Python.

```jinja2
{{ memories|length }}          {# count items #}
{{ 'hello world'|title }}      {# Hello World #}
{{ tag.strip()|lower }}        {# normalize whitespace + case #}
{{ (task.tags or '')|split(',') }}  {# split string to list #}
```

**Chaining:** filters can be chained left to right.

```jinja2
{{ text|strip|lower|truncate(40) }}
```

**✓ used here:**
- `|length` — task/memory counts in list headers
- `|safe` — render pre-converted markdown HTML without escaping (`{{ selected_html | safe }}`)
- `|split` — tag strings from the DB are comma-separated; split before iterating
- `|startswith` — filter tag lists inline inside `{% for %}` conditions

---

## Inline For-Loop Filtering

Jinja2 allows an `if` guard directly in a `{% for %}` tag to skip items without a separate `{% if %}` inside.

```jinja2
{# Only render tags that aren't domain: or type: prefixed #}
{% for tag in (m.tags or '').split(',') if tag.strip() and not tag.strip().startswith(('domain:', 'type:')) %}
  <span class="tag">{{ tag.strip() }}</span>
{% endfor %}
```

This keeps templates compact — no nested if/endif blocks for simple filtering.

**✓ used here:** tag rendering in `memory/list.html` and `partials/task_detail.html` both use inline for-if to strip system tags before display.

---

## The `or` Default Pattern

When a value might be `None`, use `or` to provide a fallback inline.

```jinja2
{{ task.tags or '' }}          {# avoid None errors on .split() #}
{{ selected_title or 'Docs' }} {# default heading when no doc selected #}
```

Cleaner than a full `{% if %}` block for simple defaults.

**✓ used here:** throughout — tag fields from the DB can be `None`; `(task.tags or '').split(',')` is the standard pattern.

---

## Conditional Classes

Jinja2 inline `{% if %}` is idiomatic for conditional CSS classes:

```jinja2
<a class="mem-nav-link {% if status == s %}mem-nav-active{% endif %}">

<li class="task-row
    {% if task.status == 'wip' %}is-wip
    {% elif task.status == 'done' %}is-done
    {% elif task.status == 'blocked' %}is-blocked
    {% endif %}
    {% if task.id == active_task_id %} is-active{% endif %}">
```

**✓ used here:** nav active states, task row status classes, chip active states — all use this pattern.

---

## Environment Globals

`JINJA_ENV.globals` injects a value once into the environment — every template can use it without passing it in the `render()` call.

```python
# hooks/ui/deps.py — defined once at startup
JINJA_ENV.globals["urls"] = {
    "tasks":        "/ui/tasks/",
    "memory":       "/ui/memory/",
    "docs":         "/ui/docs/",
    "search":       "/ui/search",
    "cockpit":      "/ui/cockpit",
    "sidebar":      "/ui/sidebar",
    "tasks_create": "/ui/tasks",
    "body_fields":  "/ui/tasks/body-fields",
    "tasks_new":    "/ui/tasks/new",
}
```

```jinja2
{# Any template — no import, no context variable needed #}
<a href="{{ urls.tasks }}">Tasks</a>
<form hx-post="{{ urls.tasks_create }}">
hx-get="{{ urls.memory }}{{ m.name }}"
```

**Why it matters:** route paths were previously hardcoded as strings in every template. Moving them here means a route rename is a one-line change in `deps.py` — not a grep-and-replace across 8 files.

**✓ used here:** `urls` is injected globally. All nav links, HTMX `hx-get`/`hx-post` attributes, and breadcrumb links now use `{{ urls.* }}` instead of literal `/ui/*` strings.

---

## What We Are Not Using Yet

| Feature | What it does | Worth adding? |
| ------- | ------------ | ------------- |
| **Custom filters** | Register Python functions as `\|myfilter` in Jinja env | Yes — `format_date`, `truncate_id` would clean up repeated inline logic |
| **Tests** (`is defined`, `is none`) | Cleaner than `!= None` checks | Minor improvement |
| **Macro arguments with defaults** | `{% macro badge(type, size='sm') %}` | Useful if badges/chips get more variants |
