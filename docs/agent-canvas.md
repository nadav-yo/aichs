# Agent Canvas Restriction Model

The canvas graph is a planning and run contract, not a free-form diagram. Restrictions should make runs predictable without forcing the graph into artificial complexity.

## Hard Constraints

These should fail instead of autocorrecting:

- Patch payloads must be valid and atomic.
- Node kind, title, status, and component details must be valid.
- Connections must match `connection_rules` exactly, including `source_port`.
- Scoped graph-agent edits must stay inside the selected goal graph.
- New nodes in a scoped edit must connect into that selected goal graph.
- The source goal for the current generation cannot be deleted or given an incoming edge.
- Directed cycles are blocked because graph runs require an acyclic branch.
- Files nodes must contain repo-like paths only, one per line.

## Soft Quality Checks

These are graph-quality checks. Keep them simple, explain failures clearly, and autocorrect only when the fix is deterministic:

- Generated context should feed an action or decision.
- Generated multi-action plans should carry real graph value, not just a generic task list.
- Generated multi-action plans with DoD should include expected evidence/proof.
- Planning, design, research, or spec actions should feed implementation actions directly.

## Current Autocorrections

- Missing crew on generated operation nodes defaults from the title/detail:
  - research/survey/investigate/compare -> Scout
  - implement/build/wire/code/engine/state/integration/frontend/backend/parser/evaluator/persist -> Coder
  - design/architecture/plan/spec/requirements/UX/UI/model/strategy -> Architect
  - otherwise -> Coder
- A patch with exactly one obvious planning/design/research/spec action and exactly one obvious implementation action gets an `operation.implement -> operation` connection when it is missing.

## Do Not Autocorrect

Do not autocorrect cycles, scope escapes, invalid connection kinds, protected-goal deletion, incoming edges to the source goal, fake file paths, or ambiguous multi-node handoffs. Those need either a clear tool failure or an `ask_user` question.
