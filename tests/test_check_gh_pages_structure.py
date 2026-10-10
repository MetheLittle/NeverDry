"""The landing-page check sees content the English page has and a translation lacks.

check_untranslated_text only notices English prose left inside a translation. The
features section was rewritten in English and stayed in its old form in eight
languages for four months, because a missing section leaves nothing English on
the translated page to match. check_structure compares the sections instead.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_gh_pages.py"
_spec = importlib.util.spec_from_file_location("check_gh_pages", _SCRIPT)
check_gh_pages = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_gh_pages)

CARD = '<div class="feature-card"><h3>{}</h3><p>text</p></div>'


def _page(cards: int, extra: str = "") -> str:
    grid = "".join(CARD.format(i) for i in range(cards))
    return f'<section class="features"><h2>Features</h2>{grid}</section>{extra}'


def test_a_card_missing_from_a_translation_changes_the_skeleton():
    assert check_gh_pages.skeleton(_page(11)) != check_gh_pages.skeleton(_page(10))


def test_a_section_only_english_has_on_purpose_is_left_out():
    english = _page(3, '<section class="how-it-works" data-en-only><h2>What\'s new</h2></section>')
    assert check_gh_pages.skeleton(english) == check_gh_pages.skeleton(_page(3))


def test_the_translation_notice_does_not_count_as_a_section():
    translated = _page(3, "<section data-translation-notice>Machine translated.</section>")
    assert check_gh_pages.skeleton(translated) == check_gh_pages.skeleton(_page(3))


def test_whitespace_is_collapsed_so_reindented_english_is_still_caught():
    texts = check_gh_pages.visible_texts("<p>Tracks evaporation and rainfall\n        day by day.</p>")
    assert texts == ["Tracks evaporation and rainfall day by day."]
