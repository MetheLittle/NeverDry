"""Tests for scripts/release_notes.py — the changelog section that becomes the
GitHub release body.

The failure this guards against is silent: an extractor that returns an empty
body, or the wrong section, still publishes a release. Nobody reads a release
body before it is public, so the check has to happen here.

Includes one test against the real CHANGELOG.md, because a fixture proves the
parser works on the shape the parser expects — not on the file it will actually
be handed.
"""

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location("release_notes", REPO_ROOT / "scripts" / "release_notes.py")
release_notes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release_notes)


CHANGELOG = """# Changelog

Preamble that must never appear in a release body.

## [Unreleased]

### Added
- Something not released yet.

## [0.11.1] - 2026-08-25

Theme line.

### Fixed
- A probe reporting percentages no longer silences a zone ([#170]).
- Something with no reference at all.

## [0.11.0] - 2026-07-26

### Added
- The first stable ([#116]).

[Unreleased]: https://github.com/never-dry/NeverDry/compare/v0.11.1...HEAD
[0.11.1]: https://github.com/never-dry/NeverDry/releases/tag/v0.11.1
[#170]: https://github.com/never-dry/NeverDry/issues/170
[#116]: https://github.com/never-dry/NeverDry/issues/116
"""


def test_extracts_only_the_requested_section():
    body = release_notes.build(CHANGELOG, "0.11.1")

    assert "Theme line." in body
    assert "A probe reporting percentages" in body
    # The neighbours on both sides must stay out.
    assert "Something not released yet" not in body
    assert "The first stable" not in body
    assert "Preamble" not in body


def test_drops_its_own_version_heading():
    body = release_notes.build(CHANGELOG, "0.11.1")

    # The release is already titled by its tag; repeating it reads as a typo.
    assert not body.startswith("## [0.11.1]")
    assert "## [0.11.1]" not in body


def test_resolves_references_to_absolute_links():
    body = release_notes.build(CHANGELOG, "0.11.1")

    # A release body has no link-reference section, so a bare [#170] would
    # render as literal brackets.
    assert "[#170](https://github.com/never-dry/NeverDry/issues/170)" in body
    assert "[#170]." not in body


def test_keeps_an_undefined_reference_readable():
    changelog = CHANGELOG.replace("[#170]: https://github.com/never-dry/NeverDry/issues/170\n", "")

    body = release_notes.build(changelog, "0.11.1")

    # Unlinked but still legible, rather than silently dropped.
    assert "#170" in body
    assert "[#170]" not in body


def test_appends_the_comparison_against_the_previous_release():
    body = release_notes.build(CHANGELOG, "0.11.1")

    assert "**Full changelog:** https://github.com/never-dry/NeverDry/compare/v0.11.0...v0.11.1" in body


def test_no_comparison_for_the_oldest_release():
    body = release_notes.build(CHANGELOG, "0.11.0")

    assert "Full changelog" not in body


def test_unreleased_is_not_publishable():
    # [Unreleased] carries no date, so it is not a released section and must not
    # be shipped as one.
    with pytest.raises(release_notes.SectionNotFound):
        release_notes.build(CHANGELOG, "Unreleased")


def test_missing_section_fails_loudly_and_says_what_exists():
    with pytest.raises(release_notes.SectionNotFound) as error:
        release_notes.build(CHANGELOG, "0.12.0")

    message = str(error.value)
    assert "0.12.0" in message
    assert "0.11.1" in message  # names what it did find, so the fix is obvious


def test_empty_section_is_refused():
    changelog = CHANGELOG.replace(
        "Theme line.\n\n### Fixed\n"
        "- A probe reporting percentages no longer silences a zone ([#170]).\n"
        "- Something with no reference at all.\n",
        "",
    )

    with pytest.raises(release_notes.SectionNotFound):
        release_notes.build(changelog, "0.11.1")


def test_the_real_changelog_yields_notes_for_the_shipped_version():
    text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    version = release_notes._split_sections(text)[0][0]

    body = release_notes.build(text, version)

    assert len(body) > 200
    assert "## [" not in body  # no version heading leaked in
    assert "]: https://" not in body  # no link-reference block leaked in
