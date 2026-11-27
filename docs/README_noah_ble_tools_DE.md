# Growatt NOAH – BLE-Registerdiagnose und Terminaloberfläche

**Aktualisiert: 27. November 2026**  
**Projekt-Release 1.1 · `noah_tui.py` v1.1 · `noah_watchdog_ui.py` v0.1.8 · `noah_watchdog.py` v0.4.2 · `noah_registers.py` v0.8.2**

Werkzeuge zum direkten Auslesen und Diagnostizieren von Growatt-NOAH-Batteriespeichern über Bluetooth Low Energy (BLE):

- **`noah_tui.py`**: Empfohlene interaktive Textual-Oberfläche zur Registerdiagnose, Geräteverwaltung, Konfiguration und Bearbeitung von Zeitfenstern.
- **`noah_watchdog_ui.py`**: Integrierte interaktive Watchdog-Verwaltung (Linux und Windows), erreichbar über `W`.
- **`noah_registers.py`**: Kommandozeilenwerkzeug zur Gerätesuche, zum Auslesen von Registern, zur Anzeige von Registerlisten und zur Diagnose.
- **`noah_watchdog.py`**: Eigenständiger Überwachungsprozess mit optionaler Wiederherstellung (Recovery).

Die BLE-Werkzeuge verwenden die vorhandenen Module `protocol.py` und `growatt_ble.py`. Der BLE-Zugriff benötigt weder die Growatt-Cloud noch MQTT. Das Projekt ist **inoffiziell und experimentell**; insbesondere sind Registerbedeutungen und Schreiboperationen von Growatt nicht für diese Verwendung freigegeben.

## 1. Voraussetzungen und Dateien

Benötigt werden ein Computer mit funktionsfähigem BLE-Adapter, Python ab Version 3.11 und Zugriff auf die Bluetooth-Schnittstelle. Beide Anwendungen verwenden `bleak`; die TUI benötigt zusätzlich `textual` und `rich` (Rich wird normalerweise mit Textual installiert).

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Unter Windows (PowerShell), aus dem Hauptverzeichnis des Repositorys:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Die Requirements-Datei installiert `windows-curses` und `psutil` ausschließlich unter Windows. Linux behält die bisherige Prozessverwaltung über `/proc` ohne `psutil` bei.

Die folgenden Dateien gehören in dasselbe Projektverzeichnis:

```text
project/
├── noah_registers.py       # v0.8.2
├── noah_tui.py             # v1.1
├── noah_watchdog_ui.py     # v0.1.8
├── noah_watchdog.py        # v0.4.2
├── protocol.py             # BLE-Protokollmodul
├── growatt_ble.py          # BLE-Gerätekommunikation
├── registers.txt           # Registerauswahl, Umrechnung, Layout
├── register_info.txt       # Optionale Erläuterungen in der Detailansicht
└── noah_devices.txt        # Durch die TUI verwaltete Geräteliste
```

## 2. Schnellstart

**TUI mit Geräteauswahl:**

```bash
python noah_tui.py
```

**TUI mit einer bestimmten BLE-Adresse:**

```bash
python noah_tui.py --address 'AA:BB:CC:DD:EE:FF'
```

**Andere Registerdatei verwenden:**

```bash
python noah_tui.py --file registers.txt
```

### Integrierte Watchdog-Oberfläche (Linux und Windows)

Drücke `W` auf dem Startbildschirm oder in der Registerübersicht. Über die vorhandene BLE-Einrichtung lassen sich Geräte verwalten (standardmäßig acht Sekunden Suchdauer), Logging und Recovery konfigurieren, der Prozess starten, stoppen und neu starten sowie PID und Laufzeit anzeigen. Watchdog- und Konsolenprotokolle stehen mit Filtern und Recovery-Statistiken zur Verfügung.

Beim Speichern der Einstellungen oder nach Rückkehr aus der Geräteeinrichtung während laufender Überwachung erscheint ein Neustarthinweis, der mit Enter bestätigt wird. Der Prozess wird nicht automatisch neu gestartet. Die Recovery-Statistik zählt gestartete Wiederherstellungsversuche, nicht erfolgreiche Reparaturen.

