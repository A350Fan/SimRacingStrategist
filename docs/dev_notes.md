# Developer Notes

This file contains internal development notes, design decisions,
and rationale behind certain implementation choices in SimRaceStrategist.

It is intended for developers and future maintainers.

---

## Project Scope & Philosophy

- SimRaceStrategist is intentionally **source-available**, not open source.
- The project prioritizes **correctness and robustness** over feature count.
- CSV import and UDP input are treated as **untrusted external data sources**.

---

## Telemetry Input Design

### CSV vs UDP Authority

Design decision:

- CSV telemetry is treated as the authoritative source for:
    - historical lap times
    - tyre wear
    - fuel load
    - degradation analysis

- UDP telemetry is treated as:
    - volatile
    - jitter-prone
    - session-local

Reasons:

- UDP lap timing can be incomplete or unstable
- CSV files represent finalized lap data
- Combining both allows stable analysis + live context

UDP lap recording exists as an optional parallel path,
but does not replace CSV-based persistence.

### CSV Import

- CSV files are treated as immutable inputs.
- Original CSV files are never modified.
- All parsing happens on cached copies to avoid corrupting user data.
- CSV import supports multi-block formats used by third-party telemetry tools.

### Third-Party Tools

- No third-party telemetry software is bundled or redistributed.
- CSV format usage is considered a data format, not code reuse.
- The project is explicitly independent of any third-party telemetry tools.

---

## UDP Telemetry Design

- UDP parsing is intentionally limited to:
    - Safety Car / VSC state
    - Weather & weather forecast
    - Selected AI field information
    - own laptime data
- Full lap-time parsing from UDP is avoided to reduce jitter and instability.
- CSV data is considered the authoritative source for historical laps.

---

## Minisector Tracking

### F1 25

- Uses native sector boundary distances provided by the game.
- Minisectors are split proportionally within sectors (default: 10 per sector).
- Special handling exists for:
    - late first UDP tick in a lap
    - lap end without final minisector tick (MS30 fix)
    - flashbacks / rewinds

### Older Games (e.g. F1 2020)

- No native minisector or sector boundary distances available.
- Sector boundaries are approximated as thirds of track length.
- Minisector data is considered **experimental** and round-local only.

### Design Rationale & Edge Cases

Minisector tracking is intentionally defensive and stateful due to
limitations and inconsistencies in F1 UDP telemetry.

Key design decisions:

- `lap_num` alone is not reliable to detect lap starts:
    - First UDP tick of a lap may arrive several seconds late
    - Distance may already be deep into the lap
    - Minisector 01 would otherwise be skipped

- Lap start detection therefore uses a combination of:
    - `cur_lap_time_ms`
    - lap distance relative to track length
    - heuristic thresholds (time + distance)

- End-of-lap handling (MS30 fix):
    - The final minisector often receives no UDP tick
    - Remaining lap time is distributed proportionally across
      the remaining minisector distance segments
    - This guarantees a complete minisector set per lap when possible

- Flashback / rewind support:
    - If lap time jumps backwards within the same lap,
      only minisectors that ended *after* the rollback point are cleared
    - PB / Best values are never rolled back

- Older games (e.g. F1 2020):
    - No native sector boundary distances exist
    - Sector boundaries are approximated as thirds of track length
    - Minisector data is treated as round-local and experimental

---

## RainEngine Design

- Strategy decisions are based on **signal fusion**, not a single metric.
- Key signals:
    - field tyre distribution
    - pace deltas (Inter–Slick, Wet–Inter)
    - weather state and forecast
    - track and air temperature trends
- Hysteresis and lockout logic is used to prevent flip-flopping.
- Early-session decisions are intentionally conservative.

### Thresholds, Hysteresis & Lockouts

Rain decisions are intentionally conservative and multi-signal-based.

Reasons:

- Single signals (rain%, field share, lap delta) are often noisy or delayed
- Early-session data is sparse and unreliable
- Flip-flopping tyres is strategically worse than being slightly late

Design choices:

- Wetness score is a weighted fusion of:
    - pace deltas (I–S, W–I)
    - rain now / forecast
    - track and air temperature trends
    - AI field tyre distribution
    - optional baseline pace loss

- Hysteresis is used to:
    - require multiple consecutive confirmations before switching
    - prevent oscillation around threshold values

- Directional lockouts:
    - Wet → Inter uses a longer lockout than Inter → Wet
    - Emergency overrides exist for extreme pace deltas or wetness

- Safety Car / VSC:
    - Wetness and confidence are boosted slightly
    - Allows earlier pit calls when pit loss is reduced

- Forecast handling:
    - Short-term horizons (3–10 min) are weighted more than long-term
    - Drying-soon detection prevents unnecessary tyre refreshes

---

## Strategy vs UI Separation

- Core strategy logic is UI-agnostic.
- Strategy modules must produce:
    - a clear recommendation
    - a confidence score
    - a short human-readable reason
- UI is treated as a presentation layer only.

---

## Data Persistence

- SQLite is used for simplicity and reliability.
- WAL mode is enabled to reduce write contention.
- Schema migrations are handled automatically at startup.

---

## Known Trade-offs

- Accuracy vs availability:
    - Prefer fewer but reliable signals over noisy real-time data.
- Flexibility vs control:
    - Source-available license chosen to protect long-term project direction.
- Early correctness vs fast iteration:
    - Complex heuristics are implemented early to avoid later rewrites.

---

## Future Refactoring Notes

- Consider extracting RainEngine into a standalone module.
- Minisector logic may be split per-game profile.
- Track metadata (lengths, sectors) may move into versioned config files.
