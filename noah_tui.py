#!/usr/bin/env python3
"""NOAH BLE TUI v0.9: cached timeslot editor, keyboard navigation and direct selection.

Requires textual, bleak, and the unchanged noah_registers.py,
protocol.py, growatt_ble.py and registers.txt in the same directory.
"""
import argparse
import asyncio
import contextlib
import io
import json
import re
import struct
import sys
import time
from datetime import datetime
from pathlib import Path

from rich.text import Text
from rich.markup import escape
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll, Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Static, Input, Button, Label, Select, OptionList
from textual.widgets.option_list import Option

import noah_registers as core

BASE = Path(__file__).resolve().parent
DEVICE_FILE = BASE / 'noah_devices.txt'
SLOT_BASE = 254
SLOT_COUNT = 9
SLOT_FIELDS = 5
SLOT_MARKER = '__timeslot_selector__'
INFO_FILE = BASE / 'register_info.txt'


def load_register_info(path=INFO_FILE):
    """Read plain-text sections [I:2], [H:256], [C:10] (UTF-8)."""
    result = {}
    current = None
    if not path.is_file():
        return result
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        header = re.fullmatch(r'\s*\[([IHCihc]):(\d+)\]\s*', line)
        if header:
            current = (header.group(1).upper(), int(header.group(2)))
            result[current] = []
        elif current is not None:
            result[current].append(line)
    return {key: '\n'.join(lines).strip() for key, lines in result.items()}



def factor_description(formula):
    if isinstance(formula, str):
        return formula
    if isinstance(formula, tuple):
        if formula[0] == 'COMB_ASCII':
            return 'Byte-Kombination ' + ''.join(formula[1]) + ' + ASCII ohne ' + repr(formula[2])
        if formula[0] == 'COMB':
            return 'Byte-Kombination ' + ''.join(formula[1])
        return f'Bitmaske {formula[1]}–{formula[2]}'
    return 'Numerischer 16-Bit-Wert (mit Umrechnung)' if callable(formula) else 'Numerischer 16-Bit-Wert'


def classify(entry):
    regs, label, formula, unit, typ = entry
    if typ in ('I,C', 'H,C'):
        return 'Kombinierte Modbus-Bytes und ASCII-Config', 'Modbus 0x17 plus Growatt Config 0x19; nur lesende Gesamtansicht'
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
    # Keep wrapped configuration rows as one selectable logical row.
    for col, lines in enumerate(columns):
        merged = []
        for line in lines:
            if (merged and line.startswith(' ' * (lines[0].find('Bezeichnung') + 11))
                    and line.strip() and re.match(r'^\s*(?:102|122|123)\s{2,}', merged[-1])
                    and not re.search(r'\[[^\]]+\]\s*$', merged[-1])):
                merged[-1] += '\n' + line
            else:
                merged.append(line)
        columns[col] = merged
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


def navigate_buttons(screen, event):
    """Arrow-key focus movement in dialogs; do not intercept editable inputs."""
    if isinstance(screen.focused, Input):
        return False
    if event.key in ('up', 'left'):
        screen.focus_previous()
    elif event.key in ('down', 'right'):
        screen.focus_next()
    else:
        return False
    event.stop()
    event.prevent_default()
    return True


class TimeslotSelect(ModalScreen):
    """Select one of the nine fixed five-register scheduling slots."""
    BINDINGS = [Binding('escape', 'cancel', 'Zurück'), Binding('q', 'cancel', 'Zurück')]
    CSS = """
    TimeslotSelect { align: center middle; background: $background 70%; }
    #slotbox { width: 52; max-width: 95%; height: auto; border: round $accent;
               background: $panel; padding: 1 2; }
    #slotbox Button { margin: 0 1 1 0; }
    """

    def __init__(self, current):
        super().__init__()
        self.current = current

    def compose(self):
        with Vertical(id='slotbox'):
            yield Label(f'Timeslot wählen (aktuell {self.current}/9)')
            for slot in range(1, SLOT_COUNT + 1):
                yield Button(f'Slot {slot}  (Register {SLOT_BASE + (slot-1)*5}–{SLOT_BASE + (slot-1)*5 + 4})',
                             id=f'slot_{slot}', variant='primary' if slot == self.current else 'default')
            yield Button('Abbrechen', id='cancel')

    def on_mount(self):
        self.query_one(f'#slot_{self.current}', Button).focus()

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == 'cancel':
            self.dismiss(None)
        elif (event.button.id or '').startswith('slot_'):
            self.dismiss(int(event.button.id.split('_')[1]))

    def on_key(self, event):
        if event.key in '123456789' and len(event.key) == 1:
            self.dismiss(int(event.key))
            event.stop()
            event.prevent_default()
        else:
            navigate_buttons(self, event)

    def action_cancel(self):
        self.dismiss(None)


class RegisterDetails(ModalScreen):
    BINDINGS = [Binding('w', 'write', 'Schreiben', show=False), Binding('g', 'device_select', 'Gerät'), Binding('k', 'device_config', 'Konfiguration'), Binding('q', 'dismiss', 'Zurück'), Binding('escape', 'dismiss', 'Zurück')]
    CSS = '''
    RegisterDetails { align: center middle; background: $background 70%; }
    #detail { width: 88; max-width: 95%; height: auto; max-height: 90%;
              border: round $accent; padding: 1 2; background: $panel; overflow-y: auto; }
    '''

    def __init__(self, entry, display_line, register_info=None):
        super().__init__()
        self.entry = entry
        self.display_line = display_line
        self.register_info = register_info or {}

    def compose(self) -> ComposeResult:
        regs, label, formula, unit, typ = self.entry
        dtype, wire = classify(self.entry)
        register_list = ', '.join(map(str, regs))
        access = 'Nur Lesen' if typ in ('I', 'I,C', 'H,C') else 'Lesen / Schreiben (Einzelregister aus Gruppe)' if typ == 'H' and len(regs) > 1 else 'Lesen / Schreiben'
        sections = []
        for idx, reg in enumerate(regs):
            reg_type = (typ.split(',')[0] if idx < len(regs) - 1 else 'C') if ',' in typ else typ
            note = self.register_info.get((reg_type, reg), '')
            if note:
                sections.append(f'[bold]Register {reg}:[/bold]\n{escape(note)}')
        notes = ('\n\n[bold]Zusätzliche Registerinformationen:[/bold]\n\n'
                 + '\n\n'.join(sections)) if sections else ''
        write_hint = 'W: Schreibdialog öffnen\n' if typ in ('C', 'H') else ''
        content = (
            f'[bold]Registerdetails[/bold]\n\n'
            f'Bezeichnung:    {escape(label)}\n'
            f'Register:       {register_list}\n'
            f'Registertyp:    {typ} – {access}\n'
            f'Datentyp:      {dtype}\n'
            f'Übertragung:    {wire}\n'
            f'Interpretation: {factor_description(formula)}\n'
            f'Einheit/Legende: {escape(unit or "–")}\n\n'
            f'[bold]Aktuelle Anzeige:[/bold]\n{escape(self.display_line.strip())}'
            f'{notes}\n\n'
            f'[dim]{write_hint}'
            f'G: anderes Gerät  K: Geräteverwaltung\n'
            f'q oder Esc: zurück zur Übersicht[/dim]'
        )
        with VerticalScroll(id='detail'):
            yield Static(content, markup=True)

    def check_action(self, action, parameters):
        if action == 'write' and self.entry[4] in ('I', 'I,C', 'H,C'):
            return False
        return True

    def action_device_select(self):
        self.dismiss()
        self.app.call_after_refresh(self.app.action_device_select)

    def action_device_config(self):
        self.dismiss()
        self.app.call_after_refresh(self.app.action_device_config)

    def action_write(self):
        # Detailfenster schließen, dann den bereits bewährten Schreibdialog starten.
        # Die zentrale action_write() prüft Typ und Datenformat.
        if self.entry[4] in ('I', 'I,C', 'H,C'):
            self.notify('Input-Register sind nur lesbar.', severity='warning')
            return
        self.dismiss()
        self.app.call_after_refresh(self.app.action_write)


