#!/usr/bin/env python3
"""NOAH BLE TUI v0.8.0: guarded C ASCII and all H register writes plus datalogger restart.

Requires textual, bleak, and the unchanged noah_registers.py,
protocol.py, growatt_ble.py and registers.txt in the same directory.
"""
import argparse
import asyncio
import contextlib
import io
import json
import re
import sys
from pathlib import Path

from rich.text import Text
from rich.markup import escape
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll, Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Static, Input, Button, Label

import noah_registers as core

BASE = Path(__file__).resolve().parent
DEVICE_FILE = BASE / 'noah_devices.txt'


def factor_description(formula):
    if isinstance(formula, str):
        return formula
    if isinstance(formula, tuple):
        return f'Bitmaske {formula[1]}–{formula[2]}'
    return 'Numerischer 16-Bit-Wert (mit Umrechnung)' if callable(formula) else 'Numerischer 16-Bit-Wert'


def classify(entry):
    regs, label, formula, unit, typ = entry
    if typ == 'C':
        return 'ASCII-Zeichenfolge (Growatt-Konfigurationsparameter)', 'ASCII-Bytes; keine 16-Bit-Modbus-Zahl'
    if formula == 'ASCII':
        return 'ASCII aus 16-Bit-Registern', 'Modbus-Register, als Zeichen interpretiert'
    if formula == 'TIME':
        return 'Zeitkodierung in 16-Bit-Register', 'Modbus-Holding/Input-Register'
    if isinstance(formula, tuple):
        return '16-Bit-Bitmaske', 'Modbus-Register'
    return '16-Bit-Zahlenwert', 'Modbus-Register'


def collect_columns(output, layout_entries):
    """Extract the two printed table columns, discarding BLE progress logs.

    Keep the original renderer's formatting, but select each logical entry
    independently rather than selecting a complete screen row.
    """
    lines = output.splitlines()
    header_idx = next((i for i, line in enumerate(lines)
                       if line.lstrip().startswith('Reg  Bezeichnung')), None)
    if header_idx is None:
        raise ValueError('Keine Registertabelle in der BLE-Ausgabe gefunden')
    header = lines[header_idx]
    first = header.find('Reg  Bezeichnung')
    second = header.find('Reg  Bezeichnung', first + 1)
    # Both page headers have identical indentation. The second 'Reg' is
    # NOT the beginning of the right-hand page: section titles and rule
    # lines start several characters earlier.
    page_start = second - first if second >= 0 else -1
    # The original renderer inserts four spaces between pages. Locate the
    # second header rather than guessing a fixed page width.
    columns = [[], []] if second >= 0 else [[]]
    for line in lines[header_idx:]:
        if 'Einträge erfolgreich gelesen' in line:
            break
        if second >= 0:
            columns[0].append(line[:max(0, page_start - 4)].rstrip())
            columns[1].append(line[page_start:].rstrip())
        else:
            columns[0].append(line.rstrip())
    # The register list is authoritative for page membership and order.
    page = 0
    wanted = [[] for _ in columns]
    for entry in layout_entries:
        if not entry[0]:
            if entry[1][0] == 'page':
                page += 1
            continue
        wanted[min(page, len(wanted) - 1)].append(entry)
    selectable = [[] for _ in columns]
    for col, entries in enumerate(wanted):
        start = 0
        for entry in entries:
            regs, label, *_ = entry
            reg_str = (f'{regs[0]}-{regs[-1]}' if len(regs) > 1
                       and regs == tuple(range(regs[0], regs[-1] + 1))
                       else ','.join(map(str, regs)))
            for idx in range(start, len(columns[col])):
                line = columns[col][idx]
                # Match by label, not by register text: multi-register
                # groups may be rendered with a shortened or clipped range.
                # Require a complete whitespace-delimited label and retain
                # the order from registers.txt.
                if re.search(r'(?<!\S)' + re.escape(label) + r'(?=\s{2,})', line):
                    selectable[col].append((idx, entry))
                    start = idx + 1
                    break
    return columns, selectable


