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
- ✅ Stabilize lap start detection (MS01 edge cases)
- ⏳ Add a minisector debug dump (per lap snapshot) to log
- ⏳ Add a per-track minisector sanity checker:
  - missing indices
  - unusual splits (too small/too large)

### Rain / Strategy Core
- 🚧 Live race strategy reacting to:
  - weather changes
  - Safety Car / VSC phases
  - pit loss vs. pace delta evaluation
- 🚧 Expose RainEngine decision details in UI/log:
  - wetness score
  - confidence
  - reasons (signals used)
- ⏳ Add safeguards for early-session low-sample situations
  (confidence gating / “collecting data” state)

---

## NEXT (User-facing strategy output)

### Linux Support
- ⏳ adding support for linux distros

### Strategy UI
- ⏳ Calculate strategy based on selected team context
  - pit loss model
  - tyre behavior differences
- ⏳ Consider opponent strategies and gaps
  - defensive pit stops (e.g. Inter → Wet if gap allows)
  - undercut / overcut awareness
- ⏳ Replace placeholder Strategy Cards with real outputs
  - Plan A/B/C from strategy core + DB stats
  - show confidence + short reasoning
- ⏳ Add "Recommendation" header:
  - BOX / STAY OUT
  - target tyre
  - box-in lap estimate
- ⏳ Integrate SC/VSC effect into pit window calculation
  - reduced pit loss
  - rejoin position estimation
  - live evaluation using UDP + DB pace

### Degradation / Pit Windows
- ⏳ Integrate degradation estimates into strategy cards:
  - max stint estimate
  - 1-stop / 2-stop feasible windows
- ⏳ Mark and exclude inlaps/outlaps/outliers in UI
  (already supported by analysis logic; needs presentation)
- 🚧 Use condition SHIFT detection in degradation model
  - exclude laps after strong condition changes from dry-fit
- 🚧 Prefer minisector (or at least sector) data over full lap times
  - allow partial laps (e.g. outlaps without pit minisectors)

### Safety Car Decisions
- ⏳ Add SC panel:
  - Box / Stay / Opposite recommendation
  - estimated delta / pit-loss model
  - basic risk tags (traffic, track position)

### UI & Output quality
- ⏳ Convert lap times from ss.ms to m:ss.ms consistently in UI
- ⏳ Display Stint ID in UI
  - e.g. "Stint 2 – Lap 5"
- 🚧 Show minisector times with color coding
  - green / yellow / purple (🚧 green not there because no opponent times)

### Driver & Data Selection
- ⏳ Allow selecting individual drivers
- ⏳ Automatically highlight the most relevant driver times
  - closest rivals
  - strategy-relevant cars
  - cars changing from e.g. Slick to Inter purple times
---

## LATER (Expansion & polish)

### Multi-Game Support
- ⏳ Add F1 24 profile (then step-by-step down to F1 2017)
  - 🚧 add F1 2020 profile (experimental)
- ⏳ Compatibility layer for missing UDP features in older games
- ⏳ Optional manual track profiles:
  - track length overrides
  - sector boundary overrides (for older games)

### Track & Metadata Coverage
- ⏳ Extend track ID mapping
- ⏳ Track-specific preset baselines (optional)

### Weather forecast expansion
- 🧪 Interpret "next lap" from minute-based forecast samples
  - map minutes → laps using estimated lap time

### Multiple OS Support
- ⏳ adding support for Android devices

### Quality of Life
- ⏳ Installer / portable build
- ⏳ Auto-update check
- ⏳ Better logging controls (verbosity levels)
- ⏳ Showing explicit words for weather etc. instead of number
- ⏳ Add audio output for key strategy events
  - open pit window (“Box box box”)
  - weather / rain threshold reactions
  - Safety Car / VSC state changes


### AI implementation
- ⏳ AI-assisted strategy logic
  - higher-level decision-making
  - scenario evaluation
  - long-term race outcome estimation

---

## Known Limitations (by design / current scope)
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