class AccessoryDeleteSelect(ModalScreen):
    """Select an individual live WLAN entry or explicitly delete all."""
    BINDINGS = [Binding('escape', 'cancel', 'Abbrechen')]
    CSS = """
    AccessoryDeleteSelect { align: center middle; background: $background 75%; }
    #deletebox { width: 82; max-width: 95%; height: auto; max-height: 85%;
                 border: round $accent; background: $panel; padding: 1 2; }
    #delete_entries { height: auto; max-height: 15; min-height: 3; }
    #deletebox Horizontal { height: 3; margin-top: 1; }
    """

    def __init__(self, entries):
        super().__init__()
        self.entries = entries

    def compose(self):
        with Vertical(id='deletebox'):
            yield Label('Welchen WLAN-Eintrag möchtest du entkoppeln?')
            yield OptionList(*[Option(label, id=number) for number, label in self.entries],
                             id='delete_entries')
            with Horizontal():
                yield Button('Auswahl löschen', id='delete_selected', variant='primary')
                yield Button('Alle löschen', id='delete_all')
                yield Button('Abbrechen', id='delete_cancel')

    def on_mount(self):
        listing = self.query_one('#delete_entries', OptionList)
        listing.highlighted = 0
        listing.focus()

    def on_button_pressed(self, event):
        event.stop()
        if event.button.id == 'delete_cancel':
            self.dismiss(None)
        elif event.button.id == 'delete_all':
            self.dismiss([number for number, _ in self.entries])
        elif event.button.id == 'delete_selected':
            listing = self.query_one('#delete_entries', OptionList)
            if listing.highlighted is not None:
                self.dismiss([listing.get_option_at_index(listing.highlighted).id])

    def action_cancel(self):
        self.dismiss(None)


