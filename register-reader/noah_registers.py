#!/usr/bin/env python3
"""Experimental Growatt NOAH BLE register diagnostic (v0.5.6).

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
    expr = source.strip().replace('−', '-').replace('–', '-').replace(',', '.')
    if not expr or expr.upper() in ('ASCII', 'TIME'):
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
    return lambda x: evaluate(tree, Decimal(x))


def parse_enum(text):
    """Interpret e.g. '0 Idle, 1 Laden, 2 Entladen' as value labels."""
    parts = [part.strip() for part in text.split(',')]
    result = {}
    for part in parts:
        match = re.fullmatch(r'(\d+)\s+(.+)', part)
        if not match:
            return None
        result[int(match.group(1))] = match.group(2).strip()
    return result if len(result) >= 2 else None


def load_registers(path):
    if not path.is_file():
        raise ValueError(f'Registerdatei nicht gefunden: {path}')
    entries = []
    for lineno, raw in enumerate(path.read_text(encoding='utf-8-sig').splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        cols = [v.strip() for v in line.split('|')]
        if len(cols) in (4, 5) and [c.lower() for c in cols[:4]] == ['register', 'bezeichnung', 'faktor', 'einheit']:
            continue
        if len(cols) not in (4, 5):
            raise ValueError(f'{path.name}:{lineno}: erwartet Register|Bezeichnung|Faktor|Einheit[|Registertyp]')
        reg_text, label, factor_text, unit = cols[:4]
        register_type = (cols[4] if len(cols) == 5 else 'I').strip().upper() or 'I'
        if register_type not in ('I', 'H'):
            raise ValueError(f'{path.name}:{lineno}: Registertyp muss I oder H sein')
        try:
            expanded = []
            for item in reg_text.split(','):
                item = item.strip()
                match = re.fullmatch(r'(\d+)\s*-\s*(\d+)', item)
                if match:
                    first, last = map(int, match.groups())
                    if first > last:
                        raise ValueError('absteigender Registerbereich')
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
        if formula == 'ASCII' and not regs:
            raise ValueError(f'{path.name}:{lineno}: ASCII benötigt Register')
        if len(regs) > 1 and formula != 'ASCII':
            raise ValueError(f'{path.name}:{lineno}: mehrere Register derzeit nur mit ASCII unterstützt')
        entries.append((regs, label, formula, unit, register_type))
    if not entries:
        raise ValueError('Registerdatei enthält keine Register')
    return entries


def display_value(values, formula, unit):
    if formula == 'TIME':
        raw = values[0]
        hours, minutes = raw >> 8, raw & 0xFF
        if hours > 23 or minutes > 59:
            raise ValueError(f'ungültige HH:MM-Codierung: 0x{raw:04X}')
        return f'{hours:02d}:{minutes:02d}', ''
    if formula == 'ASCII':
        # Modbus words are big-endian; combine in the order from registers.txt.
        raw = b''.join(struct.pack('>H', word) for word in values)
        return raw.rstrip(b'\x00').decode('ascii', errors='replace'), ''
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
    frame = modbus_read_frame(kind, args.register, args.count)
    async with NoahBle(address, verbose=args.verbose) as noah:
        try:
            payload = await receive_reply(noah, frame, args.timeout, args.verbose)
        except TimeoutError:
            print('Keine 0x17-Antwort. Dies beweist NICHT, dass das Register fehlt.')
            return
    print('Antwortdaten (entschlüsselt):', payload.hex(' '))
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
    entries = load_registers(Path(args.file))
    address = resolve_device(args)
    max_words = getattr(args, 'block_size', 16)
    max_gap = getattr(args, 'max_gap', 2)
    if not 1 <= max_words <= 32 or not 0 <= max_gap <= 8:
        raise ValueError('block-size muss 1..32 und max-gap 0..8 sein')
    wanted = {kind: set() for kind in (3, 4)}
    for regs, _, _, _, typ in entries:
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

        for kind, start, count in blocks:
            t0 = time.monotonic()
            try:
                words = await fetch(kind, start, count)
                for offset, value in enumerate(words):
                    if start + offset in wanted[kind]:
                        values[(kind, start + offset)] = value
                print(f'  {"I" if kind == 4 else "H"} {start}-{start + count - 1}: '
                      f'OK ({time.monotonic() - t0:.2f}s)')
            except Exception as exc:
                print(f'  {"I" if kind == 4 else "H"} {start}-{start + count - 1}: '
                      f'Block fehlgeschlagen ({type(exc).__name__}: {exc}); '
                      'Einzelregister-Fallback')
                for reg in range(start, start + count):
                    if reg not in wanted[kind]:
                        continue
                    try:
                        words = await fetch(kind, reg, 1)
                        values[(kind, reg)] = words[0]
                    except Exception as fallback_exc:
                        errors[(kind, reg)] = (f'{type(fallback_exc).__name__}: '
                                               f'{fallback_exc}')

    results = []
    for regs, label, formula, unit, typ in entries:
        kind = 4 if typ == 'I' else 3
        missing = [reg for reg in regs if (kind, reg) not in values]
        reg_label = ','.join(str(reg) for reg in regs)
        if missing:
            error = '; '.join(f'{reg}: {errors.get((kind, reg), "kein Wert")}'
                              for reg in missing)
            results.append((reg_label, label, '—', unit, error))
            continue
        try:
            rendered, shown_unit = display_value([values[(kind, reg)] for reg in regs],
                                                 formula, unit)
            results.append((reg_label, label, rendered, shown_unit, 'OK'))
        except (ValueError, InvalidOperation, ZeroDivisionError, UnicodeError) as exc:
            results.append((reg_label, label, '—', unit, f'Umrechnung: {exc}'))

    reg_width = max(5, *(len(reg) for reg, *_ in results))
    label_width = max(11, *(len(label) for _, label, *_ in results))
    value_width = max(12, *(len(value) for _, _, value, _, _ in results))
    print(f'{"Reg":>{reg_width}}  {"Bezeichnung":<{label_width}}  '
          f'{"Wert":>{value_width}}  {"Einheit":<8}  Status')
    print('-' * (reg_width + label_width + value_width + 32))
    for reg, label, value, unit, status in results:
        print(f'{reg:>{reg_width}}  {label:<{label_width}}  '
              f'{value:>{value_width}}  {unit:<8}  {status}')
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
    frame = modbus_read_frame(kind, register, 1)
    payload = await receive_reply(noah, frame, timeout, verbose)
    if len(payload) < 10:
        raise ValueError('Registerantwort zu kurz')
    status, words = decode_modbus(payload[10:], kind, 1)
    if words is None:
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
    print('Sensible Parameter (z.B. 54 BLE-Schlüssel, 57 WLAN-Passwort) werden nicht angezeigt.')
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
    if param_id in (54, 57):
        print('Dieser Parameter enthält Zugangsdaten/Schlüssel; Ausgabe aus Sicherheitsgründen gesperrt.')
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
    print('\nNOAH BLE REGISTER TOOL v0.5.6 (experimentell)')
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
                else:
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
