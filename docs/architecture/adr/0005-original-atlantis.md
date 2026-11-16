# 0005 — Atlantis example is an original design

**Status:** Accepted (2026-11-16)

**Context.** The flagship example is an underwater city. The legend of Atlantis is public domain, but specific
fictional depictions of it, and their props and names, belong to their rights holders.

**Decision.** The published Atlantis is an **original** design: no elements, names or likenesses from films, TV
series or games. It is produced by the project's own generator. It ships in two forms: **Atlantis Lite**, a
compact version that is quick to test (central structure, one district, one underwater section), and
**Atlantis Full**, split into sections. Schematics are published as release assets with a manifest and SHA-256
checksums, not stored in Git.

**Consequences.** Reused generator code must be reviewed for franchise-specific landmarks and names, which get
replaced before publication. A private deny-list check (`scripts/check-private.sh`) helps catch leftovers.
