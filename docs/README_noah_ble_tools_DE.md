# Growatt NOAH – BLE-Registerdiagnose und Terminaloberflächen

**Stand: 27. November 2026 · Projekt-Release 1.0**

| Komponente | Version |
|---|---|
| `noah_tui.py` | 1.0 |
| `noah_watchdog_ui.py` | 0.1.6 |
| `noah_watchdog.py` | 0.4.2 |
| `noah_registers.py` | 0.8.2 |

Die interaktiven Terminaloberflächen (TUI) sind der empfohlene Einstieg für Diagnose, Konfiguration und Überwachung des Growatt NOAH über Bluetooth Low Energy (BLE). Die BLE-Kommunikation benötigt weder die Growatt-Cloud noch eine funktionierende MQTT-Verbindung. Die Kommandozeilenwerkzeuge stehen weiterhin für Skripte und automatisierte Abläufe zur Verfügung.

## 1. Installation

Benötigt werden Python 3.11 oder neuer für den vollständigen Funktionsumfang, ein BLE-Adapter und entsprechende Bluetooth-Berechtigungen.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Im Projektverzeichnis befinden sich die vier oben genannten Python-Dateien sowie `protocol.py`, `growatt_ble.py`, `registers.txt` und optional `register_info.txt`. Die TUI benötigt Textual und Rich, die BLE-Kommunikation Bleak. Die integrierte Watchdog-Prozessverwaltung setzt Linux voraus.

## 2. Interaktive Oberflächen

```bash
python noah_tui.py
```

Die Startoberfläche ermöglicht die Geräteauswahl und den direkten Zugang zur Watchdog-Verwaltung. Die Registeransicht zeigt die ausgelesenen Werte in zwei Spalten. Mit Enter werden Details zum ausgewählten Register angezeigt. `register_info.txt` ergänzt Erläuterungen, beispielsweise unter `[I:120]`, `[H:254]` oder `[C:21]`.

| Taste | Funktion |
|---|---|
| ↑ / ↓ | Register auswählen |
| ← / → | Spalte wechseln |
| Enter | Details öffnen |
| R | Register neu lesen |
| G | Gerät wechseln |
| K | Geräte verwalten |
| N | Datenlogger neu starten (aktiver Eingriff) |
| W | Watchdog-Verwaltung öffnen |
| Q | Beenden bzw. Dialog schließen |

Optional kann ein Gerät oder eine Registerdatei direkt angegeben werden:

```bash
python noah_tui.py --address 'AA:BB:CC:DD:EE:FF'
python noah_tui.py --file registers.txt
```

### Watchdog-Verwaltung (Linux)

Mit W gelangt man aus der Start- oder Registeransicht zur Watchdog-Verwaltung. Dort lassen sich der Prozess starten, stoppen und neu starten, PID und Laufzeit anzeigen sowie Intervall und Scandauer einstellen. Standardwerte sind fünf Minuten und acht Sekunden.

Über **Geräte verwalten (BLE-Scan)** wird das vorhandene Setup mit seiner Standard-Scandauer aufgerufen. Der Watchdog kann dabei weiterlaufen. Die Geräteliste wird anschließend neu geladen. Logging, WLAN-Recovery, MQTT-Prüfung/-Recovery und Logger-Neustart werden über eindeutige `[X]`- bzw. `[ ]`-Schaltflächen eingestellt; der Schwellenwert beträgt 1–3 Zyklen.

Beim Speichern oder nach Rückkehr aus dem Setup erscheint bei laufendem Watchdog ein modaler Neustarthinweis, der mit Enter bestätigt wird. Ein Neustart erfolgt ausschließlich auf Entscheidung des Benutzers, da der Prozess seine Konfiguration beim Start lädt.

Die Protokollansicht bietet Watchdog- und Konsolenlog, Zeiträume Heute/Gesamt, Textfilter, Recovery-Schnellfilter und Sprung ans Ende. Anzeigen stellt nach einem Recovery-Schnellfilter den vorherigen manuellen Filter wieder her. Die Statistik zählt Recovery-Starts je Gerät für heute, sieben Tage und insgesamt, nicht erfolgreiche Reparaturen. Weitere Informationen: [Watchdog-Dokumentation](README_watchdog.md).

### Schreibzugriffe und Timeslots

Die TUI unterstützt experimentelle Schreibzugriffe auf einzelne Holding-Register, ausgewählte ASCII-Konfigurationsparameter und neun Timeslots mit jeweils fünf Holding-Registern. Nach Schreibzugriffen wird versucht, die tatsächlichen Werte zurückzulesen. Ein Timeout beweist nicht, dass der Schreibzugriff wirkungslos war. Kombinierte I,C-Firmwarewerte sind nur lesbar.

### WLAN-Zubehör (experimentell)