Unter Windows verwendet die Prozessverwaltung `psutil`, fasst zusammengehörige Python-Launcher- und Interpreterprozesse einer virtuellen Umgebung zu einer Instanz zusammen und startet die Überwachung ohne zusätzliches Konsolenfenster. Linux verwendet weiterhin `/proc`. Das Schließen und erneute Öffnen der TUI unterbricht die Überwachung nicht. Unter Windows beendet die Stop-Funktion den Prozess unmittelbar; ein Abbruch während Recovery-Schreiboperationen sollte vermieden werden. Siehe auch die [Watchdog-Dokumentation](README_watchdog.md).

### Kommandozeilenschnittstelle (CLI)

**CLI – Geräte suchen:**

```bash
python noah_registers.py scan --seconds 8
```

**CLI – Registerliste anzeigen:**

```bash
python noah_registers.py list --address 'AA:BB:CC:DD:EE:FF' --file registers.txt
```

**Einzelnes Input-Register lesen:**

```bash
python noah_registers.py read --address 'AA:BB:CC:DD:EE:FF' --type input --register 120
```

**Holding-Register lesen:**

```bash
python noah_registers.py read --address 'AA:BB:CC:DD:EE:FF' --type holding --register 254 --count 5
```

Der Aufruf von `noah_registers.py` ohne Unterbefehl öffnet das interaktive Diagnosemenü.

## 3. Registertypen und Übertragung

| Kürzel | Datenquelle | Lesemechanismus | Datentyp |
|---|---|---|---|
| `I` | Input-Register | Growatt BLE `0x17` mit Modbus FC04 | 16-Bit-Wörter |
| `H` | Holding-Register | Growatt BLE `0x17` mit Modbus FC03 | 16-Bit-Wörter |
| `C` | Growatt-Konfigurationsparameter | Growatt BLE `0x19` (QUERY) | Bytefolgen variabler Länge, häufig ASCII |
| `I,C` / `H,C` | Gemischte Quellen | Beide entsprechenden Lesemechanismen | Kombinierter **Anzeigewert**, kein gemeinsames physisches Register |

Ein Holding-Register ist nicht automatisch ein Datalogger-Parameter. Der Adressraum `C` verwendet eine separate Growatt-Konfigurationsschnittstelle.

### Optimierte Lesezugriffe auf Input-Register

Für die Input-Register **I:0–123** verwendet die aktuelle Implementierung experimentell verifizierte Leseblöcke mit jeweils 16 Wörtern:

```text
0–15, 16–31, 32–47, 48–63,
64–79, 80–95, 96–111, 108–123
```

Der letzte Block überlappt absichtlich. Einzelzugriffe auf I:119/I:120 waren in Tests unzuverlässig, während das Lesen des 16-Wort-Blocks funktionierte. Holding-Register und Konfigurationsparameter werden weiterhin mit ihren jeweiligen Methoden gelesen. Register außerhalb der verifizierten Blöcke werden über die bestehende Leselogik verarbeitet.

## 4. Format der `registers.txt`

Grundformat (UTF-8), ein Eintrag pro Zeile:

```text
Register|Label|Factor|Unit|RegisterType
```

Beispiele:

```text
101|Charge cycles|1|cycles|I
102|SOH|1|%|I
119-120|Firmware components|CombLBHBLB|.|I
21|Firmware suffix|ASCII||C
119-120,21|Firmware|CombLBHBLB+ASCIIstrip(.)|.|I,C
```

Die fünfte Spalte ist optional; fehlt sie, wird `I` angenommen. Zeilen mit führendem `#` sind Kommentare; Leerzeilen werden ignoriert.

### Registerangaben

- `119`: Ein einzelnes Register.
- `119-120`: Zusammenhängender Bereich einschließlich beider Grenzen.
- `100->16`: 16 Register ab Adresse 100 (100 bis 115).
- `30,42`: Explizite Liste einzelner Register.
- `119-120,21` mit Typ `I,C`: **Erste Gruppe** = Input-Register 119–120; **zweite Gruppe** = Konfigurationsparameter 21.

Die aktuelle Syntax für gemischte Quellen unterstützt gezielt `I,C` und `H,C`, jeweils mit einem Modbus-Registerbereich und **einer** C-Parameter-ID. Sie ist kein universeller Ausdrucksparser.

### Faktoren und Anzeigeformate

