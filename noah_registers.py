#!/usr/bin/env python3
"""Experimental Growatt NOAH BLE register diagnostic (v0.7).

Requires the unmodified protocol.py and growatt_ble.py from the watchdog project.
Based on the unofficial NEXA/NOAH protocol specification, section 8.10.
"""
import argparse
import ast
import re
import asyncio
from decimal import Decimal, InvalidOperation
from pathlib import Path
import struct
import time

from bleak import BleakScanner
import protocol as p
from growatt_ble import NoahBle

REGISTER_FILE = Path(__file__).resolve().parent / 'registers.txt'


def parse_wlan_entries(raw):
    """Parse C122 without changing wire order or identifiers; reject unknown syntax."""
    if isinstance(raw, bytes):
        raw = raw.decode('ascii')
    raw = raw.strip(' \r\n\t\x00').removesuffix(r'\x00')
    if not raw.startswith('DEV:'):
        raise ValueError('Unbekannte Geräteliste')
    entries = []
    for item in raw[4:].split('&') if raw[4:] else []:
        match = re.fullmatch(r'(\d+)-(\d+)-(\d+),([^,]*),([^,]*)', item)
        if not match:
            raise ValueError(f'Unbekannter Geräteeintrag: {item}')
        entries.append(match.groups())
    return entries


def wlan_display_name(mode, name):
    if mode == '7' and re.fullmatch(r'[0-9]+', name) and int(name) < 2**48:
        return int(name).to_bytes(6, 'big').hex(':').upper()
    return name


def format_config_row(prefix, value, value_width, unit, unit_width, status):
    """Wrap long config values without widening either table column."""
    value = str(value)
    chunks = []
    if re.fullmatch(r'(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}(?:\s*&\s*(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2})*', value):
        chunks = [part.strip() for part in value.split('&')]
        value = chunks.pop()
    while len(value) > value_width:
        cut = max(value.rfind(',', 0, value_width), value.rfind('&', 0, value_width)) + 1
        if not cut:
            cut = value_width
        chunks.append(value[:cut])
        value = value[cut:].lstrip()
    chunks.append(value)
    return '\n'.join(
        (prefix if i == 0 else ' ' * len(prefix)) + f'{chunk:>{value_width}}' +
        (f'  {unit:<{unit_width}}  [{status}]' if i == len(chunks)-1 else '')
        for i, chunk in enumerate(chunks))


def modbus_read_frame(kind, start, count):
    # Inner RTU CRC is little endian; outer Growatt frame CRC is big endian.
    req = struct.pack('>BBHH', 1, kind, start, count)
    req += struct.pack('<H', p.crc16_modbus(req))
    return p.build_frame(p.FUNC_PASSTHROUGH, p.APP_SERIAL + struct.pack('>H', len(req)) + req)


def decode_modbus(data, kind, count):
    """Best-effort decoding: BLE 0x17 reply format is not documented conclusively."""
    candidates = [('raw', data)]
    if len(data) >= 2:
        n = int.from_bytes(data[:2], 'big')
        if n == len(data) - 2:
            candidates.insert(0, ('length-prefixed', data[2:]))
    for label, payload in candidates:
        if len(payload) < 5 or payload[0] != 1:
            continue
        crc_ok = p.crc16_modbus(payload[:-2]) == int.from_bytes(payload[-2:], 'little')
        if not crc_ok:
            continue
        if payload[1] == (kind | 0x80) and len(payload) == 5:
            return f'Modbus exception: code {payload[2]} (0x{payload[2]:02X})', None
        if payload[1] != kind or payload[2] != 2 * count or len(payload) != 5 + 2 * count:
            continue
        words = struct.unpack('>' + 'H' * count, payload[3:-2])
        return f'RTU-Antwort gültig ({label}, CRC OK)', words
    return 'Antwortformat nicht sicher dekodierbar; Rohdaten beachten', None


async def receive_reply(noah, frame, timeout, verbose, expected_func=p.FUNC_PASSTHROUGH):
    # The shared NoahBle.request() prints and returns only the payload; this
    # diagnostic instead checks the outer function and keeps the raw bytes.
    if verbose:
        print('TX BLE frame:', frame.hex(' '))
    while not noah.rx.empty():
        noah.rx.get_nowait()
    await noah.client.write_gatt_char(p.CHAR_UUID, frame, response=True)
    buffer = b''
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise TimeoutError('Keine vollständige BLE-Antwort innerhalb des Zeitlimits')
        chunk = await asyncio.wait_for(noah.rx.get(), remaining)
        if verbose:
            print('RX GATT chunk:', chunk.hex(' '))
        frames, buffer = p.extract_frames(buffer + chunk)
        for received in frames:
            proto, addr, func, data = p.parse_frame(received)
            print('RX BLE frame:', received.hex(' '))
            print(f'Äußerer Frame: proto={proto} addr={addr} func=0x{func:02X}')
            if func != expected_func:
                if verbose:
                    print(f'Hinweis: anderes Antworttelegramm 0x{func:02X}; warte auf 0x{expected_func:02X}.')
                continue
            return data



def explain_register_reply(payload, kind, start, count):
    """Print byte-accurate diagnostics; never infer values from invalid RTU data."""
    print(f'  Äußere Nutzdaten: {len(payload)} Bytes: {payload.hex(" ")}')
    if len(payload) < 10:
        print('  WARNUNG: weniger als 10 Bytes; Seriennummern-Präfix nicht gesichert.')
        return None
    serial, body = payload[:10], payload[10:]
    print(f'  Präfix [0:10]: {serial.hex(" ")}  ASCII={serial.decode("ascii", "backslashreplace")!r}')
    print(f'  Body   [10:]: {len(body)} Bytes: {body.hex(" ") or "(leer)"}')
    if len(body) >= 2:
        prefix_len = int.from_bytes(body[:2], 'big')
        print(f'  Body[0:2] als Länge: {prefix_len}; restliche Bytes: {len(body)-2}')
    status, words = decode_modbus(body, kind, count)
    print('  Strikte RTU-Prüfung:', status)
    if words is not None:
        for i, value in enumerate(words):
            print(f'    {"I" if kind == 4 else "H"}:{start+i:<5} = {value:5} / 0x{value:04X}')
    else:
        print('  KEINE gültigen Registerwerte: Nullbytes oder Seriennummernbytes nicht als Messwert interpretieren.')
    return words


# Experimentally CRC-validated input windows on NOAH (FC04).
# The last window overlaps the previous one intentionally.
FAST_INPUT_WINDOWS = tuple((start, 16) for start in (0, 16, 32, 48, 64, 80, 96, 108))


def input_window_for(register):
    """Return a known working FC04 window for an input register, if any."""
    return next(((start, count) for start, count in FAST_INPUT_WINDOWS
                 if start <= register < start + count), None)


async def read_input_window(noah, start, count, timeout, verbose=False):
    """Read and CRC-validate a complete input window; never return partial data."""
    payload = await receive_reply(noah, modbus_read_frame(4, start, count), timeout, verbose)
    if len(payload) < 10:
        raise ValueError('BLE-Antwort zu kurz')
    status, words = decode_modbus(payload[10:], 4, count)
    if words is None:
        raise ValueError(f'I:{start}..{start+count-1}: {status}')
    return words


async def discover_devices(seconds=8):
    """Live BLE scan only; no watchdog configuration is read."""
    results = await BleakScanner.discover(timeout=seconds, return_adv=True)
    devices = []
    for device, adv in results.values():
        if p.SERVICE_UUID.lower() in {u.lower() for u in adv.service_uuids}:
            devices.append((device.address, device.name or adv.local_name or 'NOAH', adv.rssi))
    return sorted(devices, key=lambda d: d[2], reverse=True)


async def scan(seconds):
    for address, name, rssi in await discover_devices(seconds):
        print(f'{address}  {name}  RSSI {rssi} dBm')