Über **Gekoppelte Geräte** und Enter kann die WLAN-Zubehörsuche geöffnet werden. Der NOAH sucht selbst per mDNS; die Steuerung erfolgt über BLE. Standardmodell ist Shelly Pro 3EM. Weitere Voreinstellungen sind Shelly 3EM, Pro 3EM-3CT63, Pro EM-50, Pro 1PM, everHome EcoTracker und Sparky P1; diese Alternativen wurden im beschriebenen Test nicht validiert. Benutzerdefinierte mDNS-Dienste und Typ-IDs sind möglich.

Die Suche umfasst 2–12 Mindestzyklen zu je fünf Sekunden. Änderungen der abschließenden MAC-Liste oder Lesefehler verlängern die Abfrage bis maximal zwölf Zyklen. Die interne Suche des NOAH kann darüber hinaus weiterlaufen. C123 wird vor der Suche experimentell auf ASCII `0` gesetzt und rückgelesen; bei fehlgeschlagenem Reset gelten vorhandene Adressen nicht als bestätigte Neufunde.

Kopplungsarten: `access,0` (messen und regeln), `access,1` (nur messen). C122-Zustand 3 bestätigt die Kopplung, nicht unabhängig die gewählte Zugriffsart. Einzelne oder alle WLAN-Einträge können gelöscht werden; die Löschung wird über C122 geprüft. Relevante Rücklesewerte aktualisieren die Hauptansicht.

| Register | Bedeutung |
|---|---|
| C102 | RF-Zubehörliste |
| C122 | WLAN-Zubehöreinträge und Befehle |
| C123 | MAC-Liste der Suche |
| C143 | Weitergeleitetes Modbus; für getestete WLAN-Kopplung nicht erforderlich |
| H310 | Einspeiseleistungsbetrag (W; Richtung bei negativer Leistung noch nicht getestet) |
| H311 | Bezugsleistung (W; verifiziert) |
| H313 | Zubehöranzahl; blieb im WLAN-Test null |

C122 verwendet `DEV:<entry>-<mode>-<state>,<name>,<address>` mit `&` als Trenner. Modus 7 steht für WLAN-Suche; Zustände 0, 3 und 5 für ungekoppelt, gekoppelt und gelöscht. Gelöschte Einträge können MAC/IP-Daten behalten. Beispiele: `ADD:111-7-_http._tcp.,2`, `CRL:112-7-add:sn,80225394980980|access,1`, `DEL:112-7-_http._tcp.,2`. Die tatsächliche Eintragsnummer ist zu verwenden.

**Firmwarebeobachtung:** Bei zwei getesteten NOAHs unterbrach eine erneute Suche mit bereits gekoppeltem Zähler die Messwertübertragung, nachdem ein ungekoppelter Eintrag angelegt worden war. Gezieltes Löschen oder Koppeln dieses Eintrags stellte die Messwerte wieder her. Bei zwei gekoppelten Zählern zeigte H311 im Test den Wert von Eintrag 112, nicht die Summe. Welcher Zähler die Regelung bestimmt und ob `access,1` zuverlässig von der Regelung ausschließt, ist offen. Ein Nullwert allein beweist keinen Fehler. Ein Zusammenhang mit fehlgeschlagenen App-Kopplungen ist nicht nachgewiesen.

## 3. Kommandozeile (CLI)

```bash
python noah_registers.py scan --seconds 8
python noah_registers.py list --address 'AA:BB:CC:DD:EE:FF' --file registers.txt
python noah_registers.py read --address 'AA:BB:CC:DD:EE:FF' --type input --register 120
python noah_registers.py read --address 'AA:BB:CC:DD:EE:FF' --type holding --register 254 --count 5
python noah_watchdog.py setup
python noah_watchdog.py run
```

Ohne Unterbefehl öffnet `noah_registers.py` sein interaktives Diagnosemenü. Hilfe erhält man über `--help`, auch für die Unterbefehle `scan`, `read` und `list`. `list` unterstützt unter anderem `--timeout`, `--block-size` (16), `--max-gap` (2) und `--verbose`. Der ältere Schalter `list --type` bleibt kompatibel; Registertypen aus der Datei haben Vorrang.

## 4. Registertypen und Transport

| Typ | Quelle | Leseverfahren | Datentyp |
|---|---|---|---|
| I | Input-Register | BLE 0x17, Modbus FC04 | 16 Bit |
| H | Holding-Register | BLE 0x17, Modbus FC03 | 16 Bit |
| C | Growatt-Konfiguration | BLE 0x19 (QUERY) | Variable Bytefolge, häufig ASCII |
| I,C / H,C | Gemischte Quellen | Beide Verfahren | Kombinierter Anzeigewert |

