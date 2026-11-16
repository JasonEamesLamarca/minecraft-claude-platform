# 0007 — Clean-room repository, ported code only

**Status:** Accepted (2026-11-16)

**Context.** The project grows out of a private home installation whose scripts, notes and Git history contain
personal identifiers, network details and live-server assumptions.

**Decision.** This repository starts with an empty history. Code from the private installation is **ported**
file by file: it is rewritten or reviewed, made generic and configuration-driven, and checked by gitleaks and the
private deny-list before commit. No private Git history, worlds, databases, logs or backups are imported. The
private installation is never used as a development or test target.

**Consequences.** Porting takes more effort than copying, but the published history is clean from the first commit.
