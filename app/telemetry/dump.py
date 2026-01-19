# app/telemetry/dump.py
from __future__ import annotations

import datetime
import struct
import time
from pathlib import Path
from typing import Iterator, Optional, Tuple

from app.logging_util import AppLogger
from app.paths import cache_dir


class UDPPacketDumpWriter:
    """
    Schreibt rohe UDP-Pakete in ein Binärformat für spätere Offline-Replays.

    Format pro Packet:
      <uint64 t_ms><uint32 n_bytes><payload...>

    - t_ms = time.monotonic()*1000 (relativ, stabil)
    - n_bytes = Länge des Payloads
    """

    def __init__(self, path: Path, *, debug: bool = False):
        self.path = Path(path)
        self.debug = bool(debug)
        self._fp = None
        self._err_logged = False

        # sicherstellen, dass Ordner existiert
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        # append-mode (falls mehrere Sessions in eine Datei sollen)
        self._fp = self.path.open("ab")
        AppLogger().info(f"UDP dump enabled -> {str(self.path)}")
        if self.debug:
            print(f"[DUMP] Writing UDP dump to: {str(self.path)}")

    @classmethod
    def from_config(cls, cfg, *, debug: bool = False) -> Optional["UDPPacketDumpWriter"]:
        """
        Erzeugt Writer basierend auf Config-Settings.
        Erwartet Felder:
          - udp_dump_enabled (bool)
          - udp_dump_file (str) optional
          - udp_output_root (str) optional
        """
        try:
            if not bool(getattr(cfg, "udp_dump_enabled", False)):
                return None

            dump_file = str(getattr(cfg, "udp_dump_file", "") or "").strip()
            if dump_file:
                p = Path(dump_file)
            else:
                root = str(getattr(cfg, "udp_output_root", "") or "").strip()
                out_dir = Path(root) if root else cache_dir()
                out_dir.mkdir(parents=True, exist_ok=True)

                ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                p = out_dir / f"udp_dump_{ts}.bin"

            return cls(p, debug=debug)

        except Exception as e:
            AppLogger().error(f"UDP dump init failed: {type(e).__name__}: {e}")
            return None

    def write_packet(self, payload: bytes) -> None:
        if not self._fp:
            return
        try:
            t_ms = int(time.monotonic() * 1000)
            self._fp.write(struct.pack("<QI", t_ms, len(payload)))
            self._fp.write(payload)
        except Exception as e:
            # kein Log-Spam
            if not self._err_logged:
                self._err_logged = True
                AppLogger().error(f"UDP dump write failed: {type(e).__name__}: {e}")

    def close(self) -> None:
        try:
            if self._fp:
                self._fp.flush()
                self._fp.close()
        except Exception:
            pass
        self._fp = None


def iter_udp_dump(path: str) -> Iterator[Tuple[int, bytes]]:
    """
    Liest UDP-Dump-Datei und yieldet (t_ms, payload) pro Packet.
    """
    p = Path(path)
    with p.open("rb") as f:
        while True:
            hdr = f.read(12)
            if len(hdr) < 12:
                break
            t_ms, n = struct.unpack("<QI", hdr)
            payload = f.read(int(n))
            if len(payload) < int(n):
                break
            yield int(t_ms), payload