async def select_live_device(seconds):
    while True:
        print('\nSuche erreichbare NOAH-Geräte über BLE ...')
        devices = await discover_devices(seconds)
        if devices:
            print('\nNr.  BLE-Gerät              Adresse                 RSSI')
            for i, (address, name, rssi) in enumerate(devices, 1):
                print(f'{i:>2}.  {name:<22} {address:<22} {rssi:>4} dBm')
        else:
            print('Keine NOAH-Geräte gefunden.')
        choice = input('Gerät wählen (r=erneut suchen, q=Ende): ').strip().lower()
        if choice == 'q':
            return None
        if choice == 'r':
            continue
        if choice.isdecimal() and 1 <= int(choice) <= len(devices):
            return devices[int(choice) - 1]
        print('Ungültige Auswahl.')


def parse_formula(source, lineno):
    """Return a safe Decimal transformation for raw register values."""
    # NULL<n> is an optional raw-value sentinel, evaluated before scaling.
    # Examples: '*0.1 -273.1 NULL0', '1 NULL65535', '1 NULL0,65535'.
    normalized = source.strip().replace('−', '-').replace('–', '-')
    # Optional presentation flag, independent of NULL sentinels and arithmetic.
    nohex = bool(re.search(r'(?<!\S)NOHEX(?!\S)', normalized, re.I))
    normalized = re.sub(r'(?<!\S)NOHEX(?!\S)', '', normalized, flags=re.I).strip()
    null_match = re.search(r'\s+NULL(\d+(?:\s*,\s*\d+)*)\s*$', normalized, re.I)
    null_values = frozenset()
    if null_match:
        null_values = frozenset(int(x.strip()) for x in null_match.group(1).split(','))
        if any(not 0 <= x <= 65535 for x in null_values):
            raise ValueError(f'Zeile {lineno}: NULL-Rohwerte müssen 0..65535 sein')
        normalized = normalized[:null_match.start()].strip() or '1'
    expr = normalized.replace(',', '.')
    bit_match = re.fullmatch(r'bit\s*(\d{1,2})\s*-\s*(\d{1,2})', expr, re.I)
    if bit_match:
        first, last = map(int, bit_match.groups())
        if not 0 <= first <= 15 or not 0 <= last <= 15:
            raise ValueError(f'Zeile {lineno}: Bitpositionen müssen 0..15 sein')
        return ('BITS', first, last)
    mixed_match = re.fullmatch(r'Comb((?:HB|LB)+)\+ASCIIstrip\(([^)]*)\)', expr, re.I)
    if mixed_match:
        return ('COMB_ASCII', tuple(re.findall(r'HB|LB', mixed_match.group(1).upper())), mixed_match.group(2))
    comb_match = re.fullmatch(r'Comb((?:HB|LB)+)', expr, re.I)
    if comb_match:
        return ('COMB', tuple(re.findall(r'HB|LB', comb_match.group(1).upper())))
    if not expr or expr.upper() in ('ASCII', 'TIME', 'HEX', 'MAC', 'RAWHEX'):
        return None if not expr else expr.upper()
    # Legacy factors (0.01, 1) are multiplication factors.
    if re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)', expr):
        expr = 'x*(' + expr + ')'
    elif expr.startswith(('*', '/', '+', '-')):
        expr = 'x' + expr
    elif not re.search(r'\bx\b', expr, re.I):
        raise ValueError(f'Zeile {lineno}: Formel benötigt x oder einen vorangestellten Operator')
    try:
        tree = ast.parse(expr, mode='eval')
    except SyntaxError:
        raise ValueError(f'Zeile {lineno}: ungültige Formel {source!r}') from None
    def evaluate(node, x):
        if isinstance(node, ast.Expression):
            return evaluate(node.body, x)
        if isinstance(node, ast.Name) and node.id.lower() == 'x':
            return x
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return Decimal(str(node.value))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            v = evaluate(node.operand, x)
            return v if isinstance(node.op, ast.UAdd) else -v
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            left, right = evaluate(node.left, x), evaluate(node.right, x)
            if isinstance(node.op, ast.Add): return left + right
            if isinstance(node.op, ast.Sub): return left - right
            if isinstance(node.op, ast.Mult): return left * right
            return left / right
        raise ValueError(f'Zeile {lineno}: nicht erlaubter Formelausdruck')
    # Validate all nodes without requiring a particular input value.
    evaluate(tree, Decimal(1))
    def transform(x):
        return evaluate(tree, Decimal(x))
    transform.null_raws = null_values
    transform.nohex = nohex
    return transform


def parse_enum(text):
    """Interpret e.g. '0 Idle, 1 Laden, 2 Entladen' as value labels."""
    parts = [part.strip() for part in text.split(',')]
    result = {}
    for part in parts:
        match = re.fullmatch(r'(\d+)\s+(.+)', part)
        if not match:
            return None
        result[int(match.group(1))] = match.group(2).strip()
    return result if result else None


def parse_layout_directive(line):
    """Return (kind, payload) for standalone **...** display directives."""
    if not (line.startswith('**') and line.endswith('**') and len(line) >= 4):
        return None
    body = line[2:-2].strip()
    if not body:
        raise ValueError('leerer Formatierungsbefehl')
    if body.upper() in ('SPALTE', 'SEITENUMBRUCH'):
        return ('page', '')
    if body.upper() == 'LEER':
        return ('blank', '')
    match = re.fullmatch(r'---(\d+)---', body)
    if match:
        width = int(match.group(1))
        if not 1 <= width <= 200:
            raise ValueError('Trennlinienbreite muss 1..200 sein')
        return ('line', width)
    if body == '---':
        return ('line', None)
    match = re.fullmatch(r'---(.+?)---', body)
    if match:
        caption = match.group(1).strip()
        if not caption:
            raise ValueError('Trennlinienbeschriftung darf nicht leer sein')
        return ('labeled_line', caption)
    match = re.fullmatch(r'===(\d+)===', body)
    if match:
        width = int(match.group(1))
        if not 1 <= width <= 200:
            raise ValueError('Trennlinienbreite muss 1..200 sein')
        return ('double', width)
    if body.startswith('[') and body.endswith(']'):
        return ('section', body[1:-1].strip())
    return ('heading', body)


