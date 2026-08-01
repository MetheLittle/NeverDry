# Changelog

All notable changes to NeverDry are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- Imperial units in the config flow and displays ([#139]):
  - Zone threshold help text no longer hardcodes "(mm)" — the field label already shows the user's unit (mm or in).
  - Deficit, threshold and ET sensors now declare a display precision, so imperial users see meaningful decimals instead of values rounded to whole inches.
  - Reconfiguring a zone in imperial is now stable: threshold and max-deficit round-trip through inches without drifting on every edit.

## [0.11.0] - 2026-07-26

First stable of the 0.11 line. Theme: **trust your water balance**.

### Fixed
- Rain over-counting on accumulator sensors: credit only positive increments, so excess rain no longer causes under-watering ([#123]).
- Phantom rain from rolling-24h sensors eliminated.
- Rain baseline survives restarts — no re-crediting of cumulative rain at boot.
- Config-entry-scoped `unique_id`s, avoiding clashes across multiple installations ([#116]).
- Manual valve close now accounts for delivered water instead of resetting the deficit.
- Hardware self-close mid-session is no longer mistaken for manual irrigation.
- Manual sessions settle on entry unload.
- Valve close verification: retry cap of 5, silent transient retries, CRITICAL only on definitive failure.
- Legacy rain entities auto-removed on setup (no orphans); new rain-sensor `unique_id` avoids the mm→L unit-changed repair.

### Added
- Per-zone authoritative deficit, scheduled top-up, and yearly rain.
- "Irrigated Yearly" sensor (irrigation-only; feeds the Home Assistant Energy dashboard).
- Full ET bypass when a soil-moisture (VWC) sensor is configured.
- Zone card: Duration and Last Duration as `mm:ss`, localized "Last irrigated", live "Session water" during flow-metered cycles.
- System sensors editable in the options flow.
- Redesigned landing page with star CTA and download/install badges.
- Public engineering docs, CONTRIBUTING, and Vision.

### Changed
- Documented the `main`/`develop` branching model.
- CI runs on `develop` and on pull requests; dependency bumps.

---

For releases prior to 0.11.0, see the [GitHub Releases](https://github.com/drake69/NeverDry/releases) page.

[Unreleased]: https://github.com/drake69/NeverDry/compare/v0.11.0...HEAD
[0.11.0]: https://github.com/drake69/NeverDry/releases/tag/v0.11.0
[#139]: https://github.com/drake69/NeverDry/issues/139
[#123]: https://github.com/drake69/NeverDry/issues/123
[#116]: https://github.com/drake69/NeverDry/issues/116
