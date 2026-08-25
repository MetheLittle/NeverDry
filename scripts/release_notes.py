#!/usr/bin/env python3
"""release_notes.py — Turn a CHANGELOG section into a GitHub release body.

Usage:
    python3 scripts/release_notes.py <version> [--changelog PATH] [--out PATH]

Why this exists: the release workflow used to ask GitHub to generate the notes,
which produces the raw list of merged pull requests — back-merges, version
bumps and CI bumps included. That is the project's internal churn served to
people who install the integration. The changelog already says what changed in
the words a user needs, so the release body is taken from there instead.

A release body has no link-reference section at the bottom, so the ``[#123]``
references are resolved into absolute URLs on the way out.

For a stable version the section is REQUIRED: a missing one fails the release
rather than falling back, because the failure this guards against is shipping a
version nobody wrote down. Pre-releases have no changelog section by
convention, and the workflow does not call this for them.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# "## [0.11.1] - 2026-08-25" — the date is required: an entry still sitting
# under [Unreleased] has not been released, and must not be published as if it
# had been.
_HEADING = re.compile(r"^## \[(?P<version>[^\]]+)\] - (?P<date>\d{4}-\d{2}-\d{2})\s*$")
_ANY_HEADING = re.compile(r"^## \[")
_REF_DEF = re.compile(r"^\[(?P<label>#\d+)\]: (?P<url>\S+)\s*$", re.MULTILINE)
_REF_USE = re.compile(r"\[(?P<label>#\d+)\]")

COMPARE_URL = "https://github.com/never-dry/NeverDry/compare/v{previous}...v{version}"


class SectionNotFound(Exception):
    """The changelog has no released section for the requested version."""


def _split_sections(text: str) -> list[tuple[str, int, int]]:
    """Return (version, start_line, end_line) for every released section."""
    lines = text.splitlines()
    starts: list[tuple[str, int]] = []
    for number, line in enumerate(lines):
        match = _HEADING.match(line)
        if match:
            starts.append((match.group("version"), number))

    sections = []
    for version, start in starts:
        end = len(lines)
        # A section ends at the next "## [" heading of any kind, which includes
        # [Unreleased] — so a section is never allowed to swallow the one below.
        for number in range(start + 1, len(lines)):
            if _ANY_HEADING.match(lines[number]):
                end = number
                break
        sections.append((version, start, end))
    return sections


def _previous_version(sections: list[tuple[str, int, int]], version: str) -> str | None:
    """The version released before ``version``, by changelog order."""
    versions = [name for name, _, _ in sections]
    try:
        index = versions.index(version)
    except ValueError:
        return None
    return versions[index + 1] if index + 1 < len(versions) else None


def _resolve_references(body: str, definitions: dict[str, str]) -> str:
    """Rewrite ``[#123]`` as ``[#123](url)``.

    A reference with no definition is left alone rather than dropped: it still
    reads as the issue number it is, and losing it silently would be worse than
    leaving it unlinked.
    """

    def replace(match: re.Match[str]) -> str:
        label = match.group("label")
        url = definitions.get(label)
        return f"[{label}]({url})" if url else label

    # Only bare references — anything already followed by "(" is an inline link.
    return re.sub(_REF_USE.pattern + r"(?!\()", replace, body)


def build(text: str, version: str) -> str:
    """Extract the release body for ``version`` from changelog ``text``."""
    sections = _split_sections(text)
    match = next((s for s in sections if s[0] == version), None)
    if match is None:
        released = ", ".join(name for name, _, _ in sections) or "none"
        raise SectionNotFound(
            f"CHANGELOG.md has no released section '## [{version}] - <date>'.\n"
            f"Released sections found: {released}.\n"
            "Write the entry (or promote [Unreleased] and date it) before tagging."
        )

    lines = text.splitlines()
    _, start, end = match
    body = "\n".join(lines[start + 1 : end]).strip()
    if not body:
        raise SectionNotFound(f"The section for {version} is empty — nothing to publish.")

    body = _resolve_references(body, dict(_REF_DEF.findall(text)))

    previous = _previous_version(sections, version)
    if previous:
        url = COMPARE_URL.format(previous=previous, version=version)
        body += f"\n\n**Full changelog:** {url}\n"
    return body + "\n" if not body.endswith("\n") else body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="Version to publish, without the leading v")
    parser.add_argument("--changelog", default="CHANGELOG.md", type=Path)
    parser.add_argument("--out", type=Path, help="Write here instead of stdout")
    args = parser.parse_args(argv)

    try:
        body = build(args.changelog.read_text(encoding="utf-8"), args.version)
    except SectionNotFound as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    if args.out:
        args.out.write_text(body, encoding="utf-8")
        print(f"Release notes for {args.version} written to {args.out}")
    else:
        print(body, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