class RegisterDetails(ModalScreen):
    BINDINGS = [Binding('w', 'write', 'Schreiben'), Binding('g', 'device_select', 'Gerät'), Binding('k', 'device_config', 'Konfiguration'), Binding('q', 'dismiss', 'Zurück'), Binding('escape', 'dismiss', 'Zurück')]
    CSS = '''
    RegisterDetails { align: center middle; background: $background 70%; }
    #detail { width: 76; max-width: 95%; height: auto; max-height: 90%;
              border: round $accent; padding: 1 2; background: $panel; }
    '''

    def __init__(self, entry, display_line):
        super().__init__()
        self.entry = entry
        self.display_line = display_line

    def compose(self) -> ComposeResult:
        regs, label, formula, unit, typ = self.entry
        dtype, wire = classify(self.entry)
        register_list = ', '.join(map(str, regs))
        access = 'Nur Lesen' if typ == 'I' else 'Lesen / Schreiben (Einzelregister aus Gruppe)' if typ == 'H' and len(regs) > 1 else 'Lesen / Schreiben'
        content = (
            f'[bold]Registerdetails[/bold]\n\n'
            f'Bezeichnung:    {escape(label)}\n'
            f'Register:       {register_list}\n'
            f'Registertyp:    {typ} – {access}\n'
            f'Datentyp:      {dtype}\n'
            f'Übertragung:    {wire}\n'
            f'Interpretation: {factor_description(formula)}\n'
            f'Einheit/Legende: {escape(unit or "–")}\n\n'
            f'[bold]Aktuelle Anzeige:[/bold]\n{self.display_line.strip()}\n\n'
            f'[dim]W: Schreibdialog öffnen (C/H); I nur lesbar.\n'
            f'G: anderes Gerät  K: Geräteverwaltung\n'
            f'q oder Esc: zurück zur Übersicht[/dim]'
        )
        yield Static(content, id='detail', markup=True)

    def action_device_select(self):
        self.dismiss()
        self.app.call_after_refresh(self.app.action_device_select)

    def action_device_config(self):
        self.dismiss()
        self.app.call_after_refresh(self.app.action_device_config)

    def action_write(self):
        # Detailfenster schließen, dann den bereits bewährten Schreibdialog starten.
        # Die zentrale action_write() prüft Typ und Datenformat.
        if self.entry[4] == 'I':
            self.notify('Input-Register sind nur lesbar.', severity='warning')
            return
        self.dismiss()
        self.app.call_after_refresh(self.app.action_write)


class WriteConfig(ModalScreen):
    """Experimental, explicitly confirmed ASCII write for one C parameter."""
    BINDINGS = [Binding('escape', 'cancel', 'Abbrechen'), Binding('q', 'cancel', 'Abbrechen')]
    CSS = """
    WriteConfig { align: center middle; background: $background 75%; }
    #writebox { width: 85; max-width: 95%; height: auto; max-height: 90%;
                border: round $warning; background: $panel; padding: 1 2; }
    #writebox Input { margin: 1 0; }
    #writebox Button { margin-right: 2; }
    #write_status { height: auto; min-height: 3; }
    """

    def __init__(self, address, param_id, label, old_value):
        super().__init__()
        self.address = address
        self.param_id = param_id
        self.param_label = label
        self.old_value = old_value
        self.pending = None
        self.running = False
        self.readback = None

    def compose(self):
        from textual.containers import Vertical, Horizontal
        with Vertical(id='writebox'):
            yield Label(f'Konfigurationsparameter {self.param_id} – {self.param_label} (ASCII)')
            yield Label(f'Aktueller Wert: {self.old_value!r}')
            yield Label('ACHTUNG: Experimenteller Schreibzugriff. Statusparameter können schreibgeschützt sein; Netzwerkverbindung kann abbrechen!')
            yield Input(value=self.old_value, placeholder='Neuer ASCII-Wert', id='new_value')
            yield Static('Neuen Wert eingeben und „Prüfen“ wählen.', id='write_status')
            with Horizontal():
                yield Button('Prüfen', id='check', variant='primary')
                yield Button('Abbrechen', id='cancel')

    def action_cancel(self):
        if not self.running:
            self.dismiss(self.readback)

    def on_button_pressed(self, event):
        if event.button.id == 'cancel':
            self.action_cancel()
        elif event.button.id == 'check':
            self.check()
        elif event.button.id == 'confirm':
            asyncio.create_task(self.perform_write())

    def on_input_changed(self, event: Input.Changed):
        self.pending = None
        for button in self.query('#confirm'):
            button.remove()

    def check(self):
        value = self.query_one('#new_value', Input).value.strip()
        try:
            raw = value.encode('ascii', errors='strict')
        except UnicodeEncodeError:
            self.query_one('#write_status', Static).update('FEHLER: Nur ASCII-Zeichen erlaubt.')
            return
        if not raw or len(raw) > 128 or any(b < 32 or b > 126 for b in raw):
            self.query_one('#write_status', Static).update('FEHLER: 1–128 druckbare ASCII-Bytes erforderlich.')
            return
        if value == self.old_value:
            self.query_one('#write_status', Static).update('Keine Änderung: Neuer und bisheriger Wert sind identisch.')
            return
        self.pending = raw
        self.query_one('#write_status', Static).update(
            f'ALT: {self.old_value!r}\nNEU: {value!r}\n'
            f'ASCII-Bytes: {raw.hex(" ")}\n'
            'Schreibzugriff auf Hardware! Mit „JA, schreiben“ ausdrücklich bestätigen.')
        if not self.query('#confirm'):
            # Textual widgets do not implement insert_after(); mount on the parent.
            check_button = self.query_one('#check', Button)
            check_button.parent.mount(
                Button('JA, schreiben', id='confirm', variant='error'),
                after=check_button,
            )

    async def perform_write(self):
        if self.running or self.pending is None:
            return
        self.running = True
        for button in self.query(Button):
            button.disabled = True
        status = self.query_one('#write_status', Static)
        status.update('Verbinde über BLE und sende Growatt 0x18 ... bitte warten.')
        import protocol as proto
        try:
            # Same authenticated BLE session for SET and QUERY.
            async with core.NoahBle(self.address) as noah:
                try:
                    reply = await core.receive_reply(noah, proto.set_request([(self.param_id, self.pending)]),
                                                     12, False, expected_func=proto.FUNC_SET)
                    ack = proto.parse_set_reply(reply)
                    ack_message = f'Growatt SET-Antwort: Status {ack}'
                except (TimeoutError, asyncio.TimeoutError) as exc:
                    ack = None
                    ack_message = f'Keine SET-Antwort ({exc}); Wirkung ungewiss'
                status.update(ack_message + f'\nLese Parameter {self.param_id} zur Kontrolle ...')
                data = await core.receive_reply(noah, proto.query_request([self.param_id]),
                                                12, False, expected_func=proto.FUNC_QUERY)
                query_status, values = proto.parse_query_reply(data)
            actual = values.get(self.param_id) if query_status == 0 else None
            if actual is not None:
                self.readback = ("C", self.param_id, actual)
            if actual is None:
                result = f'Rücklesen fehlgeschlagen (QUERY-Status {query_status}). Änderung NICHT verifiziert.'
            else:
                text = actual.decode('ascii', errors='backslashreplace')
                result = f'Rückgelesen: {text!r}\n' + (
                    'VERIFIZIERT: neuer ASCII-Wert gespeichert.' if actual == self.pending
                    else 'ABWEICHUNG: neuer Wert nicht gespeichert bzw. bereits überschrieben.')
            status.update(ack_message + '\n' + result + '\nEsc oder q: zurück; Tabellenzeile wird aktualisiert.')
        except Exception as exc:
            status.update(f'BLE-Fehler: {exc}\nSchreibstatus unbekannt. Bitte vor Wiederholung rücklesen.')
        finally:
            self.running = False
            self.query_one('#cancel', Button).disabled = False
            self.query_one('#cancel', Button).label = 'Schließen'

