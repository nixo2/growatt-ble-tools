# NOAH BLE TUI — Version 0.8.0

**Interaktiver Registerleser und experimentelles Konfigurationswerkzeug für Growatt NOAH über Bluetooth Low Energy (BLE).**

Die TUI baut auf `noah_registers.py`, `growatt_ble.py` und `protocol.py` auf. Sie ermöglicht das lokale Auslesen und — nach ausdrücklicher Bestätigung — Schreiben ausgewählter Gerätedaten, ohne die Growatt-App für diese Vorgänge zu benötigen. Das Projekt ist **inoffiziell** und steht in keiner Verbindung zu Growatt.

> **Wichtiger Sicherheitshinweis:** Schreibzugriffe und der Datenlogger-Neustart sind experimentell. Falsche Werte können Zeitpläne, Leistungsgrenzen, Netzwerkverbindungen oder andere Gerätefunktionen verändern. Nur an eigenen bzw. autorisiert verwalteten Geräten verwenden. Vor Änderungen bisherige Werte dokumentieren. Nicht alle lesbaren Register sind tatsächlich beschreibbar; ihre Bedeutung kann je nach Firmware abweichen.

## Funktionen

### Registerübersicht und Diagnose

- Zweispaltige, interaktive Terminalansicht mit unabhängiger Auswahl in beiden Spalten.
- Darstellung der in `registers.txt` definierten **Input- (`I`), Holding- (`H`) und Konfigurationsparameter (`C`)**.
- Auswahl per Pfeiltasten; **Enter** öffnet Registerdetails mit Adresse, Typ und Interpretation.
- Unterstützung für Einzelregister, Registerbereiche und nicht zusammenhängende Registerlisten.
- Darstellung von Zahlenwerten, Faktoren/Umrechnungen, Einheiten, Enumerationen, Bitfeldern, ASCII-Text und Zeitwerten entsprechend der Registerdefinition.
- Fortschritt und Diagnose der vollständigen BLE-Abfrage werden vor dem Öffnen der Übersicht in der Konsole angezeigt.
- **R** liest bei Bedarf die gesamte Registerliste erneut ein.

### Kontrollierter Schreibzugriff

- **W** öffnet den Schreibdialog **innerhalb des Registerdetailfensters**; die Hauptübersicht selbst bietet keinen direkten Schreibbefehl.
- **C-Parameter:** Schreiben einzelner ASCII-Konfigurationswerte über den Growatt-BLE-Konfigurationsbefehl `0x18`; anschließendes Kontrolllesen über `0x19`. Beispielsweise wurden MQTT-Server (Parameter 17) und MQTT-Port (Parameter 18) praktisch getestet.
- **H-Register:** Schreiben einzelner 16-Bit-Holding-Register über Growatt-BLE-Passthrough `0x17` mit Modbus-Funktion `0x06` (FC06).
- **Registergruppen:** Bei Einträgen wie `45–50` wird zuerst **die konkrete Registeradresse** und danach der **neue Rohwert** eingegeben. Es wird ausschließlich dieses einzelne Register geschrieben, nicht die ganze Gruppe.
- Dezimale Rohwerte und `0x`-Hexwerte; für einzelne mit `TIME` definierte H-Register Eingabe als `HH:MM`.
- Vor dem Schreiben: Eingabeprüfung, erneutes Lesen des alten Werts und ausdrückliche Bestätigung **„JA, schreiben“**.
- Nach dem Schreiben: Kontrolllesen des tatsächlich übernommenen Werts. Die Anzeige verwendet den **Rücklesewert**, nicht ungeprüft die Eingabe.
- **Gezielte Tabellenaktualisierung:** Nur der betroffene Tabelleneintrag wird aktualisiert. Bei kombinierten H-Registern werden die weiteren Gruppenmitglieder nachgelesen; die gesamte Liste muss nicht neu abgefragt werden.
- Die Spaltenformatierung bleibt auch nach Einzelaktualisierungen erhalten.

**Hinweis zu C-Parametern:** Auch lesbare Statusparameter (z. B. 55/60) können in der experimentellen Oberfläche zur Bearbeitung angeboten werden. Daraus folgt **nicht**, dass sie beschreibbar sind oder ein Schreibversuch sinnvoll ist.

**Hinweis zu H-Registern:** Alle in `registers.txt` als `H` aufgeführten Register sind grundsätzlich im Schreibdialog erreichbar, auch unbekannte. Die TUI kennt nicht für jedes Register zulässige Wertebereiche oder Nebenwirkungen. Bei Gruppen mit ASCII-Anzeige werden **16-Bit-Rohwerte**, keine vollständigen ASCII-Zeichenfolgen geschrieben.

### Datenlogger-Neustart

- **N** öffnet den Neustartdialog.
- Ein Neustartbefehl wird erst nach ausdrücklicher Bestätigung gesendet.
- Der Befehl kann die BLE- bzw. MQTT-Verbindung vorübergehend unterbrechen.

