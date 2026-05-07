# Structured Endpoint Rewrite Follow-Ups

## Context

The edit core now resolves endpoint expressions to physical endpoint keys before
rewriting source YAML. This means internal edits can split aggregate expressions:

```yaml
nets:
  USB_D_P:
    - J1.D+/all
```

Removing `J1.D+@A6` rewrites to:

```yaml
nets:
  USB_D_P:
    - J1.D+@B6
```

Adding the last missing duplicate pin collapses back to `/all`. The same
resolved-key rewrite path is used for `no_connects`. The edit core also supports
internal symbol-reference renames and net renames, updating coupled schema
locations before validation.

## Remaining Work

The edit core is still intentionally internal. Future useful work should build
larger semantic operations on top of this substrate instead of exposing tiny
list-mutation commands.

Good next candidates:

- Move endpoint groups between nets as one validated transaction.
- Extract a set of symbols/nets into a child sheet and rewrite sheet ports.
- Support explicit net merge semantics for rename-like operations.
- Preserve source comments around rewritten endpoint lists if ruamel node
  mutations need more care.

## Non-Goal

Do not add more public CLI verbs for small YAML list edits unless the command is
clearly easier and safer than direct `.ksch.yaml` editing followed by
`ksch validate`, `ksch gen`, and `ksch verify`.