def load_registers(path):
    if not path.is_file():
        raise ValueError(f'Registerdatei nicht gefunden: {path}')
    entries = []
    in_refresh_group = False
    for lineno, raw in enumerate(path.read_text(encoding='utf-8-sig').splitlines(), 1):
        line = raw.strip()
        if re.fullmatch(r'\[F[1-8]\]', line, re.I):
            in_refresh_group = True
            continue
        if in_refresh_group:
            if re.fullmatch(r'\[[^]]+\]', line):
                in_refresh_group = False
            else:
                continue
        if not line or line.startswith('#'):
            continue
        try:
            directive = parse_layout_directive(line)
        except ValueError as exc:
            raise ValueError(f'{path.name}:{lineno}: {exc}') from None
        if directive is not None:
            entries.append(((), directive, None, '', ''))
            continue
        cols = [v.strip() for v in line.split('|')]
        if len(cols) in (4, 5) and [c.lower() for c in cols[:4]] == ['register', 'bezeichnung', 'faktor', 'einheit']:
            continue
        # Accept an optional sixth column for a descriptive bit label, e.g.
        # 14|Heizung|1|bit3-0|Akku4-1|I
        if len(cols) == 6 and re.fullmatch(r'bit\s*\d{1,2}\s*-\s*\d{1,2}', cols[3], re.I):
            cols = [cols[0], cols[1], cols[3], cols[4], cols[5]]
        if len(cols) not in (4, 5):
            raise ValueError(f'{path.name}:{lineno}: erwartet Register|Bezeichnung|Faktor|Einheit[|Registertyp]')
        reg_text, label, factor_text, unit = cols[:4]
        register_type = (cols[4] if len(cols) == 5 else 'I').strip().upper() or 'I'
        if register_type not in ('I', 'H', 'C', 'I,C', 'H,C'):
            raise ValueError(f'{path.name}:{lineno}: Registertyp muss I, H oder C sein')
        try:
            expanded = []
            for item in reg_text.split(','):
                item = item.strip()
                # Start->Anzahl: 100->16 entspricht 100-115.
                count_match = re.fullmatch(r'(\d+)\s*->\s*(\d+)', item)
                range_match = re.fullmatch(r'(\d+)\s*-\s*(\d+)', item)
                if count_match:
                    first, count = map(int, count_match.groups())
                    if count < 1 or first > 65535 or count > 65536 - first:
                        raise ValueError('ungültige Registeranzahl oder Bereichsende')
                    expanded.extend(range(first, first + count))
                elif range_match:
                    first, last = map(int, range_match.groups())
                    if first > last or last > 65535:
                        raise ValueError('ungültiger Registerbereich')
                    expanded.extend(range(first, last + 1))
                else:
                    expanded.append(int(item, 10))
            regs = tuple(expanded)
        except ValueError:
            raise ValueError(f'{path.name}:{lineno}: ungültige Registerliste oder Registerbereich') from None
        if not regs or any(not 0 <= reg <= 65535 for reg in regs) or not label:
            raise ValueError(f'{path.name}:{lineno}: ungültiger Eintrag')
        if len(set(regs)) != len(regs):
            raise ValueError(f'{path.name}:{lineno}: doppelte Register innerhalb einer Zeile')
        try:
            formula = parse_formula(factor_text, lineno)
        except (ValueError, InvalidOperation, ZeroDivisionError) as exc:
            raise ValueError(f'{path.name}:{lineno}: {exc}') from None
        if factor_text.strip().upper() == 'TIME':
            formula = 'TIME'
        if formula in ('MAC', 'RAWHEX') and register_type != 'C':
            raise ValueError(f'{path.name}:{lineno}: {formula} ist nur für C-Parameter erlaubt')
        if formula == 'ASCII' and not regs:
            raise ValueError(f'{path.name}:{lineno}: ASCII benötigt Register')
        if register_type in ('I,C', 'H,C'):
            if not (isinstance(formula, tuple) and formula[0] == 'COMB_ASCII'):
                raise ValueError(f'{path.name}:{lineno}: Gemischte Register benötigen Comb...+ASCIIstrip(...)')
            groups = reg_text.split(',')
            if len(groups) != 2 or not re.fullmatch(r'\s*\d+\s*', groups[1]):
                raise ValueError(f'{path.name}:{lineno}: I,C/H,C erwartet Modbus-Bereich,einzelne C-ID')
            if len(regs) < 2:
                raise ValueError(f'{path.name}:{lineno}: Modbus- und C-Register fehlen')
        if isinstance(formula, tuple) and formula[0] in ('COMB', 'COMB_ASCII'):
            selectors = formula[1]
            # Each register contributes HB, LB, or HB+LB in that order.
            # The first register may start at LB; no register may be skipped.
            index = 0
            used_registers = 0
            for _reg in (regs[:-1] if formula[0] == 'COMB_ASCII' else regs):
                if index >= len(selectors):
                    break
                used_registers += 1
                if selectors[index] == 'HB' and index + 1 < len(selectors) and selectors[index + 1] == 'LB':
                    index += 2
                else:
                    index += 1
            if index != len(selectors) or used_registers != (len(regs)-1 if formula[0] == 'COMB_ASCII' else len(regs)):
                raise ValueError(f'{path.name}:{lineno}: Comb-Bytefolge passt nicht zum Registerbereich')
            if register_type == 'C':
                raise ValueError(f'{path.name}:{lineno}: Comb ist nur für I/H-Register erlaubt')
        if len(regs) > 1 and (formula == 'TIME' or (isinstance(formula, tuple) and formula[0] == 'BITS')):
            raise ValueError(f'{path.name}:{lineno}: TIME und Bitmasken benötigen ein einzelnes Register')
        entries.append((regs, label, formula, unit, register_type))
    if not any(regs for regs, *_ in entries):
        raise ValueError('Registerdatei enthält keine Register')
    return entries


def is_null_raw(formula, raw):
    return raw in getattr(formula, 'null_raws', ())


def config_mac_numbers(raw):
    text = raw.decode('ascii').strip(' \t\r\n\x00')
    if not text:
        return []
    numbers = []
    for part in text.split('&'):
        part = part.strip()
        if not re.fullmatch(r'[0-9]+', part) or int(part) >= 1 << 48:
            raise ValueError(f'Ungültiger MAC-Wert: {raw!r}')
        numbers.append(int(part))
    return list(dict.fromkeys(numbers))


def display_config_value(values, formula, unit):
    """Format variable-length C parameter payloads, including decimal ASCII IDs."""
    chunks = []
    for raw in values:
        if formula == 'RAWHEX':
            chunks.append(raw.hex(' ').upper())
        elif formula == 'MAC':
            numbers = [n for n in config_mac_numbers(raw) if n != 0]
            chunks.append(' & '.join(n.to_bytes(6, 'big').hex(':').upper() for n in numbers) if numbers else 'Keine Suchergebnisse')
        elif formula == 'HEX':
            text = raw.decode('ascii').strip()
            if not re.fullmatch(r'[0-9]+', text):
                raise ValueError(f'{formula}: keine dezimale ASCII-Zahl')
            number = int(text, 10)
            if formula == 'MAC':
                if number >= 1 << 48:
                    raise ValueError('MAC: Wert außerhalb des 48-Bit-Bereichs')
                chunks.append(number.to_bytes(6, 'big').hex(':').upper())
            else:
                chunks.append(f'{number:X}')
        else:
            chunks.append(raw.decode('utf-8', errors='backslashreplace')
                          .encode('unicode_escape').decode('ascii'))
    legend = parse_enum(unit)
    if legend is not None and formula not in ('HEX', 'MAC', 'RAWHEX'):
        for i, chunk in enumerate(chunks):
            try:
                number = int(chunk.strip())
            except ValueError:
                continue
            chunks[i] = f'{number} ({legend.get(number, "unbekannt")})'
        unit = ''
    return ', '.join(chunks), unit


