#!/usr/bin/env python3
"""Interactive, read-only Growatt NOAH BLE monitor.

Place alongside growatt_ble.py and protocol.py. Requires bleak and the existing
Growatt protocol implementation. Select NOAHs with arrows/space/Enter; monitor
one device at a time every five minutes.
"""
import argparse
import asyncio
import contextlib
import curses
import io
import json
import logging
from pathlib import Path
import sys
import time

from bleak import BleakScanner
import protocol as p
from growatt_ble import NoahBle

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "noah_watchdog_devices.json"
LOG = ROOT / "noah_watchdog.log"


def load_devices():
    if not CONFIG.exists():
        return []
    data = json.loads(CONFIG.read_text(encoding="utf-8"))
    return [x for x in data if isinstance(x, dict) and x.get("address")]


def save_devices(devices):
    CONFIG.write_text(json.dumps(devices, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


async def discover(seconds):
    found = await BleakScanner.discover(timeout=seconds, return_adv=True)
    devices = []
    for dev, adv in found.values():
        if p.SERVICE_UUID.lower() not in {u.lower() for u in adv.service_uuids}:
            continue
        devices.append({"address": dev.address, "name": dev.name or "(ohne Namen)", "rssi": adv.rssi})
    return sorted(devices, key=lambda x: x["name"])


def choose_ui(stdscr, candidates, previously):
    curses.curs_set(0)
    stdscr.keypad(True)
    pos = 0
    chosen = {d["address"] for d in previously}
    while True:
        stdscr.erase()
        height, width = stdscr.getmaxyx()
        lines = ["NOAH BLE – Geräte auswählen", "↑/↓ bewegen  Leertaste markieren  Enter speichern  q abbrechen", ""]
        for i, line in enumerate(lines[:height]):
            stdscr.addnstr(i, 0, line, max(1, width - 1))
        start = max(0, min(pos - max(0, height - 5) + 1, len(candidates) - max(1, height - 4)))
        for row, item in enumerate(candidates[start:start + max(1, height - 4)], 3):
            idx = start + row - 3
            marker = "[x]" if item["address"] in chosen else "[ ]"
            label = f"{marker} {item['name']:<20.20} {item['address']}  {item['rssi']:>4} dBm"
            stdscr.addnstr(row, 0, label, max(1, width - 1), curses.A_REVERSE if idx == pos else 0)
        stdscr.refresh()
        key = stdscr.getch()
        if key in (curses.KEY_UP, ord('k')):
            pos = max(0, pos - 1)
        elif key in (curses.KEY_DOWN, ord('j')):
            pos = min(len(candidates) - 1, pos + 1)
        elif key == ord(' '):
            address = candidates[pos]["address"]
            if address in chosen:
                chosen.remove(address)
            else:
                chosen.add(address)
        elif key in (10, 13, curses.KEY_ENTER):
            return [{"address": d["address"], "name": d["name"]} for d in candidates if d["address"] in chosen]
        elif key in (ord('q'), 27):
            return None


async def configure(seconds):
    print(f"Scanne {seconds:g} Sekunden nach NOAH-Geräten ...", flush=True)
    candidates = await discover(seconds)
    if not candidates:
        print("Keine Geräte mit Growatt-NOAH-Service gefunden. Vorhandene Auswahl bleibt erhalten.")
        return
    previous = load_devices()
    # Keep previously selected devices even if not visible in this scan.
    seen = {d["address"] for d in candidates}
    for d in previous:
        if d["address"] not in seen:
            candidates.append({**d, "rssi": -999})
    selected = curses.wrapper(choose_ui, candidates, previous)
    if selected is None:
        print("Auswahl abgebrochen; keine Änderung.")
        return
    save_devices(selected)
    print(f"{len(selected)} Gerät(e) gespeichert: {CONFIG}")


async def poll_one(device):
    address = device["address"]
    name = device.get("name", "")
    logger = logging.getLogger("noah")
    try:
        # Capture the original library's decoded response lines without
        # making assumptions about the parse_query_reply return structure.
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            async with asyncio.timeout(35):
                async with NoahBle(address) as noah:
                    await noah.query([p.P_WIFI_STATUS, p.P_CONNECTION_STATE])
        lines = [line.strip() for line in output.getvalue().splitlines() if line.strip()]
        for line in lines:
            logger.info("%s %s | %s", name, address, line)
        if not lines:
            logger.warning("%s %s | Abfrage ohne Antworttext", name, address)
    except Exception as exc:
        logger.error("%s %s | FEHLER: %s: %s", name, address, type(exc).__name__, exc)


async def monitor(minutes, scan_seconds):
    devices = load_devices()
    if not devices:
        print(f"Keine Geräte ausgewählt. Zuerst: python {Path(__file__).name} setup")
        return
    logger = logging.getLogger("noah")
    logger.info("Überwachung gestartet: %d Geräte, Zyklus %.2f Minuten, nur Lesen", len(devices), minutes)
    try:
        while True:
            cycle_start = time.monotonic()
            # Scan first: sleeping or powered-off NOAHs must not be treated as errors.
            try:
                visible = {d["address"].upper() for d in await discover(scan_seconds)}
            except Exception as exc:
                logger.error("BLE-Scan fehlgeschlagen (%s: %s); keine Geräteabfragen in diesem Zyklus", type(exc).__name__, exc)
                visible = None
            if visible is not None:
                for device in devices:
                    address = device["address"]
                    name = device.get("name", "")
                    if address.upper() not in visible:
                        logger.info("%s %s | NICHT SICHTBAR (Scan %.0f s); Abfrage übersprungen", name, address, scan_seconds)
                        continue
                    await poll_one(device)  # strictly sequential; no gather/tasks
                    await asyncio.sleep(1)
            remaining = max(0, minutes * 60 - (time.monotonic() - cycle_start))
            logger.info("Zyklus abgeschlossen; nächste Runde in %.0f Sekunden", remaining)
            await asyncio.sleep(remaining)
    except asyncio.CancelledError:
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    setup = sub.add_parser("setup", help="NOAHs scannen und per Tastatur auswählen")
    setup.add_argument("--timeout", type=float, default=8, help="Scandauer in Sekunden (Standard: 8)")
    run = sub.add_parser("run", help="Ausgewählte Geräte nacheinander überwachen")
    run.add_argument("--minutes", type=float, default=5, help="Zyklusdauer in Minuten (Standard: 5)")
    run.add_argument("--scan-timeout", type=float, default=8, help="Scandauer vor jedem Zyklus in Sekunden (Standard: 8)")
    args = parser.parse_args()
    if args.command == "setup":
        if args.timeout <= 0:
            parser.error("--timeout muss positiv sein")
        asyncio.run(configure(args.timeout))
    else:
        if args.minutes <= 0:
            parser.error("--minutes muss positiv sein")
        if args.scan_timeout <= 0:
            parser.error("--scan-timeout muss positiv sein")
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(LOG, encoding="utf-8")])
        try:
            asyncio.run(monitor(args.minutes, args.scan_timeout))
        except KeyboardInterrupt:
            print("\nÜberwachung beendet.")


if __name__ == "__main__":
    main()
