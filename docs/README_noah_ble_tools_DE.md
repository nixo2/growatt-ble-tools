# Growatt NOAH – BLE-Registerdiagnose und Terminal-Oberfläche

**Stand: 26. November 2026**  
**`noah_registers.py` v0.8.2 · `noah_tui.py` v0.9.7**

Zwei Python-Programme zum direkten Auslesen und zur Diagnose von Growatt-NOAH-Speichern über Bluetooth Low Energy (BLE):

- **`noah_registers.py`**: Kommandozeilenwerkzeug für Gerätesuche, Einzelabfragen, Registerlisten und Diagnose.
- **`noah_tui.py`**: interaktive Terminaloberfläche auf Basis von Textual mit Geräteverwaltung, zweispaltiger Registeranzeige, Detailansichten und ausgewählten Schreibfunktionen.

Die Programme nutzen die vorhandenen Module `protocol.py` und `growatt_ble.py`. Für BLE-Abfragen ist keine Growatt-Cloud und kein MQTT erforderlich. Das Projekt ist **inoffiziell und experimentell**; insbesondere Registerbedeutungen und Schreibzugriffe sind nicht durch Growatt für diese Anwendung freigegeben.

## 1. Voraussetzungen und Dateien

Ein Computer mit funktionierendem BLE-Adapter, Python 3 und Zugriff auf die Bluetooth-Schnittstelle. Die Programme importieren `bleak`; die TUI benötigt zusätzlich `textual` und `rich` (Rich wird üblicherweise mit Textual installiert).

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install bleak textual rich
```

Die Dateien müssen im selben Projektverzeichnis liegen:

```text
projekt/
├── noah_registers.py       # v0.8.2
├── noah_tui.py             # v0.9.7
├── protocol.py             # BLE-Protokollmodul
├── growatt_ble.py          # BLE-Gerätekommunikation
├── registers.txt           # Registerauswahl, Umrechnung, Layout
├── register_info.txt       # optional: Erläuterungen im Detailfenster
└── noah_devices.txt        # von der TUI verwaltete Geräteliste
```


## 2. Schnellstart

**TUI mit Geräteauswahl:**

```bash
python noah_tui.py
```

**TUI mit direkter BLE-Adresse:**

```bash
python noah_tui.py --address 'AA:BB:CC:DD:EE:FF'
```

**Andere Registerdatei verwenden:**

```bash
python noah_tui.py --file registers.txt
```

**Kommandozeile – Geräte suchen:**

```bash
python noah_registers.py scan --seconds 8
```

**Kommandozeile – Registerliste ausgeben:**

```bash
python noah_registers.py list --address 'AA:BB:CC:DD:EE:FF' --file registers.txt
```

**Einzelnes Input-Register auslesen:**

```bash
python noah_registers.py read --address 'AA:BB:CC:DD:EE:FF' --type input --register 120
```

**Holding-Register auslesen:**

```bash
python noah_registers.py read --address 'AA:BB:CC:DD:EE:FF' --type holding --register 254 --count 5
```

Ohne Unterbefehl startet `noah_registers.py` sein interaktives Diagnosemenü.

## 3. Registertypen und Transport

| Kürzel | Datenquelle | Leseweg | Datentyp |
|---|---|---|---|
| `I` | Input-Register | Growatt BLE `0x17`, darin Modbus FC04 | 16-Bit-Wörter |
| `H` | Holding-Register | Growatt BLE `0x17`, darin Modbus FC03 | 16-Bit-Wörter |
| `C` | Growatt-Konfigurationsparameter | Growatt BLE `0x19` (QUERY) | Bytefolgen variabler Länge, oft ASCII |
| `I,C` / `H,C` | Gemischte Quellen | beide passenden Lesewege | kombinierte **Anzeige**, kein gemeinsames physisches Register |

Ein Holding-Register ist nicht automatisch ein Datalogger-Parameter. Der `C`-Bereich ist ein separater Growatt-Konfigurationszugriff.

### Optimierte Input-Abfragen

Für die Input-Register im Bereich **I:0–123** verwendet die aktuelle Implementierung experimentell bestätigte 16-Wort-Lesefenster:

```text
0–15, 16–31, 32–47, 48–63,
64–79, 80–95, 96–111, 108–123
```

Das letzte Fenster überlappt absichtlich. Diese Optimierung ist wichtig: Einzelabfragen von I:119/I:120 waren bei Tests nicht zuverlässig, während der 16er-Block erfolgreich war. Holding- und Config-Abfragen verwenden weiterhin ihre eigenen Verfahren. Register außerhalb der bestätigten Fenster werden mit der bisherigen Abfragelogik behandelt.

## 4. Aufbau der `registers.txt`

Grundformat (UTF-8), ein Eintrag pro Zeile:

```text
Register|Bezeichnung|Faktor|Einheit|Registertyp
```

Beispiele:

```text
101|Ladezyklen|1|Zyklen|I
102|SOH|1|%|I
119-120|Firmware-Teile|CombLBHBLB|.|I
21|Firmware-Zusatz|ASCII||C
119-120,21|Firmware|CombLBHBLB+ASCIIstrip(.)|.|I,C
```

Die fünfte Spalte ist optional; ohne Angabe wird `I` angenommen. Kommentarzeilen beginnen mit `#`, Leerzeilen werden ignoriert.

