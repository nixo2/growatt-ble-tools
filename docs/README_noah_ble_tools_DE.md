# Growatt BLE Tools – NOAH-Registerleser und Terminaloberfläche

**Stand: 23. November 2026**  
**`noah_registers.py` v0.7 · `noah_tui.py` v0.9**

Zwei Python-Anwendungen für den direkten Zugriff auf Growatt-NOAH-Batteriespeicher über Bluetooth Low Energy (BLE), ohne Growatt-Cloud oder MQTT:

- **`noah_registers.py`**: Kommandozeilenwerkzeug für Gerätesuche, Registerlisten, manuelles Lesen und Schreiben sowie Konfigurationsabfragen.
- **`noah_tui.py`**: Interaktive Terminaloberfläche (Textual) mit zwei Registerspalten, Geräteverwaltung, Detailansichten, gezielter Aktualisierung und Schreibdialogen.

Das Projekt ist **inoffiziell und experimentell**. Registerbedeutungen und Schreibbefehle sind nicht von Growatt für diese Verwendung freigegeben.

## 1. Voraussetzungen und Dateien

Benötigt werden Python 3, ein funktionierender BLE-Adapter und die erforderlichen Bluetooth-Berechtigungen. Die TUI verwendet zusätzlich Textual und Rich.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install bleak textual rich
```

Die Dateien müssen im selben Projektverzeichnis liegen:

```text
project/
├── noah_registers.py       # v0.7
├── noah_tui.py             # v0.9
├── protocol.py             # BLE-Protokoll
├── growatt_ble.py          # BLE-Kommunikation
├── registers.txt           # Register, Umrechnung, Darstellung, F-Gruppen
├── register_info.txt       # Optionale Detailinformationen
├── noah_devices.txt        # Von der TUI verwaltete Geräteliste
└── docs/
    ├── README_noah_ble_tools.md
    └── README_noah_ble_tools_DE.md
```

**Wichtig:** Die Python-Dateien müssen exakt `noah_registers.py` und `noah_tui.py` heißen, weil die TUI das Registermodul unter diesem Namen importiert.

## 2. Schnellstart

```bash
# TUI mit Geräteauswahl
python noah_tui.py

# TUI für eine bestimmte BLE-Adresse
python noah_tui.py --address 'AA:BB:CC:DD:EE:FF'

# Alternative Registerdatei
python noah_tui.py --file registers.txt

# Geräte suchen
python noah_registers.py scan --seconds 8

# Registerliste auslesen
python noah_registers.py list --address 'AA:BB:CC:DD:EE:FF' --file registers.txt

# Einzelnes Input-Register lesen
python noah_registers.py read --address 'AA:BB:CC:DD:EE:FF' --type input --register 120

# Fünf Holding-Register lesen
python noah_registers.py read --address 'AA:BB:CC:DD:EE:FF' --type holding --register 254 --count 5
```

Ohne Unterbefehl startet `noah_registers.py` ein interaktives Menü mit **vier Funktionen**: (1) Registerliste, (2) manuelles Lesen/Schreiben, (3) Datenlogger-Neustart und (4) Konfigurationsparameter lesen. Die früheren Debug-Menüpunkte 5–8 wurden in **v0.7 entfernt**. Die bewährte optimierte Registerabfrage bleibt erhalten.

## 3. Registertypen und Übertragung

| Typ | Datenquelle | BLE/Modbus | Format |
|---|---|---|---|
| `I` | Input-Register | Growatt `0x17` / Modbus FC04 | 16-Bit-Werte |
| `H` | Holding-Register | Growatt `0x17` / Modbus FC03 | 16-Bit-Werte |
| `C` | Konfigurationsparameter | Growatt `0x19` (QUERY) | Variable Bytefolgen, oft ASCII |
| `I,C` / `H,C` | Kombinierte Anzeige | Beide jeweiligen Abfragewege | Zusammengesetzter Anzeigewert |

Holding-Register und C-Konfigurationsparameter sind **unterschiedliche Adressräume**.

### Optimierte Input-Abfragen

Für **I:0–123** werden experimentell bestätigte 16er-Lesefenster verwendet:

```text
0–15, 16–31, 32–47, 48–63,
64–79, 80–95, 96–111, 108–123
```

Die letzte Überlappung ist beabsichtigt. Insbesondere I:119 und I:120 waren bei Einzelabfragen unzuverlässig, während das Blocklesen funktionierte. Für Holding- und Konfigurationsparameter gelten andere Abfragewege; außerhalb dieser Fenster bleibt die bisherige Abfragelogik erhalten.

## 4. Format der `registers.txt`

UTF-8, eine Definition pro Zeile:

```text
Register|Bezeichnung|Faktor|Einheit|Registertyp
```

Beispiele:

```text
101|Ladezyklen|1|Zyklen|I
102|SOH|1|%|I
119-120|Firmware-Bestandteile|CombLBHBLB|.|I
21|Firmware-Suffix|ASCII||C
119-120,21|Firmware|CombLBHBLB+ASCIIstrip(.)|.|I,C
```

Der fünfte Eintrag ist optional (Standard: `I`). Leerzeilen und Zeilen mit `#` werden ignoriert.