class WriteHolding(ModalScreen):
    """Confirmed FC06 write to ONE Holding register, with read-before/write/read-back."""
    BINDINGS = [Binding('escape', 'cancel', 'Abbrechen'), Binding('q', 'cancel', 'Abbrechen')]
    CSS = """
    WriteHolding { align: center middle; background: $background 75%; }
    #hbox { width: 88; max-width: 95%; height: auto; max-height: 90%;
            border: round $warning; background: $panel; padding: 1 2; }
    #hbox Input { margin: 1 0; }
    #hbox Button { margin-right: 2; }
    #hstatus { height: auto; min-height: 3; }
    """

    def __init__(self, address, entry):
        super().__init__()
        self.address = address
        self.entry = entry
        self.pending = None
        self.current = None
        self.running = False
        self.readback = None
        self.selected_register = entry[0][0]

    def compose(self):
        from textual.containers import Vertical, Horizontal
        regs, label, formula, unit, typ = self.entry
        with Vertical(id='hbox'):
            yield Label(f'Holding-Register {", ".join(map(str, regs))} – {label}')
            if len(regs) > 1:
                yield Label('Registergruppe: Es wird ausschließlich EIN ausgewähltes Register geschrieben.')
                yield Input(value=str(regs[0]), placeholder='Registeradresse aus der Gruppe', id='hreg')
            yield Label(f'Format: {"HH:MM (TIME)" if formula == "TIME" and len(regs) == 1 else "16-Bit-Rohwert (0–65535), auch bei ASCII-Gruppen"}')
            yield Label('Experimenteller FC06-Schreibzugriff! Gerätefunktionen können sich sofort ändern.')
            yield Label('Der aktuelle Rohwert wird vor dem Schreiben erneut ausgelesen.')
            yield Input(placeholder='HH:MM' if formula == 'TIME' else 'Dezimalzahl oder 0x...', id='hvalue')
            yield Static('Neuen Wert eingeben und „Prüfen“ wählen.', id='hstatus')
            with Horizontal():
                yield Button('Prüfen', id='hcheck', variant='primary')
                yield Button('Abbrechen', id='hcancel')

    def action_cancel(self):
        if not self.running:
            self.dismiss(self.readback)

    def on_button_pressed(self, event):
        if event.button.id == 'hcancel':
            self.action_cancel()
        elif event.button.id == 'hcheck':
            asyncio.create_task(self.check())
        elif event.button.id == 'hconfirm':
            asyncio.create_task(self.perform_write())

    def on_input_changed(self, event: Input.Changed):
        self.pending = None
        self.current = None
        for button in self.query('#hconfirm'):
            button.remove()

    def parse_value(self, text):
        formula = self.entry[2]
        if formula == 'TIME' and len(self.entry[0]) == 1:
            m = re.fullmatch(r'(\d{1,2}):(\d{2})\s*[hH]?', text)
            if not m:
                raise ValueError('TIME benötigt HH:MM, beispielsweise 08:30.')
            h, minute = map(int, m.groups())
            if h > 23 or minute > 59:
                raise ValueError('Uhrzeit außerhalb 00:00–23:59.')
            return (h << 8) | minute
        if not re.fullmatch(r'(?:0[xX][0-9a-fA-F]+|[0-9]+)', text):
            raise ValueError('Nur dezimale Rohwerte oder 0x-Hexwerte erlaubt.')
        val = int(text, 16 if text.lower().startswith('0x') else 10)
        if not 0 <= val <= 65535:
            raise ValueError('Rohwert muss zwischen 0 und 65535 liegen.')
        return val

    async def check(self):
        if self.running:
            return
        status = self.query_one('#hstatus', Static)
        try:
            target = self.parse_value(self.query_one('#hvalue', Input).value.strip())
            regs = self.entry[0]
            if len(regs) > 1:
                raw_address = self.query_one('#hreg', Input).value.strip()
                if not re.fullmatch(r'[0-9]+', raw_address) or int(raw_address) not in regs:
                    raise ValueError('Registeradresse muss zu dieser Gruppe gehören: ' + ', '.join(map(str, regs)))
                self.selected_register = int(raw_address)
            else:
                self.selected_register = regs[0]
        except ValueError as exc:
            status.update(f'Eingabefehler: {exc}')
            return
        self.running = True
        self.query_one('#hcheck', Button).disabled = True
        status.update('Lese aktuellen Rohwert über BLE FC03 ...')
        try:
            async with core.NoahBle(self.address) as noah:
                old = await core.read_one(noah, 3, self.selected_register, 12, False)
        except Exception as exc:
            status.update(f'Vorab-Lesen fehlgeschlagen: {exc}. Kein Schreiben möglich.')
            return
        finally:
            self.running = False
            self.query_one('#hcheck', Button).disabled = False
        self.current = old
        self.pending = target
        if self.entry[2] == 'TIME' and len(self.entry[0]) == 1:
            fmt = lambda n: f'{n >> 8:02d}:{n & 255:02d}'
            detail = f'ALT: {fmt(old)} / NEU: {fmt(target)}\n'
        else:
            detail = ''
        status.update(f'Zielregister: {self.selected_register}\n' + detail + f'Rohwert ALT: {old} (0x{old:04X})\n'
                      f'Rohwert NEU: {target} (0x{target:04X})\n'
                      'ACHTUNG: Hardware-Schreibzugriff! „JA, schreiben“ ausdrücklich bestätigen.')
        if not self.query('#hconfirm'):
            check_button = self.query_one('#hcheck', Button)
            check_button.parent.mount(Button('JA, schreiben', id='hconfirm', variant='error'), after=check_button)

    async def perform_write(self):
        if self.running or self.pending is None or self.current is None:
            return
        self.running = True
        for button in self.query(Button):
            button.disabled = True
        status = self.query_one('#hstatus', Static)
        reg, target, old = self.selected_register, self.pending, self.current
        status.update(f'Sende FC06 an Holding-Register {reg} ...')
        import protocol as proto
        try:
            async with core.NoahBle(self.address) as noah:
                try:
                    response = await core.receive_reply(noah, core.modbus_write_frame(reg, target),
                                                        12, False, expected_func=proto.FUNC_PASSTHROUGH)
                    ack_state, ack_text = core.decode_write_ack(response, reg, target)
                    ack_message = f'Schreibantwort [{ack_state}]: {ack_text}'
                except (TimeoutError, asyncio.TimeoutError):
                    ack_message = 'Keine Schreibantwort; Wirkung unbekannt.'
                status.update(ack_message + '\nLese Holding-Register zur Kontrolle ...')
                actual = await core.read_one(noah, 3, reg, 12, False)
            self.readback = ("H", reg, actual)
            if actual == target and actual != old:
                result = 'VERIFIZIERT: neuer Rohwert zurückgelesen.'
            elif actual == target:
                result = 'Sollwert zurückgelesen; Wert war bereits vorher identisch.'
            elif actual == old:
                result = 'UNVERÄNDERT: alter Rohwert zurückgelesen.'
            else:
                result = 'ABWEICHUNG: anderer Rohwert zurückgelesen.'
            status.update(ack_message + f'\nRückgelesen: {actual} (0x{actual:04X})\n'
                          + result + '\nEsc oder q: zurück; Tabellenzeile wird aktualisiert.')
        except Exception as exc:
            status.update(f'BLE-Fehler: {exc}\nSchreibstatus nicht verifiziert. Vor erneutem Versuch rücklesen.')
        finally:
            self.running = False
            self.query_one('#hcancel', Button).disabled = False
            self.query_one('#hcancel', Button).label = 'Schließen'


