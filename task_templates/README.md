# task_templates

Quick-reference body templates for `tasks__create`, one per `issue_type`.

The dispatcher gate (`hooks/dispatcher.py` → `_TASK_BODY_SECTIONS`) rejects any
`tasks__create` whose body doesn't start with `Type: <type>` and contain the
required sections. Copy the matching template, fill it in, and pass as `body`.

| issue_type | required sections |
|------------|-------------------|
| [feature](feature.md)   | Task, Resolution, Motivation, Files |
| [bug](bug.md)           | Task, Resolution, Cause, Files |
| [research](research.md) | Task, Finding, Context, Files |
| [misc](misc.md)         | Task, Resolution, Notes, Files |
| [epic](epic.md)         | Task, Resolution, Notes, Files |

Notes:
- Section order is flexible; only presence of each `Label:` is checked.
- For epics, prefer `tasks__create_epic(title, motivation, ...)` which builds the
  body for you and skips this gate.
- `Resolution:`/`Finding:` can be `(pending)` or `TBD` while the task is open.
- These are reference scaffolds — keep them in sync with `_TASK_BODY_SECTIONS`.