| Faktor | Bedeutung |
|---|---|
| `1`, `0.1`, `0.01` | Rohwert multiplizieren |
| `*0.1 -273.1` | Arithmetische Umrechnung |
| `ASCII` | 16-Bit-Wörter als ASCII bzw. C-Parameter als Zeichenkette interpretieren |
| `HEX` | Bytes hexadezimal anzeigen |
| `TIME` | Zeitangabe mit High-Byte (Stunden) und Low-Byte (Minuten) |
| `bit3-0` | Ausgewählte Bits als Bitfolge anzeigen |
| `CombLBHBLB` | Ausgewählte Bytes mehrerer 16-Bit-Wörter kombinieren |
| `CombLBHBLB+ASCIIstrip(.)` | Ausgewählte Bytes mit einer bereinigten ASCII-Zeichenkette eines C-Parameters kombinieren |

Weitere Optionen für numerische Formeln sind `NULL0`, `NULL65535` und `NOHEX`. Bei numerischen Statuswerten kann die Einheitenspalte auch eine Legende enthalten, beispielsweise `0 Off, 1 On`.

### Rekonstruktion der vollständigen Firmwareversion

**Empfohlener Eintrag:**

```text
119-120,21|Firmware|CombLBHBLB+ASCIIstrip(.)|.|I,C
```

Beobachtete Rohwerte:

```text
I:119 = 0x0013  → Low-Byte  = 19
I:120 = 0x140E  → High-Byte = 20; Low-Byte = 14
C:21  = "4.0.2.6" (ASCII)
```

Jede numerische Komponente erhält das Suffix aus der Einheitenspalte (`.`), **einschließlich der letzten numerischen Komponente**. Daraus entsteht `19.20.14.`. Anschließend entfernt `ASCIIstrip(.)` **alle** Punkte aus der C-Zeichenkette (`4026`) und hängt diese an.

**Ergebnis: `19.20.14.4026`**, entsprechend der in der Growatt-App beobachteten Firmwareversion.

`HB` bezeichnet das High-Byte (Bits 15–8), `LB` das Low-Byte (Bits 7–0). Diese Operation **formatiert bereits gelesene Werte** und führt keinen neuen BLE-Befehl ein. Trotz der Bezeichnung entfernt `ASCIIstrip(.)` alle Punkte (entsprechend `replace`), nicht nur führende oder abschließende Punkte.

### Layout-Direktiven

Eigenständige, von doppelten Sternchen umschlossene Zeilen steuern die Anzeige:

```text
**[Battery]**
**LEER**
**---**
**===40===**
**SPALTE**
**[Datalogger]**
```

`**SPALTE**` oder `**SEITENUMBRUCH**` beginnt eine neue Anzeigespalte. Diese Schlüsselwörter sind Bestandteil der Parser-Syntax. Überschriften und Trennlinien lösen keine Registerzugriffe aus.

## 5. Bedienung der TUI

| Taste | Funktion |
|---|---|
| `↑` / `↓` | Vorherigen / nächsten Eintrag auswählen |
| `←` / `→` | Linke / rechte Spalte auswählen |
| `Enter` | Details zum ausgewählten Eintrag anzeigen |
| `R` | Alle Register aktualisieren |
| `G` | Gerät wechseln |
| `K` | Geräteverwaltung öffnen |
| `N` | Datalogger-Neustart auslösen (**aktiver Eingriff**) |
| `W` | Watchdog-Verwaltung öffnen (Linux und Windows) |
| `Q` | Beenden oder aktuellen Dialog schließen |

Die TUI liest Register über `noah_registers.py`, zeigt die Ergebnisse in zwei Spalten an und öffnet mit Enter eine Detailansicht. Die optionale `register_info.txt` ergänzt Erläuterungen unter Abschnittsüberschriften wie `[I:120]`, `[H:254]` oder `[C:21]`.

### Schreiboperationen – Vorsicht

Die TUI enthält experimentelle Funktionen zum Ändern einzelner Holding-Register, bestimmter ASCII-Konfigurationsparameter und der neun Zeitfenster (jeweils fünf Holding-Register). Schreibzugriffe verwenden separate Growatt-/Modbus-Befehle; anschließend versucht die Implementierung, die tatsächlichen Werte zurückzulesen. Ein Timeout **beweist nicht**, dass ein Schreibzugriff wirkungslos geblieben ist.