def display_value(values, formula, unit):
    if isinstance(formula, tuple) and formula[0] in ('COMB', 'COMB_ASCII'):
        selectors = formula[1]
        pieces = []
        index = 0
        for word in (values[:-1] if formula[0] == 'COMB_ASCII' else values):
            if index >= len(selectors):
                break
            if selectors[index] == 'HB':
                pieces.append(word >> 8)
                index += 1
                if index < len(selectors) and selectors[index] == 'LB':
                    pieces.append(word & 0xFF)
                    index += 1
            else:
                pieces.append(word & 0xFF)
                index += 1
        if index != len(selectors):
            raise ValueError('Comb-Bytefolge passt nicht zu den gelesenen Registern')
        rendered = ''.join(f'{part}{unit}' for part in pieces)
        if formula[0] == 'COMB_ASCII':
            tail = values[-1]
            if not isinstance(tail, bytes):
                raise ValueError('Config-Wert ist keine ASCII-Bytefolge')
            rendered += tail.decode('ascii', errors='replace').replace(formula[2], '') if formula[2] else tail.decode('ascii', errors='replace')
        return rendered, ''
    if formula == 'TIME':
        raw = values[0]
        hours, minutes = raw >> 8, raw & 0xFF
        if hours > 23 or minutes > 59:
            raise ValueError(f'ungültige HH:MM-Codierung: 0x{raw:04X}')
        return f'{hours:02d}:{minutes:02d}', unit
    if formula == 'ASCII':
        # Modbus words are big-endian; combine in the order from registers.txt.
        raw = b''.join(struct.pack('>H', word) for word in values)
        return raw.rstrip(b'\x00').decode('ascii', errors='replace'), ''
    if formula == 'HEX':
        # Each Modbus register is a 16-bit word (high byte first).
        return ' '.join(f'{byte:02X}' for word in values for byte in (word >> 8, word & 0xFF)), unit
    if len(values) > 1:
        # Keep each register in its original order. Show decimal/scaled values
        # as requested by the factor, plus a byte-separated hexadecimal dump.
        rendered_values = []
        for raw in values:
            if is_null_raw(formula, raw):
                rendered_values.append('-')
                continue
            value = formula(Decimal(raw)) if formula else Decimal(raw)
            if not value.is_finite():
                raise ValueError('Ergebnis ist nicht endlich')
            shown = format(value, 'f')
            if '.' in shown:
                shown = shown.rstrip('0').rstrip('.')
            rendered_values.append(shown)
        hex_bytes = ' '.join(f'{byte:02X}' for word in values for byte in (word >> 8, word & 0xFF))
        if getattr(formula, 'nohex', False):
            return ', '.join(rendered_values), unit
        return ' '.join(rendered_values) + '  [HEX: ' + hex_bytes + ']', unit
    if isinstance(formula, tuple) and formula[0] == 'BITS':
        _, first, last = formula
        raw = values[0]
        step = -1 if first > last else 1
        return ''.join(str((raw >> bit) & 1) for bit in range(first, last + step, step)), unit
    # Also allow bit3-0 in the unit column with factor 1.
    unit_bits = re.fullmatch(r'bit\s*(\d{1,2})\s*-\s*(\d{1,2})', unit, re.I)
    if unit_bits:
        first, last = map(int, unit_bits.groups())
        if not 0 <= first <= 15 or not 0 <= last <= 15:
            raise ValueError('Bitpositionen müssen 0..15 sein')
        raw = values[0]
        step = -1 if first > last else 1
        return ''.join(str((raw >> bit) & 1) for bit in range(first, last + step, step)), ''
    if is_null_raw(formula, values[0]):
        return '-', unit
    value = formula(Decimal(values[0])) if formula else Decimal(values[0])
    if not value.is_finite():
        raise ValueError('Ergebnis ist nicht endlich')
    rendered = format(value, 'f')
    if '.' in rendered:
        rendered = rendered.rstrip('0').rstrip('.')
    labels = parse_enum(unit)
    if labels is not None:
        if value == value.to_integral_value():
            return f'{rendered} ({labels.get(int(value), "unbekannt")})', ''
        return rendered, ''
    return rendered, unit


def resolve_device(args):
    if args.address:
        return args.address
    raise ValueError('Bitte --address angeben oder das interaktive Menü ohne Argumente starten.')


async def read(args):
    address = resolve_device(args)
    kind = 4 if args.type == 'input' else 3
    print(f'BLE: {address} | {args.type} | Start {args.register} | Anzahl {args.count}')
    print('Lesezugriff über BLE 0x17.')
    window = input_window_for(args.register) if kind == 4 and args.count == 1 else None
    frame = modbus_read_frame(kind, *(window or (args.register, args.count)))
    if window:
        print(f'Optimiertes Lesefenster: I:{window[0]}..{window[0]+window[1]-1}')
    async with NoahBle(address, verbose=args.verbose) as noah:
        try:
            payload = await receive_reply(noah, frame, args.timeout, args.verbose)
        except TimeoutError:
            print('Keine 0x17-Antwort. Dies beweist NICHT, dass das Register fehlt.')
            return
    if window:
        status, words = decode_modbus(payload[10:] if len(payload) >= 10 else b'', 4, window[1])
        if words is None:
            explain_register_reply(payload, 4, *window)
            print(status)
        else:
            value = words[args.register-window[0]]
            print(f'I:{args.register} = {value} (0x{value:04X}); CRC OK')
        return
    explain_register_reply(payload, kind, args.register, args.count)
    if len(payload) < 12:
        print('Kurze Antwort; Interpretation offen.')
        return
    serial, body = payload[:10], payload[10:]
    print('Datalogger-Kennung:', serial.decode('ascii', errors='replace'))
    print('Antwort-Body:', body.hex(' '))
    status, values = decode_modbus(body, kind, args.count)
    print(status)
    if values is not None:
        for index, value in enumerate(values):
            reg = args.register + index
            name = 'battery_heater_state (NEXA-Hypothese)' if args.type == 'input' and reg == 14 else ''
            print(f'{reg:5d}  0x{value:04X}  {value:5d}  {name}')
            if args.type == 'input' and reg == 14:
                print('       Heizungs-Bitmaske (NEXA):', ', '.join(f'Pack {i+1}={"AN" if value & (1 << i) else "AUS"}' for i in range(4)))