class AccessorySearch(ModalScreen):
    """Experimental WLAN discovery via BLE; C123 reset before discovery, with verified readback."""
    BINDINGS = [Binding('escape', 'close', 'Schließen')]
    CSS = """
    AccessorySearch { align: center middle; background: $background 75%; }
    #accessorybox { width: 85; max-width: 95%; height: 90%; border: round $accent; background: $panel; padding: 1 2; }
    #accessorybox Horizontal { height: 3; }
    #accessorybox Input { width: 1fr; }
    #accessory_manual_label { margin-top: 1; height: 1; }
    #accessory_cycle_row { margin-top: 1; }
    #accessory_cycle_row Label { height: 3; content-align: left middle; }
    #accessorybox #accessory_cycles { width: 12; min-width: 12; padding: 0 1; }
    #accessory_cycle_row Button { min-width: 5; width: 5; margin: 0 1; }

    #accessory_status { height: auto; min-height: 3; }
    #accessory_results { height: 1fr; min-height: 4; border: solid $accent; }
    #accessory_list { height: 1fr; }
    #accessory_access { height: 3; }
    #accessory_log { height: 1fr; }
    """
    TYPES = {
        'Shelly Pro 3EM': ('_http._tcp.', 2),
        'Shelly 3EM': ('_http._tcp.', 1),
        'Shelly Pro 3EM 3CT63': ('_http._tcp.', 8),
        'Shelly Pro EM 50': ('_http._tcp.', 7),
        'Shelly Pro 1PM': ('_http._tcp.', 6),
        'Everhome Ecotracker': ('_everhome._tcp.', 3),
        'Sparky P1': ('_chargee_p1._tcp.', 50),
        'Benutzerdefiniert': None,
    }
    DEFAULT_CYCLES = 2
    INTERVAL = 5

    def __init__(self, address):
        super().__init__()
        self.address = address
        self.running = False
        self.results = {}
        self.scan_log = []
        self.readbacks = {}

    def compose(self):
        with Vertical(id='accessorybox'):
            yield Label('Gekoppelte Geräte – experimentelle WLAN-Suche')
            yield Select([(name, name) for name in self.TYPES], value='Shelly Pro 3EM', allow_blank=False, id='accessory_type')
            yield Label('Optional: mDNS-Dienst / Typ-ID (nur Benutzerdefiniert)', id='accessory_manual_label')
            with Horizontal():
                yield Input(value='_http._tcp.', placeholder='mDNS-Dienst', id='accessory_service')
                yield Input(value='2', placeholder='Typ-ID', id='accessory_model')
            with Horizontal(id='accessory_cycle_row'):
                yield Label('Mindestzyklen (2–12, je 5 s): ')
                yield Button('−', id='accessory_cycle_minus')
                yield Input(value=str(self.DEFAULT_CYCLES), id='accessory_cycles')
                yield Button('+', id='accessory_cycle_plus')
            with Horizontal():
                yield Button('Suche starten', id='accessory_start', variant='primary')
                yield Button('Gerät entkoppeln', id='accessory_delete')
                yield Button('C123 leeren', id='accessory_clear')
                yield Button('Schließen', id='accessory_close')
            yield Static('Bereit. C123 wird vor der Suche zurückgesetzt.', id='accessory_status', markup=False)
            yield Label('MAC-Adressen (Startwert / neu beobachtet)')
            with Vertical(id='accessory_results'):
                yield OptionList(id='accessory_list')
            yield Label('Kopplungsart')
            yield Select([('Messen und regeln', 0), ('Nur messen', 1)], value=0, allow_blank=False, id='accessory_access')
            yield Button('Gerät koppeln', id='accessory_pair', disabled=True)
            yield Label('Abfrageprotokoll')
            with VerticalScroll(id='accessory_log'):
                yield Static('', id='accessory_trace', markup=False)

    def action_close(self):
        if not self.running:
            self.dismiss(dict(self.readbacks))

    def on_button_pressed(self, event):
        if event.button.id in ('accessory_cycle_minus', 'accessory_cycle_plus'):
            if not self.running:
                field = self.query_one('#accessory_cycles', Input)
                try:
                    value = int(field.value)
                except ValueError:
                    value = self.DEFAULT_CYCLES
                field.value = str(max(2, min(12, value + (1 if event.button.id.endswith('plus') else -1))))
            return
        if event.button.id == 'accessory_close':
            self.action_close()
        elif event.button.id in ('accessory_start', 'accessory_delete', 'accessory_pair', 'accessory_clear') and not self.running:
            listing = self.query_one('#accessory_list', OptionList)
            selected = int(listing.get_option_at_index(listing.highlighted).id) if listing.highlighted is not None else None
            access = self.query_one('#accessory_access', Select).value
            if event.button.id == 'accessory_pair' and selected is None:
                return
            cycles = self.DEFAULT_CYCLES
            if event.button.id == 'accessory_start':
                try:
                    cycles = int(self.query_one('#accessory_cycles', Input).value)
                    if not 2 <= cycles <= 12:
                        raise ValueError
                except ValueError:
                    self.query_one('#accessory_status', Static).update('Bitte 2 bis 12 Abfragezyklen eingeben.')
                    return
            self.running = True
            self.query_one('#accessory_start', Button).disabled = True
            self.query_one('#accessory_close', Button).disabled = True
            self.query_one('#accessory_type', Select).disabled = True
            for ident in ('accessory_delete', 'accessory_clear', 'accessory_service', 'accessory_model', 'accessory_cycles', 'accessory_cycle_minus', 'accessory_cycle_plus', 'accessory_pair', 'accessory_list', 'accessory_access'):
                self.query_one('#' + ident).disabled = True
            self.run_worker(self.search(delete=event.button.id == 'accessory_delete', pair=selected if event.button.id == 'accessory_pair' else None, access=access, clear=event.button.id == 'accessory_clear', cycles=cycles), exclusive=True)

    def trace(self, message):
        self.scan_log.append(f'{datetime.now():%H:%M:%S}  {message}')
        self.query_one('#accessory_trace', Static).update('\n'.join(self.scan_log))
        self.query_one('#accessory_log', VerticalScroll).scroll_end(animate=False)

    def show_results(self):
        listing = self.query_one('#accessory_list', OptionList)
        previous = (listing.get_option_at_index(listing.highlighted).id
                    if listing.highlighted is not None else None)
        listing.clear_options()
        listing.add_options([Option(f'{n.to_bytes(6, "big").hex(":").upper()}    {stamp}', id=str(n))
                             for n, stamp in self.results.items()])
        if self.results:
            ids = [str(n) for n in self.results]
            listing.highlighted = ids.index(previous) if previous in ids else 0

    async def search(self, delete=False, pair=None, access=0, clear=False, cycles=2):
        import protocol as proto
        status = self.query_one('#accessory_status', Static)
        if pair is None:
            self.results.clear()
            self.show_results()
        self.scan_log.clear()
        self.query_one('#accessory_trace', Static).update('')
        async def read(noah, reg):
            reply = await core.receive_reply(noah, proto.query_request([reg]), 12, False, expected_func=proto.FUNC_QUERY)
            code, values = proto.parse_query_reply(reply)
            if code != 0 or reg not in values:
                raise ValueError(f'C{reg}: QUERY-Status {code}, Wert fehlt oder ungültig')
            self.readbacks[('C', reg)] = values[reg]
            return values[reg]
        async def meter_readback(noah):
            for reg in (310, 311, 313):
                try:
                    self.readbacks[('H', reg)] = await core.read_one(noah, 3, reg, 12, False)
                    self.trace(f'H{reg}: {self.readbacks[("H", reg)]}')
                except Exception as exc:
                    self.trace(f'H{reg} Lesefehler: {exc}')
        async def reset_list(noah):
            self.trace('C123 SET: ASCII 0 (hex 30)')
            self.readbacks.pop(('C', 123), None)
            reply = await core.receive_reply(noah, proto.set_request([(123, b'0')]), 12, False, expected_func=proto.FUNC_SET)
            ack = proto.parse_set_reply(reply)
            self.trace(f'SET-Status: {ack}')
            raw = await read(noah, 123)
            self.trace(f'C123 nachher: {raw!r}')
            return ack, raw
        try:
            if clear:
                status.update('Experiment: C123 auf ASCII 0 setzen ...')
                async with core.NoahBle(self.address) as noah:
                    self.trace(f'C123 vorher: {await read(noah, 123)!r}')
                    ack, raw = await reset_list(noah)
                    if ack == 0 and raw.strip(b' \r\n\t\x00') == b'0':
                        status.update('C123 auf 0 gesetzt und rückgelesen.')
                    else:
                        status.update(f'C123 Reset nicht bestätigt | SET: {ack} | QUERY: {raw!r}')
                return
            model = self.TYPES[self.query_one('#accessory_type', Select).value]
            if model is None:
                model = (self.query_one('#accessory_service', Input).value.strip(), int(self.query_one('#accessory_model', Input).value))
            service, kind = model
            if not re.fullmatch(r'[A-Za-z0-9_.-]{1,17}', service) or not 0 <= kind <= 65535:
                raise ValueError('Ungültiger mDNS-Dienst oder Typ-ID')
            command = f'ADD:111-7-{service},{kind}'.encode('ascii')
            status.update('Verbinde über BLE und lese Ausgangswerte ...')
            async with core.NoahBle(self.address) as noah:
                before = await read(noah, 122)
                self.trace(f'C122 vorher: {before!r}')
                text = before.decode('ascii').strip(' \r\n\t\x00')
                if pair is not None:
                    if pair not in self.results:
                        raise ValueError('Bitte eine MAC-Adresse auswählen.')
                    if not text.startswith('DEV:'):
                        raise ValueError(f'Unbekannte Geräteliste: {text}')
                    pending = [number for number, mode, state, _, _ in core.parse_wlan_entries(before)
                               if mode == '7' and state == '0']
                    if len(pending) != 1:
                        raise ValueError('Kein eindeutiger Sucheintrag (Status 0); bitte erneut suchen.')
                    if access not in (0, 1):
                        raise ValueError('Bitte eine Kopplungsart auswählen.')
                    mode = 'Messen und regeln' if access == 0 else 'Nur messen'
                    cmd = f'CRL:{pending[0]}-7-add:sn,{pair}|access,{access}'.encode('ascii')
                    self.readbacks.pop(('C', 122), None)
                    self.trace(f'C122 SET: {cmd.decode()}')
                    reply = await core.receive_reply(noah, proto.set_request([(122, cmd)]), 12, False, expected_func=proto.FUNC_SET)
                    ack = proto.parse_set_reply(reply)
                    self.trace(f'SET-Status: {ack}')
                    if ack != 0:
                        raise ValueError(f'Kopplungsbefehl abgelehnt: {ack}')
                    for _ in range(6):
                        await asyncio.sleep(2)
                        raw = await read(noah, 122)
                        self.trace(f'C122 nachher: {raw!r}')
                        for entry in raw.decode('ascii').strip(' \r\n\t\x00').removeprefix('DEV:').split('&'):
                            match = re.fullmatch(r'(\d+)-7-3,(\d+),([^,]+)', entry)
                            if match and int(match.group(2)) == pair:
                                status.update(f'Gerät gekoppelt | IP: {match.group(3)} | {mode} angefordert (nicht rückbestätigt)')
                                await meter_readback(noah)
                                return
                    status.update('Kopplungsbefehl angenommen; Status 3 noch nicht bestätigt.')
                    return
                if delete:
                    if not text.startswith('DEV:'):
                        raise ValueError(f'Unbekannte Geräteliste: {text}')
                    entries = {}
                    for number, mode, state, name, ip in sorted(core.parse_wlan_entries(before), key=lambda e: int(e[0])):
                        if mode != '7' or state == '5':
                            continue
                        name = core.wlan_display_name(mode, name)
                        state_label = {'0': 'ungekoppelt', '3': 'gekoppelt'}.get(state, f'Status {state}')
                        entries[number] = f'{number}  {name or "–"}  {ip or "–"}  {state_label}'
                    if not entries:
                        status.update('Keine aktiven WLAN-Einträge vorhanden.')
                        return
                    selected_entries = await self.app.push_screen_wait(AccessoryDeleteSelect(list(entries.items())))
                    if not selected_entries:
                        status.update('Entkopplung abgebrochen.')
                        return
                    # Re-read after the selection; do not delete a changed/reused entry.
                    current = await read(noah, 122)
                    if current != before:
                        status.update('Geräteliste hat sich geändert. Bitte erneut auswählen.')
                        return
                    accepted = 0
                    for number in selected_entries:
                        cmd = f'DEL:{number}-7-{service},{kind}'.encode('ascii')
                        self.readbacks.pop(('C', 122), None)
                        self.trace(f'C122 SET: {cmd.decode()}')
                        reply = await core.receive_reply(noah, proto.set_request([(122, cmd)]), 12, False, expected_func=proto.FUNC_SET)
                        ack = proto.parse_set_reply(reply)
                        self.trace(f'SET-Status: {ack}')
                        accepted += ack == 0
                    confirmed = set()
                    for _ in range(6):
                        await asyncio.sleep(2)
                        raw = await read(noah, 122)
                        self.trace(f'C122 nachher: {raw!r}')
                        if not raw.startswith(b'DEV:'):
                            raise ValueError(f'Unbekannte Geräteliste: {raw!r}')
                        states = {m.group(1): m.group(2) for entry in raw.decode('ascii').strip(' \r\n\t\x00').removeprefix('DEV:').split('&')
                                  if (m := re.fullmatch(r'(\d+)-7-(\d+),.*', entry))}
                        confirmed = {number for number in selected_entries if states.get(number) in (None, '5')}
                        if len(confirmed) == len(selected_entries):
                            break
                    await meter_readback(noah)
                    status.update(f'Entkopplung: {accepted}/{len(selected_entries)} angenommen; {len(confirmed)}/{len(selected_entries)} gelöscht bestätigt.')
                    return
                if text != 'DEV:':
                    self.trace('Hinweis: C122 enthält bereits Einträge; Suche wird trotzdem gestartet.')
                try:
                    ack, raw = await reset_list(noah)
                    if ack != 0 or raw.strip(b' \r\n\t\x00') != b'0':
                        self.trace('C123 Reset nicht bestätigt; vorhandene Startwerte bleiben unbestätigt.')
                except Exception as exc:
                    self.trace(f'C123 Reset fehlgeschlagen: {exc}; lese Startwert erneut.')
                    raw = await read(noah, 123)
                last_value = set(core.config_mac_numbers(raw)) - {0}
                self.results.update({n: 'Startwert (nicht bestätigt)' for n in sorted(last_value)})
                self.show_results()
                self.trace(f'C123 Startwert: {raw!r}')
                self.readbacks.pop(('C', 122), None)
                self.trace(f'C122 SET: {command.decode()}')
                reply = await core.receive_reply(noah, proto.set_request([(122, command)]), 12, False, expected_func=proto.FUNC_SET)
                ack = proto.parse_set_reply(reply)
                self.trace(f'SET-Status: {ack}')
                if ack != 0:
                    raise ValueError(f'Suchbefehl abgelehnt: Status {ack}')
                target = cycles
                start = time.monotonic()
                count = 0
                while count < target:
                    offset = (count + 1) * self.INTERVAL
                    await asyncio.sleep(max(0, start + offset - time.monotonic()))
                    count += 1
                    status.update(f'Suche: {offset}/{target * self.INTERVAL} s | Abfragen: {count} | Treffer: {sum(v.endswith("beobachtet") for v in self.results.values())}')
                    try:
                        raw = await read(noah, 123)
                        value = set(core.config_mac_numbers(raw)) - {0}
                        changed = value != last_value
                        for n in sorted(value - last_value):
                            self.results[n] = datetime.now().strftime('%H:%M:%S') + ' neu beobachtet'
                        last_value = value
                        self.trace(f'C123: {raw!r}' + (' [Wechsel]' if changed else ' [unverändert]'))
                        self.show_results()
                        if count == target and changed and target < 12:
                            target += 1
                            self.trace(f'C123 geändert: verlängere auf {target} Zyklen.')
                        elif count == target and changed:
                            self.trace('Maximale Beobachtungsdauer (12 Zyklen) erreicht.')
                    except Exception as exc:
                        self.trace(f'C123 Lesefehler: {exc}')
                        if count == target and target < 12:
                            target += 1
                            self.trace(f'Letzte Abfrage fehlgeschlagen: verlängere auf {target} Zyklen.')
                self.trace(f'C122 nachher: {await read(noah, 122)!r}')
                if count < 12:
                    self.trace('BLE-Polling beendet; interne mDNS-Suche kann noch laufen.')
                status.update(f'Polling beendet | Abfragen: {count} | Treffer: {sum(v.endswith("beobachtet") for v in self.results.values())}')
        except Exception as exc:
            self.trace(f'Fehler: {exc}')
            status.update(f'Suche beendet: {exc}')
        finally:
            self.running = False
            self.query_one('#accessory_start', Button).disabled = False
            self.query_one('#accessory_close', Button).disabled = False
            self.query_one('#accessory_type', Select).disabled = False
            for ident in ('accessory_delete', 'accessory_clear', 'accessory_service', 'accessory_model', 'accessory_cycles', 'accessory_cycle_minus', 'accessory_cycle_plus', 'accessory_pair', 'accessory_list', 'accessory_access'):
                self.query_one('#' + ident).disabled = False
            self.query_one('#accessory_pair', Button).disabled = not bool(self.results)


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
            yield Static('Neuen Wert eingeben und „Schreiben“ wählen.', id='write_status')
            with Horizontal():
                yield Button('Schreiben', id='check', variant='primary')
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
            'Schreibvorgang wird ausgeführt und anschließend rückgelesen.')
        asyncio.create_task(self.perform_write())

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


