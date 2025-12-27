# TODO (Roadmap)

This file tracks planned work items for SimRaceStrategist.
The project is under active development; priorities may change.

Legend:
- ✅ done
- 🚧 in progress
- 🧪 experimental
- ⏳ planned

---

## NOW (Stabilize the core)

### Telemetry & Data Pipeline
- ⏳ Add a "data health" panel/log summary:
  - CSV import status (last file, last parse time)
  - UDP status (connected, last packet age, port)
  - DB write status (last insert/update)
- ⏳ Improve CSV robustness:
  - handle missing/renamed columns gracefully
  - validate multi-block structure
  - better error messages (file + block + reason)
- ⏳ Add basic data retention tools:
  - clear cache button / CLI option
  - DB export (CSV) for debugging

### Minisectors (F1 25)
- 🚧 Stabilize lap start detection (MS01 edge cases)
- ⏳ Add a minisector debug dump (per lap snapshot) to log
- ⏳ Add a per-track minisector sanity checker:
  - missing indices
  - unusual splits (too small/too large)

### Rain / Strategy Core
- ⏳ Expose RainEngine decision details in UI/log:
  - wetness score
  - confidence
  - reasons (signals used)
- ⏳ Add safeguards for early-session low-sample situations
  (confidence gating / “collecting data” state)

---

## NEXT (User-facing strategy output)

### Strategy UI
- ⏳ Replace placeholder Strategy Cards with real outputs
  - Plan A/B/C from strategy core + DB stats
  - show confidence + short reasoning
- ⏳ Add "Recommendation" header:
  - BOX / STAY OUT
  - target tyre
  - box-in lap estimate

### Degradation / Pit Windows
- ⏳ Integrate degradation estimates into strategy cards:
  - max stint estimate
  - 1-stop / 2-stop feasible windows
- ⏳ Mark and exclude inlaps/outlaps/outliers in UI
  (already supported by analysis logic; needs presentation)

### Safety Car Decisions
- ⏳ Add SC panel:
  - Box / Stay / Opposite recommendation
  - estimated delta / pit-loss model
  - basic risk tags (traffic, track position)

---

## LATER (Expansion & polish)

### Multi-Game Support
- ⏳ Add F1 24 profile (then step-by-step down to F1 2017)
- ⏳ Compatibility layer for missing UDP features in older games
- ⏳ Optional manual track profiles:
  - track length overrides
  - sector boundary overrides (for older games)

### Track & Metadata Coverage
- ⏳ Extend track ID mapping
- ⏳ Track-specific preset baselines (optional)

### Quality of Life
- ⏳ Installer / portable build
- ⏳ Auto-update check (optional)
- ⏳ Better logging controls (verbosity levels)

---

## Known Limitations (by design / current scope)
- CSV import relies on user-generated CSV files from third-party telemetry tools.
  No third-party telemetry software is bundled or redistributed.
- Minisector accuracy varies by game:
  - F1 25: native minisectors
  - older titles: approximation/fallback where necessary

---

## Definition of Done (for features)
A feature is considered "done" when:
- it does not break existing functionality
- it logs useful debug info on failure
- it has a minimal UI output OR clear CLI/log output
- it is documented briefly in CHANGELOG / README if user-visible