### Registerangaben

- `119`: Einzelregister.
- `119-120`: Einschließlicher Bereich.
- `100->16`: 16 Register ab Adresse 100.
- `30,42`: Explizite Registerliste.
- `119-120,21` mit Typ `I,C`: Modbus-Register 119–120 plus **ein** C-Parameter (21).

`I,C` und `H,C` sind für einen Modbus-Bereich und einen einzelnen C-Parameter vorgesehen, nicht für beliebige Formelausdrücke.

### Umrechnungen und Darstellung

| Faktor | Bedeutung |
|---|---|
| `1`, `0.1`, `0.01` | Rohwert multiplizieren |
| `*0.1 -273.1` | Arithmetische Umrechnung |
| `ASCII` | Registerbytes oder C-Parameter als Text |
| `HEX` | Hexadezimale Byteanzeige |
| `TIME` | Stunden im High-Byte, Minuten im Low-Byte |
| `bit3-0` / `bit0-3` | Bitfolge in angegebener Reihenfolge |
| `CombLBHBLB` | Ausgewählte Bytes kombinieren |
| `CombLBHBLB+ASCIIstrip(.)` | Registerbytes und bereinigten ASCII-Text kombinieren |

Weitere Optionen sind `NULL0`, `NULL65535` und `NOHEX`. Die Einheitsspalte kann auch Wertelisten wie `0 Aus, 1 Ein` enthalten. Bei Bitmasken ist die angegebene Bitreihenfolge entscheidend: `bit0-3` zeigt die Bits 0, 1, 2, 3 von links nach rechts.

### Vollständige Firmware-Version

```text
119-120,21|Firmware|CombLBHBLB+ASCIIstrip(.)|.|I,C
```

Beobachtete Werte:

```text
I:119 = 0x0013       → Low-Byte: 19
I:120 = 0x140E       → High-Byte: 20; Low-Byte: 14
C:21  = "4.0.2.6"    → ASCII
```

Mit `.` als Trennzeichen für die Zahlen und Entfernen der Punkte aus C:21 entsteht **`19.20.14.4026`**, entsprechend der in der Growatt-App beobachteten Version. `HB` bezeichnet das High-Byte, `LB` das Low-Byte. Diese Kombination ist ausschließlich eine Darstellung bereits gelesener Werte.

### Formatierungsbefehle

```text
**[Batterie]**
**LEER**
**---**
**===40===**
**SPALTE**
**[Datenlogger]**
```

`**SPALTE**` und `**SEITENUMBRUCH**` beginnen eine neue Anzeigespalte. Die Schlüsselwörter bleiben auch in englischen Registerdateien deutsch, weil der Parser diese Schreibweise erwartet. Formatierungszeilen lösen keine BLE-Abfragen aus.

### Gezielte Aktualisierung mit F1–F8

Optional können in `registers.txt` Gruppen vorhandener Anzeigeeinträge definiert werden:

```text
[F1]
13|SOC Total
29,41,53,65|SOC Bat.1-4
```

Format: `Register[,Register...]|Exakte Bezeichnung`. Diese Einträge sind **Selektoren**, keine zusätzlichen Registerdefinitionen. Mit F1 bis F8 werden nur die zugeordneten Messwerte aktualisiert, ohne die gesamte Tabelle neu zu lesen.

## 5. Bedienung der TUI

| Taste | Funktion |
|---|---|
| `↑` / `↓` | Vorherigen/nächsten Eintrag auswählen |
| `←` / `→` | Zwischen den Spalten wechseln |
| `Enter` | Details und gegebenenfalls Bearbeitung öffnen |
| `R` | Alle Register neu lesen |
| `F1`–`F8` | Definierte Registergruppe aktualisieren |
| `G` | Anderes Gerät auswählen |
| `K` | Geräteverwaltung |
| `N` | Datenlogger-Neustart (**aktiver Eingriff**) |
| `Q` | Beenden bzw. Dialog schließen |

Die TUI zeigt die Register in zwei Spalten. Mit `Enter` erscheint die Detailansicht; `register_info.txt` kann ergänzende Texte in Abschnitten wie `[I:120]`, `[H:254]` und `[C:21]` bereitstellen. Über die Timeslot-Überschrift lassen sich die **neun Timeslots** auswählen.

