#!/usr/bin/env python3
"""Growatt NOAH BLE Watchdog v0.4.2 – experimental, opt-in recovery."""
import argparse
import asyncio
import configparser
import contextlib
import curses
import io
import json
import logging
import os
import re
from pathlib import Path
import sys
import time

from bleak import BleakScanner
import protocol as p
from growatt_ble import NoahBle

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / 'noah_watchdog_devices.json'
CREDENTIALS = ROOT / 'credentials.txt'
LOG = ROOT / 'noah_watchdog.log'
DEFAULTS = {'logging': True, 'wifi': False, 'mqtt': False, 'restart': False, 'cycles': 3}


def load_config():
    if not CONFIG.exists():
        return [], DEFAULTS.copy()
    data = json.loads(CONFIG.read_text(encoding='utf-8'))
    if isinstance(data, list):  # v0.1.x compatibility
        return [d for d in data if isinstance(d, dict) and d.get('address')], DEFAULTS.copy()
    if not isinstance(data, dict):
        raise ValueError('Ungültige Konfigurationsdatei')
    devices = [d for d in data.get('devices', []) if isinstance(d, dict) and d.get('address')]
    options = DEFAULTS.copy()
    for key in ('logging', 'wifi', 'mqtt', 'restart'):
        if isinstance(data.get('options', {}).get(key), bool):
            options[key] = data['options'][key]
    cycles = data.get('options', {}).get('cycles', 3)
    if type(cycles) is int and cycles in (1, 2, 3):
        options['cycles'] = cycles
    return devices, options