### Verwaltung mehrerer NOAHs

- Beim ersten Start **ohne `noah_devices.txt`** öffnet sich die Geräteverwaltung.
- BLE-Suche nach erreichbaren NOAH-Geräten; gefundene Geräte können der gespeicherten Liste hinzugefügt oder daraus entfernt werden.
- Bei späteren Starts öffnet sich zunächst die Auswahl der gespeicherten Geräte.
- **G**: anderes gespeichertes Gerät auswählen; **K**: Geräteverwaltung öffnen — auch aus den Registerdetails heraus.
- Die Geräte werden in der Datei `noah_devices.txt` gespeichert (UTF-8-Text mit JSON-Struktur).
- **`--address`** erlaubt die direkte Auswahl einer BLE-Adresse ohne Gerätemenü.

## Voraussetzungen

- Python 3.11 oder neuer empfohlen.
- Funktionierender Bluetooth-Low-Energy-Adapter und Zugriff auf das betreffende NOAH-Gerät.
- Terminal mit ausreichender Breite für die zweispaltige Darstellung; unter Windows wird Windows Terminal empfohlen.
- Unter Linux muss der BLE-Adapter über BlueZ erreichbar sein. Unter Windows verwendet Bleak die Windows-Bluetooth-Schnittstelle. BLE-Adressen/Identifikatoren können zwischen Betriebssystemen abweichen; Geräte gegebenenfalls erneut suchen.

## Installation

Die TUI benötigt die folgenden Projektdateien **im selben Verzeichnis**:

```text
noah-ble/
├── noah_tui.py
├── noah_registers.py
├── growatt_ble.py
├── protocol.py
├── registers.txt
├── requirements.txt
└── noah_devices.txt      # wird bei Bedarf angelegt
```

Die zuletzt getestete Entwicklungsdatei `noah_tui_v0_8_2.py` wird für die Veröffentlichung als **`noah_tui.py`** bereitgestellt. Die Versionsnummer **0.8.0** bezeichnet den öffentlichen Release-Stand; die interne Entwicklungsnummer muss für das Release gegebenenfalls noch angepasst werden.

Empfohlene `requirements.txt` (bestehende Mindestversionen beibehalten):

```text
bleak>=0.21
cryptography>=42
windows-curses; sys_platform == "win32"
textual
rich
```

`windows-curses` bleibt für gegebenenfalls vorhandene ältere curses-basierte Projektteile erhalten; die TUI selbst verwendet **Textual** und **Rich**.

### Linux

```bash
cd ~/growatt-ble
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python noah_tui.py
```

### Windows (PowerShell)

```powershell
cd C:\NOAH-BLE
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python noah_tui.py
```

