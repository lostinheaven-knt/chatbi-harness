# Domain Docs

This repository uses a single-context domain-documentation layout.

## Before exploring or changing the system

Read these sources when they exist:

- `CONTEXT.md` at the repository root
- Relevant architectural decision records under `docs/adr/`

Missing domain documents do not block exploration. Create them only when
domain concepts or decisions need to be recorded.

## Vocabulary

Use the terms defined in `CONTEXT.md` in issues, plans, tests, and code. Avoid
introducing synonyms for established concepts. If a required concept is absent,
record the gap for domain modeling rather than silently inventing terminology.

## Architectural decisions

When a proposal conflicts with an existing ADR, identify the conflict
explicitly instead of silently overriding the decision.

## Expected layout

```text
/
├── CONTEXT.md
├── docs/
│   └── adr/
└── src/
```