def modbus_write_multiple_frame(start, values):
    """Experimental Modbus RTU FC16 (0x10) inside Growatt BLE 0x17."""
    import protocol as proto
    if not values or len(values) > 123:
        raise ValueError('Ungültige Registeranzahl für FC16')
    if not 0 <= start <= 65535 or start + len(values) > 65536:
        raise ValueError('Registeradresse außerhalb 16 Bit')
    if any(not isinstance(v, int) or not 0 <= v <= 65535 for v in values):
        raise ValueError('Ungültiger 16-Bit-Registerwert')
    request = struct.pack('>BBHHB', 1, 0x10, start, len(values), 2 * len(values))
    request += struct.pack('>' + 'H' * len(values), *values)
    request += struct.pack('<H', proto.crc16_modbus(request))
    return proto.build_frame(proto.FUNC_PASSTHROUGH,
                             proto.APP_SERIAL + struct.pack('>H', len(request)) + request)


def decode_write_multiple_ack(data, start, count):
    """Check FC16 RTU response: unit, function, start, quantity, CRC."""
    import protocol as proto
    if len(data) < 10:
        return 'unknown', 'Growatt-Antwort ohne vollständige Seriennummer'
    body = data[10:]
    if len(body) >= 2 and int.from_bytes(body[:2], 'big') == len(body) - 2:
        body = body[2:]
    if len(body) < 5 or body[0] != 1:
        return 'unknown', 'Keine gültige RTU-Antwort: ' + body.hex(' ')
    if proto.crc16_modbus(body[:-2]) != int.from_bytes(body[-2:], 'little'):
        return 'unknown', 'RTU-CRC ungültig: ' + body.hex(' ')
    if body[1] == 0x90 and len(body) == 5:
        return 'error', f'Modbus-Ausnahme 0x{body[2]:02X}'
    if body[1] != 0x10 or len(body) != 8:
        return 'unknown', 'Unerwartete FC16-Antwort: ' + body.hex(' ')
    ack_start, ack_count = struct.unpack('>HH', body[2:6])
    if (ack_start, ack_count) != (start, count):
        return 'unknown', f'Antwort meldet Start {ack_start}, Anzahl {ack_count}'
    return 'ok', f'FC16 bestätigt Start {start}, Anzahl {count} (CRC OK)'


async def read_timeslot_block(noah, base):
    """Read five contiguous holding registers with one FC03 request."""
    import protocol as proto
    reply = await core.receive_reply(noah, core.modbus_read_frame(3, base, SLOT_FIELDS),
                                     12, False, expected_func=proto.FUNC_PASSTHROUGH)
    if len(reply) < 10:
        raise ValueError('FC03-Antwort zu kurz')
    state, words = core.decode_modbus(reply[10:], 3, SLOT_FIELDS)
    if words is None:
        raise ValueError(f'FC03-Rücklesen fehlgeschlagen: {state}')
    return list(words)


class SlotLoading(ModalScreen):
    """Non-interactive progress overlay during a BLE timeslot read."""
    CSS = """
    SlotLoading { align: center middle; background: $background 55%; }
    #slotloadingbox { width: 43; height: 5; border: round $accent;
                      background: $panel; content-align: center middle; }
    """

    def __init__(self, slot):
        super().__init__()
        self.slot = slot

    def compose(self):
        yield Static(f'Timeslot {self.slot}/9 wird gelesen ...', id='slotloadingbox')


class GroupLoading(ModalScreen):
    """Short modal status during targeted F-key BLE refresh."""
    CSS = """
    GroupLoading { align: center middle; background: $background 55%; }
    #grouploadingbox { width: 52; height: 5; border: round $accent;
                       background: $panel; content-align: center middle; }
    """

    def __init__(self, message):
        super().__init__()
        self.message = message

    def compose(self):
        yield Static(self.message, id='grouploadingbox')