class RestartDatalogger(ModalScreen):
    """Send the documented Growatt config restart request after confirmation."""
    BINDINGS = [Binding('escape', 'cancel', 'Abbrechen'), Binding('q', 'cancel', 'Abbrechen')]
    CSS = """
    RestartDatalogger { align: center middle; background: $background 75%; }
    #restartbox { width: 78; max-width: 95%; height: auto; border: round $warning;
                  background: $panel; padding: 1 2; }
    #restartbox Button { margin-right: 2; }
    #restart_status { height: auto; min-height: 3; }
    """

    def __init__(self, address):
        super().__init__()
        self.address = address
        self.running = False

    def compose(self):
        from textual.containers import Vertical, Horizontal
        with Vertical(id='restartbox'):
            yield Label('Datenlogger-Neustart über BLE')
            yield Static('ACHTUNG: WLAN/MQTT werden möglicherweise unterbrochen.\n'
                         'Der Neustartbefehl ist experimentell und seine Wirkung nicht abschließend verifiziert.',
                         id='restart_status')
            with Horizontal():
                yield Button('JA, neu starten', id='restart_confirm', variant='error')
                yield Button('Abbrechen', id='restart_cancel')

    def action_cancel(self):
        if not self.running:
            self.dismiss(False)

    def on_button_pressed(self, event):
        if event.button.id == 'restart_cancel':
            self.action_cancel()
        elif event.button.id == 'restart_confirm':
            asyncio.create_task(self.perform_restart())

    async def perform_restart(self):
        if self.running:
            return
        self.running = True
        for button in self.query(Button):
            button.disabled = True
        status = self.query_one('#restart_status', Static)
        status.update('Verbinde über BLE, authentifiziere und sende Neustartbefehl ...')
        import protocol as proto
        try:
            async with core.NoahBle(self.address) as noah:
                # As in core.restart_datalogger: GATT write confirmation,
                # not a guarantee that the datalogger has actually rebooted.
                await noah.client.write_gatt_char(proto.CHAR_UUID, proto.restart_request(), response=True)
            status.update('Neustartbefehl über BLE gesendet (GATT-Schreibvorgang bestätigt).\n'
                          'Neustart/WLAN-MQTT-Verbindung bitte separat prüfen.\n'
                          'q oder Esc: schließen; R: anschließend neu lesen.')
        except Exception as exc:
            status.update(f'Neustartbefehl fehlgeschlagen/ungewiss: {exc}\n'
                          'Bitte Zustand prüfen, bevor der Befehl wiederholt wird.')
        finally:
            self.running = False
            self.query_one('#restart_cancel', Button).disabled = False
            self.query_one('#restart_cancel', Button).label = 'Schließen'


