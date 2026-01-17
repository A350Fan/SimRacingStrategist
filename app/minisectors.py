# app/minisectors.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, List, Tuple


MINIS_PER_SECTOR = 10
TOTAL_MINIS = 3 * MINIS_PER_SECTOR

def _clamp(x: float, a: float, b: float) -> float:
    return a if x < a else b if x > b else x

@dataclass
class MiniRow:
    idx: int
    last_ms: Optional[int] = None

    # NEW: Which lap does last_ms belong to?
    # This allows the UI to keep showing old values across the lap line,
    # while calculations (Theo/Missing) can ignore stale values.
    last_lap_tag: Optional[int] = None

    # If True, last_ms was not measured directly but derived (fallback).
    # We only use this as a safety net when a lap would otherwise be unusable.
    last_estimated: bool = False

    # "When did this minisector finish in the CURRENT lap timeline?"
    # Used to roll back only affected minisectors on flashback/rewind.
    last_end_ms: Optional[int] = None

    pb_ms: Optional[int] = None
    best_ms: Optional[int] = None  # session best (for now: same as pb; later can be "best of all cars")

@dataclass
class MiniSectorTracker:
    minis_per_sector: int = MINIS_PER_SECTOR

    _rows: List[MiniRow] = field(default_factory=lambda: [MiniRow(i) for i in range(TOTAL_MINIS)])
    _cur_idx: Optional[int] = None
    _last_split_ms: Optional[int] = None
    _last_lap_num: Optional[int] = None
    _last_seen_lap_ms: Optional[int] = None
    _last_split_dist_m: Optional[float] = None   # start distance of current minisector (within lap)
    _last_seen_dist_m: Optional[float] = None    # last seen lap distance (within lap)
    _just_lapped: bool = False  # set True on lap change to avoid skipping MS01 if first tick arrives late
    _partial_split: bool = True

    # Completed-lap queue (additive): snapshot of last_ms when lap number increments
    _completed_laps: List[dict] = field(default_factory=list)

    # last computed boundaries (meters)
    _track_len_m: Optional[float] = None
    _s2_m: Optional[float] = None
    _s3_m: Optional[float] = None

    def rows(self) -> List[MiniRow]:
        return self._rows

    def current_index(self) -> Optional[int]:
        return self._cur_idx

    def sum_last_ms(self) -> Optional[int]:
        vals = [r.last_ms for r in self._rows]
        if any(v is None for v in vals):
            return None
        return int(sum(int(v) for v in vals))

    def sum_pb_ms(self) -> Optional[int]:
        vals = [r.pb_ms for r in self._rows]
        if any(v is None for v in vals):
            return None
        return int(sum(int(v) for v in vals))

    def sum_best_ms(self) -> Optional[int]:
        vals = [r.best_ms for r in self._rows]
        if any(v is None for v in vals):
            return None
        return int(sum(int(v) for v in vals))

    def missing_last_indices(self) -> list[int]:
        # 1-based minisector numbers
        return [r.idx + 1 for r in self._rows if r.last_ms is None]

    def sum_last_ms_current(self) -> Optional[int]:
        """
        Sum of minisectors for the CURRENT lap only.
        We require that each minisector has a value tagged with the current lap number.
        """
        if self._last_lap_num is None:
            return self.sum_last_ms()

        vals = []
        for r in self._rows:
            if r.last_ms is None:
                return None
            vals.append(int(r.last_ms))
        return int(sum(vals))

    def missing_current_indices(self) -> list[int]:
        """
        Minisectors that are missing for the CURRENT lap (tag mismatch or None).
        1-based minisector numbers.
        """
        if self._last_lap_num is None:
            return self.missing_last_indices()

        cur = int(self._last_lap_num)
        out = []
        for r in self._rows:
            if r.last_ms is None:
                out.append(r.idx + 1)
        return out

    def pop_completed_laps(self) -> List[dict]:
        """
        Return and clear completed-lap snapshots.
        Each entry is a dict with lap_num, lap_time_ms, minis[], missing[], complete.
        """
        out = list(self._completed_laps)
        self._completed_laps.clear()
        return out

    def _snapshot_lap(self, lap_num: Optional[int], lap_time_ms: Optional[int]) -> dict:
        minis = []
        for r in self._rows:
            minis.append({
                "ms_no": r.idx + 1,
                "split_ms": r.last_ms,
                "end_ms": r.last_end_ms,
                "pb_ms": r.pb_ms,
                "best_ms": r.best_ms,
                "estimated": bool(r.last_estimated),  # NEW: allows main.py to add '*' for MS01
            })

        missing = [m["ms_no"] for m in minis if m["split_ms"] is None]
        complete = (len(missing) == 0)

        return {
            "lap_num": int(lap_num) if lap_num is not None else None,
            "lap_time_ms": int(lap_time_ms) if lap_time_ms is not None else None,
            "minis_per_sector": int(self.minis_per_sector),
            "total_minis": int(len(minis)),
            "minis": minis,
            "missing": missing,
            "complete": bool(complete),
        }

    def reset_lap(self, cur_lap_time_ms: Optional[int]) -> None:
        # Start a new lap timeline: Last values are per-lap, PB/Best are session-wide.
        # Clearing Last here prevents stale minisector times from leaking into the next lap
        # when we miss early ticks (which is the root cause for MS1 being "empty" or wrong).
        for r in self._rows:
            # IMPORTANT:
            # Do NOT wipe r.last_ms here, otherwise the UI will "blank" the whole Last column on lap line.
            # We only reset per-lap timeline bookkeeping. Each minisector will be overwritten individually
            # as soon as we measure it in the new lap.
            r.last_end_ms = None
            # keep last_estimated + last_ms visible (belongs to previous lap via last_lap_tag)

        self._cur_idx = None
        self._last_split_ms = 0 if isinstance(cur_lap_time_ms, int) else None
        self._last_seen_lap_ms = None
        self._partial_split = True

        # Distance bookkeeping for the new lap
        self._last_split_dist_m = None
        self._last_seen_dist_m = None

    def _maybe_estimate_ms1_from_lap_time(self, lap_time_ms: Optional[int]) -> None:
        """
        Fallback for the (common) case where MS01 is missing because the first UDP tick
        of the lap arrives late.

        If we have a reliable total lap time AND all other minisectors (MS02..MS30)
        are present, we can derive:
            MS01 = lap_time_ms - sum(MS02..MS30)

        This keeps lap time / delta / prediction features usable, but we mark it as estimated.
        """
        if lap_time_ms is None:
            return

        try:
            lt = int(lap_time_ms)
        except Exception:
            return

        if lt <= 0:
            return

        # Only if MS01 is missing and MS02..MS30 are all present.
        if not self._rows:
            return
        if self._rows[0].last_ms is not None:
            return

        rest = [r.last_ms for r in self._rows[1:]]
        if any(v is None for v in rest):
            return

        try:
            rest_sum = int(sum(int(v) for v in rest if v is not None))
        except Exception:
            return

        ms1 = lt - rest_sum

        # sanity: minisectors should not be crazy
        if not (120 <= ms1 <= 120_000):
            return

        self._rows[0].last_ms = int(ms1)
        self._rows[0].last_estimated = True

    def rollback_last_after(self, now_ms: int) -> None:
        """
        Flashback: remove ONLY minisectors that ended after now_ms in the current lap timeline.
        PB/Best stay untouched.
        """
        for r in self._rows:
            if r.last_end_ms is not None and r.last_end_ms > now_ms:
                r.last_ms = None
                r.last_end_ms = None
                r.last_estimated = False

    def _compute_idx(self, lap_dist_m: float, track_len_m: float, s2_m: float, s3_m: float) -> int:
        # normalize distance into [0, track_len)
        ld = lap_dist_m % track_len_m
        ld = _clamp(ld, 0.0, track_len_m - 1e-6)

        # sector ranges
        s1_len = max(1.0, s2_m - 0.0)
        s2_len = max(1.0, s3_m - s2_m)
        s3_len = max(1.0, track_len_m - s3_m)

        if ld < s2_m:
            sector = 0
            frac = (ld - 0.0) / s1_len
        elif ld < s3_m:
            sector = 1
            frac = (ld - s2_m) / s2_len
        else:
            sector = 2
            frac = (ld - s3_m) / s3_len

        mini = int(frac * self.minis_per_sector)
        if mini < 0:
            mini = 0
        if mini >= self.minis_per_sector:
            mini = self.minis_per_sector - 1

        return sector * self.minis_per_sector + mini

    def _bounds_for_idx(self, idx: int, tl: float, s2: float, s3: float) -> tuple[float, float]:
        """
        Return (start_m, end_m) of minisector idx within the lap distance domain [0, tl].
        Minisectors are 10 per sector, using sector boundaries s2, s3.
        """
        idx = max(0, min(int(idx), TOTAL_MINIS - 1))

        # sector 1: [0, s2)
        # sector 2: [s2, s3)
        # sector 3: [s3, tl)
        if idx < self.minis_per_sector:
            a, b = 0.0, float(s2)
            j = idx
        elif idx < 2 * self.minis_per_sector:
            a, b = float(s2), float(s3)
            j = idx - self.minis_per_sector
        else:
            a, b = float(s3), float(tl)
            j = idx - 2 * self.minis_per_sector

        seg = (b - a) / float(self.minis_per_sector)
        start = a + j * seg
        end = start + seg
        return start, end

    def _start_for_idx(self, idx: int, tl: float, s2: float, s3: float) -> float:
        return self._bounds_for_idx(idx, tl, s2, s3)[0]

    def update(
            self,
            *,
            lap_dist_m: Optional[float],
            cur_lap_time_ms: Optional[int],
            cur_lap_num: Optional[int],
            track_len_m: Optional[int],
            sector2_start_m: Optional[float],
            sector3_start_m: Optional[float],
            allow_sector_fallback: bool = False,  # NEW: workaround for older games (e.g. F1 2020)
    ) -> bool:
        """
        Returns True if we completed a minisector split (table should refresh).
        """
        if lap_dist_m is None or cur_lap_time_ms is None:
            return False
        if track_len_m is None or track_len_m <= 0:
            return False

        tl = float(track_len_m)
        now_ms = int(cur_lap_time_ms)

        # Prefer real sector boundaries (F1 25 normal path)
        if sector2_start_m is not None and sector3_start_m is not None:
            s2 = float(sector2_start_m)
            s3 = float(sector3_start_m)
        else:
            # Workaround path (F1 2020): approximate sector boundaries as thirds of track length
            if not allow_sector_fallback:
                return False
            s2 = tl / 3.0
            s3 = 2.0 * tl / 3.0

        # sanity: must be ordered and within track
        if not (0.0 < s2 < s3 < tl):
            return False

        # lap change -> finalize last minisector using current lap time as end-of-lap boundary
        try:
            if cur_lap_num is not None and self._last_lap_num is not None and int(cur_lap_num) != int(
                    self._last_lap_num):
                # close the minisector we were in at end of previous lap
                if self._cur_idx is not None and self._last_split_ms is not None:
                    try:
                        # Use last seen lap time BEFORE the reset as end-of-lap boundary
                        end_ms = int(self._last_seen_lap_ms) if self._last_seen_lap_ms is not None else int(
                            cur_lap_time_ms)
                        split_ms = end_ms - int(self._last_split_ms)

                        if 120 <= split_ms <= 120_000:
                            # --- Robust: determine end-of-lap minisector by LAST seen distance (prevents wrap-to-0 issue) ---
                            try:
                                end_dist = self._last_seen_dist_m
                                if end_dist is None:
                                    end_dist = float(lap_dist_m)
                                end_idx = self._compute_idx(float(end_dist), tl, s2, s3)
                            except Exception:
                                end_idx = int(self._cur_idx)

                            cur = int(end_idx)

                            # ensure we have a plausible "start distance" for proportional split
                            if self._last_split_dist_m is None:
                                try:
                                    self._last_split_dist_m = self._start_for_idx(cur, tl, s2, s3)
                                except Exception:
                                    self._last_split_dist_m = None

                            # --- MS30 fix (robust) ---
                            last_idx = TOTAL_MINIS - 1  # MS30 index (29)
                            prev_idx = TOTAL_MINIS - 2  # MS29 index (28)
                            ms30_row = self._rows[last_idx]

                            # If we didn't get ticks for the very end of the lap, we may "end" in MS27/MS28/MS29.
                            # Robustly distribute the remaining time from our last split start distance up to tl
                            # across ALL remaining minisectors, ensuring MS30 gets a value.
                            if ms30_row.last_ms is None and self._last_split_dist_m is not None:
                                start_d = float(self._last_split_dist_m)

                                # Build remaining minisector segments from current (cur) to last (MS30)
                                segs = []
                                for k in range(cur, last_idx + 1):
                                    a, b = self._bounds_for_idx(k, tl, s2, s3)
                                    a = float(a)
                                    b = float(b)

                                    # effective start: can't start before our last split start
                                    eff_a = max(a, start_d)
                                    eff_b = b
                                    if eff_b > eff_a:
                                        segs.append((k, eff_b - eff_a))

                                if segs:
                                    total_d = sum(d for _, d in segs)
                                    # safety: if total_d is weird, fallback to old behavior
                                    if total_d > 0.0:
                                        remaining = int(split_ms)

                                        # allocate times proportionally, keep sum == split_ms and each >= 1ms
                                        alloc = []
                                        for i, (k, d) in enumerate(segs):
                                            if i == len(segs) - 1:
                                                t = remaining
                                            else:
                                                t = int(round(split_ms * (d / total_d)))
                                                t = max(1,
                                                        min(t, remaining - (len(segs) - i - 1)))  # keep room for rest
                                                remaining -= t
                                            alloc.append((k, t))

                                        # write sequential end_ms timestamps
                                        t_left = int(split_ms)
                                        end_cursor = int(end_ms)
                                        for k, t in reversed(alloc):
                                            r = self._rows[k]
                                            r.last_ms = int(t)
                                            r.last_end_ms = int(end_cursor)
                                            if r.pb_ms is None or t < r.pb_ms:
                                                r.pb_ms = int(t)
                                            if r.best_ms is None or t < r.best_ms:
                                                r.best_ms = int(t)
                                            end_cursor -= int(t)
                                            t_left -= int(t)
                                    else:
                                        r = self._rows[cur]
                                        r.last_ms = split_ms
                                        r.last_end_ms = end_ms
                                        if r.pb_ms is None or split_ms < r.pb_ms:
                                            r.pb_ms = split_ms
                                        if r.best_ms is None or split_ms < r.best_ms:
                                            r.best_ms = split_ms
                                else:
                                    r = self._rows[cur]
                                    r.last_ms = split_ms
                                    r.last_end_ms = end_ms
                                    if r.pb_ms is None or split_ms < r.pb_ms:
                                        r.pb_ms = split_ms
                                    if r.best_ms is None or split_ms < r.best_ms:
                                        r.best_ms = split_ms
                            else:
                                # default: close minisector at lap end
                                r = self._rows[cur]
                                r.last_ms = split_ms
                                r.last_end_ms = end_ms
                                if r.pb_ms is None or split_ms < r.pb_ms:
                                    r.pb_ms = split_ms
                                if r.best_ms is None or split_ms < r.best_ms:
                                    r.best_ms = split_ms

                    except Exception:
                        pass

                # snapshot previous lap minisectors BEFORE resetting (additive)
                try:

                    prev_lap_num = int(self._last_lap_num) if self._last_lap_num is not None else None
                    prev_lap_time_ms = int(self._last_seen_lap_ms) if self._last_seen_lap_ms is not None else int(
                        cur_lap_time_ms)

                    # --- MS01 fallback (only if it was missing) ---
                    # This is intentionally conservative: it only fills MS01 when we have a complete
                    # lap time and ALL other minisectors (MS02...MS30) are present.
                    self._maybe_estimate_ms1_from_lap_time(prev_lap_time_ms)
                    self._completed_laps.append(self._snapshot_lap(prev_lap_num, prev_lap_time_ms))
                except Exception:
                    pass

                self._just_lapped = True
                self.reset_lap(cur_lap_time_ms)


        except Exception:
            pass

        self._last_lap_num = int(cur_lap_num) if cur_lap_num is not None else self._last_lap_num

        # Keep last seen lap time updated on EVERY tick (not only on minisector transitions).
        # Otherwise, if the lap ends while we stay inside the same minisector, the lap-change
        # handler may close the last minisector using a stale _last_seen_lap_ms -> MS30 missing.
        self._last_seen_lap_ms = now_ms

        # keep last seen distance inside lap domain (0..tl)
        try:
            self._last_seen_dist_m = float(lap_dist_m) % tl
        except Exception:
            pass

        idx = self._compute_idx(float(lap_dist_m), tl, s2, s3)

        if self._cur_idx is None:
            # Determine lap distance in [0..tl)
            try:
                ld0 = float(lap_dist_m) % tl
            except Exception:
                ld0 = None

            # IMPORTANT:
            # LapNum is not reliable/early enough in some situations. Use cur_lap_time_ms instead:
            # If curLap time is still small, we assume we are at the start of a (new) lap even if the first
            # UDP tick arrives already deep into the lap (e.g. 600m / 7.5s).

            # Treat as lap start in two situations:
            # 1) We JUST detected a lap change (most reliable signal we have).
            #    In that case, even if the first tick arrives at ld0 ~ 0..5m, we MUST start timing at 0ms,
            #    otherwise MS01 becomes a "partial split" and will be discarded forever.
            # 2) We attach early in a lap (now_ms small) even if lapNum is unreliable.

            force_lap_start = bool(self._just_lapped and (now_ms <= 120_000))

            treat_as_lap_start = (
                    force_lap_start
                    or (
                            (ld0 is not None)
                            and (now_ms <= 15000)
                            and (ld0 < 0.35 * tl)
                    )
            )

            print(
                f"[MS INIT] now_ms={now_ms} "
                f"ld0={None if ld0 is None else round(ld0, 1)} "
                f"idx={idx} "
                f"lap_start={treat_as_lap_start} "
                f"just_lapped={self._just_lapped} "
                f"last_lap_num={self._last_lap_num} "
                f"cur_lap_num={cur_lap_num}"
            )

            # We consumed the "just lapped" hint now (regardless of which branch we take).
            # Prevent it from leaking into later ticks.
            if self._just_lapped:
                self._just_lapped = False

            if treat_as_lap_start:
                # Backfill minisectors 0..idx-1 proportionally by distance, then start timing from start of idx.
                try:
                    start_d = float(self._start_for_idx(idx, tl, s2, s3))
                    # time from lap start (0ms) to start of current idx (distance proportion)
                    if ld0 > 0.5 and start_d > 0.0:
                        t_to_start = int(round(now_ms * (start_d / max(1e-3, ld0))))
                        t_to_start = max(0, min(t_to_start, now_ms))
                    else:
                        t_to_start = 0

                    if idx > 0 and t_to_start > 0:
                        segs = []
                        for k in range(0, idx):
                            a, b = self._bounds_for_idx(k, tl, s2, s3)
                            d = float(b) - float(a)
                            if d > 0.0:
                                segs.append((k, d))

                        total_d = sum(d for _, d in segs)
                        if total_d > 0.0 and segs:
                            remaining = int(t_to_start)
                            alloc = []
                            for i, (k, d) in enumerate(segs):
                                if i == len(segs) - 1:
                                    t = remaining
                                else:
                                    t = int(round(t_to_start * (d / total_d)))
                                    t = max(1, min(t, remaining - (len(segs) - i - 1)))
                                    remaining -= t
                                alloc.append((k, t))

                            end_cursor = int(t_to_start)
                            for k, t in reversed(alloc):
                                r = self._rows[k]
                                r.last_ms = int(t)
                                r.last_end_ms = int(end_cursor)
                                if r.pb_ms is None or t < r.pb_ms:
                                    r.pb_ms = int(t)
                                if r.best_ms is None or t < r.best_ms:
                                    r.best_ms = int(t)
                                end_cursor -= int(t)

                    # Start current minisector at its boundary
                    self._cur_idx = int(idx)
                    self._last_split_ms = int(t_to_start)  # 0..now_ms
                    self._last_split_dist_m = float(start_d)
                    self._partial_split = False
                    self._just_lapped = False
                    return True

                except Exception:
                    # fallback below
                    pass

                # No backfill needed (idx==0 or insufficient data): start at 0ms
                self._cur_idx = int(idx)
                self._last_split_ms = 0
                self._last_split_dist_m = self._start_for_idx(idx, tl, s2, s3)
                self._partial_split = False
                self._just_lapped = False
                return False

            # --- Not lap start (true mid-lap attach / resync) ---
            self._cur_idx = int(idx)
            self._last_split_ms = int(now_ms)
            self._last_split_dist_m = self._start_for_idx(idx, tl, s2, s3)
            self._partial_split = True
            return False

        if idx == self._cur_idx:
            return False

        # We crossed into a new minisector => close previous minisector time
        # Use difference in current lap time
        try:
            # now_ms = int(cur_lap_time_ms)
            # Flashback/rewind: lap time jumps backwards -> roll back only affected minisectors
            # Flashback/rewind guard:
            # Only treat "time went backwards" as flashback if we are STILL in the same lap.
            # On a lap change, cur_lap_time_ms naturally resets near 0 and must NOT trigger rollback.
            same_lap = (cur_lap_num is not None and self._last_lap_num is not None and int(cur_lap_num) == int(
                self._last_lap_num))

            if same_lap and self._last_seen_lap_ms is not None and now_ms < self._last_seen_lap_ms - 150:
                # delete only minisectors that ended after now_ms in the undone timeline
                self.rollback_last_after(now_ms)

                # restart timing from the new timeline position
                self._last_split_ms = now_ms

                # re-sync current minisector index to the current position
                self._cur_idx = self._compute_idx(float(lap_dist_m), tl, s2, s3)
                self._partial_split = True
                self._last_split_dist_m = self._start_for_idx(self._cur_idx, tl, s2, s3)

                self._last_seen_lap_ms = now_ms
                return False

            self._last_seen_lap_ms = now_ms
        except Exception:
            return False

        if self._last_split_ms is None:
            self._last_split_ms = now_ms
            self._cur_idx = idx
            return False

        split_ms = now_ms - int(self._last_split_ms)
        # sanity (avoid pit/teleport spikes)
        if split_ms <= 0 or split_ms > 120_000:
            self._last_split_ms = now_ms
            self._cur_idx = idx
            self._partial_split = True
            return False

        # store for the minisector we just finished
        finished_idx = self._cur_idx
        r = self._rows[finished_idx]

        if self._partial_split:
            # This split is not a full minisector (started mid-minisector / resynced / rewind)
            # -> do NOT write Last/PB/Best (keeps stored lap data clean)
            self._partial_split = False
        else:
            r.last_ms = split_ms
            r.last_end_ms = now_ms
            r.last_estimated = False  # echtes Messsignal überschreibt Fallback

            # Tag this split as belonging to the current lap number (if available)
            try:
                r.last_lap_tag = int(cur_lap_num) if cur_lap_num is not None else self._last_lap_num
            except Exception:
                r.last_lap_tag = self._last_lap_num

            if r.pb_ms is None or split_ms < r.pb_ms:
                r.pb_ms = split_ms
            if r.best_ms is None or split_ms < r.best_ms:
                r.best_ms = split_ms

        # advance
        self._last_split_ms = now_ms
        self._cur_idx = idx
        self._last_split_dist_m = self._start_for_idx(idx, tl, s2, s3)
        return True