"""The method-choice and warming-up sentences, now assembled card-side (GH #313).

`_method_reason` and `warming_up_because` used to be English f-strings built in
`sensor.py`, invisible to every catalogue. The hub now publishes a stable
`reason_code` / `reason_params` (and `warming_up_code` / `warming_up_params`)
pair instead, and the card turns that into text from its own `I18N`
dictionary — the same place, and the same mechanism, every other card label
already lives in. This file checks the card's half of that contract
statically, the way `test_card_i18n.py` already checks the rest of `I18N`;
`tests/test_et_method_reason_code.py` checks the hub's half.
"""

from __future__ import annotations

import re
from pathlib import Path

_COMPONENT = Path(__file__).resolve().parent.parent / "custom_components" / "never_dry"
_CARD = _COMPONENT / "www" / "never-dry-zone-card.js"
_LANGUAGES = ("en", "es", "de", "it")

# One of each kind of sentence the hub can send: a plain code, a code with a
# method name, and a code with a number. Mirrors the branches in
# DrynessIndexSensor._select_model and _warming_up_inputs.
_REASON_KEYS = {
    "reasonExplicit": (),
    "reasonFallback": ("method",),
    "reasonFlatRange": ("range", "hargreaves"),
    "reasonAuto": (),
}
_WARMING_KEYS = {
    "warmingDailyRange": ("hours",),
    "warmingSensorUnreadable": (),
}
_METHOD_KEYS = ("method_et_simple", "method_hargreaves", "method_penman_monteith")


def _lang_block(lang: str) -> str:
    """The body of ``I18N.<lang> = { ... }``, by brace-matching from the card source.

    Lifted from the same approach `test_card_i18n.py` uses for the whole `I18N`
    object, narrowed to one language so a value-bearing regex below never
    strays into a sibling language's keys.
    """
    src = _CARD.read_text(encoding="utf-8")
    start = src.index(f"\n  {lang}: {{")
    start = src.index("{", start)
    depth, i, quote = 0, start, ""
    while i < len(src):
        ch = src[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in "\"'`":
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return src[start : i + 1]
        i += 1
    raise AssertionError(f"unbalanced braces reading I18N.{lang}")


def _value(block: str, key: str) -> str | None:
    match = re.search(rf'(?<!\w){key}:\s*"((?:[^"\\]|\\.)*)"', block)
    return match.group(1) if match else None


def test_every_language_defines_every_reason_and_warming_key():
    missing = {
        lang: sorted(k for k in (*_REASON_KEYS, *_WARMING_KEYS, *_METHOD_KEYS) if _value(_lang_block(lang), k) is None)
        for lang in _LANGUAGES
    }
    missing = {lang: keys for lang, keys in missing.items() if keys}
    assert not missing, f"keys the card needs but a language is missing: {missing}"


def test_every_language_keeps_the_same_placeholders_per_key():
    # The general form of what #279's reason-translation PR got right by hand:
    # a template that drops a {placeholder} in one language renders that
    # language's sentence with a literal, untranslated "{method}" in it.
    for key, placeholders in {**_REASON_KEYS, **_WARMING_KEYS}.items():
        for lang in _LANGUAGES:
            value = _value(_lang_block(lang), key)
            assert value is not None, f"{lang}.{key} missing"
            found = set(re.findall(r"\{(\w+)\}", value))
            assert found == set(placeholders), (
                f"{lang}.{key}: expected placeholders {placeholders}, found {sorted(found)}"
            )


def test_method_labels_have_no_placeholders():
    # These are plain names (Hargreaves-Samani, ...), substituted into the
    # reason sentences by the card — not sentences of their own.
    for key in _METHOD_KEYS:
        for lang in _LANGUAGES:
            value = _value(_lang_block(lang), key)
            assert value, f"{lang}.{key} missing or empty"
            assert "{" not in value, f"{lang}.{key} should not carry its own placeholder: {value!r}"


def test_reason_text_falls_back_to_english_for_an_unrecognised_code():
    # reasonText()'s `default:` branch — read statically, since there is no JS
    # runtime in this suite. A code this card version does not know (an older
    # integration, or a future one) must not render as a blank sub-title.
    src = _CARD.read_text(encoding="utf-8")
    assert 'return a.reason || "";' in src, "reasonText() lost its English fallback for an unknown reason_code"
    assert 'return a.warming_up_because || "";' in src, (
        "warmingUpText() lost its English fallback for an unknown warming_up_code"
    )
