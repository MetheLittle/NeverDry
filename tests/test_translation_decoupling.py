"""The integration's Python must not read translation files. This keeps it that way.

Localisation belongs to the two presentation surfaces and to nothing else: Home Assistant
resolves ``translations/<lang>.json`` for the backend GUI, and the card carries its own
dictionaries for the browser. Neither is the integration's business — the Python side deals
in identifiers (``estimated_flow``, ``req_open``, error codes) and hands them out; the text
is somebody else's problem, resolved later, in the user's language, in a layer that knows
what language that is.

That separation held everywhere until the notifications were localised, and the argument the
docstring above asked for was duly had. It has two exceptions now, and both are instructive
rather than grudging - they are the two places that hand Home Assistant a finished string.

``persistent_notification.create`` takes a finished title and a finished message. There is no
later layer: for a notification, the string handed over *is* the presentation, so "emit an
identifier and let somebody else resolve it" has nobody to name. Either the text is resolved
in Python or the notification reads ``stuck_open`` at the user. ``valve_notifier.py`` is
therefore allowed to resolve, and nothing else is.

The honest version of the rule the exception implies: a condition that wants to reach a person
*without* Python composing prose has to stop being a notification and become an entity, which
the user's own automation can act on in their own words. That is tracked separately, and it is
the direction of travel; until it arrives, the bell needs its words.

Worth recording how this was found, because it says something about the guard: it fired on a
*comment* that happened to name a translation file, not on the call that does the resolving.
The substring list below could not see ``async_get_translations`` at all, so the coupling it
exists to prevent would have walked straight past it. The detection is widened here to match
what the docstring always claimed to check.

``manifest.json`` is the one file production code legitimately reads (``__init__.py`` takes
the version from it) and it is not a translation file, so it is out of scope by construction
rather than by exception.
"""

from __future__ import annotations

import re
from pathlib import Path

_COMPONENT = Path(__file__).resolve().parent.parent / "custom_components" / "never_dry"

# Reaching for translated text, by either route: opening a file, or asking Home Assistant to
# resolve one. The second was missing until it was needed, which is the more common of the two
# in a modern integration and the only one that actually appears in this codebase.
_FORBIDDEN = ("strings.json", "translations/", "translations\\", "async_get_translations")

# Raising a notification at all: the delivery half of the same rule.
_NOTIFICATION_DOMAIN = "persistent_notification"

# The modules allowed to resolve text, for the reason set out at the top of this file.
# Two now, and the second was added deliberately rather than by drift.
#
# ``config_flow.py`` shows plausibility warnings on the soft-confirm step, and a
# config flow hands Home Assistant a *finished* string: ``description_placeholders``
# substitutes values into a translated description, it does not translate the
# values. So the same argument applies word for word - there is no later layer to
# hand an identifier to, and the alternative is what actually happened: twelve
# sentences, every one of them explaining why something the user configured will
# not behave as they expect, reaching every installation in English inside a form
# that was otherwise entirely in their language (GH #286).
#
# The test below is what keeps this a list of two rather than a habit.
_ALLOWED = {"valve_notifier.py", "config_flow.py"}


def test_no_production_module_reads_translation_files():
    offenders: list[str] = []
    for module in sorted(_COMPONENT.rglob("*.py")):
        if "__pycache__" in module.parts or module.name in _ALLOWED:
            continue
        source = module.read_text(encoding="utf-8")
        for line_number, line in enumerate(source.splitlines(), start=1):
            for needle in _FORBIDDEN:
                if needle in line:
                    offenders.append(f"{module.relative_to(_COMPONENT)}:{line_number} mentions '{needle}'")

    assert not offenders, (
        "production code must not resolve translated text: emit identifiers and let the "
        "presentation layer resolve them. If this module genuinely has no later layer to "
        "hand an identifier to, say so at the top of this file and add it to the exception "
        "list, rather than widening the rule quietly:\n  " + "\n  ".join(offenders)
    )


def test_no_module_raises_a_notification_outside_the_notifier():
    """The other half of the same rule, and the one that was missing.

    The guard above asks who *resolves* text. This one asks who *sends* it, which
    is where the escape actually happened: three sites called
    ``persistent_notification.create`` directly with their title and message
    written as English literals. They resolved nothing, so the guard above had
    nothing to catch them with, and they reached every non-English installation
    untranslated for months after the interface was supposed to have stopped
    doing that.

    Composing the text and delivering it are the same act for a notification -
    the string handed over *is* the presentation - so the module allowed to do
    one is the module allowed to do the other, and it is the same single name.
    """
    offenders: list[str] = []
    for module in sorted(_COMPONENT.rglob("*.py")):
        if "__pycache__" in module.parts or module.name in _ALLOWED:
            continue
        source = module.read_text(encoding="utf-8")
        for line_number, line in enumerate(source.splitlines(), start=1):
            if _NOTIFICATION_DOMAIN in line:
                offenders.append(f"{module.relative_to(_COMPONENT)}:{line_number}")

    assert not offenders, (
        "a notification must go through ValveNotifier, which takes its title and body from "
        "the catalogue. A direct persistent_notification call carries text written in "
        "Python, and text written in Python reaches every user in English:\n  " + "\n  ".join(offenders)
    )


def test_no_user_facing_sentence_is_written_in_python():
    """The third question, and the one nothing was asking.

    The two guards above ask who *resolves* translated text and who *sends* a
    notification. Neither asks who **writes prose aimed at a user**, which is
    how twelve plausibility warnings sat in ``config_flow.py`` and reached every
    installation in English - inside a form that was otherwise entirely in the
    reader's language (GH #286).

    They are in the catalogue now, so the door can be closed behind them. The
    order mattered: a guard added while those twelve were still there would have
    shipped with twelve exceptions listed, and a guard born disabled is not a
    guard.

    A sentence is the thing being looked for, not a string. Identifiers, format
    keys, entity ids and log messages are all strings, and none of them is
    prose: the test asks for several words of running English on one line,
    outside a comment or a docstring, in a module that builds what a user reads.
    """
    offenders: list[str] = []
    # Log lines are for whoever reads the log, and are deliberately not
    # translated; the modules below are the ones that compose interface text.
    surfaces = ("config_flow.py",)
    for name in surfaces:
        module = _COMPONENT / name
        source = module.read_text(encoding="utf-8")
        in_docstring = False
        for line_number, line in enumerate(source.splitlines(), start=1):
            stripped = line.strip()
            quotes = stripped.count('"""')
            if quotes == 1:
                in_docstring = not in_docstring
                continue
            # Two on one line is a single-line docstring: prose, but for a
            # reader of the source rather than a user of the product.
            if quotes >= 2 or in_docstring or stripped.startswith("#") or "_LOGGER" in line:
                continue
            for literal in re.findall(r'"([^"\n]{30,})"', line):
                words = literal.split()
                if len(words) >= 6 and literal[0].isupper() and "_" not in literal:
                    offenders.append(f"{name}:{line_number} {literal[:60]}...")

    assert not offenders, (
        "a sentence a user will read, written in Python rather than taken from the catalogue. "
        "It will reach every non-English installation in English:\n  " + "\n  ".join(offenders)
    )


def test_the_exception_list_names_only_modules_that_exist():
    """A stale name in the exception list is a hole nobody can see.

    A module renamed or deleted leaves its exemption behind, and the next file to take that
    name inherits a permission it never argued for.
    """
    missing = sorted(name for name in _ALLOWED if not (_COMPONENT / name).exists())
    assert not missing, f"exempted modules that no longer exist: {missing}"