### Registerangaben

- `119`: einzelnes Register.
- `119-120`: zusammenhängender Bereich einschließlich beider Grenzen.
- `100->16`: 16 Register ab Adresse 100 (100 bis 115).
- `30,42`: explizite Liste einzelner Register.
- `119-120,21` mit Typ `I,C`: **erste Gruppe** Input-Register 119–120, **zweite Gruppe** Config-Parameter 21.

Die derzeitige gemischte Syntax unterstützt konkret `I,C` und `H,C` mit einem Modbus-Bereich und **einer** C-Parameter-ID. Sie ist kein beliebig erweiterbarer Ausdrucksparser.

### Faktoren und Darstellungsformen

| Faktor | Bedeutung |
|---|---|
| `1`, `0.1`, `0.01` | Multiplikation des Rohwerts |
| `*0.1 -273.1` | arithmetische Umrechnung |
| `ASCII` | 16-Bit-Wörter als ASCII bzw. C-Parameter als Zeichenfolge |
| `HEX` | Hexadezimalanzeige; bei C dezimalen ASCII-Zahlenwert umrechnen |
| `RAWHEX` | unveränderte Bytes hexadezimal |
| `MAC` | dezimale MAC-Adressen aus C-Parametern als Hex-MAC, auch mehrere mit `&` getrennte Werte |
| `TIME` | Uhrzeit aus High-Byte (Stunden) und Low-Byte (Minuten) |
| `bit3-0` | ausgewählte Bits als Bitfolge |
| `CombLBHBLB` | ausgewählte Bytes mehrerer 16-Bit-Wörter kombinieren |
| `CombLBHBLB+ASCIIstrip(.)` | ausgewählte Bytes plus bereinigte C-ASCII-Zeichenfolge |

Weitere unterstützte Optionen bei numerischen Formeln sind `NULL0`, `NULL65535` und `NOHEX`. Bei numerischen Statuswerten kann die Einheitsspalte auch eine Legende enthalten, beispielsweise `0 Aus, 1 Ein`.

### Firmwareversion vollständig zusammensetzen

**Empfohlene Zeile:**

```text
119-120,21|Firmware|CombLBHBLB+ASCIIstrip(.)|.|I,C
```

Die bekannten Rohwerte sind:

```text
I:119 = 0x0013  → Low-Byte  = 19
I:120 = 0x140E  → High-Byte = 20; Low-Byte = 14
C:21  = "4.0.2.6" (ASCII)
```

Die numerischen Teilwerte bekommen **jeweils auch nach dem letzten Wert** das Suffix aus der Einheitsspalte (`.`): `19.20.14.`. `ASCIIstrip(.)` entfernt anschließend **alle** Punkte aus der C-Zeichenfolge (`4026`), bevor sie angehängt wird.

**Ergebnis: `19.20.14.4026`**, entsprechend der beobachteten Anzeige in der Growatt-App.

`HB` bedeutet High-Byte (Bits 15–8), `LB` Low-Byte (Bits 7–0). Die Kombination ist eine **Formatierung bereits gelesener Werte**, kein neuer BLE-Befehl. `ASCIIstrip(.)` bezeichnet hier trotz des Namens ein Entfernen sämtlicher Punkte (`replace`), nicht nur ein Entfernen am Anfang und Ende.

### Layoutbefehle

Standalone-Zeilen mit doppelten Sternchen steuern die Ausgabe:

```text
**[Batterie]**
**LEER**
**---**
**===40===**
**SPALTE**
**[Datalogger]**
```

`**SPALTE**` bzw. `**SEITENUMBRUCH**` beginnt eine neue Anzeigespalte. Überschriften und Trennlinien benötigen keinen Registerzugriff.

