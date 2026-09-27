"""What the pre-release pruner must never do.

The function deletes things, so the tests are written around the ways it could
delete the wrong one rather than around the happy path.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from prune_prereleases import superseded


def release(tag, created_at, *, prerelease=True, draft=False):
    return {
        "tag_name": tag,
        "created_at": created_at,
        "prerelease": prerelease,
        "draft": draft,
    }


def test_keeps_the_newest_prerelease_and_drops_the_rest():
    releases = [
        release("v0.12.0-beta.1", "2026-09-01T10:00:00Z"),
        release("v0.12.0-beta.3", "2026-09-03T10:00:00Z"),
        release("v0.12.0-beta.2", "2026-09-02T10:00:00Z"),
    ]
    assert superseded(releases) == ["v0.12.0-beta.2", "v0.12.0-beta.1"]


def test_never_touches_a_stable_release():
    """The oldest stable would be first to go if the flag were ignored."""
    releases = [
        release("v0.11.2", "2026-08-29T10:00:00Z", prerelease=False),
        release("v0.10.9", "2026-07-01T10:00:00Z", prerelease=False),
        release("v0.12.0-beta.1", "2026-09-01T10:00:00Z"),
        release("v0.12.0-beta.2", "2026-09-02T10:00:00Z"),
    ]
    assert superseded(releases) == ["v0.12.0-beta.1"]


def test_a_stable_retires_its_own_betas_including_the_newest():
    """The one case where nothing at all is kept: the question is answered."""
    releases = [
        release("v0.12.0-beta.1", "2026-09-01T10:00:00Z"),
        release("v0.12.0-beta.2", "2026-09-02T10:00:00Z"),
        release("v0.12.0", "2026-09-10T10:00:00Z", prerelease=False),
    ]
    assert superseded(releases, current_tag="v0.12.0") == [
        "v0.12.0-beta.2",
        "v0.12.0-beta.1",
    ]


def test_a_stable_does_not_sweep_away_a_series_in_flight():
    """0.12.1 shipping must not delete the 0.13.0 betas under test."""
    releases = [
        release("v0.12.1", "2026-09-20T10:00:00Z", prerelease=False),
        release("v0.12.0-beta.9", "2026-09-01T10:00:00Z"),
        release("v0.13.0-beta.1", "2026-09-18T10:00:00Z"),
        release("v0.13.0-beta.2", "2026-09-19T10:00:00Z"),
    ]
    assert superseded(releases, current_tag="v0.12.1") == [
        "v0.13.0-beta.1",
        "v0.12.0-beta.9",
    ]


def test_a_beta_sorts_below_its_own_stable():
    """String order gets this backwards, and the stable rule depends on it."""
    from prune_prereleases import _version_key

    assert _version_key("v0.12.0-beta.11") < _version_key("v0.12.0")
    assert _version_key("v0.12.0") < _version_key("v0.12.1-beta.1")
    assert _version_key("v0.12.0-beta.2") < _version_key("v0.12.0-beta.11")


def test_never_touches_a_draft():
    """A draft is on offer to nobody, and may be somebody's work in progress."""
    releases = [
        release("v0.12.0-beta.1", "2026-09-01T10:00:00Z"),
        release("v0.12.0-beta.2", "2026-09-02T10:00:00Z"),
        release("v0.13.0-rc.1", "2026-09-05T10:00:00Z", draft=True),
    ]
    assert superseded(releases) == ["v0.12.0-beta.1"]


def test_a_rerun_on_an_old_tag_does_not_delete_the_newest():
    """The dangerous case: re-publishing beta.1 today makes it look newest.

    Without the current tag being kept explicitly, the re-run would keep the
    republished old tag and delete beta.3, which is the release people are on.
    """
    releases = [
        release("v0.12.0-beta.3", "2026-09-03T10:00:00Z"),
        release("v0.12.0-beta.1", "2026-09-27T10:00:00Z"),
    ]
    assert superseded(releases, current_tag="v0.12.0-beta.1") == []


def test_crossing_a_version_series_is_not_special():
    """An old series' beta is superseded like any other: newest wins."""
    releases = [
        release("v0.11.1-beta.8", "2026-08-10T10:00:00Z"),
        release("v0.12.0-beta.1", "2026-09-01T10:00:00Z"),
    ]
    assert superseded(releases) == ["v0.11.1-beta.8"]


def test_one_prerelease_is_left_alone():
    releases = [release("v0.12.0-beta.1", "2026-09-01T10:00:00Z")]
    assert superseded(releases) == []


def test_no_prereleases_at_all():
    releases = [release("v0.11.2", "2026-08-29T10:00:00Z", prerelease=False)]
    assert superseded(releases) == []


def test_a_missing_timestamp_does_not_crash():
    """The API has always sent created_at; a sort that assumes it would fail
    the release workflow rather than the pruning step."""
    releases = [
        {"tag_name": "v0.12.0-beta.1", "prerelease": True},
        release("v0.12.0-beta.2", "2026-09-02T10:00:00Z"),
    ]
    assert superseded(releases) == ["v0.12.0-beta.1"]


def test_the_shape_of_the_repository_today():
    """0.11.2 is the stable and the 0.12.0 betas are above it, so the newest
    beta is kept and everything below it goes - stables untouched."""
    releases = [
        release("v0.11.2", "2026-08-29T10:00:00Z", prerelease=False),
        release("v0.11.0-beta.1", "2026-07-10T10:00:00Z"),
        release("v0.11.1-beta.8", "2026-08-10T10:00:00Z"),
        release("v0.12.0-beta.10", "2026-09-26T10:00:00Z"),
        release("v0.12.0-beta.11", "2026-09-27T10:00:00Z"),
    ]
    assert superseded(releases, current_tag="v0.12.0-beta.11") == [
        "v0.12.0-beta.10",
        "v0.11.1-beta.8",
        "v0.11.0-beta.1",
    ]
