# Known Issues

This document lists known problems, limitations, and workarounds of the project.

---

## 🟥 Critical Issues

### KI-001 – Minisector tracking unstable
**Affected version(s):** v0.1.0 - v0.3.1   
**Affected modules:** MiniSectorTracker  

**Description:**  
When using flashbacks, minisectors may be overwritten or counted twice if lap time and lap distance do not rewind consistently.
When restarting a session, every minisector is filled with a tiny number (0.xxx ms)

**Impact:**  
- Inconsistent minisector times  
- PB / best values may be corrupted  

**Workaround:**  
Flashback: Rollback logic only removes minisectors whose `end_ms` is greater than the current lap time.

**Planned fix:**  
Additional safeguards using a lap UID or distance–time consistency checks.

---

## 🟧 Medium Issues

### KI-002 – Minisector timing may deviate by up to ±16.67 ms
**Affected version(s):** v0.1.0 - v0.3.1   
**Affected modules:** MiniSectorTracker  

**Description:**  
Due to the maximum 60 Hz UDP tick rate of the F1 games, minisector times cannot be measured with true millisecond precision.  
As a result, minisector times and theoretical lap times may partially deviate from in-game timings.

**Impact:**  
- Inaccurate minisector times  
- PB / best values may be affected
- inaccurate lap predictions

**Planned fix:**  
A solution for more accurate minisector timing is still under investigation.

### KI-003 – Lap Timer might be inaccurate
**Affected version(s):** v0.1.0 - v0.3.2   
**Affected modules:** HUD Widget Live UI  

**Description:**  
60Hz limitation sends data every 16,6ms, so pause of timer might be delayed

**Impact:**  
- Lap timer is not accurate if game is paused 


**Planned fix:**  
Solution will be investigated


### KI-004 – Minisector fallback in F1 2020 is only approximate
**Affected version(s):** v0.1.0 - v0.3.1   
**Affected modules:** MiniSectorTracker  

**Description:**  
Since F1 2020 does not provide real sector start distances, sectors are approximated as thirds of the total track length.

**Impact:**  
- Minisectors are not track-accurate  
- Comparability is limited  

**Workaround:**  
Explicitly enable the fallback only for older games (`allow_sector_fallback=True`).

**Planned fix:**  
Optional manual track profiles with real sector distances.

> **Note:** Minisectors in F1 2020 are currently considered an **experimental feature**  
> and are not comparable in accuracy to F1 25.

---

### KI-005 – F1 2020: Minisector times are cleared on every new lap
**Affected version(s):** v0.1.0 - v0.3.1   
**Affected modules:** MiniSectorTracker, F1 2020 fallback logic  

**Description:**  
In F1 2020, all minisector `last_ms` values are reset at the start of each new lap.  
Instead of overwriting minisector times when crossing them again, the entire minisector set is cleared.

**Impact:**  
- No continuous minisector history across laps  
- No PB / delta comparisons between laps  
- Minisector-based strategy evaluation is severely limited  

**Root cause:**  
Design limitation caused by missing native minisector and sector distance data in F1 2020.  
The current logic treats each lap as an isolated unit.

**Planned fix:**  
- Enforce separation between `last_ms` (current lap) and `pb_ms` (persistent best)  
- Optional persistence of last valid minisector times across laps  
- Clear feature flag: `persistent_minisectors=False` for older games


### KI-006 – F1 2017 - F1 24: Teams are not detected yet
**Affected version(s):** v0.3.4 - v0.3.4   
**Affected modules:** CSV logger  

**Description:**  
As F1 2017-24 compatibility is WIP, some features, like Team ID detection, might be inaccurate

**Impact:**  
- data might be misread  

**Root cause:**  
- byte reading in F1 2017 - 2024 is not perfectly working yet

**Planned fix:**  
- fix byte reading offsets  


---

### KI-007 – Rain forecast occasionally reported as `n/a`
**Affected version(s):** v0.1.0 - v0.3.1   
**Affected modules:** RainEngine, f1_udp  

**Description:**  
Forecast values (e.g. minute 3 / 20) may be `None` if a complete UDP forecast series has not yet been received.

**Impact:**  
- Reduced confidence in strategy decisions  
- Early session phases are less reliable  

    > This typically affects the first ~5–15 seconds after session start.

**Workaround:**  
Median-based data fusion automatically ignores missing values.

**Planned fix:**  
Forecast pre-buffering using a minimum number of samples.

---

## 🟨 Low Issues

### KI-008 – Strategy Cards are currently placeholders
**Affected version(s):** v0.1.0 - v0.3.1   
**Affected modules:** UI  

**Description:**  
Strategy Cards currently use placeholder data only.

**Impact:**  
- UI does not yet display real live recommendations  

**Workaround:**  
Use for visual layout and UI validation only.

**Planned fix:**  
Connect to real strategy outputs.

---

### KI-009 – lap timing is not done yet
**Affected version(s):** v0.1.0 - v0.3.1   
**Affected modules:** UI  

**Description:**  
- some data could be strange
- tyre sets are shown wrongly
- speed etc. have no data

**Impact:**  
- some features are just not done yet --> can't use them  

**Planned fix:**  
- finish UI implementation

---

## 🧪 Experimental / Design-related Limitations

### KI-010 – Field deltas not always available
**Affected version(s):** v0.1.0 - v0.3.1   
**Affected modules:** RainEngine  

**Description:**  
Field-based pace deltas may be empty in short sessions.

**Note:**  
By design – sufficient sample size is required.

**Planned improvement:**  
- Fallback to own reference laps  
- Integration with lap database

---

### KI-011 – Tyre wear learning requires sufficient data
**Affected version(s):** v0.1.0 - v0.3.1  
**Affected modules:** Degradation Model  

**Description:**  
Wear and degradation models only produce reliable results after several clean stints.

**Note:**  
Expected behavior, not a bug.

**Planned improvement:**  
Confidence indicators and minimum data hints in the UI.

---