async def read_list(args):
    """Read register blocks into a dictionary, then render original entries.

    Modbus 0x03 and 0x04 must be queried separately. Small gaps may be
    included in a block, but unrelated register regions are never swept.
    """
    layout_entries = load_registers(Path(args.file))
    entries = [entry for entry in layout_entries if entry[0]]
    address = resolve_device(args)
    max_words = getattr(args, 'block_size', 16)
    max_gap = getattr(args, 'max_gap', 2)
    if not 1 <= max_words <= 32 or not 0 <= max_gap <= 8:
        raise ValueError('block-size muss 1..32 und max-gap 0..8 sein')
    wanted = {kind: set() for kind in (3, 4)}
    for regs, _, _, _, typ in entries:
        
        if typ in ('I,C', 'H,C'):
            wanted[4 if typ == 'I,C' else 3].update(regs[:-1])
        elif typ != 'C':
            wanted[4 if typ == 'I' else 3].update(regs)

    blocks = []
    for kind in (4, 3):
        regs = sorted(wanted[kind])
        if not regs:
            continue
        start = end = regs[0]
        for reg in regs[1:]:
            if reg - end - 1 <= max_gap and reg - start + 1 <= max_words:
                end = reg
            else:
                blocks.append((kind, start, end - start + 1))
                start = end = reg
        blocks.append((kind, start, end - start + 1))

    print(f'\nNOAH: {address} | Registerdatei: {args.file}')
    print(f'{sum(map(len, wanted.values()))} unterschiedliche Register in {len(blocks)} Blöcken '
          f'(max. {max_words} Wörter, max. {max_gap} Lückenregister).')
    values = {}
    errors = {}
    transactions = 0
    started = time.monotonic()
    async with NoahBle(address, verbose=args.verbose) as noah:
        async def fetch(kind, start, count):
            nonlocal transactions
            transactions += 1
            frame = modbus_read_frame(kind, start, count)
            import contextlib
            import io
            if args.verbose:
                payload = await receive_reply(noah, frame, args.timeout, True)
            else:
                with contextlib.redirect_stdout(io.StringIO()):
                    payload = await receive_reply(noah, frame, args.timeout, False)
            if len(payload) < 10:
                raise ValueError('Antwort zu kurz')
            status, words = decode_modbus(payload[10:], kind, count)
            if words is None:
                raise ValueError(status)
            return words

        # Adaptive fallback: probe both edges of a failed block. If neither
        # edge is readable, try the remaining words individually instead of
        # recursively generating many more failed block requests.
        # Cached single-register probes are never sent twice.
        probed = {}  # (kind, register) -> True (readable) / False (unreadable)

        async def fetch_single(kind, reg):
            key = (kind, reg)
            if key in probed:
                return probed[key]
            try:
                word = (await fetch(kind, reg, 1))[0]
                probed[key] = True
                if reg in wanted[kind]:
                    values[key] = word
                print(f'  {"I" if kind == 4 else "H"} {reg}-{reg}: OK', flush=True)
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as exc:
                probed[key] = False
                if reg in wanted[kind]:
                    errors[key] = f'{type(exc).__name__}: {exc}'
                print(f'  {"I" if kind == 4 else "H"} {reg}-{reg}: NICHT LESBAR '
                      f'({type(exc).__name__}: {exc})', flush=True)
            return probed[key]

        async def fetch_range(kind, start, count):
            if count == 1:
                await fetch_single(kind, start)
                return
            t0 = time.monotonic()
            prefix = f'  {"I" if kind == 4 else "H"} {start}-{start + count - 1}'
            try:
                words = await fetch(kind, start, count)
                for offset, value in enumerate(words):
                    reg = start + offset
                    if reg in wanted[kind]:
                        values[(kind, reg)] = value
                    probed[(kind, reg)] = True
                print(f'{prefix}: OK ({time.monotonic() - t0:.2f}s)', flush=True)
                return
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as exc:
                reason = f'{type(exc).__name__}: {exc}'
                print(f'{prefix}: Block nicht lesbar ({reason}); '
                      'prüfe Randregister', flush=True)

            left_ok = await fetch_single(kind, start)
            right_ok = await fetch_single(kind, start + count - 1)
            if not left_ok and not right_ok:
                print(f'{prefix}: beide Randregister unlesbar; '
                      'prüfe übrige Register einzeln', flush=True)
                for reg in range(start + 1, start + count - 1):
                    await fetch_single(kind, reg)
                return

            half = count // 2
            print(f'{prefix}: mindestens ein Randregister lesbar; '
                  f'teile in {start}-{start + half - 1} und '
                  f'{start + half}-{start + count - 1}', flush=True)
            await fetch_range(kind, start, half)
            await fetch_range(kind, start + half, count - half)

        # Fast FC04 pass: read complete, experimentally validated 16-word
        # windows. Cache all requested values; retain old logic for holding
        # registers and input registers outside the covered windows.
        for start, count in FAST_INPUT_WINDOWS:
            needed = [reg for reg in range(start, start+count) if reg in wanted[4]
                      and (4, reg) not in values]
            if not needed:
                continue
            transactions += 1
            try:
                words = await read_input_window(noah, start, count, args.timeout, args.verbose)
                for offset, word in enumerate(words):
                    reg = start + offset
                    if reg in wanted[4]:
                        values[(4, reg)] = word
                        probed[(4, reg)] = True
                        errors.pop((4, reg), None)
                print(f'  I {start}-{start+count-1}: OK (16er-Schnellfenster)', flush=True)
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as exc:
                print(f'  I {start}-{start+count-1}: FEHLER ({exc}); '
                      'versuche bisherige Abfragelogik', flush=True)

        for block_number, (kind, start, count) in enumerate(blocks, 1):
            # Entirely covered blocks do not require another BLE transaction.
            if all((kind, reg) in values for reg in range(start, start+count)
                   if reg in wanted[kind]):
                continue
            print(f'Block {block_number}/{len(blocks)} wird geprüft ...', flush=True)
            await fetch_range(kind, start, count)
        # Datalogger configuration is a separate address space (Growatt 0x19).
        # Query each parameter individually, using the same authenticated BLE session.
        config_ids = list(dict.fromkeys(reg for regs, _, _, _, typ in entries
                                        if typ == 'C' for reg in regs))
        config_ids.extend(regs[-1] for regs, _, _, _, typ in entries if typ in ('I,C', 'H,C'))
        config_ids = list(dict.fromkeys(config_ids))
        for param_id in config_ids:
            transactions += 1
            try:
                reply = await receive_reply(noah, p.query_request([param_id]),
                                            args.timeout, args.verbose,
                                            expected_func=p.FUNC_QUERY)
                status, config_data = p.parse_query_reply(reply)
                if status != 0:
                    raise ValueError(f'QUERY-Status {status}')
                if param_id not in config_data:
                    raise ValueError('Parameter fehlt in der Antwort')
                values[('C', param_id)] = config_data[param_id]
                print(f'  C {param_id}: OK', flush=True)
            except Exception as exc:
                errors[('C', param_id)] = str(exc)
                print(f'  C {param_id}: NICHT LESBAR ({exc})', flush=True)

    results = []
    for regs, label, formula, unit, typ in entries:
        if typ in ('I,C', 'H,C'):
            kind = 4 if typ == 'I,C' else 3
            missing = [reg for reg in regs[:-1] if (kind, reg) not in values]
            if ('C', regs[-1]) not in values:
                missing.append(regs[-1])
            reg_label = f'{regs[0]}-{regs[-2]},{regs[-1]}' if len(regs) > 2 else f'{regs[0]},{regs[-1]}'
            if missing:
                results.append((reg_label, label, '—', '', 'NICHT LESBAR'))
            else:
                try:
                    raw_values = [values[(kind, reg)] for reg in regs[:-1]] + [values[('C', regs[-1])]]
                    rendered, shown_unit = display_value(raw_values, formula, unit)
                    results.append((reg_label, label, rendered, shown_unit, 'OK'))
                except (ValueError, UnicodeError) as exc:
                    results.append((reg_label, label, '—', '', f'Umrechnung: {exc}'))
            continue
        if typ == 'C':
            # Config parameters are variable-length byte strings, not 16-bit words.
            # Never interpret them via Modbus register scaling.
            missing = [reg for reg in regs if ('C', reg) not in values]
            reg_label = (str(regs[0]) if len(regs) == 1 else ','.join(map(str, regs)))
            if missing:
                results.append((reg_label, label, '—', unit, 'NICHT LESBAR'))
            else:
                try:
                    rendered, shown_unit = display_config_value(
                        [values[('C', reg)] for reg in regs], formula, unit)
                    results.append((reg_label, label, rendered, shown_unit, 'OK'))
                except (ValueError, UnicodeError) as exc:
                    results.append((reg_label, label, '—', unit, f'Umrechnung: {exc}'))
            continue
        kind = 4 if typ == 'I' else 3
        missing = [reg for reg in regs if (kind, reg) not in values]
        reg_label = (f'{regs[0]}-{regs[-1]}' if len(regs) > 1 and regs == tuple(range(regs[0], regs[-1] + 1)) else ','.join(str(reg) for reg in regs))
        if missing:
            # Show all successfully read words even when some registers fail.
            # ?? marks unknown bytes (one 16-bit Modbus register = two bytes).
            words = [values.get((kind, reg)) for reg in regs]
            if len(regs) > 1:
                if formula == 'HEX':
                    rendered = ' '.join(f'{word:04X}'[:2] + ' ' + f'{word:04X}'[2:]
                                        if word is not None else '?? ??' for word in words)
                elif formula == 'ASCII':
                    rendered = ' '.join('??' if word is None else
                                        struct.pack('>H', word).decode('ascii', errors='replace')
                                        for word in words)
                else:
                    rendered = ' '.join('?' if word is None else str(word) for word in words)
                readable = len(regs) - len(missing)
                status = (f'TEILWEISE ({readable}/{len(regs)} Register)' if readable
                          else f'NICHT LESBAR (0/{len(regs)} Register)')
                results.append((reg_label, label, rendered, unit, status))
            else:
                results.append((reg_label, label, '—', unit, 'NICHT LESBAR'))
            continue
        try:
            rendered, shown_unit = display_value([values[(kind, reg)] for reg in regs],
                                                 formula, unit)
            results.append((reg_label, label, rendered, shown_unit, 'OK'))
        except (ValueError, InvalidOperation, ZeroDivisionError, UnicodeError) as exc:
            results.append((reg_label, label, '—', unit, f'Umrechnung: {exc}'))

    def format_separate_values(regs, formula, typ):
        """Format explicit comma-separated measurements independently."""
        kind = 4 if typ == 'I' else 3
        formatted = []
        for number in regs:
            raw = values.get((kind, number))
            if raw is None:
                formatted.append('?')
            elif is_null_raw(formula, raw):
                formatted.append('-')
            else:
                try:
                    converted = formula(Decimal(raw)) if callable(formula) else Decimal(raw)
                    if not converted.is_finite():
                        raise ValueError('Ergebnis ist nicht endlich')
                    dec = format(converted, 'f')
                    if '.' in dec:
                        dec = dec.rstrip('0').rstrip('.')
                    formatted.append(dec)
                except (ValueError, InvalidOperation, ZeroDivisionError):
                    formatted.append('?')
        return ', '.join(formatted)

    # Register ranges: one row of decimal values and one aligned row of hex
    # bytes. A fixed six-character cell keeps the two representations aligned.
    # Single registers and ASCII/TIME/BITS keep the existing compact output.
    reg_width = max(5, *(len(reg) for reg, *_ in results))
    label_width = max(11, *(len(label) for _, label, *_ in results))
    prefix_width = reg_width + 2 + label_width + 2
    # Display unit is the interpreted unit returned by display_value(), not
    # the raw column from registers.txt (which can contain enum legends).
    # Align compact single-register rows without widening the whole table
    # to the length of multi-register scans.
    # Width must also include explicit comma lists. Their formatted text can
    # exceed a single value, but contiguous register dumps must not stretch
    # the compact table columns.
    compact_widths = []
    for entry, result in zip(entries, results):
        regs, _label, formula, _unit, typ = entry
        is_contiguous = (len(regs) > 1 and
                         regs == tuple(range(regs[0], regs[-1] + 1)))
        if len(regs) == 1 or typ in ('C', 'I,C', 'H,C'):
            compact_widths.append(min(len(result[2]), 17) if typ == 'C' and any(r in (102, 122, 123) for r in regs) else len(result[2]))
        elif (isinstance(formula, tuple) and formula[0] == 'COMB'):
            compact_widths.append(len(result[2]))
        elif (not is_contiguous or getattr(formula, 'nohex', False)) and formula != 'HEX':
            compact_widths.append(len(format_separate_values(regs, formula, typ)))
    value_width = max([5, *compact_widths])
    unit_width = max(7, max((len(result[3]) for entry, result in zip(entries, results)
                      if len(entry[0]) == 1 or entry[4] in ('C', 'I,C', 'H,C') or getattr(entry[2], 'nohex', False) or (isinstance(entry[2], tuple) and entry[2][0] == 'COMB')), default=4))
    header = (f'{"Reg":>{reg_width}}  {"Bezeichnung":<{label_width}}  '
              f'{"Wert":>{value_width}}  {"Einheit":<{unit_width}}  Status')
    pages = [[]]
    def emit(line=''):
        pages[-1].append(line)  # Preserve labeled-rule markers until final rendering
    emit(header)
    emit('__RULE__')
    result_iter = iter(results)
    for regs, label, formula, unit, typ in layout_entries:
        if not regs:
            directive_kind, directive_value = label
            if directive_kind == 'page':
                pages.append([header, '__RULE__'])
            elif directive_kind == 'blank':
                emit()
            elif directive_kind in ('line', 'double'):
                emit(('__RULE__' if directive_kind == 'line' else '__DOUBLE__')
                     if directive_value is None else
                     ('-' if directive_kind == 'line' else '=') * directive_value)
            elif directive_kind == 'labeled_line':
                emit(('__LABELED_RULE__', directive_value))
            elif directive_kind == 'section':
                emit(f'[{directive_value}]')
            else:
                emit(directive_value)
            continue
        reg, _label, value, shown_unit, status = next(result_iter)
        kind = 4 if typ == 'I' else 3
        prefix = f'{reg:>{reg_width}}  {label:<{label_width}}  '
        # Explicit comma lists (e.g. 30, 42) are separate measurements,
        # not a contiguous raw-register dump. Show each converted value,
        # without an unsolicited hexadecimal line. HEX remains explicit.
        is_contiguous = (len(regs) > 1 and
                         regs == tuple(range(regs[0], regs[-1] + 1)))
        if typ in ('I,C', 'H,C') or (typ != 'C' and len(regs) > 1 and isinstance(formula, tuple) and formula[0] == 'COMB'):
            emit(prefix + f'{value:>{value_width}}  {shown_unit:<{unit_width}}  [{status}]')
        elif typ != 'C' and len(regs) > 1 and (not is_contiguous or getattr(formula, 'nohex', False)) and formula != 'HEX':
            # Use the same column widths as single-register rows.
            formatted = format_separate_values(regs, formula, typ)
            emit(prefix + f'{formatted:>{value_width}}  '
                  f'{shown_unit:<{unit_width}}  [{status}]')
        elif typ != 'C' and len(regs) > 1 and formula not in ('ASCII', 'TIME'):

            # Each cell is 5 chars wide, followed by one separator space.
            # HEX: high byte then low byte, e.g. decimal 536 -> 02 18.
            raw_words = [values.get((kind, number)) for number in regs]
            decimal_cells = []
            hex_cells = []
            for raw in raw_words:
                if raw is None:
                    decimal_cells.append(f'{"?":>5}')
                    hex_cells.append('?? ??')
                    continue
                if is_null_raw(formula, raw):
                    decimal_cells.append(f'{"-":>5}')
                    hex_cells.append(f'{raw >> 8:02X} {raw & 0xFF:02X}')
                    continue
                try:
                    converted = formula(Decimal(raw)) if callable(formula) else Decimal(raw)
                    dec = format(converted, 'f').rstrip('0').rstrip('.') if '.' in format(converted, 'f') else format(converted, 'f')
                except (ValueError, InvalidOperation, ZeroDivisionError):
                    dec = '?'
                decimal_cells.append(f'{dec:>5}')
                hex_cells.append(f'{raw >> 8:02X} {raw & 0xFF:02X}')
            emit(prefix + ' '.join(decimal_cells) + (f'  {shown_unit}' if shown_unit else ''))
            emit(' ' * prefix_width + ' '.join(hex_cells) + f'  [{status}]')
        elif typ == 'C':
            # Config values share the same right-aligned value column as I/H.
            # Keep status on the same row even for variable-length ASCII data.
            for part in format_config_row(prefix, value, value_width, shown_unit, unit_width, status).splitlines():
                emit(part)
        else:
            emit(prefix + f'{value:>{value_width}}  {shown_unit:<{unit_width}}  [{status}]')
    # Calculate each page's width from actual rendered rows, not a fixed 120-char rule.
    # Explicit **SPALTE** / **SEITENUMBRUCH** starts the next screen column.
    # No BLE reads are repeated: only the final output is rearranged.
    page_widths = []
    for page in pages:
        width = max((len(f' {line[1]} ') if isinstance(line, tuple) and line[0] == '__LABELED_RULE__'
                     else len(line) for line in page
                     if line not in ('__RULE__', '__DOUBLE__')), default=len(header))
        page_widths.append(width)
    def render_layout_line(line, width):
        if line == '__RULE__':
            return '-' * width
        if line == '__DOUBLE__':
            return '=' * width
        if isinstance(line, tuple) and line[0] == '__LABELED_RULE__':
            caption = f' {line[1]} '
            remaining = max(0, width - len(caption))
            left = remaining // 2
            return '-' * left + caption + '-' * (remaining - left)
        return line

    rendered_pages = []
    for page, width in zip(pages, page_widths):
        rendered_pages.append([render_layout_line(line, width) for line in page])
    if len(rendered_pages) == 1:
        for line in rendered_pages[0]:
            print(line)
    else:
        from itertools import zip_longest
        for row in zip_longest(*rendered_pages, fillvalue=''):
            # Trim the right edge: no trailing whitespace beyond the last column.
            print('    '.join(cell.ljust(width) for cell, width in
                              zip(row, page_widths)).rstrip())
    print(f'\n{sum(status == "OK" for *_, status in results)}/{len(results)} '
          f'Einträge erfolgreich gelesen. {transactions} BLE-Abfragen, '
          f'{time.monotonic() - started:.2f}s insgesamt.')


