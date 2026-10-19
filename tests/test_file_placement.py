"""The files the integration loads live at one path each. This keeps it that way.

Written after a contribution arrived with the German catalogue and the card added at the
repository root instead of replacing the files they were meant to replace. Ten checks
reported green: Ruff, Bandit, CodeQL, hassfest, the forbidden-pattern guard, the whole
suite. Every one of them was right, and the change would still have reached no user,
because Home Assistant loads ``custom_components/never_dry/translations/de.json`` and the
browser loads ``custom_components/never_dry/www/never-dry-zone-card.js``, and both of
those were untouched by the merge.

That is the shape of the gap worth naming: the guards all read *content*, and not one of
them asked where the content was. A file in the wrong place is not malformed, it is not
insecure, and it does not fail a schema. It is simply inert, and inert is the one defect
a reviewer is least likely to catch by reading the diff, because the diff looks correct.

Two checks, because the incident had two different ways of going wrong.

``test_no_stray_content_files_at_repository_root`` is hygiene: the root holds project
prose and packaging, nothing else. It catches this exact incident and the next stray file
someone drops beside it.

``test_load_bearing_files_exist_at_exactly_one_path`` is the sharper one, and the reason
the first is not enough: the copy does not have to be at the root to be inert. Anywhere
that is not the loading path is equally invisible, so the rule is stated as the property
that actually matters. There is exactly one ``de.json`` in this repository, and it is the
one Home Assistant opens.
"""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_COMPONENT = _REPO / "custom_components" / "never_dry"

# Directories that hold no source of ours: build output, caches, virtualenvs, the agent's
# own scratch. Walking into them would report duplicates that are copies by design.
_SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".code_reader",
    ".coding_agent",
    "htmlcov",
    "dist",
    "build",
    ".idea",
    ".vscode",
}

# Suffixes that carry content a human wrote or a machine loads. Anything else at the root
# is local noise (``.coverage``, ``.DS_Store``) and not this test's business.
_CONTENT_SUFFIXES = {".json", ".js", ".py", ".md", ".yaml", ".yml", ".css", ".html", ".ts"}

# What the repository root is allowed to hold. Project prose, packaging, and the two files
# HACS itself reads. Adding to this list is a decision; landing in it by accident is the
# failure this test exists to prevent.
_ROOT_ALLOWED = {
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "README.md",
    "SECURITY.md",
    "hacs.json",
    "info.md",
    "pyproject.toml",
    ".pre-commit-config.yaml",
}


def _tracked_files() -> list[Path]:
    """Every file in the working tree that is ours, as paths relative to the repository."""
    out: list[Path] = []
    for path in _REPO.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(_REPO)
        if any(part in _SKIP_DIRS for part in rel.parts):
            continue
        out.append(rel)
    return out


def _load_bearing() -> dict[str, Path]:
    """Basename to the single path it is loaded from, keyed by what the runtime opens.

    The translation catalogues are read off disk rather than listed, so a language added
    tomorrow is covered the day it lands without anybody remembering this file exists.
    """
    canonical = {
        "manifest.json": _COMPONENT / "manifest.json",
        "strings.json": _COMPONENT / "strings.json",
        "never-dry-zone-card.js": _COMPONENT / "www" / "never-dry-zone-card.js",
    }
    for catalogue in sorted((_COMPONENT / "translations").glob("*.json")):
        canonical[catalogue.name] = catalogue
    return {name: path.relative_to(_REPO) for name, path in canonical.items() if path.exists()}


def test_no_stray_content_files_at_repository_root() -> None:
    """The root holds prose and packaging. A catalogue or a card there is a misplaced file."""
    stray = sorted(
        path.name
        for path in _REPO.iterdir()
        if path.is_file() and path.suffix in _CONTENT_SUFFIXES and path.name not in _ROOT_ALLOWED
    )
    assert not stray, (
        "Content files at the repository root that do not belong there: "
        + ", ".join(stray)
        + ". A translation catalogue belongs in custom_components/never_dry/translations/, "
        "the card in custom_components/never_dry/www/, Python in the component or in tests. "
        "A file here is loaded by nothing, so it changes nothing, and every other check "
        "will still pass. If the file really does belong at the root, add it to "
        "_ROOT_ALLOWED in this test and say in the pull request why."
    )


def test_load_bearing_files_exist_at_exactly_one_path() -> None:
    """Each file the runtime opens exists once, at the path the runtime opens it from."""
    canonical = _load_bearing()
    assert canonical, "No load-bearing files found: the component layout has moved."

    duplicates: list[str] = []
    for rel in _tracked_files():
        expected = canonical.get(rel.name)
        if expected is not None and rel != expected:
            duplicates.append(f"{rel} (loaded from {expected})")

    assert not duplicates, (
        "Files that shadow something the integration loads, without being what it loads: "
        + "; ".join(sorted(duplicates))
        + ". Home Assistant and the browser open one path each. A second copy anywhere "
        "else is inert: it passes review, it passes every other check, and it reaches "
        "nobody. Move the change onto the loading path instead of beside it."
    )
