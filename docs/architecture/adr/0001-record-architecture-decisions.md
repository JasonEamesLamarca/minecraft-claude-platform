# 0001 — Record architecture decisions

**Status:** Accepted (2026-11-16)

**Context.** The platform spans a game mod, several services, an AI runtime and a security model. Contributors
need to know why things are the way they are.

**Decision.** We record significant decisions as short ADRs in `docs/architecture/adr/`. A change to architecture,
security boundaries, supported platforms or licensing needs a new ADR in the same PR.

**Consequences.** There's a small amount of extra writing per change, and the history of decisions stays in Git
next to the code.