def modbus_write_frame(register, value):
    """Wrap Modbus RTU FC06 in the same Growatt BLE 0x17 envelope as reads.

    RTU: unit(1), function(0x06), register(u16 BE), value(u16 BE), CRC16 LE.
    Outer: Growatt FC0x17, APP_SERIAL, RTU length(u16 BE), RTU bytes.
    Experimental: correct framing does not guarantee firmware write support.
    """
    req = struct.pack('>BBHH', 1, 0x06, register, value)
    req += struct.pack('<H', p.crc16_modbus(req))
    return p.build_frame(p.FUNC_PASSTHROUGH, p.APP_SERIAL + struct.pack('>H', len(req)) + req)


def decode_write_ack(data, register, value):
    """Validate inner RTU FC06 response; allow optional u16 length prefix.

    Returns (status, detail). No response is NOT proof of a failed write.
    """
    if len(data) < 10:
        return 'unknown', 'Äußere Antwort enthält keine vollständige Seriennummer.'
    body = data[10:]
    if len(body) >= 2 and int.from_bytes(body[:2], 'big') == len(body) - 2:
        body = body[2:]
    if len(body) < 5 or body[0] != 1:
        return 'unknown', 'Kein erkennbares Modbus-RTU-Telegramm: ' + body.hex(' ')
    if p.crc16_modbus(body[:-2]) != int.from_bytes(body[-2:], 'little'):
        return 'unknown', 'Innerer CRC ungültig: ' + body.hex(' ')
    if body[1] == 0x86 and len(body) == 5:
        return 'error', f'Modbus-Ausnahme 0x{body[2]:02X}'
    if body[1] != 0x06 or len(body) != 8:
        return 'unknown', 'Unerwartete Modbus-Antwort: ' + body.hex(' ')
    ack_reg, ack_value = struct.unpack('>HH', body[2:6])
    if (ack_reg, ack_value) != (register, value):
        return 'unknown', f'Antwort für Register {ack_reg}, Wert {ack_value} statt {register}, {value}'
    return 'ok', f'RTU FC06 Echo bestätigt Register {ack_reg} = {ack_value} (CRC OK)'


