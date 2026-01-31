# Changelog

All notable changes to this project will be documented in this file.

The format is based on *Keep a Changelog*, and the project follows
Semantic Versioning (in an early 0.x stage).

---

## [Unreleased]

### Added

- None

### Changed

- None

### Fixed

- None

---

## [0.3.5] - 2025-01-31

### Added

- experimental F1 2017-F1 24 functionality
    - not all features might be working yet
- code optimizations
- outsourced some stuff
- tyre choice and wear widget

---

## [0.3.4] - 2025-01-18

### Added

- Debugging
    - minisector debug dump
    - missing indices snapshot
- Rain Forecast
    - next lap forecast

---

## [0.3.3] - 2025-01-17

### Added

- pause of lap timer when pausing game

### Fixed

- delta is now calculated as intended

---

## [0.3.2] - 2025-01-17

### Added

- health bar for easier bug fixing
- option to export CSV data
- cache clear button

---

## [0.3.1] - 2025-01-17

### Changed

- current minisectors are now shown as a grey bar

### Fixed

- fixed delta not being shown as intended

---

## [0.3.0] - 2025-01-16

### Added

- begin of building intuitive UI
    - added flag widget
    - added lap timing data widget
        - lap timing
        - minisectors
        - delta (WIP)
        - other informations (e.g. weather, tyre, speed); WIP

### Changed

- outsourcing of elements for smaller files

---

## [0.2.1] - 2026-01-16

### Fixed

- MS01 not being recognized

---

## [0.2.0] - 2025-12-27

### Added

- full standalone CSV writing and tracking
    - separate CSV writing telemetry tools are no longer mandatory, but can still be used as alternative

---

## [0.1.3] - 2025-12-27

### Added

- standalone CSV creation and tracking

---

## [0.1.2] - 2025-12-27

### Added

- debugging via UDP dump & replay (Instructions can be found at `.docs/how to use/debugging`)

---

## [0.1.1] - 2025-12-27

### Added

- weather visualization as text

---

## [0.1.0] - 2025-12-27

### Added

- Live UDP telemetry support for **F1 25**
- Automatic detection of supported F1 games (profile-based system)
- Minisector tracking (30 minisectors per lap) for F1 25
- Robust MS30 detection (end-of-lap fix)
- Flashback / rollback-safe minisector logic
- Fallback sector logic for older F1 titles (e.g. F1 2020)

- CSV import (compatible with Overtake Telemetry Tool)
- Automatic detection of new or updated CSV files (folder watcher)
- Persistent storage via SQLite (WAL mode) with automatic schema migration
- Session UID support (overflow-safe)
- Track-based aggregations and historical lap storage

- RainEngine (stateful decision engine) for tyre crossover decisions:
    - Slick ↔ Inter
    - Inter ↔ Wet
- Multi-signal fusion for decisions:
    - field tyre distribution
    - pace deltas (Inter–Slick / Wet–Inter)
    - weather state & rain forecast
    - track and air temperature trends
- Hysteresis and lockout system to prevent flip-flopping
- Payback-based pit stop evaluation
- Safety Car awareness

- Stint reconstruction from historical lap data
- Tyre wear models per track and compound
- Degradation estimation (wear per lap, pace loss vs wear, max stint length)
- Pit window helpers (one-stop and two-stop)

- Multilingual support (JSON-based i18n): EN, DE, FR, IT, ES, PT, Sinhala
- Centralized logging (UI + file)
- Configurable application settings
- Platform-neutral application directories (config/cache/db/log)
