"""Keep only the newest pre-release on GitHub, and delete the ones it replaced.

HACS offers the user every published release of the repository, so each beta
adds a line to the list a tester scrolls through, and that list only ever grows:
twenty-five pre-releases were on offer by the time this was written. A beta is
read once, by the people who install it the day it appears. Eleven of them on
offer at the same time helps nobody and hides the one that matters.

When a **stable** release ships, the betas it supersedes go with it, the newest
included: they were asking the question the finished version answers. Betas of a
higher version survive, so a series already in testing is not swept away by a
patch to the one before it.

Two things this deliberately does not do.

It never touches a **stable** release. The filter is the `prerelease` flag
GitHub records at publication, not the shape of the tag name: a tag that looks
like a beta but was published as stable stays, and the reverse also holds.

It never deletes the **tag**. The tag is what makes a beta reproducible, it
costs nothing, and HACS does not read tags while releases exist. The archive can
be rebuilt from the tag by re-running the release workflow, so what is thrown
away is an auto-generated list of merged pull requests.

The decision is a pure function so it can be tested without a network: see
`tests/test_prune_prereleases.py`.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys


def _version_key(tag: str) -> tuple:
    """Order tags the way versions are ordered, not the way strings are.

    Written out rather than imported because the release job installs pytest and
    nothing else, and a parsing helper is not worth a dependency in the one
    workflow that must not fail.

    A tag nothing can be made of sorts to the bottom, where it will be deleted
    rather than kept: the alternative is keeping something unrecognisable in
    preference to a real release.
    """
    text = tag[1:] if tag.startswith("v") else tag
    core, _, pre = text.partition("-")

    nums = [int(p) if p.isdigit() else 0 for p in core.split(".")]
    nums = [*nums, 0, 0, 0][:3]

    if not pre:
        # No pre-release part means the final release, and the final release
        # sorts ABOVE every pre-release of the same version: 0.12.0 is later
        # than 0.12.0-beta.11, which string order gets backwards.
        return (tuple(nums), (1, ()))

    pre_key = []
    for part in pre.split("."):
        if part.isdigit():
            pre_key.append((0, int(part), ""))
        else:
            pre_key.append((1, 0, part))

    return (tuple(nums), (0, tuple(pre_key)))


def superseded(releases: list[dict], current_tag: str | None = None) -> list[str]:
    """Return the tags of the pre-releases that are no longer worth offering.

    Everything that is not a published pre-release is left alone: stable
    releases, and drafts, which are not on offer to anybody yet and may be
    someone's work in progress.

    The newest pre-release is kept, and *newest* means the highest version, not
    the most recently published. The difference is not academic: re-running this
    workflow on an old tag republishes it with today's timestamp, and ordering
    by time would then read that old tag as the newest and delete the release
    people are actually running. Publication time is kept only as a tiebreaker
    between tags that parse to the same version.

    `current_tag` is kept as well, so the tag this run just published is never
    deleted by the same run whatever its version says.

    And a **stable** release retires the betas it supersedes, the newest
    included: once the finished version is on the shelf, the pre-releases that
    were asking its question have been answered. Only pre-releases above the
    highest stable survive, which is how a next series already in testing is not
    swept away by a patch to the current one.
    """
    published = [r for r in releases if r.get("draft") is not True]
    prereleases = [r for r in published if r.get("prerelease") is True]
    stables = [r for r in published if r.get("prerelease") is not True]

    prereleases.sort(
        key=lambda r: (_version_key(r["tag_name"]), r.get("created_at") or ""),
        reverse=True,
    )

    keep = {r["tag_name"] for r in prereleases[:1]}
    if current_tag:
        keep.add(current_tag)

    if stables:
        # A stable release answers the question its own betas were asking, so
        # they stop being worth offering the moment it ships - the newest of
        # them included, which the rule above would otherwise have kept. A beta
        # ABOVE the highest stable is a series still in flight and survives.
        highest_stable = max(_version_key(r["tag_name"]) for r in stables)
        keep = {tag for tag in keep if tag == current_tag or _version_key(tag) > highest_stable}

    return [r["tag_name"] for r in prereleases if r["tag_name"] not in keep]


def _gh() -> str:
    """Resolve the GitHub CLI once, and fail loudly if it is not there.

    Resolving the path is what lets the two subprocess calls below be read as
    fixed argv on a known executable rather than as a shell invocation.
    """
    path = shutil.which("gh")
    if path is None:
        raise SystemExit("gh is not on PATH: cannot list or delete releases.")
    return path


def _fetch(gh: str, repo: str) -> list[dict]:
    out = subprocess.run(  # noqa: S603 - resolved path, fixed argv, no shell
        [gh, "api", f"repos/{repo}/releases", "--paginate"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return json.loads(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument(
        "--current-tag",
        default=None,
        help="the tag just released, kept even if it is not the newest",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually delete; without it the tags are only printed",
    )
    args = parser.parse_args(argv)

    gh = _gh()
    stale = superseded(_fetch(gh, args.repo), args.current_tag)
    if not stale:
        print("No superseded pre-release to remove.")
        return 0

    for tag in stale:
        if not args.apply:
            print(f"would remove {tag}")
            continue
        print(f"removing superseded pre-release {tag} (tag kept)")
        subprocess.run(  # noqa: S603 - resolved path, fixed argv, no shell
            [gh, "release", "delete", tag, "--repo", args.repo, "--yes"],
            check=True,
        )

    print(f"{len(stale)} pre-release(s) {'removed' if args.apply else 'listed'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