def save_config(devices, options):
    CONFIG.write_text(json.dumps({'devices': devices, 'options': options}, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    if os.name != 'nt':
        CONFIG.chmod(0o600)


async def discover(seconds):
    found = await BleakScanner.discover(timeout=seconds, return_adv=True)
    devices = []
    for dev, adv in found.values():
        if p.SERVICE_UUID.lower() not in {u.lower() for u in adv.service_uuids}:
            continue
        devices.append({'address': dev.address, 'name': dev.name or '(ohne Namen)', 'rssi': adv.rssi})
    return sorted(devices, key=lambda x: x['name'])


def choose_ui(stdscr, candidates, previous, initial):
    curses.curs_set(0)
    stdscr.keypad(True)
    chosen = {d['address'] for d in previous}
    opts = initial.copy()
    actions = [('logging', 'Logging'), ('wifi', 'WLAN-Daten neu schreiben'),
               ('mqtt', 'MQTT-Daten neu schreiben'), ('restart', 'Datalogger NOAH neu starten')]
    pos = 0
    offset = 0
    while True:
        rows = [('header', 'NOAH BLE Watchdog – Setup'), ('info', '↑/↓: bewegen  Leertaste: ändern  Enter: speichern  q: abbrechen'),
                ('blank', ''), ('header', 'Geräte auswählen:')]
        for d in candidates:
            mark = 'x' if d['address'] in chosen else ' '
            rows.append(('device', f"[{mark}] {d['name'][:20]:<20} {d['address']} {d.get('rssi', -999):>4} dBm", d['address']))
        rows.extend([('blank', ''), ('header', 'Maßnahmen:')])
        for key, label in actions:
            rows.append(('action', f"[{'x' if opts[key] else ' '}] {label}", key))
        rows.append(('cycles', f"Automatischer Recoveryversuch nach [{opts['cycles']}] Zyklen"))
        selectable = [i for i, row in enumerate(rows) if row[0] in ('device', 'action', 'cycles')]
        if not selectable:
            return None
        pos = min(pos, len(selectable) - 1)
        active = selectable[pos]
        h, w = stdscr.getmaxyx()
        offset = max(0, min(offset, max(0, len(rows) - h)))
        if active < offset:
            offset = active
        if active >= offset + h:
            offset = active - h + 1
        stdscr.erase()
        for i in range(offset, min(len(rows), offset + h)):
            try:
                stdscr.addnstr(i - offset, 0, rows[i][1], max(1, w - 1), curses.A_REVERSE if i == active else 0)
            except curses.error:
                pass
        stdscr.refresh()
        key = stdscr.getch()
        if key in (curses.KEY_UP, ord('k')):
            pos = max(0, pos - 1)
        elif key in (curses.KEY_DOWN, ord('j')):
            pos = min(len(selectable) - 1, pos + 1)
        elif key == ord(' '):
            row = rows[active]
            if row[0] == 'device':
                if row[2] in chosen:
                    chosen.remove(row[2])
                else:
                    chosen.add(row[2])
            elif row[0] == 'action':
                opts[row[2]] = not opts[row[2]]
            else:
                opts['cycles'] = opts['cycles'] % 3 + 1
        elif key in (10, 13, curses.KEY_ENTER):
            return ([{'address': d['address'], 'name': d['name']} for d in candidates if d['address'] in chosen], opts)
        elif key in (ord('q'), 27):
            return None


async def configure(seconds):
    print(f'Scanne {seconds:g} Sekunden nach NOAH-Geräten ...', flush=True)
    candidates = await discover(seconds)
    previous, options = load_config()
    seen = {d['address'].upper() for d in candidates}
    for d in previous:
        if d['address'].upper() not in seen:
            candidates.append({**d, 'rssi': -999})
    if not candidates:
        print('Keine NOAHs gefunden und keine Geräte gespeichert.')
        return
    result = curses.wrapper(choose_ui, candidates, previous, options)
    if result is None:
        print('Abgebrochen; keine Änderung.')
        return
    save_config(*result)
    print(f'{len(result[0])} Gerät(e) und Maßnahmen gespeichert: {CONFIG}')


def load_credentials(options):
    if not (options['wifi'] or options['mqtt']):
        return None
    if not CREDENTIALS.is_file():
        raise ValueError('credentials.txt fehlt; Recovery mit WLAN/MQTT nicht möglich')
    if os.name != 'nt' and CREDENTIALS.stat().st_mode & 0o077:
        raise ValueError('credentials.txt ist für Gruppe/Andere zugänglich; zuerst chmod 600 credentials.txt')
    cp = configparser.ConfigParser(interpolation=None)
    cp.read(CREDENTIALS, encoding='utf-8')
    if options['wifi'] and (not cp.get('WIFI', 'ssid', fallback='') or not cp.get('WIFI', 'password', fallback='')):
        raise ValueError('WIFI ssid/password fehlen in credentials.txt')
    if options['mqtt']:
        host = cp.get('MQTT', 'host', fallback='')
        port = cp.get('MQTT', 'port', fallback='')
        if not host or not port.isdecimal() or not 1 <= int(port) <= 65535:
            raise ValueError('MQTT host/port fehlen oder sind ungültig')
    return cp


def decode(raw):
    return raw.decode('utf-8', errors='replace').strip('\x00') if isinstance(raw, bytes) else str(raw)


class MQTTRecoveryError(Exception):
    """MQTT configuration repair was attempted but could not be verified."""


def mqtt_target(cp):
    return {p.P_MQTT_HOST: cp.get('MQTT', 'host'),
            p.P_MQTT_PORT: cp.get('MQTT', 'port')}


def mqtt_differences(values, cp):
    """Compare only complete, successfully read values; never guess missing data."""
    target = mqtt_target(cp)
    missing = [pid for pid in target if pid not in values or values[pid] is None]
    if missing:
        raise ValueError('MQTT-Konfigurationsabfrage unvollständig')
    return [(pid, expected) for pid, expected in target.items()
            if decode(values[pid]) != expected]


async def repair_mqtt(noah, device, cp, logger, current=None):
    """Update differing MQTT fields only, then verify by querying again."""
    name, address = device.get('name', ''), device['address']
    if current is None:
        current = await noah.query([p.P_MQTT_HOST, p.P_MQTT_PORT])
    updates = mqtt_differences(current, cp)
    if not updates:
        return False
    logger.warning('%s %s | MQTT RECOVERY START: Abweichung bei %s erkannt; stelle Sollwerte wieder her',
                   name, address, ', '.join(p.PARAM_NAMES.get(pid, str(pid)) for pid, _ in updates))
    try:
        await noah.set_params(updates)
        verified = await noah.query([p.P_MQTT_HOST, p.P_MQTT_PORT])
        remaining = mqtt_differences(verified, cp)
        if remaining:
            raise ValueError('MQTT-Konfiguration nach Schreibzugriff weiterhin abweichend')
    except Exception as exc:
        raise MQTTRecoveryError('MQTT-Korrektur oder Verifikation fehlgeschlagen') from exc
    logger.warning('%s %s | MQTT RECOVERY ERFOLGREICH: Sollwerte geschrieben und verifiziert', name, address)
    return True


async def recovery(noah, device, options, cp, logger):
    """Perform selected steps in order; reconnect separately after the 30-second delay."""
    name, address = device.get('name', ''), device['address']
    actions = []
    if options['wifi']:
        actions.append('WLAN')
        await noah.set_params([(p.P_WIFI_SSID, cp.get('WIFI', 'ssid')),
                               (p.P_WIFI_PASSWORD, cp.get('WIFI', 'password'))])
        logger.warning('%s %s | RECOVERY WLAN-Daten erneut übertragen', name, address)
    if options['mqtt']:
        actions.append('MQTT')
        changed = await repair_mqtt(noah, device, cp, logger)
        if not changed:
            logger.warning('%s %s | RECOVERY MQTT-Sollwerte bereits vorhanden', name, address)
    if options['restart']:
        actions.append('Restart')
        # Restart may sever BLE immediately; writing the request is the best available confirmation.
        await noah.client.write_gatt_char(p.CHAR_UUID, p.restart_request(), response=True)
        logger.warning('%s %s | RECOVERY Datalogger-Neustart gesendet', name, address)
    return ', '.join(actions)


async def poll_one(device, options, cp, streak):
    address, name = device['address'], device.get('name', '')
    logger = logging.getLogger('noah')
    output = io.StringIO()
    try:
        with contextlib.redirect_stdout(output):
            async with asyncio.timeout(35):
                async with NoahBle(address) as noah:
                    requested = [p.P_WIFI_STATUS, p.P_CONNECTION_STATE]
                    if options['mqtt']:
                        requested.extend((p.P_MQTT_HOST, p.P_MQTT_PORT))
                    values = await noah.query(requested)
                    # MQTT consistency is checked on EVERY successful poll,
                    # independently of the connection-state recovery threshold.
                    if options['mqtt']:
                        try:
                            mqtt_changed = await repair_mqtt(noah, device, cp, logger, values)
                            if options['logging'] and not mqtt_changed:
                                logger.info('%s %s | MQTT PRÜFUNG OK: Server und Port stimmen mit credentials.txt überein',
                                            name, address)
                        except MQTTRecoveryError as exc:
                            logger.error('%s %s | MQTT RECOVERY FEHLGESCHLAGEN: %s',
                                         name, address, type(exc.__cause__).__name__ if exc.__cause__ else type(exc).__name__)
                        except Exception as exc:
                            logger.error('%s %s | MQTT PRÜFUNG FEHLGESCHLAGEN: %s',
                                         name, address, type(exc).__name__)
        wifi = decode(values.get(p.P_WIFI_STATUS, b''))
        state = decode(values.get(p.P_CONNECTION_STATE, b''))
        if options['logging']:
            # Log only allowlisted protocol metadata. Never forward raw MQTT
            # query responses, which may include credentials or broker details.
            for line in output.getvalue().splitlines():
                if re.fullmatch(r'dev->app SET-ACK sn=[A-Za-z0-9]+ count=\d+ status=\d+', line.strip()):
                    logger.info('%s %s | %s', name, address, line.strip())
                elif line.strip().startswith('dev->app QUERY-RSP '):
                    match = re.match(r'dev->app QUERY-RSP sn=([A-Za-z0-9]+) status=(\d+)(?:\s|$)', line.strip())
                    if match:
                        logger.info('%s %s | dev->app QUERY-RSP sn=%s status=%s wifi_status=%r, connection_state=%r',
                                    name, address, match.group(1), match.group(2),
                                    values.get(p.P_WIFI_STATUS, b''), values.get(p.P_CONNECTION_STATE, b''))
            # Keep a status line even if a library version changes its stdout format.
            if not any(line.strip().startswith('dev->app QUERY-RSP ') for line in output.getvalue().splitlines()):
                logger.info('%s %s | STATUS wifi_status=%r, connection_state=%r',
                            name, address, values.get(p.P_WIFI_STATUS, b''), values.get(p.P_CONNECTION_STATE, b''))
        if wifi == '0' and state != '16' and state != '':
            streak[address] = streak.get(address, 0) + 1
        else:
            streak[address] = 0
        if not (options['wifi'] or options['mqtt'] or options['restart']):
            return
        if streak[address] < options['cycles']:
            return
        logger.warning('%s %s | RECOVERY START: wifi_status=%s connection_state=%s, %d auffällige Zyklen',
                       name, address, wifi, state, streak[address])
        streak[address] = 0  # At most one attempt per device per cycle.
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                async with asyncio.timeout(45):
                    async with NoahBle(address) as noah:
                        actions = await recovery(noah, device, options, cp, logger)
        except Exception as exc:
            # Never expose exception messages here: BLE/library exceptions can contain data.
            if isinstance(exc, MQTTRecoveryError):
                logger.error('%s %s | MQTT RECOVERY FEHLGESCHLAGEN: %s',
                             name, address, type(exc.__cause__).__name__ if exc.__cause__ else type(exc).__name__)
            else:
                logger.error('%s %s | RECOVERY Aktion fehlgeschlagen: %s', name, address, type(exc).__name__)
            return
        logger.warning('%s %s | RECOVERY Maßnahmen: %s; warte 30 Sekunden', name, address, actions)
        await asyncio.sleep(30)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                async with asyncio.timeout(35):
                    async with NoahBle(address) as noah:
                        checked = await noah.query([p.P_CONNECTION_STATE])
            new_state = decode(checked.get(p.P_CONNECTION_STATE, b''))
            if new_state == '16':
                logger.warning('%s %s | RECOVERY ERFOLGREICH (connection_state=16)', name, address)
            else:
                logger.error('%s %s | RECOVERY NICHT ERFOLGREICH (connection_state=%s)', name, address, new_state)
        except Exception as exc:
            logger.error('%s %s | RECOVERY NICHT ERFOLGREICH (Kontrollabfrage: %s)', name, address, type(exc).__name__)
    except Exception as exc:
        streak[address] = 0
        logger.error('%s %s | BLE-Abfrage fehlgeschlagen: %s', name, address, type(exc).__name__)


async def monitor(minutes, scan_seconds):
    devices, options = load_config()
    if not devices:
        print('Keine Geräte ausgewählt. Zuerst: python noah_watchdog.py setup')
        return
    cp = load_credentials(options)
    logger = logging.getLogger('noah')
    logger.warning('Überwachung gestartet: %d Geräte, Zyklus %.2f Minuten, Recovery=%s, Schwelle=%d',
                   len(devices), minutes, ','.join(k for k in ('wifi', 'mqtt', 'restart') if options[k]) or 'aus', options['cycles'])
    streak = {}
    while True:
        cycle_start = time.monotonic()
        try:
            visible = {d['address'].upper() for d in await discover(scan_seconds)}
        except Exception as exc:
            logger.error('BLE-Scan fehlgeschlagen: %s', type(exc).__name__)
            visible = None
        if visible is not None:
            for device in devices:
                address = device['address']
                if address.upper() not in visible:
                    streak[address] = 0  # Not visible is not evidence of network failure.
                    if options['logging']:
                        logger.info('%s %s | NICHT SICHTBAR (Scan %.0f s)', device.get('name', ''), address, scan_seconds)
                    continue
                await poll_one(device, options, cp, streak)
                await asyncio.sleep(1)
        remaining = max(0, minutes * 60 - (time.monotonic() - cycle_start))
        if options['logging']:
            logger.info('Zyklus abgeschlossen; nächste Runde in %.0f Sekunden', remaining)
        await asyncio.sleep(remaining)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    setup = sub.add_parser('setup', help='Geräte und Recovery-Maßnahmen auswählen')
    setup.add_argument('--timeout', type=float, default=8)
    run = sub.add_parser('run', help='Geräte nacheinander überwachen')
    run.add_argument('--minutes', type=float, default=5)
    run.add_argument('--scan-timeout', type=float, default=8)
    args = parser.parse_args()
    if args.command == 'setup':
        if args.timeout <= 0:
            parser.error('--timeout muss positiv sein')
        asyncio.run(configure(args.timeout))
    else:
        if args.minutes <= 0 or args.scan_timeout <= 0:
            parser.error('Intervalle müssen positiv sein')
        logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
                            handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(LOG, encoding='utf-8')])
        try:
            asyncio.run(monitor(args.minutes, args.scan_timeout))
        except KeyboardInterrupt:
            print('\nÜberwachung beendet.')
        except (ValueError, configparser.Error) as exc:
            print(f'Konfigurationsfehler: {exc}', file=sys.stderr)
            sys.exit(2)


if __name__ == '__main__':
    main()