Der kombinierte Firmwareeintrag `I,C` ist **schreibgeschützt**. Niemals ungetestete Schreibwerte an ein Gerät senden: Änderungen an Betriebsmodi, Ausgangsleistung, Zeitplänen oder Kommunikation können sich unmittelbar auf den Betrieb auswirken.

### Verwaltung von WLAN-Zubehör (experimentell)

Den Abschnitt **Gekoppelte Geräte** mit Enter öffnen. Der NOAH selbst sucht WLAN-Zubehör über mDNS; der Computer steuert Suche und Kopplung über BLE. ShinePhone oder eine Cloud-Verbindung sind dafür nicht erforderlich.

- Zubehörmodell auswählen (standardmäßig Shelly Pro 3EM) oder einen eigenen mDNS-Dienst und eine Typ-ID eingeben. Weitere Voreinstellungen sind Shelly 3EM, Pro 3EM-3CT63, Pro EM-50, Pro 1PM, everHome EcoTracker und Sparky P1; diese Alternativen wurden in diesem Test nicht validiert.
- Über das Eingabefeld oder die −/+-Schaltflächen 2–12 Mindest-Abfragezyklen einstellen. Jeder Zyklus dauert fünf Sekunden. Ändert sich die MAC-Adressmenge im letzten Zyklus oder tritt dort ein Lesefehler auf, wird die Abfrage um einen Zyklus verlängert, bis maximal 12 Zyklen erreicht sind. Die interne Suche des NOAH kann nach Ende der BLE-Abfragen weiterlaufen.
- C123 wird vor der Suche experimentell auf ASCII `0` zurückgesetzt und durch Rücklesen überprüft. Eine separate Schaltfläche zum Löschen ist vorhanden. Schlägt das Zurücksetzen fehl, gelten vorhandene Adressen nicht als bestätigte neue Suchergebnisse.
- Gefundene MAC-Adresse und Kopplungsmodus auswählen: `access,0` (messen und regeln) oder `access,1` (nur messen). Die Kopplung wird anhand des C122-Status 3 überprüft; der angeforderte Zugriffsmodus wird nicht unabhängig bestätigt.
- Einzelne WLAN-Einträge, auch ungekoppelte, gezielt löschen oder ausdrücklich alle Einträge löschen. Die Löschung wird anhand von C122 überprüft. Relevante Rücklesewerte aktualisieren die Haupttabelle ohne vollständige Aktualisierung.
- C122-Einträge werden nach Eintragsnummer sortiert und in separaten Blöcken mit hexadezimaler MAC-Adresse, IP-Adresse und Status angezeigt. Die Protokoll-Rohdaten bleiben für Diagnosezwecke verfügbar.

| Register | Bedeutung |
|---|---|
| C102 | RF-Zubehörliste |
| C122 | WLAN-Zubehöreinträge und Befehle |
| C123 | MAC-Adressliste der Gerätesuche |
| C143 | Weitergeleitetes Modbus; für die getestete WLAN-Kopplung nicht erforderlich |
| H310 | Betrag der Einspeiseleistung (W; Richtungszuordnung am NOAH mit negativer Leistung noch nicht getestet) |
| H311 | Bezugsleistung (W; verifiziert) |
| H313 | Anzahl der Zubehörgeräte; blieb in den WLAN-Tests null |

C122 verwendet `DEV:<entry>-<mode>-<state>,<name>,<address>` mit `&` als Trennzeichen zwischen Einträgen. Modus 7 steht für die WLAN-Suche; die Statuswerte 0, 3 und 5 stehen für ungekoppelt, gekoppelt bzw. gelöscht. Gelöschte Einträge können weiterhin MAC-/IP-Daten enthalten. Beispielbefehle: `ADD:111-7-_http._tcp.,2`, `CRL:112-7-add:sn,80225394980980|access,1`, `DEL:112-7-_http._tcp.,2`. Zum Koppeln und Löschen die tatsächliche Eintragsnummer verwenden.

