# Switching between LIVE and REPLAY mode (UDP)

This guide explains how to switch **SimRacingStrategist** between **LIVE telemetry** (game running) and **REPLAY mode** (offline playback from a dump file).

---

## Core concept

- **LIVE**: The app receives UDP telemetry directly from the game (F1 25 / F1 2020).
- **REPLAY**: The app replays previously recorded UDP dumps (`.bin`) offline.
- Both modes use **the exact same telemetry parsing and logic pipeline**.

Switching modes is done exclusively via `config.json`.

---

## Location of config.json (IMPORTANT)

The active configuration file is located at:

```
%LOCALAPPDATA%\SimRacingStrategist\config.json
```

> Editing any other `config.json` (e.g. in the project folder) has **no effect**.

---

## LIVE mode (normal operation without dumping)

### Purpose
- Normal day-to-day usage with the game running
- **No UDP dump recording**
- Recommended for pure racing / strategy usage

### Minimal configuration (LIVE without dump)
```json
{
  "udp_enabled": true,
  "udp_source": "LIVE",
  "udp_dump_enabled": false
}
```

### Notes
- **No** `.bin` files are created
- Behaviour is identical to LIVE mode before the replay system existed
- All existing features (minisectors, strategy, weather, tyres) work unchanged

---

## LIVE mode (with dump recording)

### Purpose
- Normal usage with the game running
- Record raw UDP telemetry for later replay

### Configuration (LIVE + dump)
```json
{
  "udp_enabled": true,
  "udp_source": "LIVE",
  "udp_dump_enabled": true,
  "udp_dump_file": "",
  "udp_output_root": "G:/eigeneSRTcsv"
}
```

### Workflow
1. Start the app
2. Start the F1 game
3. Drive (Practice / Qualifying / Race)
4. A dump file is created automatically:
   ```
   udp_dump_YYYYMMDD_HHMMSS.bin
   ```

---

## REPLAY mode (offline, no game required)

### Purpose
- Feature development
- Debugging and reproducible testing
- No need to start the F1 game

### Configuration for REPLAY
```json
{
  "udp_enabled": true,
  "udp_source": "REPLAY",
  "udp_replay_file": "G:/eigeneSRTcsv/F1_25_Imola_Race_Test.bin",
  "udp_replay_speed": 1.0
}
```

### Notes
- The game **must not be running**
- `udp_replay_file` must point to an existing `.bin` file
- Replay speed:
  - `1.0` = real time
  - `2.0` = double speed
  - `0.5` = half speed

---

## Managing dump files

### Renaming dumps
- Dumps can be renamed **at any time**
- File names have **no impact** on replay

Examples:
```
F1_25_Imola_Race_WetStart.bin
F1_2020_Monza_Quali_MinisectionBug.bin
```

### Moving / archiving
- Dumps can be moved freely (other folders, NAS, archive, etc.)
- Only requirement: `udp_replay_file` must reference the correct path

---

## Common issues

### ❌ No dump file created
- `udp_dump_enabled` is not set to `true`
- Wrong `config.json` edited (not LocalAppData)
- Output directory not accessible

### ❌ Replay does not start
- `udp_source` still set to `LIVE`
- Invalid path in `udp_replay_file`
- Dump file does not exist

---

## Recommended workflow

1. Record a session using **LIVE + dump**
2. Rename the dump meaningfully
3. Switch to **REPLAY** mode
4. Develop and test features offline
5. Record new dumps only when needed

---

## Summary

| Action | Setting |
|------|---------|
| Drive live | `udp_source = LIVE` |
| Record dumps | `udp_dump_enabled = true` |
| Offline testing | `udp_source = REPLAY` |
| Change replay speed | `udp_replay_speed` |

---

This guide applies to **F1 25** and **F1 2020** (with game-specific limitations).