## 5. TUI: Bedienung

| Taste | Funktion |
|---|---|
| `↑` / `↓` | vorherigen / nächsten Eintrag auswählen |
| `←` / `→` | linke / rechte Spalte auswählen |
| `Enter` | Details zum markierten Eintrag |
| `R` | alle Register neu lesen |
| `G` | Gerät wechseln |
| `K` | Geräteverwaltung öffnen |
| `N` | Datalogger-Neustart anstoßen (**aktiver Eingriff**) |
| `Q` | beenden bzw. Dialog verlassen |

Die TUI liest die Register über `noah_registers.py`, zeigt die Daten zweispaltig an und öffnet per Enter eine Detailansicht. Die optionale Datei `register_info.txt` ergänzt dort erläuternde Texte unter Abschnittsüberschriften wie `[I:120]`, `[H:254]` oder `[C:21]`.

### Schreibfunktionen – Vorsicht

Die TUI enthält experimentelle Funktionen zum Ändern einzelner Holding-Register, bestimmter ASCII-Config-Parameter sowie zum Bearbeiten der neun Timeslots (je fünf Holding-Register). Schreibvorgänge erfolgen über separate Growatt-/Modbus-Befehle; die Implementierung versucht anschließend, die tatsächlichen Werte zurückzulesen. Ein Timeout beweist **nicht**, dass ein Schreibvorgang wirkungslos geblieben ist.

Die kombinierte Firmwarezeile `I,C` ist **nur lesbar**. Niemals ungetestete Schreibwerte an Geräte senden; Änderungen an Betriebsart, Leistung, Zeitplänen oder Kommunikation können den Betrieb unmittelbar beeinflussen.

### WLAN-Zubehörverwaltung (experimentell)

Über **Gekoppelte Geräte** und Enter öffnet sich die Zubehörverwaltung. Der NOAH selbst sucht WLAN-Geräte per mDNS; der Computer steuert Suche und Kopplung über BLE. ShinePhone und Cloud sind dafür nicht erforderlich.

- Gerätetyp auswählen (Standard Shelly Pro 3EM) oder mDNS-Dienst und Typ-ID benutzerdefiniert eingeben. Weitere Vorlagen: Shelly 3EM, Pro 3EM-3CT63, Pro EM-50, Pro 1PM, everHome EcoTracker und Sparky P1. Diese Alternativen wurden hier nicht praktisch verifiziert.
- 2–12 Mindestzyklen über Eingabefeld oder −/+ einstellen. Ein Zyklus dauert fünf Sekunden. Ändert sich die MAC-Menge bei der letzten Abfrage oder schlägt diese fehl, verlängert sich das Polling um einen Zyklus, maximal auf insgesamt zwölf. Die interne mDNS-Suche kann danach weiterlaufen.
- C123 wird vor der Suche experimentell auf ASCII `0` gesetzt und rückgelesen. Ein separater Button zum Leeren ist vorhanden. Bei fehlgeschlagenem Reset gelten vorhandene Adressen nicht als bestätigte neue Treffer.
- Gefundene MAC und Kopplungsart auswählen: `access,0` (Messen und regeln) oder `access,1` (Nur messen). Die Kopplung wird über C122, Status 3, bestätigt; die Kopplungsart selbst wird nicht unabhängig rückbestätigt.
- Einzelne WLAN-Einträge einschließlich ungekoppelter Einträge gezielt löschen oder ausdrücklich alle löschen. Der Löschstatus wird über C122 kontrolliert. Relevante Rücklesewerte aktualisieren die Haupttabelle ohne vollständigen Reload.
- C122 wird nach Eintragsnummer sortiert und in getrennten Blöcken mit Hex-MAC, IP und Status angezeigt. Die Rohdaten bleiben zur Diagnose erhalten.

| Register | Bedeutung |
|---|---|
| C102 | RF-Geräteliste |
| C122 | WLAN-Geräteeinträge und Befehle |
| C123 | MAC-Suchliste |
| C143 | Routed Modbus; für die getestete WLAN-Kopplung nicht erforderlich |
| H310 | Einspeiseleistung als Betrag (W; negative Richtung am NOAH noch nicht praktisch verifiziert) |
| H311 | Bezugsleistung (W; verifiziert) |
| H313 | Zubehöranzahl; blieb bei WLAN-Tests null |

