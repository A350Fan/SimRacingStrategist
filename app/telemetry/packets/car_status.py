from __future__ import annotations

import struct
import time


def handle_car_status_packet(self, hdr, data: bytes) -> None:
    """
    PID 7: CarStatus parsing (tyre cat/compound, FIA flag, fuel, etc.)
    """
    base = int(hdr.get("headerSize", 29))

    remaining = len(data) - base

    pkt_fmt = int(hdr.get("packetFormat", 0))

    # Robust: car_size aus Paketlänge ableiten (2017-2024 variieren)
    if remaining <= 0:
        return

    car_size = remaining // 22

    # Plausi: CarStatus pro Auto liegt typischerweise ~50-60 Bytes (je nach Spiel)
    if not (45 <= car_size <= 80):
        if self.debug:
            print(
                f"[CARSTATUS] unexpected car_size={car_size} remaining={remaining} len={len(data)} base={base} fmt={pkt_fmt}")
        return

    if remaining < 22 * car_size:
        return

    # car_size = remaining // 22  # bei dir i.d.R. 55

    changed = False

    for i in range(22):

        off = base + i * car_size

        if off + car_size > len(data):
            break

        try:
            (
                _tc,
                _abs,
                _fuel_mix,
                _bbias,
                _pitlim,
                _fuel_in_tank,
                _fuel_cap,
                _fuel_rem_laps,
                _max_rpm,
                _idle_rpm,
                _max_gears,
                _drs_allowed,
                _drs_dist,
                actual,
                visual,
                _tyre_age,
                fia_flag,
            ) = struct.unpack_from("<BBBBBfffHHBBHBBBb", data, off)

            if self.debug and i == int(hdr.get("playerCarIndex", 0)):
                print(f"[CARSTATUS PLAYER] fmt={pkt_fmt} car_size={car_size} actual={actual} visual={visual}")

            self._tyre_actual[i] = int(actual)
            self._tyre_visual[i] = int(visual)

            # Save player-specific FIA flag (blue/yellow/green/none)
            player_idx = int(hdr.get("playerCarIndex", 0))
            if i == player_idx:
                # FIA flag (existing)
                if self.state.player_fia_flag != int(fia_flag):
                    self.state.player_fia_flag = int(fia_flag)
                    changed = True

                # --- NEW: fuel (additive, best-effort) ---
                try:
                    fin = float(_fuel_in_tank)
                    if self.state.player_fuel_in_tank != fin:
                        self.state.player_fuel_in_tank = fin
                        changed = True
                except Exception:
                    pass

                try:
                    fcap = float(_fuel_cap)
                    if self.state.player_fuel_capacity != fcap:
                        self.state.player_fuel_capacity = fcap
                        changed = True
                except Exception:
                    pass

                try:
                    frem = float(_fuel_rem_laps)
                    if self.state.player_fuel_remaining_laps != frem:
                        self.state.player_fuel_remaining_laps = frem
                        changed = True
                except Exception:
                    pass

        except struct.error:
            continue

        if visual == 8:
            tyre_cat = "WET"
        elif visual == 7:
            tyre_cat = "INTER"
        else:
            tyre_cat = "SLICK"

        # NEW: exact compound label for DB/strategy (C1-C6 for slicks)
        try:
            self._tyre_compound[i] = self._compound_label(
                actual=int(actual), visual=int(visual), tyre_cat=tyre_cat
            )
        except Exception:
            self._tyre_compound[i] = tyre_cat

        now = time.monotonic()
        self._tyre_last_seen[i] = now

        pit = self._pit_status[i]

        # Während Pit nur "merken" (damit du es nicht VOR dem Stopp siehst)
        if pit in (1, 2):
            self._pending_tyre[i] = tyre_cat
        else:
            # auf Strecke: normal aktualisieren (z.B. Start, SC, etc.)
            prev_cat = self._tyre_cat[i]
            if prev_cat != tyre_cat:
                self._tyre_cat[i] = tyre_cat
                changed = True

                # WICHTIG:
                # Reifenklasse wechselt oft VOR dem nächsten LapTime-Event.
                # Dann würde die letzte Slick-Zeit fälschlich als Inter/Wet gezählt werden.
                self._last_lap_ms[i] = None
                self._lap_valid[i] = False
                self._lap_flag[i] = "TYRE_SWAP"

                # Arm outlap-ignore ONLY if this looks like a real pit tyre change:
                if prev_cat is not None:
                    self._pit_cycle[i] = 2
                    self._ignore_next_lap[i] = True

                self._last_tyre_cat[i] = tyre_cat

    # DEBUG: nach dem Verarbeiten aller 22 Autos einmal ausgeben (sonst spam)
    interwet = []
    for j in range(22):
        if self._tyre_cat[j] in ("INTER", "WET"):
            interwet.append(
                (j, self._tyre_cat[j], self._last_lap_ms[j], self._tyre_actual[j], self._tyre_visual[j]))
    if self.debug:
        print("[TYRE DEBUG] inter/wet cars:", interwet)

    if changed:
        self._dirty = True
    pass