class WriteTimeslot(ModalScreen):
    """Edit five slot registers; experimental single FC16 write and FC03 verification."""
    BINDINGS = [Binding('escape', 'close', 'Zurück'), Binding('q', 'close', 'Zurück')]
    CSS = """
    WriteTimeslot { align: center middle; background: $background 75%; }
    #slotedit { width: 86; max-width: 95%; height: auto; max-height: 94%;
                overflow-y: auto; border: round $warning; background: $panel; padding: 1 2; }
    #slotedit Input { margin: 0 0 1 0; }
    #slotedit Button { margin: 1 1 0 0; }
    #slotstatus { height: auto; min-height: 3; }
    """
    LABELS = ('Startzeit (HH:MM)', 'Endzeit (HH:MM)',
              'Betriebsart (0=Load First, 1=Battery First, 2=Smart Self-Use)',
              'Ausgangsleistung (W)', 'Aktiviert (0=Aus, 1=Ein)')

    def __init__(self, address, slot, initial_values=None):
        super().__init__()
        self.address = address
        self.slot = slot
        self.base = SLOT_BASE + (slot - 1) * SLOT_FIELDS
        self.current = None
        self.readback = None
        self.running = False
        self.pending = None
        self.initial_values = list(initial_values) if initial_values is not None else None

    def compose(self):
        with VerticalScroll(id='slotedit'):
            yield Label(f'Timeslot {self.slot}/9 bearbeiten – Holding-Register {self.base}–{self.base + 4}')
            yield Label('Alle fünf Werte werden gemeinsam per FC16 geschrieben und per FC03 kontrolliert.')
            for i, label in enumerate(self.LABELS):
                yield Label(label)
                yield Input(placeholder='Wert wird übernommen ...', id=f'slotvalue_{i}', disabled=True)
            yield Static('Übernehme Werte aus der Registerübersicht ...', id='slotstatus')
            with Horizontal():
                yield Button('Schreiben', id='slotwrite', variant='primary', disabled=True)
                yield Button('Timeslot löschen', id='slotdelete', variant='error', disabled=True)
                yield Button('Abbrechen', id='slotcancel')

    async def on_mount(self):
        if self.initial_values is not None and len(self.initial_values) == SLOT_FIELDS:
            self.set_fields(self.initial_values)
            self.query_one('#slotstatus', Static).update(
                'Werte aus der Tabelle übernommen. Änderungen eingeben und Schreiben wählen.')
        else:
            # Only if table values cannot be reconstructed safely.
            await self.load_values()

    def set_fields(self, values):
        self.current = list(values)
        for i, value in enumerate(values):
            field = self.query_one(f'#slotvalue_{i}', Input)
            field.value = self.format_time(value) if i < 2 else str(value)
            field.disabled = False
        self.query_one('#slotwrite', Button).disabled = False
        self.query_one('#slotdelete', Button).disabled = False

    @staticmethod
    def format_time(raw):
        return f'{raw >> 8:02d}:{raw & 255:02d}'

    async def load_values(self):
        self.running = True
        loading = SlotLoading(self.slot)
        self.app.push_screen(loading)
        await asyncio.sleep(0.05)  # Let Textual paint the progress dialog before BLE starts.
        try:
            async with core.NoahBle(self.address) as noah:
                values = await read_timeslot_block(noah, self.base)
            self.set_fields(values)
            self.query_one('#slotstatus', Static).update('Werte geladen. Änderungen eingeben und Schreiben wählen.')
        except Exception as exc:
            self.query_one('#slotstatus', Static).update(f'BLE-Lesen fehlgeschlagen: {exc}. Keine Schreibfunktion freigegeben.')
        finally:
            loading.dismiss()
            self.running = False

    def parse_fields(self):
        values = []
        for i in range(SLOT_FIELDS):
            value = self.query_one(f'#slotvalue_{i}', Input).value.strip()
            if i < 2:
                match = re.fullmatch(r'(\d{1,2}):(\d{2})', value)
                if not match:
                    raise ValueError(f'{self.LABELS[i]}: Uhrzeit im Format HH:MM erforderlich.')
                h, minute = map(int, match.groups())
                if h > 23 or minute > 59:
                    raise ValueError(f'{self.LABELS[i]}: Uhrzeit außerhalb 00:00–23:59.')
                values.append((h << 8) | minute)
            else:
                if not re.fullmatch(r'\d+', value):
                    raise ValueError(f'{self.LABELS[i]}: positive ganze Zahl erforderlich.')
                raw = int(value)
                if raw > 65535:
                    raise ValueError(f'{self.LABELS[i]}: maximal 65535.')
                if i == 2 and raw not in (0, 1, 2):
                    raise ValueError('Betriebsart muss 0, 1 oder 2 sein.')
                if i == 4 and raw not in (0, 1):
                    raise ValueError('Aktiviert muss 0 oder 1 sein.')
                values.append(raw)
        return values

    def on_button_pressed(self, event: Button.Pressed):
        name = event.button.id
        if name == 'slotcancel':
            self.action_close()
        elif name == 'slotwrite' and not self.running:
            try:
                target = self.parse_fields()
            except ValueError as exc:
                self.query_one('#slotstatus', Static).update(f'Eingabefehler: {exc}')
                return
            asyncio.create_task(self.perform_write(target))
        elif name == 'slotdelete' and not self.running:
            # Explicitly requested: delete writes five zeros without an extra confirmation.
            asyncio.create_task(self.perform_write([0] * SLOT_FIELDS))

    async def perform_write(self, target):
        self.running = True
        for button in self.query(Button):
            button.disabled = True
        for field in self.query(Input):
            field.disabled = True
        status = self.query_one('#slotstatus', Static)
        import protocol as proto
        ack_state = 'unknown'
        ack_detail = 'Keine FC16-Antwort erhalten'
        actual = None
        try:
            async with core.NoahBle(self.address) as noah:
                status.update(f'Sende FC16: Timeslot {self.slot}, Register {self.base}–{self.base + 4} ...')
                try:
                    reply = await core.receive_reply(
                        noah, modbus_write_multiple_frame(self.base, target), 12, False,
                        expected_func=proto.FUNC_PASSTHROUGH)
                    ack_state, ack_detail = decode_write_multiple_ack(reply, self.base, SLOT_FIELDS)
                except Exception as exc:
                    ack_detail = f'FC16-Anfrage/Antwort fehlgeschlagen: {exc}'
                # Read back even on timeout or exception: write may have been applied.
                status.update(f'{ack_detail}\nLese fünf Register gemeinsam per FC03 zurück ...')
                try:
                    actual = await read_timeslot_block(noah, self.base)
                except Exception as exc:
                    status.update(f'{ack_detail}\nFC03-Rücklesen fehlgeschlagen: {exc}\n'
                                  'Schreibwirkung UNBEKANNT. Kein automatischer FC06-Fallback.')
            if actual is not None:
                self.readback = (self.slot, actual)
                # After deleting or saving, show the actual verified state in
                # the editor itself, not stale pre-write field contents.
                self.set_fields(actual)
                differences = [str(self.base + i) for i, value in enumerate(actual)
                               if value != target[i]]
                if differences:
                    status.update(f'FC16-Antwort [{ack_state}]: {ack_detail}\n'
                                  f'Rücklesewerte abweichend bei Register(n): {", ".join(differences)}.\n'
                                  'Tabelle wird mit den tatsächlichen Werten aktualisiert.')
                else:
                    note = ('FC16-Antwort gültig.' if ack_state == 'ok' else
                            'FC16-Antwort nicht bestätigt; Werte stimmen beim Rücklesen.')
                    status.update(f'{note}\nAlle fünf Werte per FC03 verifiziert.\n'
                                  'Schließen aktualisiert die Tabelle.')
        except Exception as exc:
            status.update(f'BLE-Fehler: {exc}. Schreibwirkung unklar; Timeslot erneut lesen.')
        finally:
            self.running = False
            self.query_one('#slotcancel', Button).disabled = False
            self.query_one('#slotcancel', Button).label = 'Schließen'
            if actual is not None:
                for field in self.query(Input):
                    field.disabled = False
                self.query_one('#slotwrite', Button).disabled = False
                self.query_one('#slotdelete', Button).disabled = False

    def on_key(self, event):
        navigate_buttons(self, event)

    def action_close(self):
        if not self.running:
            self.dismiss(self.readback)