**Wichtige Firmwarebeobachtung:** Bei zwei getesteten NOAHs erzeugte eine erneute Suche bei bereits gekoppeltem Zähler einen ungekoppelten Eintrag und unterbrach anschließend die Zählerwertübertragung. Bloßes Abwarten stellte die Messwerte nicht wieder her. Durch gezieltes Löschen des ungekoppelten Eintrags wurden die Messwerte wiederhergestellt, ohne die funktionierende Kopplung zu löschen. Auch das Koppeln des zweiten Eintrags stellte die Messwerte wieder her. Bei zwei gekoppelten Zählern zeigte H311 im beobachteten Test den Wert des Eintrags 112 und nicht die Summe an. Welcher Zähler die Ausgangsleistung regelt und ob `access,1` ihn zuverlässig von der Regelung ausschließt, ist weiterhin nicht verifiziert. Ein Nullwert allein beweist keine Störung. Dieses Verhalten könnte manche erfolglosen Kopplungsversuche in der App erklären; dieser Zusammenhang wurde jedoch nicht nachgewiesen.

## 6. CLI-Optionen

```bash
python noah_registers.py --help
python noah_registers.py scan --help
python noah_registers.py read --help
python noah_registers.py list --help
python noah_tui.py --help
```

Der Befehl `list` unterstützt unter anderem `--timeout`, `--block-size` (Standard 16), `--max-gap` (Standard 2) und `--verbose`. Die ältere Option `list --type` bleibt aus Kompatibilitätsgründen erhalten; die in der Datei angegebenen Registertypen haben Vorrang.

## 7. Fehlerbehebung

**`ModuleNotFoundError: noah_registers`** – Die versionierte Datei muss `noah_registers.py` heißen und neben `noah_tui.py` liegen.

**`ModuleNotFoundError: textual` / `bleak`** – Virtuelle Umgebung aktivieren und Abhängigkeiten installieren.

**`ModuleNotFoundError: psutil` / `curses` unter Windows** – Die aktuelle `requirements.txt` in der aktiven virtuellen Umgebung installieren.

**Keine BLE-Geräte gefunden** – Bluetooth-Adapter, Berechtigungen, Reichweite und mögliche gleichzeitige Verbindungen prüfen. Ein bei der Suche nicht gefundenes Gerät ist nicht zwangsläufig defekt.

**Einzelnes Register liefert keine Werte** – Nicht automatisch davon ausgehen, dass das Register nicht existiert. Manche NOAH-Register antworten nur beim Lesen eines geeigneten Blocks; deshalb sind die optimierten 16-Wort-Leseblöcke wichtig.

**Firmwarekombination wird abgelehnt** – Prüfen, ob der Eintrag exakt `119-120,21|Firmware|CombLBHBLB+ASCIIstrip(.)|.|I,C` lautet und beide Anwendungen die oben angegebenen Versionsstände verwenden.

**Anzeige teilweise unvollständig** – Für einen kombinierten Wert müssen alle erforderlichen Quellen erfolgreich gelesen werden. Ein fehlender C-Parameter darf nicht stillschweigend als gültige Firmwareversion interpretiert werden.

## 8. Projektstatus und Einschränkungen

BLE-Registerzugriffe wurden an vier NOAH-Geräten getestet. WLAN-Suche, Kopplung, gezieltes Löschen und Rücklesen der Zählerwerte wurden mit Firmware 19.20.14.4026 und zwei Shelly-Pro-3EM-Emulatoren getestet. Andere Firmwareversionen und Zubehörmodelle sind weiterhin nicht verifiziert.

Auch der SOH-Wert in I:102 sollte vorsichtig interpretiert werden: Für BMS-Version 14 beschreibt die zugehörige NEXA-Dokumentation einen konstanten Wert von 100. Ob und in welchem Umfang sich der NOAH identisch verhält, sollte separat überprüft werden.

## 9. Danksagung und Haftungsausschluss

Die BLE-Protokollgrundlage (`protocol.py`, `growatt_ble.py`) baut auf dem Community-Projekt **meiser79/growatt_noah_ble** auf. Bei einer Veröffentlichung sind dessen Lizenz- und Urheberrechtshinweise zu beachten.

**Keine Verbindung zu Growatt. Keine Gewähr für Korrektheit oder Betriebssicherheit.** Lesezugriffe dienen der Diagnose. Schreib- und Neustartfunktionen nur bewusst und auf eigenes Risiko verwenden.