### Schnelle Holding-Schreibdialoge (v0.9)

Bei einzelnen Holding-Registern mit eindeutigem **Rohwert** in der Tabelle, insbesondere **H:250 Ladegrenze**, **H:251 Entladegrenze** und **H:252 Standardausgangsleistung**, wird der Wert **sofort aus der vorhandenen Anzeige vorbelegt**.

Ablauf:

1. Schreibdialog öffnen: **keine zusätzliche BLE-Abfrage**.
2. Wert ändern und „Schreiben“ wählen: **direkt FC06 schreiben**, ohne vorheriges FC03-Lesen.
3. Anschließend den tatsächlichen Wert **einmal per FC03 zurücklesen und verifizieren**.
4. Die Tabelle mit dem rückgelesenen Wert aktualisieren.

Skalierte, nicht eindeutig interpretierbare oder gruppierte Register werden nicht blind als Rohwert übernommen; gegebenenfalls ist eine manuelle Eingabe erforderlich.

### Timeslots und Konfigurationsparameter

Jeder der neun Timeslots besteht aus fünf Holding-Registern. Der Timeslot-Editor übernimmt vorhandene Tabellenwerte, soweit sicher verfügbar; Änderungen erfolgen per **FC16**, die Prüfung anschließend per **FC03**. ASCII-Konfigurationsparameter werden über Growatt SET geschrieben und per QUERY geprüft.

**Vorsicht bei Schreibzugriffen:** Änderungen an Lade-/Entladegrenzen, Ausgangsleistung, Zeitplänen oder Kommunikationsparametern können sofort wirksam werden. Ein Timeout bedeutet **nicht**, dass der Schreibvorgang wirkungslos geblieben ist. Kombinierte `I,C`- und `H,C`-Anzeigen sind schreibgeschützt.

## 6. Kommandozeilenhilfe

```bash
python noah_registers.py --help
python noah_registers.py scan --help
python noah_registers.py read --help
python noah_registers.py list --help
python noah_tui.py --help
```

Der Befehl `list` unterstützt unter anderem `--timeout`, `--block-size` (Standard 16), `--max-gap` (Standard 2) und `--verbose`. Das ältere `list --type` ist aus Kompatibilitätsgründen vorhanden; maßgeblich sind die Registertypen in der Datei.

## 7. Fehlerbehebung

**`ModuleNotFoundError: noah_registers`** – Die Datei muss `noah_registers.py` heißen und neben `noah_tui.py` liegen.

**`ModuleNotFoundError: textual` oder `bleak`** – Virtuelle Umgebung aktivieren und Abhängigkeiten installieren.

**Kein BLE-Gerät gefunden** – Adapter, Berechtigungen, Reichweite und gleichzeitige Verbindungen prüfen.

**Einzelregister nicht lesbar** – Daraus folgt nicht, dass das Register fehlt. Manche NOAH-Register benötigen geeignete Lesefenster.

**Firmware-Zusammensetzung fehlerhaft** – Registerdefinition und erfolgreiche Abfrage von I:119, I:120 und C:21 prüfen.

**Holding-Schreibfeld leer** – Der Tabellenwert lässt sich möglicherweise nicht eindeutig als unskalierter Rohwert bestimmen. Nur geprüfte Rohwerte eingeben; anschließend Rückleseergebnis kontrollieren.

## 8. Projektstand und Grenzen

BLE-Lesezugriffe und optimierte Input-Fenster wurden an **vier NOAH-Geräten** eingesetzt. Die schnelle Vorbelegung der Holding-Schreibdialoge und das Rücklesen nach dem Schreiben wurden im aktuellen Entwicklungsstand erfolgreich getestet. Andere Geräte- und Firmwarestände können sich unterscheiden.

Der SOH-Wert I:102 ist vorsichtig zu interpretieren: Für BMS-Version 14 beschreibt die verwandte NEXA-Dokumentation einen konstanten Wert von 100. Ob das beim NOAH ebenso gilt, muss gesondert geprüft werden.

**Noch nicht implementiert:** Die Kopplung externer Stromzähler über BLE. Eine entsprechende Erweiterung erfordert zunächst die Klärung der unterstützten Geräte und des Kopplungsprotokolls.

## 9. Herkunft und Haftungsausschluss

Die BLE-Protokollbasis (`protocol.py`, `growatt_ble.py`) baut auf dem Community-Projekt **meiser79/growatt_noah_ble** auf. Bei Veröffentlichungen sind dessen Lizenz- und Copyright-Hinweise zu beachten.

**Keine Verbindung zu Growatt. Keine Gewähr für Richtigkeit oder Betriebssicherheit.** Schreib- und Neustartfunktionen nur bewusst und auf eigenes Risiko verwenden.