async def read_one(noah, kind, register, timeout, verbose=False):
    if kind == 4:
        window = input_window_for(register)
        if window is not None:
            start, count = window
            words = await read_input_window(noah, start, count, timeout, verbose)
            return words[register-start]
    frame = modbus_read_frame(kind, register, 1)
    payload = await receive_reply(noah, frame, timeout, verbose)
    if len(payload) < 10:
        raise ValueError('Registerantwort zu kurz')
    status, words = decode_modbus(payload[10:], kind, 1)
    if words is None:
        explain_register_reply(payload, kind, register, 1)
        raise ValueError(status + '; Rohdaten: ' + payload.hex(' '))
    return words[0]


async def manual_register(address, timeout=12, verbose=False):
    print('\nManueller Registerzugriff (dezimal, 0x... oder HH:MM bei TIME-Registern).')
    print('Input-Register: nur lesen. Holding-Register: lesen und optional schreiben.')
    choice = input('Registertyp [i=Input, h=Holding, Enter=Abbruch]: ').strip().lower()
    if choice not in ('i', 'h'):
        return
    kind = 4 if choice == 'i' else 3
    reg_text = input('Registernummer [0..65535, Enter=Abbruch]: ').strip()
    if not reg_text:
        return
    try:
        register = int(reg_text, 0) if reg_text.lower().startswith('0x') else int(reg_text, 10)
    except ValueError:
        print('Ungültige Registernummer.')
        return
    if not 0 <= register <= 65535:
        print('Register außerhalb des gültigen Bereichs.')
        return
    async with NoahBle(address, verbose=verbose) as noah:
        print(f'\nLese {"Input" if kind == 4 else "Holding"}-Register {register} ...')
        current = await read_one(noah, kind, register, timeout, verbose)
        print(f'Aktueller Rohwert: {current} (0x{current:04X})')
        if kind == 4:
            print('Input-Register sind nur lesbar. Keine Änderung.')
            return
        # Zeitformat nur für explizit als TIME deklarierte Holding-Register erlauben.
        # Das verhindert versehentliche Zeit-Codierungen in beliebigen Steuerregistern.
        try:
            entries = load_registers(REGISTER_FILE)
            is_time = any(register in regs and formula == 'TIME' and reg_type == 'H'
                          for regs, _label, formula, _unit, reg_type in entries)
        except (ValueError, OSError) as exc:
            print(f'Hinweis: Registerdatei nicht auswertbar ({exc}); Zeit-Eingabe deaktiviert.')
            is_time = False
        prompt = ('Neuer Wert (HH:MM, HH:MMh, dezimal oder 0x..., Enter=Abbruch): '
                  if is_time else 'Neuer Rohwert (dezimal oder 0x..., Enter=Abbruch): ')
        new_text = input(prompt).strip()
        if not new_text:
            print('Keine Änderung.')
            return
        time_match = re.fullmatch(r'(\d{1,2}):(\d{2})\s*[hH]?', new_text)
        if time_match:
            if not is_time:
                print('Zeitangaben sind nur für Holding-Register mit Datentyp TIME in registers.txt erlaubt.')
                return
            hours, minutes = map(int, time_match.groups())
            if hours > 23 or minutes > 59:
                print('Ungültige Uhrzeit. Erlaubt sind 00:00 bis 23:59.')
                return
            value = (hours << 8) | minutes
            print(f'Uhrzeit {hours:02d}:{minutes:02d} -> Rohwert {value} (0x{value:04X}).')
        else:
            try:
                value = int(new_text, 0) if new_text.lower().startswith('0x') else int(new_text, 10)
            except ValueError:
                print('Ungültiger Wert. Erwartet: Dezimalzahl, 0x... oder HH:MM bei TIME-Registern.')
                return
        if not 0 <= value <= 65535:
            print('Wert muss zwischen 0 und 65535 liegen.')
            return
        if value == current:
            print('Hinweis: Neuer Wert entspricht dem gelesenen Wert. Schreibbefehl wird auf Wunsch trotzdem gesendet.')
        print(f'\nACHTUNG: Schreibzugriff auf Hardware! Register {register}: {current} -> {value}.')
        print('Manche Register lösen unmittelbar Betriebsänderungen aus.')
        confirm = input('Zum Schreiben exakt JA eingeben (sonst Abbruch): ').strip()
        if confirm != 'JA':
            print('Schreibvorgang abgebrochen.')
            return
        print('Sende experimentell: Growatt BLE 0x17 mit innerem Modbus RTU 0x06 ...')
        try:
            payload = await receive_reply(noah, modbus_write_frame(register, value), timeout, verbose,
                                          expected_func=p.FUNC_PASSTHROUGH)
        except (TimeoutError, asyncio.TimeoutError):
            print('Keine 0x17-Schreibantwort erhalten. Ob der Befehl ausgeführt wurde, ist UNBEKANNT.')
        else:
            status, detail = decode_write_ack(payload, register, value)
            print(f'Schreibantwort [{status}]: {detail}')
            if status != 'ok':
                print('Rohdaten:', payload.hex(' '))
        print('Kontrollabfrage über 0x17 ...')
        try:
            actual = await read_one(noah, 3, register, timeout, verbose)
        except Exception as exc:
            print(f'Rücklesen fehlgeschlagen: {exc}')
            print('Ergebnis nicht verifiziert.')
        else:
            print(f'Tatsächlicher Rohwert: {actual} (0x{actual:04X})')
            if actual == value:
                if value == current:
                    print('Rücklesewert entspricht dem Sollwert; da er bereits vorher gleich war, bestätigt das Rücklesen keine Zustandsänderung.')
                else:
                    print('Verifiziert: gewünschter Wert ist gespeichert.')
            elif actual == current:
                print('Unverändert: ursprünglicher Wert weiterhin vorhanden.')
            else:
                print('Abweichung: Gerät liefert einen anderen Wert (z.B. begrenzt).')