Falls PowerShell die Aktivierung der virtuellen Umgebung verhindert, kann Python direkt aufgerufen werden:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe noah_tui.py
```

## Bedienung

| Taste | In der Registerübersicht | In den Registerdetails |
|---|---|---|
| `↑` / `↓` | Register auswählen | — |
| `←` / `→` | Spalte wechseln | — |
| `Enter` | Details und Ändern öffnen | — |
| `W` | — | Schreibdialog für `H` bzw. geeignete `C`-Parameter |
| `G` | Gerät auswählen | Gerät auswählen |
| `K` | Geräte verwalten | Geräte verwalten |
| `N` | Datenlogger-Neustart mit Sicherheitsabfrage | — |
| `R` | Alle Register neu lesen | — |
| `Q` | Programm beenden | Detailfenster schließen |
| `Esc` | — | Detailfenster schließen |

**Typischer Schreibablauf:** Register markieren → `Enter` → `W` → Wert eingeben → **Prüfen** → angezeigte alte/neue Rohwerte kontrollieren → **JA, schreiben** → Rückleseergebnis abwarten → zur Übersicht zurückkehren. Die betreffende Tabellenzeile wird automatisch aktualisiert.

### Beispiel: Einzelregister

Bei Holding-Register `251` (Entladegrenze) kann ein zulässiger Rohwert eingegeben und nach der Sicherheitsabfrage zurückgelesen werden. Änderungen an Lade- oder Entladegrenzen wirken sich auf das Geräteverhalten aus.

### Beispiel: Registergruppe 45–50

Die Gruppe wird anhand bisheriger Messungen **vermutlich** als Gerätezeit interpretiert (Jahr, Monat, Tag, Stunde, Minute, Sekunde). Diese Zuordnung ist nicht offiziell bestätigt.

Um nur das mutmaßliche Tagesregister zu bearbeiten, im Schreibdialog **Registeradresse `47`** auswählen und darunter den gewünschten **16-Bit-Rohwert** eingeben. Andere Gruppenregister werden nicht geschrieben. Ein falscher Wert kann die interne Zeit oder zeitabhängige Funktionen beeinflussen.

## Gerätedatei `noah_devices.txt`

Die Geräteverwaltung erzeugt eine Textdatei mit JSON-Inhalt, beispielsweise:

```json
[
  {"address": "AA:BB:CC:DD:EE:01", "name": "NOAH Terrasse"},
  {"address": "AA:BB:CC:DD:EE:02", "name": "NOAH Garage"}
]
```

Die Adressen sind **Platzhalter**. Die Datei kann bei Bedarf manuell bearbeitet werden; gültige JSON-Syntax ist erforderlich. Die TUI speichert Änderungen über die Geräteverwaltung.

## Eigene Registerdefinitionen

Die Datei `registers.txt` bestimmt, welche Register gelesen und angezeigt werden. Sie unterstützt Registertypen `I`, `H` und `C`, einzelne Adressen, Bereiche, Registerlisten sowie Formatierungsangaben und Abschnittsüberschriften. Die TUI übernimmt die Interpretation aus dem vorhandenen Registerleser `noah_registers.py`.

Nicht dokumentierte Register sollten vor dem Schreiben zunächst über längere Zeit beobachtet und mit bekannten Betriebszuständen verglichen werden.

## Kommandozeilenoptionen

```bash
python noah_tui.py
python noah_tui.py --address AA:BB:CC:DD:EE:FF
python noah_tui.py --file /pfad/zu/registers.txt
```

Die Adresse bei `--address` muss zum jeweiligen Betriebssystem und BLE-Backend passen.

## Technischer Hintergrund

- **BLE:** Kommunikation über `bleak` und die vorhandenen Projektmodule `growatt_ble.py` / `protocol.py`.
- **Registerlesen:** Modbus-Funktionen `0x03` (Holding) und `0x04` (Input) im Growatt-BLE-Passthrough `0x17`.
- **Holding-Schreiben:** Modbus `0x06` (ein Register) im Passthrough `0x17`.
- **Konfigurationsparameter:** Growatt-Konfigurationsbefehle `0x18` (Schreiben) und `0x19` (Lesen).
- **TUI:** `textual` / `rich`.
- **Darstellung:** Wiederverwendung der Registerdefinitionen und Umrechnungen aus `noah_registers.py`.

Das Lesen und Schreiben der MQTT-Konfiguration wurde an einem realen NOAH erprobt; nach Änderung der MQTT-Serveradresse wurden tatsächlich Daten am eigenen MQTT-/Node-RED-System empfangen. Daraus lässt sich **keine allgemeine Kompatibilitätsgarantie** für alle Firmwarestände oder alle Register ableiten.

## Neuerungen des 0.8.0-Releases

Gegenüber dem früheren reinen Registerleser bietet die interaktive Ausgabe insbesondere:

1. Vollbild-TUI mit zwei unabhängig navigierbaren Registerspalten.
2. Registerdetailfenster mit integrierter Schreibfunktion (`Enter` → `W`).
3. Bestätigtes ASCII-Schreiben von Growatt-Konfigurationsparametern.
4. Experimentelles Schreiben aller definierten Holding-Register, einschließlich einzelner Mitglieder von Registergruppen.
5. Vorab-Lesen, explizite Sicherheitsabfrage und Kontrolllesen nach Schreibzugriffen.
6. Neustartfunktion für den Datenlogger mit Sicherheitsabfrage.
7. Persistente Geräteverwaltung mit BLE-Suche, Geräteauswahl und Gerätewechsel.
8. Gezielte Aktualisierung geänderter Tabellenzeilen ohne vollständige neue BLE-Abfrage.
9. Korrigierte feste Spaltenformatierung bei der gezielten Aktualisierung.
10. Manuelles vollständiges Neueinlesen (`R`) weiterhin jederzeit möglich.

## Grenzen und bekannte Risiken

- Keine Garantie, dass jedes in `registers.txt` definierte `H`- oder `C`-Register tatsächlich beschreibbar ist.
- Keine vollständige Validierung gerätespezifischer Wertebereiche für unbekannte Holding-Register.
- Schreibzugriffe auf Registergruppen betreffen immer nur **ein** 16-Bit-Register pro Vorgang.
- Bei einer abgebrochenen BLE-Verbindung kann der tatsächliche Schreibzustand unklar sein; in diesem Fall vor erneutem Schreiben erst auslesen.
- Eine erfolgreiche Lesekontrolle beweist nicht automatisch, dass eine Einstellung nach jedem Neustart dauerhaft erhalten bleibt.
- Bluetooth-Erkennung und Berechtigungen unterscheiden sich zwischen Linux und Windows.

## Lizenz

Es gelten die Lizenz- und Hinweisdateien (`LICENSE`, `NOTICE`) des jeweiligen Projekt-Repositories. Vor der Veröffentlichung bitte prüfen, dass diese Dateien zusammen mit den übernommenen Projektmodulen ausgeliefert werden.