class WriteHolding(ModalScreen):
    """FC06 single-register editor: cached prefill, read-before-write and readback."""
    BINDINGS = [Binding('escape', 'cancel', 'Abbrechen'), Binding('q', 'cancel', 'Abbrechen')]
    CSS = """
    WriteHolding { align: center middle; background: $background 75%; }
    #hbox { width: 88; max-width: 95%; height: auto; max-height: 90%;
            border: round $warning; background: $panel; padding: 1 2; }
    #hbox Input { margin: 1 0; }
    #hbox Button { margin-right: 2; }
    #hstatus { height: auto; min-height: 3; }
    """

    def __init__(self, address, entry, initial_value=None):
        super().__init__()
        self.address = address
        self.entry = entry
        self.initial_value = initial_value
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
            yield Label('Schreiben ohne Vorab-Lesen; anschließend wird der Wert per FC03 verifiziert.')
            yield Input(value=self.initial_value or '', placeholder='HH:MM' if formula == 'TIME' else 'Dezimalzahl oder 0x...', id='hvalue')
            yield Static('Neuen Wert eingeben und „Schreiben“ wählen.', id='hstatus')
            with Horizontal():
                yield Button('Schreiben', id='hcheck', variant='primary')
                yield Button('Abbrechen', id='hcancel')

    async def on_mount(self):
        # Prefill exclusively from the existing table; never initiate BLE I/O
        # merely because the editor was opened.
        status = self.query_one('#hstatus', Static)
        if self.initial_value is not None:
            status.update('Wert aus der Tabelle übernommen. Ändern und Schreiben wählen.')
        else:
            status.update('Kein eindeutiger Tabellenwert verfügbar. Wert manuell eingeben.')

    def on_key(self, event):
        navigate_buttons(self, event)

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
        self.pending = target
        # No pre-write FC03 request. The displayed value is only informational;
        # verification relies solely on the post-write FC03 readback.
        self.current = None
        if self.initial_value is not None and len(regs) == 1:
            try:
                self.current = self.parse_value(self.initial_value)
            except ValueError:
                pass
        status.update(f'Zielregister: {self.selected_register}\n'
                      f'Rohwert NEU: {target} (0x{target:04X})\n'
                      'Schreibe jetzt per FC06 und verifiziere anschließend per FC03 ...')
        await self.perform_write()

    async def perform_write(self):
        if self.running or self.pending is None:
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
            if actual == target and old is not None and actual != old:
                result = 'VERIFIZIERT: neuer Rohwert zurückgelesen.'
            elif actual == target:
                result = 'VERIFIZIERT: Sollwert per FC03 zurückgelesen.'
            elif old is not None and actual == old:
                result = 'UNVERÄNDERT: alter Tabellenwert zurückgelesen.'
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

    def on_key(self, event):
        navigate_buttons(self, event)

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