class LiveCapture(io.StringIO):
    """Show the core's progress live while retaining its full table output."""

    def write(self, value):
        written = super().write(value)
        sys.__stdout__.write(value)
        sys.__stdout__.flush()
        return written


def read_args(address, file):
    return argparse.Namespace(address=address, type='input', file=str(file),
                              timeout=12, verbose=False, block_size=16, max_gap=2)


class NoahTUI(App):
    TITLE = 'NOAH BLE – Registerübersicht (Diagnose)'
    CSS = '''
    Screen { layout: vertical; }
    #columns { height: 1fr; width: 100%; }
    .pane { height: 100%; width: 1fr; min-width: 0; overflow-x: hidden; overflow-y: auto; padding: 0; }
    .pane Static { width: 100%; height: auto; }
    #notice { height: 1; padding: 0 1; color: $text-muted; }
    '''
    BINDINGS = [
        Binding('up', 'previous', 'Vorheriges Register', show=False),
        Binding('down', 'next', 'Nächstes Register', show=False),
        Binding('left', 'left_column', 'Linke Spalte', show=False),
        Binding('right', 'right_column', 'Rechte Spalte', show=False),
        Binding('enter', 'details', 'Details'),
        Binding('n', 'restart', 'Datenlogger-Neustart'),
        Binding('g', 'device_select', 'Gerät wählen'),
        Binding('k', 'device_config', 'Geräte verwalten'),
        Binding('r', 'refresh', 'Neu lesen'),
        Binding('q', 'quit', 'Beenden'),
    ]

    def __init__(self, address, file, initial_output=None):
        super().__init__()
        self.initial_output = initial_output
        self.address = address
        self.file = file
        self.layout_entries = core.load_registers(file)
        self.columns = []
        self.rows = []
        self.column = 0
        self.cursors = [0, 0]
        self.busy = False
        self.reload_requested = False
        self.next_menu = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id='columns'):
            with VerticalScroll(classes='pane', id='pane0'):
                yield Static('Warte auf erste BLE-Abfrage ...', id='table0')
            with VerticalScroll(classes='pane', id='pane1'):
                yield Static('', id='table1')
        yield Static('↑↓ Auswahl  ←→ Spalte  Enter: Details und Ändern  G: Gerät  K: Konfiguration  N: Neustart  R: Neu lesen  Q: Ende', id='notice')
        yield Footer()

    async def on_mount(self):
        if self.initial_output is not None:
            try:
                self.apply_output(self.initial_output)
            except Exception as exc:
                self.query_one('#notice', Static).update(f'Fehler bei der Darstellung: {exc} – R erneut versuchen')
            finally:
                self.initial_output = None
        else:
            await self.load_data()

    def apply_output(self, output):
        self.columns, self.rows = collect_columns(output, self.layout_entries)
        self.column = min(self.column, len(self.columns) - 1)
        for i, rows in enumerate(self.rows):
            self.cursors[i] = min(self.cursors[i], max(0, len(rows) - 1))
        self.draw()
        self.query_one('#notice', Static).update(
            f'{sum(map(len, self.rows))} Registerzeilen | ↑↓ Auswahl  ←→ Spalte  Enter: Details und Ändern  G: Gerät  K: Konfiguration  N: Neustart  R: Neu lesen  Q: Ende')

    async def load_data(self):
        if self.busy:
            return
        self.busy = True
        self.query_one('#notice', Static).update('BLE-Abfrage läuft ... bitte warten')
        args = read_args(self.address, self.file)
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                await core.read_list(args)
            self.apply_output(out.getvalue())
        except Exception as exc:
            self.query_one('#notice', Static).update(f'BLE-Fehler: {exc} – R erneut versuchen')
            if not self.columns:
                self.query_one('#table0', Static).update(f'Fehler beim Auslesen: {exc}')
        finally:
            self.busy = False

    def draw(self):
        for col in range(2):
            lines = self.columns[col] if col < len(self.columns) else []
            rows = self.rows[col] if col < len(self.rows) else []
            selected = rows[self.cursors[col]][0] if rows else -1
            rendered = Text()
            for idx, line in enumerate(lines):
                if idx:
                    rendered.append('\n')
                rendered.append(line, style='reverse bold' if idx == selected and col == self.column else '')
            # Prevent Rich/Textual from treating section labels in square
            # brackets as markup, and keep each pane in its own viewport.
            self.query_one(f'#table{col}', Static).update(rendered)
        rows = self.rows[self.column] if self.rows else []
        if rows:
            selected = rows[self.cursors[self.column]][0]
            pane = self.query_one(f'#pane{self.column}', VerticalScroll)
            pane.scroll_to(y=max(0, selected - max(1, pane.size.height // 2)), animate=False)

    def action_previous(self):
        if self.rows and not self.busy and self.rows[self.column]:
            self.cursors[self.column] = (self.cursors[self.column] - 1) % len(self.rows[self.column])
            self.draw()

    def action_next(self):
        if self.rows and not self.busy and self.rows[self.column]:
            self.cursors[self.column] = (self.cursors[self.column] + 1) % len(self.rows[self.column])
            self.draw()

    def action_left_column(self):
        if self.rows and not self.busy and self.column > 0:
            self.column -= 1
            self.draw()

    def action_right_column(self):
        if self.rows and not self.busy and self.column + 1 < len(self.rows):
            self.column += 1
            self.draw()

    def action_details(self):
        if self.rows and not self.busy and self.rows[self.column]:
            line, entry = self.rows[self.column][self.cursors[self.column]]
            self.push_screen(RegisterDetails(entry, self.columns[self.column][line]))

    def on_write_finished(self, result):
        """Use the verified BLE readback; never display the unverified target."""
        if result is not None:
            asyncio.create_task(self.update_written_entry(result))

    async def update_written_entry(self, result):
        typ, changed_reg, actual = result
        if not self.rows or self.busy:
            return
        self.busy = True
        try:
            location = next(((col, idx, entry) for col, rows in enumerate(self.rows)
                             for idx, (_, entry) in enumerate(rows)
                             if entry[4] == typ and changed_reg in entry[0]), None)
            if location is None:
                raise ValueError('Geschriebenes Register nicht in der Tabelle')
            col, idx, entry = location
            regs, label, formula, unit, _ = entry
            if typ == 'C':
                value = actual.decode('ascii', errors='backslashreplace')
                shown_unit = ''
            else:
                # For groups, the single readback cannot reconstruct the displayed
                # value. Fetch exactly the other members, not the whole table.
                words = []
                if len(regs) > 1:
                    async with core.NoahBle(self.address) as noah:
                        for reg in regs:
                            words.append(actual if reg == changed_reg else
                                         await core.read_one(noah, 3, reg, 12, False))
                else:
                    words = [actual]
                value, shown_unit = core.display_value(words, formula, unit)
            line_idx, _ = self.rows[col][idx]
            # Reproduce the core reader's fixed-width renderer exactly.
            # Never infer widths from a previously updated data row.
            header = self.columns[col][0]
            label_start = header.find('Bezeichnung')
            unit_start = header.find('Einheit')
            status_start = header.find('Status', unit_start)
            if min(label_start, unit_start, status_start) < 0:
                raise ValueError('Tabellenspalten nicht erkannt')
            reg_width = label_start - 2
            label_width = max(11, *(len(item[1][1]) for item in self.rows[col]))
            value_start = label_start + label_width + 2
            value_width = unit_start - 2 - value_start
            unit_width = status_start - 2 - unit_start
            if min(reg_width, value_width, unit_width) < 1:
                raise ValueError('Ungültige Spaltenbreiten')
            original = self.columns[col][line_idx]
            reg_text = original[:reg_width].strip()
            # Use the same format string as noah_registers.py.
            # Its status field has no fixed right-padding.
            self.columns[col][line_idx] = (
                f'{reg_text:>{reg_width}}  {label:<{label_width}}  '
                f'{str(value):>{value_width}}  {shown_unit:<{unit_width}}  [OK]'
            )
            self.draw()
            self.query_one('#notice', Static).update(
                f'Register {changed_reg}: Rücklesewert übernommen | Enter: Details und Ändern  R: Alle neu lesen')
        except Exception as exc:
            self.query_one('#notice', Static).update(
                f'Einzelaktualisierung fehlgeschlagen ({exc}). R: Alle neu lesen')
        finally:
            self.busy = False

    def action_write(self):
        """C: ASCII config; H: single raw FC06 write, also within register groups."""
        if not self.rows or self.busy or not self.rows[self.column]:
            return
        line, entry = self.rows[self.column][self.cursors[self.column]]
        regs, label, formula, unit, typ = entry
        if typ == 'H':
            self.push_screen(WriteHolding(self.address, entry), self.on_write_finished)
            return
        if typ != 'C' or len(regs) != 1 or formula != 'ASCII':
            self.query_one('#notice', Static).update(
                'Schreiben nur für H-Register oder einzelne C-Parameter mit ASCII möglich.')
            return
        displayed = self.columns[self.column][line]
        match = re.search(r'(?<!\S)' + re.escape(label) + r'\s{2,}(.+?)\s{2,}\[OK\]\s*$', displayed)
        if not match:
            self.query_one('#notice', Static).update('Wert nicht eindeutig erkannt. Bitte R neu lesen.')
            return
        old = match.group(1).strip()
        if unit and re.search(r'\b\d+\s+[^,]+', unit):
            old = re.sub(r'\s+\([^()]*\)$', '', old)
        self.push_screen(WriteConfig(self.address, regs[0], label, old), self.on_write_finished)

    def action_device_select(self):
        if not self.busy:
            self.next_menu = 'select'
            self.exit()

    def action_device_config(self):
        if not self.busy:
            self.next_menu = 'config'
            self.exit()

    def action_restart(self):
        if not self.busy:
            self.push_screen(RestartDatalogger(self.address))

    def action_refresh(self):
        """Return terminal control to the console for a visible BLE reload."""
        if not self.busy:
            self.reload_requested = True
            self.exit()


def load_devices(path=DEVICE_FILE):
    """The .txt is UTF-8 JSON; preserve BLE addresses exactly as scanned."""
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, list):
        raise ValueError('Gerätedatei muss eine JSON-Liste sein.')
    result = []
    for item in data:
        if not isinstance(item, dict) or not isinstance(item.get('address'), str):
            raise ValueError('Ungültiger Eintrag in der Gerätedatei.')
        address = item['address'].strip()
        if address and address not in [d['address'] for d in result]:
            result.append({'address': address, 'name': str(item.get('name') or 'NOAH')})
    return result


def save_devices(devices, path=DEVICE_FILE):
    # Atomic replacement avoids leaving a truncated device file.
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(devices, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    temporary.replace(path)


class DeviceMenu(App):
    """Select a saved NOAH, or scan/toggle the saved device list."""
    TITLE = 'NOAH BLE – Geräteverwaltung'
    CSS = """
    Screen { align: center middle; }
    #devicebox { width: 95; max-width: 96%; height: auto; max-height: 94%;
                 border: round $accent; padding: 1 2; background: $panel; overflow-y: auto; }
    #devicebox Button { margin: 0 1 1 0; }
    #devicebox Label { margin-bottom: 1; }
    #devices { height: auto; }
    """
    BINDINGS = [Binding('escape', 'quit', 'Abbrechen'), Binding('q', 'quit', 'Beenden')]

    def __init__(self, devices, mode='select'):
        super().__init__()
        self.devices = [dict(x) for x in devices]
        self.mode = mode
        self.found = []
        self.selected = None
        self.scanning = False

    def compose(self):
        with VerticalScroll(id='devicebox'):
            yield Label('NOAH-Geräteverwaltung' if self.mode == 'config' else 'NOAH auswählen')
            yield Static('', id='menu_status')
            yield Vertical(id='devices')
            with Horizontal():
                yield Button('BLE-Suche', id='scan', variant='primary')
                yield Button('Konfiguration' if self.mode == 'select' else 'Geräteauswahl', id='mode')
                yield Button('Speichern' if self.mode == 'config' else 'Beenden', id='save')

    async def on_mount(self):
        await self.refresh_list()
        if self.mode == 'config' and not self.devices:
            self.run_worker(self.scan_devices(), exclusive=True)

    async def refresh_list(self):
        box = self.query_one('#devices', Vertical)
        for child in list(box.children):
            await child.remove()
        if self.mode == 'select':
            self.query_one('#menu_status', Static).update(
                'Gespeichertes Gerät auswählen. Konfiguration öffnet die Geräteliste.' if self.devices
                else 'Noch keine Geräte gespeichert. Bitte Konfiguration öffnen.')
            for idx, item in enumerate(self.devices):
                await box.mount(Button(f"{idx + 1}. {item['name']}  [{item['address']}]", id=f'choose_{idx}'))
        else:
            self.query_one('#menu_status', Static).update(
                'BLE-Suche starten. Gefundene NOAHs anklicken, um sie hinzuzufügen/zu entfernen. Danach Speichern.')
            await box.mount(Label('Gespeicherte Geräte:'))
            for idx, item in enumerate(self.devices):
                await box.mount(Button(f"✓ {item['name']}  [{item['address']}]  (entfernen)", id=f'remove_{idx}'))
            await box.mount(Label('Gefundene Geräte (anklicken zum Hinzufügen):'))
            known = {d['address'] for d in self.devices}
            for idx, (address, name, rssi) in enumerate(self.found):
                if address not in known:
                    await box.mount(Button(f'+ {name}  [{address}]  RSSI {rssi}', id=f'add_{idx}'))
        self.query_one('#mode', Button).label = 'Konfiguration' if self.mode == 'select' else 'Geräteauswahl'
        self.query_one('#save', Button).label = 'Beenden' if self.mode == 'select' else 'Speichern'

    async def scan_devices(self):
        if self.scanning:
            return
        self.scanning = True
        self.query_one('#scan', Button).disabled = True
        self.query_one('#menu_status', Static).update('Suche NOAH-Geräte über BLE (8 Sekunden) ...')
        try:
            self.found = await core.discover_devices(8)
            if self.mode != 'config':
                self.mode = 'config'
            await self.refresh_list()
            if not self.found:
                self.query_one('#menu_status', Static).update('Keine NOAHs gefunden. Suche wiederholen oder gespeichertes Gerät wählen.')
        except Exception as exc:
            self.query_one('#menu_status', Static).update(f'BLE-Suche fehlgeschlagen: {exc}')
        finally:
            self.scanning = False
            self.query_one('#scan', Button).disabled = False

    async def on_button_pressed(self, event: Button.Pressed):
        name = event.button.id or ''
        if name == 'scan':
            self.run_worker(self.scan_devices(), exclusive=True)
        elif name == 'mode':
            self.mode = 'config' if self.mode == 'select' else 'select'
            await self.refresh_list()
        elif name == 'save':
            if self.mode == 'config':
                try:
                    save_devices(self.devices)
                    self.mode = 'select'
                    await self.refresh_list()
                    self.notify(f'{len(self.devices)} Gerät(e) gespeichert.')
                except OSError as exc:
                    self.query_one('#menu_status', Static).update(f'Speichern fehlgeschlagen: {exc}')
            else:
                self.exit()
        elif name.startswith('choose_'):
            self.selected = self.devices[int(name.split('_')[1])]['address']
            self.exit()
        elif name.startswith('remove_'):
            self.devices.pop(int(name.split('_')[1]))
            await self.refresh_list()
        elif name.startswith('add_'):
            address, device_name, _ = self.found[int(name.split('_')[1])]
            if not any(x['address'] == address for x in self.devices):
                self.devices.append({'address': address, 'name': device_name})
            await self.refresh_list()


async def start():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--address', help='BLE-Adresse: direkte Verbindung ohne Geräteauswahl')
    ap.add_argument('--file', default=str(BASE / 'registers.txt'))
    args = ap.parse_args()
    file = Path(args.file)
    if not file.is_file():
        ap.error(f'Nicht gefunden: {file}')
    address = args.address
    menu_mode = 'config' if not DEVICE_FILE.is_file() else 'select'
    while True:
        if not address:
            try:
                devices = load_devices()
            except (ValueError, OSError, json.JSONDecodeError) as exc:
                print(f'Gerätedatei fehlerhaft: {exc}. Öffne Konfiguration.')
                devices = []
                menu_mode = 'config'
            menu = DeviceMenu(devices, menu_mode)
            await menu.run_async()
            if menu.selected is None:
                return
            address = menu.selected
        print(f'\nNOAH {address}: Lese Register – BLE-Diagnose:\n', flush=True)
        capture = LiveCapture()
        try:
            with contextlib.redirect_stdout(capture):
                await core.read_list(read_args(address, file))
        except Exception as exc:
            print(f'\nBLE-Abfrage fehlgeschlagen: {exc}', flush=True)
            # Allow selection of another device rather than aborting the program.
            address = None
            menu_mode = 'select'
            continue
        print('\nRegister vollständig ausgelesen. Öffne interaktive Übersicht ...', flush=True)
        app = NoahTUI(address, file, initial_output=capture.getvalue())
        await app.run_async()
        if app.next_menu:
            address = None
            menu_mode = app.next_menu
            continue
        if not app.reload_requested:
            break
        print('\nAktualisierung angefordert – starte erneute BLE-Abfrage ...', flush=True)


if __name__ == '__main__':
    try:
        asyncio.run(start())
    except KeyboardInterrupt:
        pass
