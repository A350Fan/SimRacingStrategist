# Known Issues

Diese Datei listet bekannte Probleme, Einschränkungen und Workarounds des Projekts auf.

---

## 🟥 Kritische Issues

### KI-001 – Minisektor-Erkennung am Rundenstart möglicherweise unzuverlässig
**Betroffene Version(en):** v0.1.0  
**Betroffene Module:** MiniSectorTracker, f1_udp  
**Beschreibung:**  
Der erste Minisektor (MS01) kann beim Rundenstart übersprungen werden, wenn der erste UDP-Tick verspätet eintrifft oder die LapDist bereits deutlich > 0 ist.

**Auswirkung:**  
- Unvollständige Minisektor-Daten für die Runde  
- Delta-/PB-Berechnungen nicht möglich

**Workaround:**  
- Robustheits-Logik aktiv (`treat_as_lap_start`)
- Backfilling über Distanzproportion

**Geplanter Fix:**  
Weitere Validierung mit unterschiedlichen FPS-/Tick-Raten, evtl. Zeit-basierter Startanker.

---

### KI-002 – Minisektor-Tracking instabil bei Flashbacks
**Betroffene Version(en):** v0.1.0  
**Betroffene Module:** MiniSectorTracker  
**Beschreibung:**  
Bei Flashbacks können Minisektoren überschrieben oder doppelt gezählt werden, wenn Lap-Zeit und Distanz nicht konsistent zurückspringen.

**Auswirkung:**  
- Inkonsistente Minisektor-Zeiten  
- PB/Best-Werte potenziell verfälscht

**Workaround:**  
Rollback-Logik entfernt nur Minisektoren, deren `end_ms` > aktuelle Zeit ist.

**Geplanter Fix:**  
Zusätzliche Absicherung über Lap-UID oder Distanz-Zeit-Konsistenzprüfung.

---

## 🟧 Mittlere Issues

### KI-003 – Minisektor-Fallback in F1 2020 nur näherungsweise korrekt
**Betroffene Version(en):** v0.1.0  
**Betroffene Module:** MiniSectorTracker  
**Beschreibung:**  
Da F1 2020 keine echten Sektor-Start-Distanzen liefert, werden Sektoren als Drittel der Streckenlänge approximiert.

**Auswirkung:**  
- Minisektoren sind nicht real streckentreu  
- Vergleichbarkeit eingeschränkt

**Workaround:**  
Fallback explizit nur für ältere Games aktivieren (`allow_sector_fallback=True`).

**Geplanter Fix:**  
Optionale manuelle Track-Profile mit echten Sektor-Distanzen.

> Hinweis: Minisektoren in F1 2020 gelten aktuell als **experimentelles Feature**  
> und sind nicht mit der Genauigkeit von F1 25 vergleichbar.

---

### KI-004 – F1 2020: Minisektor-Zeiten werden bei jeder neuen Runde geleert
**Betroffene Version(en):** v0.1.0  
**Betroffene Module:** MiniSectorTracker, F1 2020 Fallback-Logik  
**Beschreibung:**  
In F1 2020 werden die `last_ms`-Werte aller Minisektoren beim Start jeder neuen Runde zurückgesetzt.  
Statt vorhandene Minisektor-Zeiten beim erneuten Überfahren zu überschreiben, wird der gesamte Satz geleert.

**Auswirkung:**  
- Keine kontinuierliche Minisektor-Historie über mehrere Runden  
- Keine PB-/Delta-Vergleiche zwischen Runden möglich  
- Minisektor-basierte Strategieauswertung stark eingeschränkt

**Ursache:**  
Design-bedingt durch fehlende native Minisektor- und Sektor-Distanzdaten in F1 2020.  
Die aktuelle Logik behandelt jede Runde als isolierte Einheit.

**Geplanter Fix:**  
- Trennung von `last_ms` (aktuelle Runde) und `pb_ms` (persistente Bestzeit) erzwingen  
- Optionales Beibehalten der letzten gültigen Minisektor-Zeiten über Runden hinweg  
- Klarer Feature-Flag: `persistent_minisectors=False` für ältere Games


---

### KI-005 – Regen-Forecast zeitweise `n/a`
**Betroffene Version(en):** v0.1.0  
**Betroffene Module:** RainEngine, f1_udp  
**Beschreibung:**  
Forecast-Werte (z. B. Minute 3 / 20) können `None` sein, wenn noch keine vollständige UDP-Serie empfangen wurde.

**Auswirkung:**  
- Geringere Confidence der Strategieentscheidung  
- Frühphase einer Session weniger zuverlässig

**Workaround:**  
Median-basierte Fusion ignoriert fehlende Werte automatisch.

**Geplanter Fix:**  
Forecast-Prebuffering über Mindestanzahl an Samples.

---

## 🟨 Niedrige Issues

### KI-006 – Strategy Cards im Moment nur Platzhalter
**Betroffene Version(en):** v0.1.0  
**Betroffene Module:** UI  
**Beschreibung:**  
Strategy Cards nutzen aktuell nur Platzhalter-Daten.

**Auswirkung:**  
- UI zeigt noch keine echten Live-Empfehlungen

**Workaround:**  
Nur zur Visualisierung nutzen.

**Geplanter Fix:**  
Anbindung an echte Strategy-Outputs.

---

## 🧪 Experimentelle / Design-bedingte Einschränkungen

### KI-006 – Feld-Deltas nicht immer verfügbar
**Betroffene Version(en):** v0.1.0  
**Betroffene Module:** RainEngine  
**Beschreibung:**  
Pace-Deltas aus dem Feld sind in kurzen Sessions oder Trainings teils leer.

**Hinweis:**  
Designbedingt – ausreichend Samples nötig.

**Geplante Verbesserung:**  
- Fallback auf eigene Referenz-Laps
- Nutzung von Rundendatenbank

---

### KI-008 – Reifenverschleiß-Lernen benötigt Datenmenge
**Betroffene Version(en):** v0.1.0  
**Betroffene Module:** Degradation Model  
**Beschreibung:**  
Verschleiß- & Degradationsmodelle liefern erst nach mehreren sauberen Stints belastbare Ergebnisse.

**Hinweis:**  
Erwartetes Verhalten, kein Bug.

**Geplante Verbesserung:**  
Konfidenz-Anzeige & Mindestdaten-Hinweise im UI.