C122 verwendet `DEV:<Eintrag>-<Modus>-<Status>,<Name>,<Adresse>` mit `&` zwischen Einträgen. Modus 7 steht für WLAN-Suche; Status 0, 3 und 5 bedeuten ungekoppelt, gekoppelt und gelöscht. Gelöschte Einträge können MAC/IP behalten. Beispielbefehle: `ADD:111-7-_http._tcp.,2`, `CRL:112-7-add:sn,80225394980980|access,1`, `DEL:112-7-_http._tcp.,2`. Zum Koppeln und Löschen die tatsächliche Eintragsnummer verwenden.

**Wichtige Firmwarebeobachtung:** Bei zwei getesteten NOAHs erzeugte eine erneute Suche mit bereits gekoppeltem Zähler einen ungekoppelten Eintrag und unterbrach anschließend die Messwertanzeige. Auch längeres Warten stellte sie nicht wieder her. Gezieltes Löschen des ungekoppelten Eintrags stellte die Messwerte wieder her, ohne die bestehende Kopplung zu löschen. Auch das Koppeln des zweiten Eintrags stellte die Messwerte wieder her. Bei zwei gekoppelten Zählern zeigte H311 im beobachteten Versuch den Wert von Eintrag 112, keine Summe. Welcher Zähler tatsächlich die Leistungsregelung bestimmt und ob `access,1` zuverlässig von der Regelung ausschließt, ist noch offen. Ein Nullwert allein beweist keine Störung. Dies könnte manche erfolglosen Kopplungen mit ShinePhone erklären; dieser Zusammenhang ist jedoch nicht nachgewiesen.

## 6. CLI-Optionen

```bash
python noah_registers.py --help
python noah_registers.py scan --help
python noah_registers.py read --help
python noah_registers.py list --help
python noah_tui.py --help
```

Für `list` stehen unter anderem `--timeout`, `--block-size` (Standard 16), `--max-gap` (Standard 2) und `--verbose` zur Verfügung. Der frühere Schalter `list --type` ist aus Kompatibilitätsgründen noch vorhanden; maßgeblich sind die Registertypen in der Datei.

## 7. Fehlersuche

**`ModuleNotFoundError: noah_registers`** – die Versionsdatei muss als `noah_registers.py` neben `noah_tui.py` liegen.

**`ModuleNotFoundError: textual` / `bleak`** – virtuelle Umgebung aktivieren und Abhängigkeiten installieren.

**Keine BLE-Geräte gefunden** – Bluetooth-Adapter, Berechtigungen, Reichweite und mögliche parallele Verbindungen prüfen. Ein nicht gefundenes Gerät ist nicht zwangsläufig defekt.

**Einzelregister liefert keine Werte** – nicht automatisch als „Register existiert nicht“ interpretieren. Einige NOAH-Register antworten nur in geeigneten Blockabfragen; die optimierten 16er-Fenster sind deshalb wichtig.

**Firmware-Kombination wird abgelehnt** – prüfen, ob die Zeile exakt `119-120,21|Firmware|CombLBHBLB+ASCIIstrip(.)|.|I,C` lautet und beide Programme in der genannten Version vorliegen.

**Anzeige teilweise unvollständig** – bei einer Kombination müssen alle benötigten Quellen erfolgreich gelesen werden. Ein fehlender C-Parameter darf nicht stillschweigend als gültige Firmwareversion ausgegeben werden.

## 8. Projektstand und Grenzen

BLE-Registerabfragen wurden an vier NOAH-Geräten getestet. WLAN-Suche, Kopplung, selektives Löschen und Messwertabfrage wurden mit Firmware 19.20.14.4026 und zwei Shelly-Pro-3EM-Emulatoren erprobt. Andere Firmwareversionen und Zubehörmodelle sind nicht verifiziert.

Die SOH-Anzeige I:102 ist ebenfalls mit Vorsicht zu interpretieren: Für BMS-Version 14 beschreibt die verwandte NEXA-Dokumentation einen konstanten Wert von 100; ob und in welchem Umfang dies beim NOAH identisch umgesetzt ist, sollte separat verifiziert werden.

## 9. Herkunft und Haftungshinweis

Die BLE-Protokollbasis (`protocol.py`, `growatt_ble.py`) baut auf dem Community-Projekt **meiser79/growatt_noah_ble** auf. Bei einer Veröffentlichung sind dessen Lizenz- und Urheberrechtshinweise zu beachten.

**Keine Verbindung zu Growatt; keine Garantie für Richtigkeit oder Betriebssicherheit.** Lesezugriffe sind für die Diagnose gedacht. Schreib- und Neustartfunktionen ausschließlich bewusst und auf eigene Verantwortung einsetzen.
