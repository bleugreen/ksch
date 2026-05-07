# Structured Edits Need Endpoint Expression Splitting

## Context

The internal `disconnect_endpoints` edit primitive currently removes exact
endpoint strings from a net. That is correct for explicit schema entries such as
`J1.D+@A6`, but it does not yet edit inside aggregate endpoint expressions such
as `J1.D+/all`.

Example:

```yaml
nets:
  USB_D_P:
    - J1.D+/all
```

Disconnecting `J1.D+@A6` from that net should eventually rewrite the aggregate
expression into the remaining physical pins, for example:

```yaml
nets:
  USB_D_P:
    - J1.D+@B6
```

## Desired Shape

The edit layer should operate on resolved physical endpoints while still
preserving compact source expressions when they remain semantically correct.

Needed pieces:

- An endpoint-expression expander that maps each source endpoint to resolved
  physical endpoint keys.
- A rewrite operation that can subtract physical endpoint keys from one source
  endpoint expression.
- Deterministic formatting for rewritten endpoint lists.
- Tests for `/all`, explicit `@pin`, and bare unique pin-name expressions.

## Why This Is Not In The Current Slice

The current inverse edit slice keeps these operations as internal validated
primitives. Splitting aggregate endpoint syntax is a larger source rewrite
problem and should be solved as a first-class semantic edit primitive rather
than hidden inside string-list mutation.
