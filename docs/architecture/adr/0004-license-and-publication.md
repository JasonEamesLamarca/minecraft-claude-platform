# 0004 — Apache-2.0; private until a publication-readiness review

**Status:** Accepted (2026-11-16)

**Context.** The project should be easy to adopt and contribute to. It ships no Mojang code or assets: server
software is downloaded by the official container image at install time, after the user accepts the EULA.

**Decision.** License the code under **Apache-2.0** (permissive, with an explicit patent grant). Example builds
(Atlantis) are original work under the same license. The GitHub repository stays **private** until a
publication-readiness report has passed (secrets and history scan, private-identifier scan, license review,
screenshot metadata review, README vs. reality) and the owner explicitly approves making it public.

**Consequences.** Third-party dependencies must be Apache-2.0-compatible and listed in
`docs/development/THIRD_PARTY.md`. GPL/LGPL Fabric libraries may be *used* at runtime, but are not vendored.