async def restart_datalogger(address):
    """Send the existing Growatt BLE SET/0x20 restart command (not Modbus)."""
    print('\nACHTUNG: Neustart des Datenloggers kann WLAN/MQTT kurz unterbrechen.')
    print('Die Wirkung des Befehls ist am NOAH noch nicht abschließend verifiziert.')
    confirmation = input('Datenlogger dieses NOAH neu starten? [ja/NEIN]: ').strip().lower()
    if confirmation != 'ja':
        print('Neustart abgebrochen.')
        return
    print('Verbinde mit NOAH und authentifiziere ...')
    # NoahBle.__aenter__ handles BLE connect, notifications and Growatt auth.
    async with NoahBle(address) as noah:
        # Follow the original growatt_ble.py restart path: the device may reboot
        # before sending a normal Growatt SET-ACK, so only await the GATT write.
        await noah.client.write_gatt_char(p.CHAR_UUID, p.restart_request(), response=True)
        print('Neustartbefehl über BLE gesendet (GATT-Schreibvorgang bestätigt).')
        print('Ob der Datenlogger tatsächlich neu gestartet wurde, bitte separat prüfen.')


async def read_config_parameter(address):
    """Read one Growatt config parameter via 0x19 (not a Modbus register)."""
    print('\nGrowatt-Konfigurationsparameter lesen (Funktion 0x19, nur lesend).')
    print('Hinweis: Parameter-IDs sind keine Input-/Holding-Register.')
    print('Bekannte Beispiele: 55 Routerstatus, 60 Serverstatus, 61 SDK-Version, 80 Update-URL.')
    print('Alle Parameter-IDs sind abfragbar. Achtung: Ausgaben können Zugangsdaten enthalten!')
    raw = input('Parameter-ID [dezimal oder 0x..., Enter=Abbruch]: ').strip()
    if not raw:
        return
    try:
        param_id = int(raw, 0) if raw.lower().startswith('0x') else int(raw, 10)
    except ValueError:
        print('Ungültige Parameter-ID.')
        return
    if not 0 <= param_id <= 65535:
        print('Parameter-ID muss zwischen 0 und 65535 liegen.')
        return
    print(f'\nLese Konfigurationsparameter {param_id} (0x{param_id:04X}) ...')
    async with NoahBle(address) as noah:
        # NoahBle handles BLE connection, notification subscription and authentication.
        # Use the existing, original protocol.py query request (Growatt function 0x19).
        data = await receive_reply(noah, p.query_request([param_id]), 12, False,
                                   expected_func=p.FUNC_QUERY)
    status, values = p.parse_query_reply(data)
    print(f'Growatt QUERY-Status: {status}' + (' (OK)' if status == 0 else ' (Fehler)'))
    if status != 0:
        return
    if param_id not in values:
        print('Der Parameter wurde in der Antwort nicht geliefert.')
        return
    value = values[param_id]
    print(f'Länge: {len(value)} Byte')
    print('Hex:   ' + (value.hex(' ') if value else '(leer)'))
    if not value:
        print('ASCII: (leer)')
    else:
        # Make non-printable bytes visible rather than silently hiding them.
        decoded = value.decode('utf-8', errors='backslashreplace')
        safe = decoded.encode('unicode_escape').decode('ascii')
        print('Text:  ' + safe)
        if value.isascii() and value.decode('ascii').strip().isdigit():
            print('Zahl:  ' + value.decode('ascii').strip())
    if param_id == 80:
        print('Hinweis: Eine Update-URL belegt weder die aktuell installierte Firmware')
        print('noch einen erfolgreichen Firmware-Download.')


async def interactive_menu():
    print('\nNOAH BLE REGISTER TOOL v0.6.20 (experimentell)')
    while True:
        selected = await select_live_device(8)
        if selected is None:
            return
        address, name, _ = selected
        while True:
            print(f'\nGewähltes Gerät: {name} [{address}]')
            print('  1) Register aus registers.txt auslesen')
            print('  2) Register manuell lesen / schreiben')
            print('  3) Datenlogger über BLE neu starten')
            print('  4) Konfigurationsparameter lesen (Growatt 0x19)')
            print('  g) Anderes Gerät auswählen')
            print('  q) Beenden')
            action = input('Aktion: ').strip().lower()
            if action == 'q':
                return
            if action == 'g':
                break
            if action not in ('1', '2', '3', '4'):
                print('Ungültige Auswahl.')
                continue
            try:
                if action == '1':
                    args = argparse.Namespace(address=address, type='input', file=str(REGISTER_FILE), timeout=12, verbose=False, block_size=16, max_gap=2)
                    await read_list(args)
                elif action == '2':
                    await manual_register(address)
                elif action == '3':
                    await restart_datalogger(address)
                elif action == '4':
                    await read_config_parameter(address)
            except (OSError, ValueError, TimeoutError, asyncio.TimeoutError, RuntimeError) as exc:
                print(f'Fehler: {exc}')
            input('\nEnter für das Menü ...')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest='command')
    s = sub.add_parser('scan', help='NOAHs per BLE suchen')
    s.add_argument('--seconds', type=float, default=8)
    r = sub.add_parser('read', help='Register per BLE 0x17 lesen (experimentell)')
    r.add_argument('--address', required=True, help='BLE-Adresse')
    r.add_argument('--type', choices=['input', 'holding'], required=True)
    r.add_argument('--register', type=int, required=True)
    r.add_argument('--count', type=int, default=1)
    r.add_argument('--timeout', type=float, default=12)
    r.add_argument('--verbose', action='store_true')
    l = sub.add_parser('list', help='Register aus Textdatei lesen und formatiert anzeigen')
    l.add_argument('--address', required=True, help='BLE-Adresse')
    l.add_argument('--file', default=str(REGISTER_FILE), help='Textdatei Register|Bezeichnung|Faktor|Einheit[|Registertyp]')
    # Die Registertypen werden pro Zeile aus der Datei gelesen (Standard: I).
    # --type bleibt aus Kompatibilitätsgründen als veraltete Option erhalten.
    l.add_argument('--type', choices=['input', 'holding'], default='input', help='Veraltet; Registertyp steht in Datei')
    l.add_argument('--timeout', type=float, default=12)
    l.add_argument('--block-size', type=int, default=16, help='Max. Register pro Block (1..32)')
    l.add_argument('--max-gap', type=int, default=2, help='Max. ungenutzte Register zwischen Blöcken (0..8)')
    l.add_argument('--verbose', action='store_true')
    args = ap.parse_args()
    if args.command is None:
        try:
            asyncio.run(interactive_menu())
        except (KeyboardInterrupt, EOFError):
            print('\nBeendet.')
    elif args.command == 'scan':
        if args.seconds <= 0:
            ap.error('--seconds muss > 0 sein')
        asyncio.run(scan(args.seconds))
    elif args.command == 'list':
        if args.timeout <= 0:
            ap.error('--timeout muss > 0 sein')
        try:
            asyncio.run(read_list(args))
        except KeyboardInterrupt:
            print('\nAbgebrochen.')
        except (ValueError, OSError, asyncio.TimeoutError) as e:
            ap.exit(2, f'Fehler: {e}\n')
    else:
        if not 0 <= args.register <= 65535 or not 1 <= args.count <= 32 or args.register + args.count > 65536:
            ap.error('Register 0..65535; count 1..32; Bereich muss passen')
        if args.timeout <= 0:
            ap.error('--timeout muss > 0 sein')
        try:
            asyncio.run(read(args))
        except (ValueError, OSError, asyncio.TimeoutError) as e:
            ap.exit(2, f'Fehler: {e}\n')


if __name__ == '__main__':
    main()