def load_refresh_groups(path):
    """Optional [F1]...[F8] groups, each line REG[,REG...]|exact display label.

    These are selectors for existing entries, not extra register definitions.
    """
    groups = {}
    active = None
    for lineno, raw in enumerate(path.read_text(encoding='utf-8-sig').splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        match = re.fullmatch(r'\[F([1-8])\]', line, re.I)
        if match:
            active = f'f{match.group(1)}'
            groups.setdefault(active, [])
            continue
        if line.startswith('[') and line.endswith(']'):
            active = None
            continue
        if active is None:
            continue
        parts = [part.strip() for part in line.split('|')]
        if len(parts) != 2 or not parts[1] or not re.fullmatch(r'\d+(?:\s*,\s*\d+)*', parts[0]):
            raise ValueError(f'{path.name}:{lineno}: F-Gruppe erwartet Register[,Register]|Bezeichnung')
        regs = tuple(int(part.strip()) for part in parts[0].split(','))
        groups[active].append((regs, parts[1]))
    return groups


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
        *[Binding(f'f{i}', f'refresh_group({i})', f'F{i}: Gruppe lesen', show=False) for i in range(1, 9)],
        Binding('q', 'quit', 'Beenden'),
    ]

    def __init__(self, address, file, initial_output=None):
        super().__init__()
        self.initial_output = initial_output
        self.address = address
        self.file = file
        self.layout_entries = core.load_registers(file)
        self.refresh_groups = load_refresh_groups(file)
        self.register_info = load_register_info()
        self.columns = []
        self.rows = []
        self.column = 0
        self.cursors = [0, 0]
        self.busy = False
        self.reload_requested = False
        self.next_menu = None
        self.timeslot = 1
        self.slot_row_indices = []
        self.slot_values = None

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
        self.timeslot = 1
        self.columns, self.rows = collect_columns(output, self.layout_entries)
        self._prepare_timeslot_rows()
        for col, lines in enumerate(self.columns):
            for idx, line in enumerate(lines):
                if re.fullmatch(r"Gekoppelte\s+Geräte(?:\s+\[Enter:\s*Auswahl\])?", line.strip().strip("-=─━ ").strip(), re.I):
                    self.columns[col][idx] = "Gekoppelte Geräte     [Enter: Auswahl]"
                    self.rows[col].append((idx, ((), "Gekoppelte Geräte", None, "", "A")))
            self.rows[col].sort(key=lambda item: item[0])
        # Recompute timeslot row positions after inserting selectable headings.
        if len(self.rows) > 1 and self.slot_row_indices:
            self.slot_row_indices = [i for i, (_, e) in enumerate(self.rows[1]) if e[4] == "H" and len(e[0]) == 1 and SLOT_BASE <= e[0][0] < SLOT_BASE + SLOT_FIELDS]
        self.slot_values = self._slot_values_from_table()
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

    def _prepare_timeslot_rows(self):
        """Make the section header selectable without changing the register file."""
        self.slot_row_indices = []
        if len(self.columns) < 2 or len(self.rows) < 2:
            return
        lines = self.columns[1]
        for idx, line in enumerate(lines):
            if re.search(r'\bTimeslot\s*:\s*\d+\s+von\s+9\b', line, re.I):
                self.slot_heading_index = idx
                self._set_slot_heading()
                self.rows[1].append((idx, ((), SLOT_MARKER, None, '', 'S')))
                self.rows[1].sort(key=lambda item: item[0])
                break
        for row_idx, (_, entry) in enumerate(self.rows[1]):
            if entry[4] == 'H' and len(entry[0]) == 1 and SLOT_BASE <= entry[0][0] < SLOT_BASE + SLOT_FIELDS:
                self.slot_row_indices.append(row_idx)
        if len(self.slot_row_indices) != SLOT_FIELDS:
            self.slot_row_indices = []

    def _set_slot_heading(self):
        idx = getattr(self, 'slot_heading_index', None)
        if idx is not None:
            self.columns[1][idx] = f'Timeslot: {self.timeslot} von 9     [Enter: Auswahl]'

    def _format_row(self, col, idx, entry, value, shown_unit):
        """Match the fixed-width layout of the original register renderer."""
        regs, label, _, _, _ = entry
        header = self.columns[col][0]
        label_start = header.find('Bezeichnung')
        unit_start = header.find('Einheit')
        status_start = header.find('Status', unit_start)
        if min(label_start, unit_start, status_start) < 0:
            raise ValueError('Tabellenspalten nicht erkannt')
        reg_width = label_start - 2
        label_width = max(11, *(len(item[1][1]) for item in self.rows[col] if item[1][4] not in ('S', 'A')))
        value_start = label_start + label_width + 2
        value_width = unit_start - 2 - value_start
        unit_width = status_start - 2 - unit_start
        if min(reg_width, value_width, unit_width) < 1:
            raise ValueError('Ungültige Spaltenbreiten')
        reg_text = str(regs[0]) if len(regs) == 1 else ','.join(map(str, regs))
        if entry[4] == 'C':
            prefix = f'{reg_text:>{reg_width}}  {label:<{label_width}}  '
            return core.format_config_row(prefix, value, value_width, shown_unit, unit_width, 'OK')
        return (f'{reg_text:>{reg_width}}  {label:<{label_width}}  '
                f'{str(value):>{value_width}}  {shown_unit:<{unit_width}}  [OK]')

    def _slot_values_from_table(self):
        """Decode the five visible values, without extra BLE traffic.

        The source of truth is the displayed table (initial FC03 reading or
        verified FC03 readback after slot change/write). If parsing is unsafe,
        the editor falls back to its existing single FC03 block read.
        """
        if len(self.slot_row_indices) != SLOT_FIELDS:
            return None
        try:
            header = self.columns[1][0]
            label_start = header.find('Bezeichnung')
            unit_start = header.find('Einheit')
            label_width = max(11, *(len(item[1][1]) for item in self.rows[1]
                                    if item[1][4] not in ('S', 'A')))
            value_start = label_start + label_width + 2
            values = []
            for offset, row_idx in enumerate(self.slot_row_indices):
                line_idx, entry = self.rows[1][row_idx]
                line = self.columns[1][line_idx]
                field = line[value_start:unit_start].strip()
                if offset < 2:
                    match = re.search(r'(\d{1,2}):(\d{2})', field)
                    if not match:
                        return None
                    hour, minute = map(int, match.groups())
                    if hour > 23 or minute > 59:
                        return None
                    values.append((hour << 8) | minute)
                else:
                    match = re.match(r'(\d+)\b', field)
                    if not match:
                        return None
                    values.append(int(match.group(1)))
            return values
        except (ValueError, IndexError, AttributeError):
            return None

    def select_timeslot(self, slot):
        if slot is not None and slot != self.timeslot:
            asyncio.create_task(self.load_timeslot(slot))

    async def load_timeslot(self, slot):
        if self.busy or not self.slot_row_indices or not 1 <= slot <= SLOT_COUNT:
            return
        self.busy = True
        loading = SlotLoading(slot)
        self.push_screen(loading)
        await asyncio.sleep(0.05)  # Ensure the loading window is visible before BLE I/O.
        self.query_one('#notice', Static).update(f'Lese Timeslot {slot}/9 (5 Holding-Register) ...')
        try:
            base = SLOT_BASE + (slot - 1) * SLOT_FIELDS
            # Read all five first: never mix old/new slot values on a partial failure.
            async with core.NoahBle(self.address) as noah:
                values = [await core.read_one(noah, 3, base + offset, 12, False)
                          for offset in range(SLOT_FIELDS)]
            replacements = []
            for offset, row_idx in enumerate(self.slot_row_indices):
                line_idx, entry = self.rows[1][row_idx]
                regs, label, formula, unit, typ = entry
                new_entry = ((base + offset,), re.sub(r'(?i)\bslot\s*1\b', f'Slot{slot}', label),
                             formula, unit, typ)
                # Keep original descriptive suffix, replacing any existing slot prefix.
                new_entry = (new_entry[0], re.sub(r'(?i)\bslot\s*\d+\b', f'Slot{slot}', label),
                             formula, unit, typ)
                shown, shown_unit = core.display_value([values[offset]], formula, unit)
                replacements.append((row_idx, line_idx, new_entry, shown, shown_unit))
            # Widths and all other columns remain stable; apply atomically.
            for row_idx, line_idx, entry, shown, shown_unit in replacements:
                self.rows[1][row_idx] = (line_idx, entry)
            for row_idx, line_idx, entry, shown, shown_unit in replacements:
                self.columns[1][line_idx] = self._format_row(1, row_idx, entry, shown, shown_unit)
            self.timeslot = slot
            self.slot_values = list(values)
            self._set_slot_heading()
            self.draw()
            self.query_one('#notice', Static).update(f'Timeslot {slot}/9 gelesen | Enter: Details und Ändern')
        except Exception as exc:
            self.query_one('#notice', Static).update(f'Timeslot {slot} nicht geladen: {exc} | Bisheriger Slot bleibt aktiv')
        finally:
            loading.dismiss()
            self.busy = False

    @staticmethod
    def accessory_status_display(line, header):
        """Annotate known WLAN entry states for display only; retain raw table data."""
        if not re.match(r'^\s*122\s{2,}', line):
            return line
        unit_start = header.find('Einheit')
        if unit_start < 0:
            return line
        value_end = unit_start - 2
        status_start = header.find('Status', unit_start)
        if status_start < 0:
            return line
        parts = line.split('\n')
        start = parts[0].find('DEV:')
        if start < 0 or start >= value_end:
            return line
        # Reassemble the original wrapped value, excluding unit/status columns.
        raw = parts[0][start:value_end].strip() + ''.join(
            part[:value_end].strip() for part in parts[1:])
        try:
            entries = sorted(core.parse_wlan_entries(raw), key=lambda e: int(e[0]))
        except (ValueError, UnicodeError):
            return line
        if not entries:
            return line
        prefix = parts[0][:start].rstrip()
        suffix = parts[-1][status_start-2:]
        output = []
        for i, (number, mode, state, name, ip) in enumerate(entries):
            if i:
                output.append('')
            title = ('DEV:' if i == 0 else '') + f'{number}-{mode}-{state}'
            marker = {'0': 'ungekopp.', '3': 'gekoppelt', '5': 'gelöscht'}.get(state, f'Status {state}') if mode == '7' else ''
            if len(prefix) + 2 + len(title) > value_end and i == 0:
                return line
            output.append((prefix if i == 0 else '').ljust(value_end-len(title)) + title + '  ' + marker)
            for field in (core.wlan_display_name(mode, name), ip):
                if field:
                    output.append(field.rjust(value_end))
        output[-1] = output[-1].ljust(status_start-2) + suffix
        return '\n'.join(output)

    def draw(self):
        for col in range(2):
            lines = self.columns[col] if col < len(self.columns) else []
            rows = self.rows[col] if col < len(self.rows) else []
            selected = rows[self.cursors[col]][0] if rows else -1
            rendered = Text()
            for idx, line in enumerate(lines):
                if idx:
                    rendered.append('\n')
                shown = self.accessory_status_display(line, lines[0])
                rendered.append(shown, style='reverse bold' if idx == selected and col == self.column else '')
            # Prevent Rich/Textual from treating section labels in square
            # brackets as markup, and keep each pane in its own viewport.
            self.query_one(f'#table{col}', Static).update(rendered)
        rows = self.rows[self.column] if self.rows else []
        if rows:
            selected = rows[self.cursors[self.column]][0]
            pane = self.query_one(f'#pane{self.column}', VerticalScroll)
            physical_row = sum(self.accessory_status_display(line, self.columns[self.column][0]).count('\n') + 1 for line in self.columns[self.column][:selected])
            pane.scroll_to(y=max(0, physical_row - max(1, pane.size.height // 2)), animate=False)

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
            if entry[4] == 'S':
                self.push_screen(TimeslotSelect(self.timeslot), self.select_timeslot)
            elif entry[4] == 'A':
                self.push_screen(AccessorySearch(self.address), self.on_accessory_closed)
            else:
                self.push_screen(RegisterDetails(entry, self.columns[self.column][line], self.register_info))

    def on_accessory_closed(self, readings):
        if not readings or not self.rows:
            return
        updated = 0
        errors = []
        for col, rows in enumerate(self.rows):
            for idx, (line_idx, entry) in enumerate(rows):
                regs, label, formula, unit, typ = entry
                if typ not in ('C', 'H') or not regs or not all((typ, r) in readings for r in regs):
                    continue
                try:
                    values = [readings[(typ, r)] for r in regs]
                    formatter = core.display_config_value if typ == 'C' else core.display_value
                    value, shown_unit = formatter(values, formula, unit)
                    self.columns[col][line_idx] = self._format_row(col, idx, entry, value, shown_unit)
                    updated += 1
                except Exception as exc:
                    errors.append(f'{label}: {exc}')
        self.draw()
        notice = f'Zubehör: {updated} Registerzeilen aktualisiert'
        if errors:
            notice += ' | ' + '; '.join(errors)
        self.query_one('#notice', Static).update(notice)

    def on_timeslot_written(self, result):
        if result is not None:
            slot, values = result
            if slot == self.timeslot and len(values) == SLOT_FIELDS:
                self.slot_values = list(values)
                for offset, row_idx in enumerate(self.slot_row_indices):
                    line_idx, entry = self.rows[1][row_idx]
                    shown, shown_unit = core.display_value([values[offset]], entry[2], entry[3])
                    self.columns[1][line_idx] = self._format_row(1, row_idx, entry, shown, shown_unit)
                self.draw()
                self.query_one('#notice', Static).update(
                    f'Timeslot {slot}: fünf Rücklesewerte übernommen | Enter: Details und Ändern')

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
                value, shown_unit = core.display_config_value([actual], formula, unit)
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
            self.columns[col][line_idx] = self._format_row(col, idx, entry, value, shown_unit)
            self.draw()
            self.query_one('#notice', Static).update(
                f'Register {changed_reg}: Rücklesewert übernommen | Enter: Details und Ändern  R: Alle neu lesen')
        except Exception as exc:
            self.query_one('#notice', Static).update(
                f'Einzelaktualisierung fehlgeschlagen ({exc}). R: Alle neu lesen')
        finally:
            self.busy = False

    def _holding_table_value(self, col, line_idx, entry):
        """Prefill a single Holding register from its displayed value.

        Numeric factors are compiled into callables by core.parse_formula(),
        including factor '1'. Never compare a parsed formula to integer 1.
        Extract the numeric field after the exact label, independent of
        display-column widths, then accept it only for identity scaling.
        """
        from decimal import Decimal

        regs, label, formula, unit, typ = entry
        if typ != 'H' or len(regs) != 1:
            return None
        line = self.columns[col][line_idx]
        match = re.search(r'(?<!\S)' + re.escape(label) + r'\s{2,}(.+?)\s{2,}', line)
        if not match:
            return None
        value = match.group(1).strip()
        if formula == 'TIME':
            if re.fullmatch(r'(?:[01]?\d|2[0-3]):[0-5]\d', value):
                return value
            return None
        # The ordinary numeric factor '1' is a callable, not the integer 1.
        # Do not prefill from scaled, enum, bitmask, or null-sentinel values.
        if formula is not None:
            if not callable(formula):
                return None
            try:
                if formula(Decimal(0)) != 0 or formula(Decimal(1)) != 1:
                    return None
            except (ArithmeticError, ValueError, TypeError):
                return None
        if not re.fullmatch(r'\d+', value):
            return None
        raw = int(value)
        if not 0 <= raw <= 65535:
            return None
        if getattr(formula, 'null_raws', frozenset()) and raw in formula.null_raws:
            return None
        return str(raw)

    def action_write(self):
        """C: ASCII config; H: single raw FC06 write, also within register groups."""
        if not self.rows or self.busy or not self.rows[self.column]:
            return
        line, entry = self.rows[self.column][self.cursors[self.column]]
        regs, label, formula, unit, typ = entry
        if typ in ('I,C', 'H,C'):
            self.query_one('#notice', Static).update('Kombinierte Registeranzeige ist schreibgeschützt.')
            return
        if typ == 'H':
            if (self.slot_row_indices and self.column == 1 and
                    self.cursors[1] in self.slot_row_indices):
                self.push_screen(WriteTimeslot(self.address, self.timeslot,
                                               self.slot_values if self.slot_values is not None
                                               else self._slot_values_from_table()),
                                 self.on_timeslot_written)
            else:
                self.push_screen(WriteHolding(self.address, entry, self._holding_table_value(self.column, line, entry)), self.on_write_finished)
            return
        if typ != 'C' or len(regs) != 1 or formula != 'ASCII':
            self.query_one('#notice', Static).update(
                'Schreiben nur für H-Register oder einzelne C-Parameter mit ASCII möglich.')
            return
        displayed = self.columns[self.column][line]
        if '\n' in displayed:
            header = self.columns[self.column][0]
            end = header.find('Einheit') - 2
            start = header.find('Bezeichnung') + max(11, *(len(item[1][1]) for item in self.rows[self.column] if item[1][4] not in ('S', 'A'))) + 2
            parts = displayed.splitlines()
            displayed = parts[0][:start] + ''.join(part[start:end].strip() for part in parts) + parts[-1][end:]
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

    async def action_refresh_group(self, number):
        key = f'f{number}'
        if self.busy:
            return
        selectors = self.refresh_groups.get(key)
        if not selectors:
            self.query_one('#notice', Static).update(f'Keine [{key.upper()}]-Gruppe in registers.txt definiert.')
            return
        # Resolve labels AND addresses to the actual displayed entries.
        targets = []
        for regs, label in selectors:
            # A range in the label (e.g. 'SOC Bat.1-4') can select
            # 'SOC Bat.1' ... 'SOC Bat.4' without changing their rows.
            label_range = re.fullmatch(r'(.*?)(\d+)-(\d+)', label)
            allowed_labels = {label}
            if label_range:
                prefix, first, last = label_range.groups()
                if int(last) >= int(first) and int(last) - int(first) <= 64:
                    allowed_labels.update(f'{prefix}{n}' for n in range(int(first), int(last) + 1))
            found = [(col, idx, entry) for col, rows in enumerate(self.rows)
                     for idx, (_, entry) in enumerate(rows)
                     if entry[1] in allowed_labels and entry[4] in ('I', 'H', 'C') and
                     set(entry[0]).issubset(set(regs))]
            if (not found or set(regs) != set().union(*(set(item[2][0]) for item in found)) or
                    len(found) != len({(item[0], item[1]) for item in found})):
                # Allow a selector to refer to several individually labelled rows
                # only if the selector label matches them (no guessing by position).
                self.query_one('#notice', Static).update(
                    f'{key.upper()}: „{label}“ ({regs}) nicht eindeutig in der Tabelle gefunden. Keine Aktualisierung.')
                return
            targets.extend(found)
        unique = {(col, idx): (col, idx, entry) for col, idx, entry in targets}
        targets = list(unique.values())
        self.busy = True
        loading = GroupLoading(f'{key.upper()}: {len(targets)} Registerzeilen werden aktualisiert ...')
        self.push_screen(loading)
        await asyncio.sleep(0)  # Allow Textual to paint the overlay before BLE I/O.
        try:
            import protocol as proto
            readings = {}
            async with core.NoahBle(self.address) as noah:
                # Reuse each verified input window once per group.
                windows = {}
                for _, _, entry in targets:
                    regs, _, _, _, typ = entry
                    for reg in regs:
                        if (typ, reg) in readings:
                            continue
                        if typ == 'I':
                            window = core.input_window_for(reg)
                            if window is not None:
                                if window not in windows:
                                    windows[window] = await core.read_input_window(noah, *window, 12, False)
                                start, _ = window
                                readings[(typ, reg)] = windows[window][reg - start]
                                continue
                            readings[(typ, reg)] = await core.read_one(noah, 4, reg, 12, False)
                        elif typ == 'H':
                            readings[(typ, reg)] = await core.read_one(noah, 3, reg, 12, False)
                        else:
                            data = await core.receive_reply(noah, proto.query_request([reg]), 12, False,
                                                            expected_func=proto.FUNC_QUERY)
                            status, values = proto.parse_query_reply(data)
                            if status != 0 or reg not in values:
                                raise ValueError(f'C:{reg} nicht lesbar (Status {status})')
                            readings[(typ, reg)] = values[reg]
            replacements = []
            for col, idx, entry in targets:
                regs, _, formula, unit, typ = entry
                if typ == 'C':
                    value, shown_unit = core.display_config_value(
                        [readings[(typ, r)] for r in regs], formula, unit)
                else:
                    value, shown_unit = core.display_value([readings[(typ, r)] for r in regs], formula, unit)
                line_idx, _ = self.rows[col][idx]
                replacements.append((col, idx, line_idx, entry, value, shown_unit))
            # Commit only after every read succeeds.
            for col, idx, line_idx, entry, value, shown_unit in replacements:
                self.columns[col][line_idx] = self._format_row(col, idx, entry, value, shown_unit)
            self.draw()
            loading.query_one('#grouploadingbox', Static).update(f'{key.upper()}: {len(replacements)} Zeilen aktualisiert')
            await asyncio.sleep(0.8)
        except Exception as exc:
            loading.query_one('#grouploadingbox', Static).update(f'{key.upper()}: Fehler: {str(exc)[:80]}')
            await asyncio.sleep(1.8)
        finally:
            loading.dismiss()
            self.busy = False

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

    def on_key(self, event):
        if (self.mode == 'select' and len(event.key) == 1 and
                event.key in '123456789'):
            idx = int(event.key) - 1
            if idx < len(self.devices):
                self.selected = self.devices[idx]['address']
                self.exit()
                event.stop()
                event.prevent_default()
                return
        navigate_buttons(self, event)

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
