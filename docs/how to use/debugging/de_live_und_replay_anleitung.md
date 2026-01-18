# Wechsel zwischen LIVE- und REPLAY-Modus (UDP)

Diese Anleitung beschreibt, wie du in **SimRacingStrategist** zwischen **LIVE-Telemetrie** (Spiel läuft) und *
*REPLAY-Modus** (Offline aus Dump-Datei) wechselst.

---

## Grundprinzip

- **LIVE**: Die App empfängt UDP-Daten direkt vom Spiel (F1 25 / F1 2020).
- **REPLAY**: Die App liest zuvor aufgezeichnete UDP-Dumps (`.bin`) und speist sie zeitkorrekt wieder ein.
- Beide Modi nutzen **denselben Telemetrie-Parser und dieselbe Logik**.

Der Wechsel erfolgt ausschließlich über die `config.json`.

---

## Speicherort der config.json (WICHTIG)

Die aktive Konfigurationsdatei liegt unter:

```
%LOCALAPPDATA%\SimRacingStrategist\config.json
```

> Änderungen an anderen `config.json`-Dateien (z. B. im Projektordner) haben **keine Wirkung**.

---

## LIVE-Modus (normales Fahren ohne Dump)

### Zweck

- Normale Nutzung der App mit laufendem Spiel
- **Keine** Aufzeichnung von UDP-Dumps
- Empfohlen für reines Racing / Strategie-Einsatz

### Minimale Konfiguration (LIVE ohne Dump)

```json
{
  "udp_enabled": true,
  "udp_source": "LIVE",
  "udp_dump_enabled": false
}
```

### Hinweise

- Es wird **keine** `.bin`-Datei erzeugt
- Verhalten entspricht dem klassischen LIVE-Betrieb vor Einführung des Replay-Systems
- Bestehende Features (Minisektoren, Strategie, Wetter, Reifen) laufen unverändert

---

## LIVE-Modus (Aufzeichnen / normales Fahren)

### Zweck

- Normale Nutzung mit laufendem Spiel
- Optional: UDP-Dumps aufzeichnen

### Minimale Konfiguration (LIVE ohne Dump)

```json
{
  "udp_enabled": true,
  "udp_source": "LIVE"
}
```

### LIVE + Dump-Aufzeichnung (empfohlen)

```json
{
  "udp_enabled": true,
  "udp_source": "LIVE",
  "udp_dump_enabled": true,
  "udp_dump_file": "",
  "udp_output_root": "G:/eigeneSRTcsv"
}
```

### Ablauf

1. App starten
2. F1-Spiel starten
3. Fahren (Training / Quali / Rennen)
4. Dump-Datei wird automatisch erstellt:
   ```
   udp_dump_YYYYMMDD_HHMMSS.bin
   ```

---

## REPLAY-Modus (Offline-Test ohne Spiel)

### Zweck

- Debugging
- Feature-Entwicklung
- Reproduzierbare Tests ohne F1-Spiel

### Konfiguration für REPLAY

```json
{
  "udp_enabled": true,
  "udp_source": "REPLAY",
  "udp_replay_file": "G:/eigeneSRTcsv/F1_25_Imola_Race_Test.bin",
  "udp_replay_speed": 1.0
}
```

### Hinweise

- Das Spiel **darf nicht laufen**
- `udp_replay_file` muss auf eine existierende `.bin` zeigen
- Replay-Geschwindigkeit:
    - `1.0` = Echtzeit
    - `2.0` = doppelte Geschwindigkeit
    - `0.5` = halbe Geschwindigkeit

---

## Dumps verwalten

### Umbenennen

- Dumps können **jederzeit umbenannt** werden
- Der Dateiname hat **keinen Einfluss** auf den Inhalt

Beispiele:

```
F1_25_Imola_Race_WetStart.bin
F1_2020_Monza_Quali_MinisectionBug.bin
```

### Verschieben

- Dumps können frei verschoben oder archiviert werden
- Wichtig ist nur, dass `udp_replay_file` den korrekten Pfad enthält

---

## Typische Fehlerquellen

### ❌ Kein Dump wird erstellt

- `udp_dump_enabled` ist nicht `true`
- falsche `config.json` editiert (nicht LocalAppData)
- Zielordner nicht erreichbar

### ❌ Replay startet nicht

- `udp_source` steht noch auf `LIVE`
- Pfad in `udp_replay_file` ist falsch
- Datei existiert nicht

---

## Empfohlener Workflow

1. **LIVE + Dump** aufnehmen
2. Dump sinnvoll umbenennen
3. Auf **REPLAY** umstellen
4. Features entwickeln / testen
5. Bei Bedarf erneut LIVE aufnehmen

---

## Zusammenfassung

| Aktion                 | Einstellung               |
|------------------------|---------------------------|
| Live fahren            | `udp_source = LIVE`       |
| Dump aufzeichnen       | `udp_dump_enabled = true` |
| Offline testen         | `udp_source = REPLAY`     |
| Geschwindigkeit ändern | `udp_replay_speed`        |

---

Diese Anleitung ist versionsunabhängig und gilt für F1 25 sowie F1 2020 (mit spielbedingten Einschränkungen).