Holding-Register sind nicht automatisch Datenloggerparameter. C besitzt einen eigenen Adressraum. Für I:0–123 werden experimentell bestätigte 16-Wort-Fenster genutzt: `0–15, 16–31, 32–47, 48–63, 64–79, 80–95, 96–111, 108–123`. Das letzte Fenster überlappt absichtlich; Einzelabfragen von I:119/I:120 waren unzuverlässig.

## 5. Format von registers.txt

```text
Register|Label|Factor|Unit|RegisterType
101|Charge cycles|1|cycles|I
102|SOH|1|%|I
119-120|Firmware components|CombLBHBLB|.|I
21|Firmware suffix|ASCII||C
119-120,21|Firmware|CombLBHBLB+ASCIIstrip(.)|.|I,C
```

UTF-8, eine Definition pro Zeile. Die fünfte Spalte ist optional (Standard I); `#` leitet Kommentare ein. Leere Zeilen werden ignoriert. `119` bezeichnet ein Register, `119-120` einen Bereich, `100->16` 16 Register ab 100, `30,42` eine explizite Liste. Gemischte Typen I,C und H,C unterstützen einen Modbus-Bereich und genau einen C-Parameter.

| Faktor | Bedeutung |
|---|---|
| `1`, `0.1`, `0.01` | Multiplikation |
| `*0.1 -273.1` | Arithmetische Umrechnung |
| `ASCII` | ASCII-Interpretation |
| `HEX` | Hexadezimale Darstellung |
| `TIME` | High Byte Stunden, Low Byte Minuten |
| `bit3-0` | Bitfolge |
| `CombLBHBLB` | Kombination ausgewählter Bytes |
| `CombLBHBLB+ASCIIstrip(.)` | Bytekombination plus bereinigter ASCII-Parameter |

Weitere numerische Optionen: `NULL0`, `NULL65535`, `NOHEX`. Die Einheitenspalte kann eine Statuslegende enthalten, z. B. `0 Off, 1 On`.

### Firmwareversion zusammensetzen

I:119 = `0x0013` (LB=19), I:120 = `0x140E` (HB=20, LB=14), C:21 = `4.0.2.6`. Die Einheit `.` wird an jede numerische Komponente angehängt; `ASCIIstrip(.)` entfernt sämtliche Punkte aus C:21. Ergebnis: **19.20.14.4026**. Dies ist eine reine Anzeigeformatierung, kein neuer BLE-Befehl. HB bezeichnet Bits 15–8, LB Bits 7–0.

### Layoutanweisungen

```text
**[Battery]**
**LEER**
**---**
**===40===**
**SPALTE**
**[Datalogger]**
```

`**SPALTE**` oder `**SEITENUMBRUCH**` beginnt eine neue Spalte. Überschriften und Trenner lösen keine Registerabfragen aus.

## 6. Fehlerbehebung

- Fehlende Module: Dateinamen prüfen, virtuelle Umgebung aktivieren und Abhängigkeiten installieren.
- Keine BLE-Geräte: Adapter, Berechtigungen, Reichweite und parallele Verbindungen prüfen.
- Einzelregister antwortet nicht: geeignete Blockabfrage versuchen; fehlende Antwort beweist nicht, dass ein Register nicht existiert.
- Firmwarekombination fehlerhaft: Definition und Versionen prüfen. Fehlende Teilwerte dürfen nicht als gültige Firmwareversion interpretiert werden.

## 7. Projektstatus, Grenzen und Lizenz

Registerlesezugriffe wurden an vier NOAHs getestet. WLAN-Suche, Kopplung, gezieltes Löschen und Rücklesen wurden mit Firmware 19.20.14.4026 und zwei Shelly-Pro-3EM-Emulatoren getestet. Andere Firmwarestände und Zubehörmodelle sind nicht verifiziert. Der SOH-Wert I:102 ist vorsichtig zu interpretieren: Für BMS-Version 14 beschreibt die verwandte NEXA-Dokumentation einen konstanten Wert von 100; die Übertragbarkeit auf NOAH ist offen. NEXA-Unterstützung ist geplant, nicht implementiert oder verifiziert.

Die BLE-Grundlage (`protocol.py`, `growatt_ble.py`) basiert auf **meiser79/growatt_noah_ble**. Lizenz und Urheberrechtshinweise beachten; siehe [LICENSE](../LICENSE) und [NOTICE](../NOTICE).

**Inoffizielles, experimentelles Projekt ohne Verbindung zu Growatt. Keine Gewähr für Korrektheit oder Betriebssicherheit.** Schreib- und Neustartfunktionen bewusst und auf eigenes Risiko verwenden. Keine Zugangsdaten oder privaten Protokolle veröffentlichen.

[English documentation](README_noah_ble_tools.md) · [Projektübersicht](../README.md)
