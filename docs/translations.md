# Translations

NeverDry ships its interface text in the files under
`custom_components/never_dry/translations/`. Home Assistant picks the one
matching the user's language and falls back to English when there is none.

## What ships, and who checked it

| Language | File | Provenance |
|---|---|---|
| English | `en.json` | Source language. Written by the maintainer; every string originates here. |
| Italian | `it.json` | Human translated and checked by a native speaker. |

Every language listed above is **human checked**. That is a deliberate bar, not
a description of how things happen to stand: a mistranslated label in a form
that decides how much water reaches a garden is not a cosmetic problem, and a
machine translation nobody has read is indistinguishable from a correct one
until it is in front of a user.

If a language ever ships without that check, this table says so in its own row
rather than leaving the reader to assume. An unchecked translation is better
than no translation, but only when it is labelled.

## Contributing a language

The shortest path, and the one that credits you automatically:

1. Copy `custom_components/never_dry/translations/en.json` to `<code>.json`,
   using the Home Assistant language code (`de`, `fr`, `nl`, …).
2. Translate the **values**. Leave every key untouched, and leave the
   `{placeholders}` in braces exactly as they are — they are filled in at
   runtime with names, numbers and units.
3. Open a pull request. Your commits carry your authorship, so GitHub records
   the contribution without anyone having to remember to.

If a pull request is inconvenient, open an issue with the file attached and it
will be added for you — but say so, because the commit will then be authored by
the maintainer and your name has to be entered by hand in the contributors list.

### What to watch for while translating

- **Labels are names, not explanations.** The label names the field; the
  explanation belongs in `data_description` beside it. A label long enough to be
  a sentence is a mistake — there is a test that fails on it.
- **Never write an identifier.** Values like `estimated_flow` are internal keys
  that have their own translated labels; naming one in a message shows the user
  the machinery. There is a test for this too.
- **Units belong to the reader.** Depths are millimetres and flows litres per
  hour in metric, inches and gallons per hour in imperial. The form does the
  conversion; the text only has to name the right one.

Both tests live in `tests/test_translation_consistency.py`, and they run against
every language file, so a new one is held to the same rules as the ones already
here.
