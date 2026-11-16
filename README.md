# Growatt NOAH BLE Watchdog

An experimental standalone Bluetooth Low Energy (BLE) watchdog for Growatt NOAH battery storage systems. Built on the BLE protocol implementation by [Peter Meiser](https://github.com/meiser79/growatt_noah_ble).

> **v0.1.0 — Monitoring Only.** This release collects and logs connection status information. It does **not** automatically repair connections, rewrite Wi-Fi credentials, modify MQTT settings, or restart devices.

## Project status and roadmap

Some NOAH devices may lose their Wi-Fi or MQTT connection and fail to reconnect without manual intervention. The long-term goal is to detect persistent connectivity problems and attempt recovery via BLE by reapplying the configured Wi-Fi credentials and/or MQTT broker settings.

### Available now

- Discover NOAH devices advertising the Growatt BLE service.
- Select monitored devices interactively (arrow keys, Space, Enter).
- Scan before each monitoring cycle (default: every five minutes).
- Connect to visible selected devices **sequentially**, not concurrently.
- Query and log `wifi_status` and `connection_state`.
- Log devices that are not BLE-visible without automatically treating them as faulty (devices may sleep or power down).
- Save selected device addresses locally for future runs.

### Planned (not implemented)

- Identify persistent Wi-Fi and/or MQTT connection failures, rather than reacting to brief interruptions.
- Optional **Recovery Mode** that rewrites stored Wi-Fi credentials and/or MQTT broker configuration via BLE.
- Check whether a recovery attempt succeeded and log its outcome.
- Rate-limit retries and avoid repeated or unnecessary changes.
- Retain **Monitor Mode** as a non-recovery option.

**Important:** The meanings of all observed BLE status values and the effectiveness of rewriting configuration as a recovery method are not yet fully validated. Automatic recovery will not be enabled until these behaviors have been tested. Please share reproducible observations of `wifi_status` and `connection_state` through GitHub Issues; redact credentials, serial numbers, and other identifying details.

## Compatibility and requirements

- **Linux:** Tested on a Raspberry Pi 5 running Debian with a working BLE adapter and BlueZ.
- **Windows:** Successfully tested on Windows 11 with Python 3.14 and a working Bluetooth adapter. BLE discovery, interactive device selection, authentication, status queries, and logging worked in this test.
- **Python:** 3.11 or newer (`asyncio.timeout` is used).
- Bluetooth Low Energy support and permission to access the adapter.
- On Windows, the interactive terminal interface uses `windows-curses`, which is installed automatically by the platform-specific entry in `requirements.txt`.

## Installation

### Linux (Debian / Raspberry Pi)

```bash
git clone https://github.com/nixo2/growatt-noah-ble-watchdog.git
cd growatt-noah-ble-watchdog
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

### Windows 11 (PowerShell)

Clone the repository using Git, or download and extract the ZIP archive from GitHub. Open PowerShell in the project folder, then run:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

The `requirements.txt` file installs `windows-curses` automatically on Windows (and skips it on Linux). No separate installation command is needed. You do not need to activate the virtual environment when using the explicit Python path shown above.

To select devices and start monitoring on Windows:

```powershell
.\.venv\Scripts\python.exe noah_watchdog.py setup --timeout 30
.\.venv\Scripts\python.exe noah_watchdog.py run
```

The generic `python` commands in the sections below work on Linux after activating the virtual environment. On Windows, use the explicit `.\.venv\Scripts\python.exe` path instead, or activate the environment first.

## Choose devices

```bash
python noah_watchdog.py setup --timeout 30
```

Use Up/Down to navigate, Space to select, and Enter to save. The selection is written to `noah_watchdog_devices.json` (excluded from Git). Previously selected devices that are temporarily not visible remain available for selection.

## Run monitoring

```bash
python noah_watchdog.py run
```

The default cycle interval is five minutes and the BLE discovery scan is eight seconds. Override these values if needed:

```bash
python noah_watchdog.py run --minutes 5 --scan-timeout 15
```

Results are printed to the console and appended to `noah_watchdog.log`. To follow the log on Linux:

```bash
tail -f noah_watchdog.log
```

On Windows (PowerShell):

```powershell
Get-Content .\noah_watchdog.log -Wait -Tail 20
```

On Linux, to keep the monitor running after closing a remote shell:

```bash
nohup python -u noah_watchdog.py run > watchdog_console.log 2>&1 < /dev/null &
```

Avoid starting multiple copies at the same time: concurrent BLE connections can interfere with monitoring. Stop the existing process before starting another.

## What "read-only" means

The watchdog queries device status and does not request changes to Wi-Fi/MQTT settings. The underlying BLE protocol requires an authentication exchange, which includes a protocol-level write. The bundled `growatt_ble.py` also offers a separate manual `config` command capable of modifying settings; **the watchdog does not invoke those operations**.

## Limitations

- BLE availability is not equivalent to Wi-Fi/MQTT connectivity.
- A NOAH that is asleep or powered down may not appear during discovery.
- Current status codes should be treated as observations, not definitive diagnostic labels.
- This is experimental software, not a guaranteed recovery or safety system.

## Credits and license

BLE protocol reverse engineering and the bundled `protocol.py` and `growatt_ble.py` originate from **Peter Meiser's** [Growatt NOAH (Bluetooth) for Home Assistant](https://github.com/meiser79/growatt_noah_ble) project, copyright 2026 Peter Meiser. The watchdog application (`noah_watchdog.py`) is a separate monitoring program built on those components.

The original `LICENSE` (Apache License, Version 2.0) and original `NOTICE` are included. Attribution and original notices are retained. The original NOTICE mentions artwork in the upstream Home Assistant integration; that artwork is not included in this repository.

This project is independently maintained and is not affiliated with, endorsed by, or sponsored by Growatt New Energy Co., Ltd. Product names and trademarks belong to their respective owners.
