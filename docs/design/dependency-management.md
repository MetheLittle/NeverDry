# Dependency management — pip over uv

*Status: Accepted (ADR) · 2026-07-29*

## Context

NeverDry is a Home Assistant custom integration distributed through the HACS
default repository. The wider engineering guideline for standalone Python
packages standardizes on [`uv`](https://github.com/astral-sh/uv) for dependency
resolution and locking (a `[project]` table in `pyproject.toml`, a populated
`uv.lock`, and `uv sync` in CI).

A prior `uv.lock` existed in this repo but was an empty stub (`requires-python`
only, zero dependencies) and was not referenced by any workflow — it declared
`>=3.13` while the lint target is `py311` and CI tests 3.11/3.12, i.e. it only
created a misleading version signal.

## Decision

**Keep pip; do not adopt uv for this project.**

- Runtime dependencies are declared in `custom_components/never_dry/manifest.json`
  (`requirements`), the mechanism Home Assistant itself uses and resolves at
  runtime. `manifest.json` is the single source of truth for runtime deps.
- Test/dev dependencies are installed with pip in CI (`pip install pytest …`)
  and pinned as needed in `requirements_test.txt`.
- The empty `uv.lock` has been removed to eliminate the false version signal.

## Rationale

- **Ecosystem alignment.** Home Assistant and the HACS toolchain (hassfest, HACS
  validation) are built around `manifest.json` + pip, not `uv`. Introducing `uv`
  would add tooling foreign to every HA contributor's mental model without
  changing what actually gets installed at runtime (HA reads `manifest.json`).
- **No lockfile value here.** The integration has a small, HA-managed dependency
  surface; a separate resolver/lockfile adds maintenance without benefit.
- **Lower contributor friction.** Contributors already have a HA dev environment;
  pip + `requirements_test.txt` matches it.

## Consequences

- This is a deliberate divergence from the standalone-package guideline, recorded
  here so it is not "fixed" by a future audit. If NeverDry ever ships a component
  that is installed outside HA's runtime, revisit this.
- Python version signals live in `pyproject.toml` (`target-version`) and the CI
  matrix; there is no lockfile to keep in sync.
