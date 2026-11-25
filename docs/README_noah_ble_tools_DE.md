# Growatt BLE Tools – NOAH-Registerleser und Terminaloberfläche

**Stand: 25. November 2026**  
**`noah_registers.py` v0.8 · `noah_tui.py` v0.91**

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
├── noah_registers.py       # v0.8
├── noah_tui.py             # v0.91
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
| `HEX` | I/H: hexadezimale Byteanzeige; C: dezimale ASCII-Zahl in Hexadezimaldarstellung |
| `RAWHEX` | Nur C: unveränderte Nutzdatenbytes in Hexadezimaldarstellung |
| `MAC` | Nur C: dezimale 48-Bit-Kennungen als MAC-Adressen mit Doppelpunkten; auch durch `&` getrennte Listen |
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

### WLAN-Smart-Meter suchen und koppeln (v0.91)

Die TUI ermöglicht die Suche, Kopplung und Entkopplung unterstützter WLAN-Smart-Meter direkt über den NOAH-Datenlogger per BLE, ohne ShinePhone oder Growatt-Serververbindung. NOAH und Messgerät müssen im lokalen Netzwerk erreichbar sein; BLE dient zur Konfiguration des NOAH, nicht zur direkten Kommunikation mit dem Messgerät.

Folgende Einträge können in `registers.txt` ergänzt werden (die Überschrift ist eine erkannte TUI-Direktive):

```text
**[Gekoppelte Geräte]**
102|RF-Geräteliste|ASCII||C
122|WLAN-Geräteliste|ASCII||C
123|Mac-Addr.|MAC||C
143|Routed Modbus|1||C
313|Anzahl Zubehörgeräte|1||H
310|Einspeiseleistung Messeinrichtung|1|W|H
311|Bezugsleistung Messeinrichtung|1|W|H
```

**Gekoppelte Geräte [Enter: Auswahl]** markieren und Enter drücken. Gerätetyp auswählen (Standard: Shelly Pro 3EM) und **Suche starten**. Die auswählbare Ergebnisliste zeigt vollständige MAC-Adressen und Zeitstempel. Ergebnis und Kopplungsart auswählen, anschließend **Gerät koppeln** drücken. **Gerät entkoppeln** sendet Löschbefehle für alle nicht gelöschten Mode-7-Einträge, nicht nur für das markierte Suchergebnis. Vor einer Suche erfolgt weder eine automatische Entkopplung noch ein Neustart des Datenloggers. Eine Suche bei bestehender Kopplung kann diese beeinträchtigen.

| Gerätetyp | mDNS-Dienst | Typ-ID |
|---|---|---:|
| Shelly 3EM | `_http._tcp.` | 1 |
| Shelly Pro 3EM | `_http._tcp.` | 2 |
| Shelly Pro 3EM 3CT63 | `_http._tcp.` | 8 |
| Shelly Pro EM 50 | `_http._tcp.` | 7 |
| Shelly Pro 1PM | `_http._tcp.` | 6 |
| Everhome Ecotracker | `_everhome._tcp.` | 3 |
| Sparky P1 | `_chargee_p1._tcp.` | 50 |

Unter `Benutzerdefiniert` lassen sich Dienstname und Typ-ID eingeben. Die Vorgaben stammen aus Heliobridge; praktisch getestet wurden am NOAH bislang zwei Shelly-Pro-3EM-Emulatoren (Tasmota), nicht alle aufgeführten Modelle.

#### Protokoll und Suchverhalten

C122 verwendet Growatt SET (`0x18`) und QUERY (`0x19`). Beispielbefehle (ASCII-Nutzdaten):

```text
ADD:111-7-_http._tcp.,2
CRL:111-7-add:sn,80225394980980|access,0
DEL:111-7-_http._tcp.,2
```

`111` ist eine beispielhafte Eintragsnummer; beim Koppeln und Löschen wird die tatsächlich von C122 gelieferte Nummer verwendet. `access,0` fordert Messen und Regeln an (Standard), `access,1` nur Messen. Die Kopplung wird durch Auslesen von C122 und Prüfung von Status 3 und ausgewählter MAC bestätigt. Die Kopplungsart selbst wird durch C122 nicht rückbestätigt. Messwerte mit `access,1` wurden beobachtet; das Ausbleiben einer Regelung wurde noch nicht separat verifiziert.

C123 enthält dezimale 48-Bit-MAC-Kennungen, gegebenenfalls durch `&` getrennt. Die TUI liest zunächst den Ausgangswert und fragt C123 anschließend nominell 60 Sekunden lang alle fünf Sekunden ab. Vorhandene Adressen werden als `Startwert (nicht bestätigt)` gekennzeichnet, neu beobachtete Adressen erhalten einen Zeitstempel. Unveränderte gespeicherte Ergebnisse lassen sich nicht von erneut gefundenen Geräten unterscheiden. C123 wird im Suchablauf ausschließlich gelesen; C143 wird nicht verändert.

Anders als beim serverseitigen Push-Verfahren von Heliobridge wird hier BLE-Polling verwendet. Beim passiven Test am 25. November 2026 wurde innerhalb von 65 Sekunden nach ADD lediglich die SET-Bestätigung empfangen. Eine anschließende QUERY lieferte beide MAC-Adressen. Unaufgeforderte C123-Meldungen wurden unter diesen Bedingungen nicht beobachtet. Da kein Ausgangswert gelesen wurde, beweist dieser Test allein nicht, dass die Adressen neu gefunden wurden.

#### Registerdarstellung und Messwerte

| C122-Status | TUI-Anzeige | Bedeutung |
|---|---|---|
| 0 | `ungekopp.` | Registriert, nicht gekoppelt |
| 3 | `gekoppelt` | Gekoppelt |
| 5 | `gelöscht` | Gelöschter Eintrag noch in der Liste |

C122 zeigt dezimale MAC-Kennungen in hexadezimaler Schreibweise mit Doppelpunkten an und blendet das abschließende NUL-Zeichen in der Übersicht aus. C123 kann mehrere vollständige MAC-Adressen untereinander anzeigen. Die Rohdaten bleiben für die manuelle Prüfung erhalten. `[OK]` bezeichnet eine erfolgreiche Registerabfrage, nicht eine erfolgreiche Kopplung. Die Navigation zur Zubehörüberschrift wurde in der jüngsten TUI-Revision korrigiert.

H310 und H311 sind separate vorzeichenlose 16-Bit-Leistungsbeträge in Watt (Einspeisung bzw. Bezug, gestützt auf die verwandte NEXA-Dokumentation und NOAH-Beobachtungen). Die Nettoleistung ergibt sich aus `H311 - H310`. Bezugsmesswerte wurden am NOAH bestätigt; die Einspeiserichtung ist noch experimentell zu verifizieren. H313 kann trotz aktiver WLAN-Kopplung null bleiben und eignet sich daher nicht allein zur Bestimmung des Kopplungsstatus.

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

Die WLAN-Zubehörbefehle und Gerätetypen orientieren sich am Projekt Heliobridge und der verwandten NEXA-Protokolldokumentation; das BLE-Verhalten wurde separat am NOAH untersucht.

Die BLE-Protokollbasis (`protocol.py`, `growatt_ble.py`) baut auf dem Community-Projekt **meiser79/growatt_noah_ble** auf. Bei Veröffentlichungen sind dessen Lizenz- und Copyright-Hinweise zu beachten.

**Keine Verbindung zu Growatt. Keine Gewähr für Richtigkeit oder Betriebssicherheit.** Schreib- und Neustartfunktionen nur bewusst und auf eigenes Risiko verwenden.
