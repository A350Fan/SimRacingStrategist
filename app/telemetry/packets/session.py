from __future__ import annotations

import struct


def handle_session_packet(self, hdr, data: bytes) -> None:
    """
    PID 1: Session packet parsing
    NOTE: This function intentionally uses `self` (the F1UDPListener instance),
    so we can move code 1:1 without refactoring.
    """

    if self.debug:
        print("[PID1] Session packet received len=", len(data))

    # basic size sanity check
    if len(data) < 150:
        return

    base = int(hdr.get("headerSize", 29))  # after PacketHeader

    changed = False

    # SessionType + TrackId (see F1 25 spec)
    # offsets: base+6 = sessionType (uint8), base+7 = trackId (int8)
    try:
        sess_type = struct.unpack_from("<B", data, base + 6)[0]
        if sess_type != self.state.session_type_id:
            self.state.session_type_id = int(sess_type)
            changed = True
    except Exception:
        pass

    try:
        trk_id = struct.unpack_from("<b", data, base + 7)[0]  # int8
        if trk_id != self.state.track_id:
            self.state.track_id = int(trk_id)
            changed = True

            # Pre-warm slick compound roles from DB as soon as we know the track.
            # This allows the UI to instantly show C# in the correct S/M/H color.
            try:
                self._seed_weekend_slick_roles_from_db()
            except Exception:
                pass
    except Exception:
        pass

    # Track length is at base+4 (see F1 25 spec: weather/temps/totalLaps then uint16 trackLength)
    try:
        track_len = struct.unpack_from("<H", data, base + 4)[0]
        if track_len > 0 and track_len != self.state.track_length_m:
            self.state.track_length_m = int(track_len)
            changed = True
    except Exception:
        pass

    # NACHDEM du self.state.track_length_m gesetzt hast (F1 25 / pkt_fmt >= 2025):
    # Im F1 25 Spec liegen diese beiden floats am Ende vom PacketSessionData:
    # float m_sector2LapDistanceStart; float m_sector3LapDistanceStart;
    # → best-effort: von hinten lesen, wenn genug Bytes da sind.

    if hdr.get("packetFormat", 0) >= 2025 and len(data) >= 8:
        try:
            s2, s3 = struct.unpack_from("<ff", data, len(data) - 8)
            # Plausi: innerhalb Tracklänge
            tl = self.state.track_length_m
            if tl and 0.0 < s2 < tl and 0.0 < s3 < tl and s2 < s3:
                self.state.sector2_start_m = float(s2)
                self.state.sector3_start_m = float(s3)
        except Exception:
            pass

    # --- Sector start distances (needed for minisectors) ---
    # F1 25+: sector2/sector3 start are appended as 2 floats at end of session packet.
    # Older games (e.g. 2020): not available -> optional fallback (approx thirds).

    sec2 = sec3 = None
    pf = int(hdr.get("packetFormat", 0) or 0)

    # F1 25+ (your normal path)
    if pf >= 2025 and len(data) >= 8:
        try:
            sec2, sec3 = struct.unpack_from("<ff", data, len(data) - 8)
        except Exception:
            sec2 = sec3 = None

    tl = float(self.state.track_length_m or 0.0)

    def _apply_sector_starts(a: float, b: float) -> None:
        nonlocal changed
        if a != self.state.sector2_start_m:
            self.state.sector2_start_m = float(a)
            changed = True
        if b != self.state.sector3_start_m:
            self.state.sector3_start_m = float(b)
            changed = True

    # sanity + apply real values if present
    if tl > 0 and sec2 is not None and sec3 is not None and 0.0 < sec2 < sec3 < tl:
        _apply_sector_starts(float(sec2), float(sec3))

    # fallback ONLY for older games / when enabled in profile
    elif tl > 0 and self._game_profile and getattr(self._game_profile, "minisector_sector_fallback", False):
        # crude but consistent: 1/3 and 2/3 of track length
        f2 = float(getattr(self._game_profile, "sector2_frac", 1.0 / 3.0))
        f3 = float(getattr(self._game_profile, "sector3_frac", 2.0 / 3.0))
        # keep sane
        f2 = max(0.10, min(0.60, f2))
        f3 = max(0.40, min(0.90, f3))
        if f2 < f3:
            _apply_sector_starts(tl * f2, tl * f3)

    # --- Marshal zones / track flags (F1 25 spec) ---
    # Your code already uses the "base + 19 + (21*5)" scheme for safetyCarStatus,
    # so we align marshal-zone offsets to that:
    # numMarshalZones @ base+18, marshalZones[] start @ base+19, each 5 bytes (float + int8).
    track_flag = None
    try:
        num_mz = data[base + 18]
        mz_start = base + 19
        max_flag = None
        for j in range(min(int(num_mz), 21)):
            zone_flag = struct.unpack_from("<b", data, mz_start + j * 5 + 4)[0]  # int8
            if zone_flag >= 0:  # ignore -1 invalid
                max_flag = zone_flag if max_flag is None else max(max_flag, zone_flag)
        track_flag = max_flag  # 0..3 or None
    except Exception:
        track_flag = None

    if self.state.track_flag != track_flag:
        self.state.track_flag = track_flag
        changed = True

    # --- Session packet fields (F1 25 spec) ---
    weather_raw = data[base + 0]  # 0..5

    if self.debug:
        print("[SESSION] weather_raw", weather_raw, "trackTemp",
              int.from_bytes(data[base + 1:base + 2], "little", signed=True))

    safety_car_off = base + 19 + (21 * 5)
    if safety_car_off + 3 >= len(data):
        return

    sc_raw = data[safety_car_off]  # 0..3
    num_fc = data[safety_car_off + 2]
    fc_off = safety_car_off + 3

    # --- Rain: current + forecast (from forecast samples) ---
    rain_now_raw = None
    rain_fc_raw = None
    fc_series = []
    self.state.rain_fc_series = None  # reset each session packet unless we fill it

    # print("[RAIN RAW]", "now", rain_now_raw, "fc", rain_fc_raw, "n_fc", int(num_fc))

    # fc_dbg = "fc:none"

    stride = 8
    if isinstance(num_fc, int) and num_fc > 0:
        need = fc_off + (num_fc * stride)
        if need <= len(data):
            for j in range(num_fc):
                o = fc_off + j * stride
                time_off_min = int(data[o + 1])  # usually minutes into future
                weather_fc = int(data[o + 2])  # 0..5
                rain_fc = int(data[o + 7])  # 0..100
                # guard
                if 0 <= time_off_min <= 240 and 0 <= weather_fc <= 5 and 0 <= rain_fc <= 100:
                    fc_series.append((time_off_min, rain_fc, weather_fc))

            # sort + dedupe by time offset
            fc_series.sort(key=lambda x: x[0])
            dedup = []
            seen = set()
            for t, r, w in fc_series:
                if t in seen:
                    continue
                seen.add(t)
                dedup.append((t, r, w))
            fc_series = dedup

            # "rain_fc_pct" = first sample (nearest future)
            if fc_series:
                # rain_now = sample with timeOffset==0 if present, else use the earliest sample as best-effort
                now_samples = [r for (t, r, w) in fc_series if t == 0]
                if now_samples:
                    rain_now_raw = now_samples[0]

                # rain_fc = nearest FUTURE sample (>0). If none, fall back to first.
                future = [(t, r) for (t, r, w) in fc_series if t > 0]
                if future:
                    future.sort(key=lambda x: x[0])
                    rain_fc_raw = future[0][1]
                else:
                    rain_fc_raw = fc_series[0][1]

        # publish series (None if empty)
        self.state.rain_fc_series = fc_series if fc_series else None

    # Rain NOW
    if rain_now_raw is not None:
        try:
            rain_now_i = int(rain_now_raw)
        except Exception:
            rain_now_i = None

        if rain_now_i is not None and 0 <= rain_now_i <= 100:
            r_now = self._deb_rain_now.update(rain_now_i)
            if r_now is not None and r_now != self.state.rain_now_pct:
                self.state.rain_now_pct = r_now
                changed = True

    # Rain FORECAST
    if rain_fc_raw is not None and 0 <= rain_fc_raw <= 100:
        r_fc = self._deb_rain_fc.update(int(rain_fc_raw))
        if r_fc is not None and r_fc != self.state.rain_fc_pct:
            self.state.rain_fc_pct = r_fc
            changed = True

    # Weather
    if 0 <= weather_raw <= 5:
        w = self._deb_weather.update(int(weather_raw))
        if w is not None and w != self.state.weather:
            self.state.weather = w
            changed = True

    # Safety Car
    if sc_raw in (0, 1, 2, 3):
        sc = self._deb_sc.update(int(sc_raw))
        if sc is not None and sc != self.state.safety_car_status:
            self.state.safety_car_status = sc
            changed = True

    if changed:
        self._dirty = True
    pass
