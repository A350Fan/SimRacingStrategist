from __future__ import annotations

import struct


def handle_lap_data_packet(self, hdr, data: bytes) -> None:
    """
    PID 2: LapData parsing
    """
    # PacketLapData total size is 1285 bytes in F1 25 spec.

    # LapData struct is exactly 57 bytes, repeated 22 times.

    base = int(hdr.get("headerSize", 29))
    pkt_fmt = int(hdr.get("packetFormat", 0))

    changed = False

    # ---------------------------------------------------------
    # Legacy LapData (F1 2017..2024):
    # header 24 + 22 * 53 bytes
    #
    # Key difference:
    # - 2017..2020: last/current lap time are FLOAT seconds
    # - 2021..2024: last/current lap time are UINT32 milliseconds
    # ---------------------------------------------------------
    if 2017 <= pkt_fmt <= 2024:
        # Der sichere Weg: car_size aus Paketlänge ableiten (F1 21-24 kann von 53 abweichen)
        remaining = len(data) - base
        if remaining <= 0:
            return

        car_size = remaining // 22

        # sanity: LapData pro Auto liegt typischerweise irgendwo um ~40-60 Bytes
        if not (40 <= car_size <= 70):
            if self.debug:
                print(
                    f"[LAP LEGACY] unexpected car_size={car_size} remaining={remaining} len={len(data)} base={base} fmt={pkt_fmt}")
            return

        lap_time_is_float = (pkt_fmt <= 2020)

        if self.debug:
            print(
                f"[LAP LEGACY] fmt={pkt_fmt} base={base} len={len(data)} remaining={remaining} car_size={car_size} float_times={lap_time_is_float}")

        for i in range(22):
            off = base + i * car_size

            try:
                # --- last/current lap time ---
                if lap_time_is_float:
                    # 2017-2020: float seconds
                    last_s = struct.unpack_from("<f", data, off + 0)[0]
                    cur_s = struct.unpack_from("<f", data, off + 4)[0]
                    last_ms = int(round(last_s * 1000.0)) if last_s and last_s > 0 else None
                    cur_ms = int(round(cur_s * 1000.0)) if cur_s and cur_s > 0 else 0
                else:
                    # 2021-2024: uint32 milliseconds
                    last_ms_raw = struct.unpack_from("<I", data, off + 0)[0]
                    cur_ms_raw = struct.unpack_from("<I", data, off + 4)[0]
                    last_ms = int(last_ms_raw) if last_ms_raw > 0 else None
                    cur_ms = int(cur_ms_raw) if cur_ms_raw > 0 else 0
                    if self.debug and i == self._player_idx:
                        print(f"[LAP LEGACY PLAYER] idx={i} cur_ms={cur_ms} last_ms={last_ms}")

                # sector times are uint16 ms (best-effort; ok if 0)
                s1_ms = struct.unpack_from("<H", data, off + 8)[0]
                s2_ms = struct.unpack_from("<H", data, off + 10)[0]

                # ---------------------------------------------------------
                # Offsets variieren je nach Spiel/Jahr.
                # Für F1 22 scheint CarLapData bei dir 43 bytes zu sein (siehe Log).
                # Daher: zwei bekannte Layouts probieren und plausibel auswählen.
                # ---------------------------------------------------------

                def _plausible(lap_dist: float, lapn: int, pit: int, res: int) -> bool:
                    # lap distance grob plausibel (m)
                    if not (-500.0 <= float(lap_dist) <= 20_000.0):
                        return False
                    if not (0 <= int(lapn) <= 80):
                        return False
                    if not (0 <= int(pit) <= 2):
                        return False
                    if not (0 <= int(res) <= 10):
                        return False
                    return True

                # Layout A (kompakt, passt zu ~43B):
                # last(0) cur(4) s1(8) s2(10) lapDist(12) totalDist(16) scDelta(20)
                # carPos(24) lapNum(25) pit(26) ... resultStatus(36/37)
                lap_dist_A = struct.unpack_from("<f", data, off + 12)[0]
                total_dist_A = struct.unpack_from("<f", data, off + 16)[0]
                lap_num_A = struct.unpack_from("<B", data, off + 25)[0]
                pit_A = struct.unpack_from("<B", data, off + 26)[0]
                res_A = struct.unpack_from("<B", data, off + 37)[0] if (off + 37) < (off + car_size) else 0

                # Layout B (dein altes 53B-Layout):
                lap_dist_B = struct.unpack_from("<f", data, off + 32)[0]
                total_dist_B = struct.unpack_from("<f", data, off + 36)[0]
                lap_num_B = struct.unpack_from("<B", data, off + 46)[0]
                pit_B = struct.unpack_from("<B", data, off + 47)[0]
                res_B = struct.unpack_from("<B", data, off + 52)[0] if (off + 52) < (off + car_size) else 0

                # Wähle das plausiblere Layout
                if _plausible(lap_dist_A, lap_num_A, pit_A, res_A):
                    lap_dist_m = lap_dist_A
                    total_dist_m = total_dist_A
                    lap_num = lap_num_A
                    pit_status = pit_A
                    result_status = res_A
                else:
                    lap_dist_m = lap_dist_B
                    total_dist_m = total_dist_B
                    lap_num = lap_num_B
                    pit_status = pit_B
                    result_status = res_B


            except struct.error:
                continue

            self._pit_status[i] = int(pit_status)
            self._result_status[i] = int(result_status)

            # update player fields
            if i == self._player_idx:
                if self.state.player_lap_distance_m != float(lap_dist_m):
                    self.state.player_lap_distance_m = float(lap_dist_m)
                    changed = True

                if self.state.player_current_lap_time_ms != int(cur_ms):
                    self.state.player_current_lap_time_ms = int(cur_ms)
                    changed = True

                if self.state.player_sector1_time_ms != int(s1_ms):
                    self.state.player_sector1_time_ms = int(s1_ms)
                    changed = True

                if self.state.player_sector2_time_ms != int(s2_ms):
                    self.state.player_sector2_time_ms = int(s2_ms)
                    changed = True

                if self.state.player_pit_status != int(pit_status):
                    self.state.player_pit_status = int(pit_status)
                    changed = True

                if self.state.player_current_lap_num != int(lap_num):
                    self.state.player_current_lap_num = int(lap_num)
                    changed = True

            # last-lap handling (for deltas/history) – lässt deine Logik intakt
            if last_ms is not None and last_ms < 10_000_000:
                prev_ms = self._last_lap_ms[i]
                if prev_ms != last_ms:
                    self._last_lap_ms[i] = last_ms
                    changed = True

                    # keep your existing validity/outlap logic as-is (minimal safe)
                    valid = True
                    lap_flag = "OK"

                    if self._pit_status[i] != 0 and last_ms >= 200_000:
                        valid = False
                        lap_flag = "IN"

                    if hasattr(self, "_ignore_next_lap") and self._ignore_next_lap[i]:
                        looks_like_outlap = False
                        if isinstance(prev_ms, int) and prev_ms > 0:
                            if (last_ms - prev_ms) >= getattr(self, "_outlap_slow_ms", 45_000):
                                looks_like_outlap = True
                        if last_ms >= 200_000:
                            looks_like_outlap = True

                        if looks_like_outlap:
                            valid = False
                            lap_flag = "OUT"
                        self._ignore_next_lap[i] = False

                    self._lap_valid[i] = valid
                    self._lap_flag[i] = lap_flag

                    if i == self._player_idx:
                        if self.state.player_last_lap_time_ms != last_ms:
                            self.state.player_last_lap_time_ms = last_ms
                            changed = True

                    # keep per-car history buffers if present
                    if hasattr(self, "_car_laps") and hasattr(self, "_tyre_cat"):
                        cat = self._tyre_cat[i]
                        if valid and cat in ("SLICK", "INTER", "WET"):
                            lap_s = last_ms / 1000.0
                            buf = self._car_laps[i][cat]
                            if self._robust_accept_lap(buf, lap_s):
                                buf.append(lap_s)

                    if (
                            hasattr(self, "_your_laps")
                            and self._player_idx is not None
                            and i == self._player_idx
                            and hasattr(self, "_tyre_cat")
                    ):
                        cat = self._tyre_cat[i]
                        if valid and cat in ("SLICK", "INTER", "WET"):
                            lap_s = last_ms / 1000.0
                            ybuf = self._your_laps[cat]
                            if self._robust_accept_lap(ybuf, lap_s):
                                ybuf.append(lap_s)

        if changed:
            self._update_field_metrics_and_emit()

        return  # IMPORTANT: stop here for 2017..2024, don’t fall through to F1 25 parser

    # ----------------------------
    # F1 25 LapData (your existing code)
    # ----------------------------
    car_size = 57
    if len(data) < base + car_size * 22:
        return

    fmt_lap = (
        "<II"  # last/current lap ms
        "H B"  # s1 ms part, s1 min part
        "H B"  # s2 ms part, s2 min part
        "H B"  # delta front ms part, delta front min part
        "H B"  # delta leader ms part, delta leader min part
        "f f f"  # lapDistance, totalDistance, safetyCarDelta
        "15B"  # 15x uint8
        "H H"  # pitLaneTimeInLaneMS, pitStopTimerInMS
        "B"  # pitStopShouldServePen
        "f"  # speedTrapFastestSpeed (km/h)
        "B"  # speedTrapFastestLap
    )

    # ... ab hier bleibt dein existierender F1-25 Loop unverändert ...

    for i in range(22):
        off = base + i * car_size

        (
            last_ms,
            cur_ms,

            s1_ms_part, s1_min_part,
            s2_ms_part, s2_min_part,

            d_front_ms_part, d_front_min_part,
            d_lead_ms_part, d_lead_min_part,

            lap_dist_m,
            total_dist_m,
            sc_delta_s,

            car_pos,
            lap_num,
            pit_status,
            num_pit,
            sector,
            lap_invalid,
            penalties,
            total_warn,
            cc_warn,
            unserved_dt,
            unserved_sg,
            grid_pos,
            driver_status,
            result_status,
            pit_lane_timer_active,

            pit_lane_time_ms,
            pit_stop_timer_ms,
            pit_should_serve_pen,

            speed_trap_fast_kmph,
            speed_trap_fast_lap,
        ) = struct.unpack_from(fmt_lap, data, off)

        # sector times are split into minutes + ms-part

        s1_ms = int(s1_ms_part) + int(s1_min_part) * 60_000

        s2_ms = int(s2_ms_part) + int(s2_min_part) * 60_000

        self._pit_status[i] = int(pit_status)
        self._result_status[i] = int(result_status)

        # update player live fields

        if i == self._player_idx:

            # lapDistance may be negative before crossing the line; keep it, but it's fine

            if self.state.player_lap_distance_m != float(lap_dist_m):
                self.state.player_lap_distance_m = float(lap_dist_m)

                changed = True

            if self.state.player_current_lap_time_ms != int(cur_ms):
                self.state.player_current_lap_time_ms = int(cur_ms)

                changed = True

            if self.state.player_sector1_time_ms != s1_ms:
                self.state.player_sector1_time_ms = s1_ms

                changed = True

            if self.state.player_sector2_time_ms != s2_ms:
                self.state.player_sector2_time_ms = s2_ms

                changed = True

            if self.state.player_pit_status != int(pit_status):
                self.state.player_pit_status = int(pit_status)

                changed = True

            if self.state.player_current_lap_num != int(lap_num):
                self.state.player_current_lap_num = int(lap_num)

                changed = True

        # Last lap time handling (this is what you used for deltas/history)

        # ignore obvious garbage

        if last_ms and last_ms < 10_000_000:

            last_ms = int(last_ms)

        else:

            last_ms = None

        if last_ms is not None:

            prev_ms = self._last_lap_ms[i]

            if prev_ms != last_ms:

                self._last_lap_ms[i] = last_ms

                changed = True

                # your existing validity/outlap logic can remain,

                # but now it uses the real pit_status and real last lap time:

                valid = True

                lap_flag = "OK"

                # conservative "IN" detection: only if pit status says pitting AND lap is very slow

                if self._pit_status[i] != 0 and last_ms >= 200_000:
                    valid = False

                    lap_flag = "IN"

                # keep your outlap ignore mechanism if you want (only if you have _ignore_next_lap)

                if hasattr(self, "_ignore_next_lap") and self._ignore_next_lap[i]:

                    looks_like_outlap = False

                    if isinstance(prev_ms, int) and prev_ms > 0:

                        if (last_ms - prev_ms) >= getattr(self, "_outlap_slow_ms", 45_000):
                            looks_like_outlap = True

                    if last_ms >= 200_000:
                        looks_like_outlap = True

                    if looks_like_outlap:
                        valid = False

                        lap_flag = "OUT"

                    self._ignore_next_lap[i] = False

                self._lap_valid[i] = valid

                self._lap_flag[i] = lap_flag

                # update player's last lap in state

                if i == self._player_idx:

                    if self.state.player_last_lap_time_ms != last_ms:
                        self.state.player_last_lap_time_ms = last_ms

                        changed = True

                # keep your per-car history updates if they exist

                if hasattr(self, "_car_laps") and hasattr(self, "_tyre_cat"):

                    cat = self._tyre_cat[i]

                    if valid and cat in ("SLICK", "INTER", "WET"):

                        lap_s = last_ms / 1000.0

                        buf = self._car_laps[i][cat]

                        if self._robust_accept_lap(buf, lap_s):
                            buf.append(lap_s)

                # keep your "your laps" buffers if they exist

                if (

                        hasattr(self, "_your_laps")

                        and self._player_idx is not None

                        and i == self._player_idx

                        and hasattr(self, "_tyre_cat")

                ):

                    cat = self._tyre_cat[i]

                    if valid and cat in ("SLICK", "INTER", "WET"):

                        lap_s = last_ms / 1000.0

                        ybuf = self._your_laps[cat]

                        if self._robust_accept_lap(ybuf, lap_s):
                            ybuf.append(lap_s)

    if changed:
        self._update_field_metrics_and_emit()
    pass
