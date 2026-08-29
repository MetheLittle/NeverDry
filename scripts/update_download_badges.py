#!/usr/bin/env python3
"""update_download_badges.py — recompute the download badges from the releases.

Why a script exists at all: the number worth showing cannot be produced by a
badge service. Shields can *extract* a value from a JSON document, but it has
no aggregation — no maximum, no sum over an array — and the GitHub releases API
returns an array. So the figure is computed here and published as a shields
`endpoint` payload the badge then reads.

**Which figure, and why.** HACS fetches the release asset on install and on
every update, so no counter here is a headcount:

- *sum across all releases* counts one long-standing user once per update;
- *latest stable* drops to zero the day a version ships and only climbs as
  people get round to updating — it read 0 half an hour after 0.11.2;
- *the most downloaded single release* is the closest honest proxy for reach,
  because each person fetches a given release once. It is also the only one of
  the three that needs computing, which is why this file exists.

None of them is an install count. The number that would mean installs is the
Home Assistant analytics figure, and this repository is not in that feed yet.
The HACS catalogue at data-v2.hacs.xyz looks like one and is not: its
`downloads` field is the latest stable release's GitHub count from a staler
snapshot — for alandtse/tesla it reads 17709 against 18148 live.

**Output.** A shields endpoint payload at ``docs/gh-pages/badges/downloads.json``.
The badge markup in the README and in the landing pages points at that URL and
never changes, so the number lives in exactly one place. Publishing happens
through the existing Pages workflow, which deploys on a push to ``main``
touching ``docs/gh-pages/**``.

**On failure it writes nothing.** A rate-limited or unauthenticated API answers
with an object rather than a list, and turning that into a zero would publish a
confident, wrong figure — the failure this whole afternoon was about.

Usage:
    python3 scripts/update_download_badges.py [--repo owner/name] [--check]

``--check`` recomputes and reports whether the published payload is stale,
without writing. Intended for CI, so a badge cannot silently fall behind.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PAYLOAD = REPO_ROOT / "docs" / "gh-pages" / "badges" / "downloads.json"
DEFAULT_REPO = "never-dry/NeverDry"
COLOR = "41BDF5"
# Where the payload above ends up once Pages deploys it. This is a project site,
# not an organisation one, so the repository name is part of the path — the badge
# first shipped pointing at the domain root and rendered "site not found".
PUBLISHED_AT = "https://never-dry.github.io/NeverDry/badges/downloads.json"


def _fetch_releases(repo: str) -> list[dict]:
    """Every release, following pagination. Raises rather than returning [].

    An empty list and a failed request must not look the same: the first is a
    repository with no releases, the second is a number we do not know. Only one
    of them may reach a badge.

    Fetched with ``curl`` rather than ``urllib`` on purpose. A network that
    intercepts TLS presents its own certificate, which the system trust store
    knows and Python's bundled one does not — so urllib fails where every other
    tool on the machine succeeds, and it fails at the point where this script
    would otherwise be run by hand.
    """
    curl = shutil.which("curl")
    if curl is None:
        raise RuntimeError("curl not found; it is what this script fetches with")
    token = os.environ.get("GITHUB_TOKEN")
    releases: list[dict] = []
    page = 1
    while True:
        cmd = [curl, "-sS", "--fail-with-body", "--max-time", "30", "-H", "Accept: application/vnd.github+json"]
        if token:
            cmd += ["-H", f"Authorization: Bearer {token}"]
        cmd.append(f"https://api.github.com/repos/{repo}/releases?per_page=100&page={page}")
        done = subprocess.run(cmd, capture_output=True, text=True)  # noqa: S603
        if done.returncode != 0:
            raise RuntimeError(f"GitHub releases {repo}: curl exit {done.returncode} {done.stderr.strip()}")
        batch = json.loads(done.stdout)
        if not isinstance(batch, list):
            raise RuntimeError(f"GitHub releases {repo}: unexpected response {batch!r:.200}")
        releases.extend(batch)
        if len(batch) < 100:
            return releases
        page += 1


def summarise(releases: list[dict]) -> dict:
    """Total, peak and latest-stable download counts, with the peak's tag."""
    counts = [
        (r.get("tag_name", "?"), sum(a.get("download_count", 0) for a in r.get("assets", [])))
        for r in releases
        if not r.get("draft")
    ]
    stable = [
        (r.get("tag_name", "?"), sum(a.get("download_count", 0) for a in r.get("assets", [])))
        for r in releases
        if not r.get("draft") and not r.get("prerelease")
    ]
    peak_tag, peak = max(counts, key=lambda t: t[1], default=("?", 0))
    return {
        "total": sum(c for _, c in counts),
        "peak": peak,
        "peak_tag": peak_tag,
        "latest_stable": stable[0][1] if stable else 0,
        "releases": len(counts),
    }


def payload(summary: dict) -> dict:
    """The shields endpoint document.

    The tag travels in the message so the badge cannot imply the figure is
    current for the version people are installing today. It is the reach of one
    past release, and saying which one is the difference between a measurement
    and a boast.
    """
    return {
        "schemaVersion": 1,
        "label": "downloads (best release)",
        "message": f"{summary['peak']} · {summary['peak_tag']}",
        "color": COLOR,
        "cacheSeconds": 21600,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--check", action="store_true", help="report staleness, write nothing")
    args = ap.parse_args()

    summary = summarise(_fetch_releases(args.repo))
    print(
        f"{args.repo}: {summary['releases']} releases · "
        f"total {summary['total']} · peak {summary['peak']} ({summary['peak_tag']}) · "
        f"latest stable {summary['latest_stable']}"
    )
    fresh = payload(summary)

    if args.check:
        if not PAYLOAD.exists():
            print(f"MISSING {PAYLOAD.relative_to(REPO_ROOT)} — run without --check", file=sys.stderr)
            return 1
        published = json.loads(PAYLOAD.read_text())
        if published.get("message") != fresh["message"]:
            print(
                f"STALE: badge says {published.get('message')!r}, releases say {fresh['message']!r}",
                file=sys.stderr,
            )
            return 1
        print("badge is current")
        return 0

    PAYLOAD.parent.mkdir(parents=True, exist_ok=True)
    PAYLOAD.write_text(json.dumps(fresh, indent=2) + "\n")
    print(f"wrote {PAYLOAD.relative_to(REPO_ROOT)}: {fresh['message']}")
    print(f"served from {PUBLISHED_AT} once Pages deploys")
    return 0


if __name__ == "__main__":
    sys.exit(main())
