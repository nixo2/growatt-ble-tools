# Growatt NOAH BLE Watchdog

**Project release 1.1 · November 27, 2026 · Watchdog 0.4.2 · Watchdog UI 0.1.8 · Experimental / unofficial**

A Python watchdog for monitoring Growatt NOAH battery storage devices over Bluetooth Low Energy (BLE). It periodically reads connectivity status and can optionally restore Wi-Fi settings, check and repair MQTT broker configuration, and request a datalogger restart.

The watchdog communicates directly with the NOAH over BLE; it does not depend on the Growatt cloud or a working MQTT connection to perform its checks.

> **Caution:** Automatic configuration changes and restarts are experimental. Test with one device first, keep a backup of your settings, and enable only the recovery actions you actually need. No recovery action is enabled by default.

## Interactive watchdog management (recommended on Linux and Windows)

```bash
python noah_tui.py
```

Press **W** on the start screen or register overview. The integrated `noah_watchdog_ui.py` provides:

- Start, stop, restart and status controls (PID and runtime); process management uses Linux `/proc` or Windows `psutil`.
- Configurable monitoring interval and scan duration (defaults: five minutes and eight seconds).
- **Geräte verwalten (BLE-Scan)** opens the existing interactive setup with its default scan duration. The running watchdog need not be stopped; the device list is reloaded when setup closes.
- Explicit `[X]` / `[ ]` toggles for logging, Wi-Fi, MQTT and datalogger restart, plus the 1–3 cycle threshold and Save button.
- A modal restart notice, confirmed with Enter, when configuration is saved or setup returns while the watchdog is running. The running process retains its loaded configuration until the user restarts it.
- Watchdog and console logs, today/all periods, manual text filtering, recovery shortcuts and jump to end. Normal display restores the previous manual filter after a recovery shortcut.
- Per-device recovery statistics for today, seven days and all time, counting recovery starts rather than successes.

A watchdog started from the interface runs independently of the TUI and writes console output to `watchdog_console.log`. Closing the interface does not stop monitoring. Reopening the TUI detects the running watchdog again. On Windows, monitoring starts without an additional console window. Set up `credentials.txt` as described below before enabling recovery.

### Platform-specific process management

| | Linux | Windows |
| --- | --- | --- |
| Process discovery | `/proc` (unchanged) | `psutil` |
| Background launch | `start_new_session=True` | `CREATE_NO_WINDOW` |
| Stop | `SIGTERM` | Direct process termination |

Windows groups matching parent/child Python launcher and interpreter processes into one watchdog instance and displays the executing interpreter PID. Independent instances are not merged; the Stop control refuses to terminate multiple independent watchdogs. Windows termination is not a graceful application shutdown. Avoid stopping during an active recovery operation, as configuration writes may be interrupted.

## Features

- Discover NOAH devices by their advertised BLE service and select which devices to monitor.
- Poll selected, BLE-visible devices sequentially at a configurable interval (five minutes by default).
- Read and log `wifi_status` and `connection_state`.
- **Check the MQTT host and port against `credentials.txt` on every successful BLE polling cycle**, even when `connection_state` looks healthy.
- If MQTT values differ, write **only the differing fields**, read them back, and log whether verification succeeded.
- Optionally reapply Wi-Fi credentials and/or restart the datalogger after **1–3 consecutive abnormal status cycles**.
- Recheck `connection_state` 30 seconds after a status-triggered recovery attempt.
- Avoid interpreting an absent BLE advertisement as evidence of a Wi-Fi/MQTT failure.
- Write diagnostic messages to both the console and `noah_watchdog.log`.

## Requirements

- Python 3.11 or later (uses `asyncio.timeout`).
- A supported BLE adapter and the necessary Bluetooth permissions.
- `bleak` and the project's `protocol.py` and `growatt_ble.py` modules.
- A terminal with `curses` support for interactive setup (`windows-curses` on Windows).
- `psutil` for integrated Windows process management only; no additional process-management dependency on Linux.

Example installation on Debian / Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

On Windows (PowerShell), from the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

The requirements file installs `windows-curses` and `psutil` only on Windows. Linux retains its existing `/proc`-based process management without `psutil`.

Place the watchdog next to its shared BLE modules:

```text
project/
├── noah_tui.py                  # 1.1
├── noah_watchdog_ui.py          # 0.1.8
├── noah_watchdog.py             # 0.4.2
├── protocol.py
├── growatt_ble.py
├── credentials.txt              # Required for enabled Wi-Fi/MQTT actions; keep private
├── noah_watchdog_devices.json   # Created by setup
└── noah_watchdog.log            # Created when running
```

## CLI / standalone operation

### 1. Select devices and recovery options

```bash
python noah_watchdog.py setup
```

The default scan takes eight seconds; use `--timeout` to override it if needed. The setup screen scans for devices and offers these options (the actual UI labels are in German):

| Setup option | Meaning |
| --- | --- |
| `Logging` | Log routine status queries and successful MQTT checks. |
| `WLAN-Daten neu schreiben` | Reapply Wi-Fi SSID/password during status-triggered recovery. |
| `MQTT-Daten neu schreiben` | Check MQTT host/port **every poll**, repair differences immediately, and also check during status-triggered recovery. |
| `Datalogger NOAH neu starten` | Send a restart request during status-triggered recovery. |
| `Automatischer Recoveryversuch nach [N] Zyklen` | Set the threshold to 1, 2, or 3 abnormal cycles. |

Use **Up/Down** to navigate, **Space** to toggle or change the selected item, **Enter** to save, and **q** to cancel. Selected devices and options are stored in `noah_watchdog_devices.json`.

**Important distinction:** The 1–3 cycle threshold applies to status-triggered recovery. MQTT configuration mismatch repair does **not** wait for this threshold: it runs during each successful poll when the MQTT option is enabled.

## 2. Configure credentials

Create `credentials.txt` in the same directory as the watchdog:

```ini
[WIFI]
ssid = YOUR_WIFI_NAME
password = YOUR_WIFI_PASSWORD

[MQTT]
host = 192.168.1.10
port = 1883
```

The `[WIFI]` section is needed only if Wi-Fi recovery is enabled. The `[MQTT]` section is needed only if MQTT checking/recovery is enabled. MQTT host and port must be present, and the port must be a number from 1 to 65535.

On Linux, restrict access to the credentials file:

```bash
chmod 600 credentials.txt
```

The watchdog refuses to use a credentials file accessible to group or other users on non-Windows systems. **Never commit `credentials.txt` to Git or publish it in issue reports.** The current MQTT comparison covers **host and port**, not additional broker usernames/passwords.

## 3. Start monitoring

```bash
python noah_watchdog.py run
```

Change the polling interval or BLE scan duration:

```bash
python noah_watchdog.py run --minutes 5 --scan-timeout 8
```

Both arguments must be positive. The watchdog runs until stopped (for example with **Ctrl+C**). The polling interval is measured from the beginning of each monitoring cycle; if processing takes longer than the requested interval, the next cycle starts without additional delay.

### Log files

The program logs to **standard output** and to `noah_watchdog.log` in the script directory. To save a separate console log as well:

```bash
python noah_watchdog.py run --minutes 5 2>&1 | tee -a watchdog_console.log
```

Follow the watchdog log:

```bash
tail -f noah_watchdog.log
```

On Windows (PowerShell), use `Get-Content .\noah_watchdog.log -Wait` to follow the log.

## 4. How MQTT checking and recovery work

With the MQTT option enabled, each successful BLE poll queries the current MQTT host and port alongside the connection status. The watchdog compares them with `[MQTT] host` and `[MQTT] port` in `credentials.txt`.

1. **Match:** No MQTT write is made. With logging enabled, an `MQTT PRÜFUNG OK` entry confirms that host and port match.
2. **Mismatch:** The watchdog logs `MQTT RECOVERY START`, writes only the mismatched parameter(s), then reads both values again.
3. **Verified repair:** It logs `MQTT RECOVERY ERFOLGREICH`.
4. **Read/repair failure:** It logs `MQTT PRÜFUNG FEHLGESCHLAGEN` or `MQTT RECOVERY FEHLGESCHLAGEN` as appropriate. Missing query values are not treated as a reason to overwrite settings blindly.

Typical log entries (the current program emits German messages):

```text
INFO ... | dev->app QUERY-RSP ... wifi_status=b'0', connection_state=b'16'
INFO ... | MQTT PRÜFUNG OK: Server und Port stimmen mit credentials.txt überein
WARNING ... | MQTT RECOVERY START: Abweichung bei ... erkannt; stelle Sollwerte wieder her
WARNING ... | MQTT RECOVERY ERFOLGREICH: Sollwerte geschrieben und verifiziert
ERROR ... | MQTT RECOVERY FEHLGESCHLAGEN: ValueError
```

The normal status-query log remains available when MQTT checking is enabled. Raw MQTT configuration responses are deliberately **not** copied verbatim into the log. Actual MQTT host/port values are not printed by the normal success/recovery messages.

**What a successful MQTT check proves:** The configured host and port match the file. It does **not** prove that the broker is reachable, that MQTT authentication works, or that messages are being published successfully.

## 5. Connection-status recovery

The status-triggered recovery counter increases only when the watchdog reads **`wifi_status == '0'` and `connection_state` is neither `'16'` nor empty**. Other combinations reset the counter. These status values are based on observations and should not be interpreted as a complete vendor-documented state model.

After the configured number of consecutive abnormal cycles, the watchdog performs the selected actions in this order:

1. Reapply Wi-Fi SSID/password (if enabled).
2. Check/repair MQTT host and port (if enabled).
3. Send a datalogger restart request (if enabled).
4. Wait 30 seconds and query `connection_state` again.

The follow-up check reports whether `connection_state` is `16`; this is a status observation, **not** proof of end-to-end MQTT delivery. A BLE scan that does not find a selected device does not increment the abnormal-status counter.

## 6. Companion tools: BLE Register Reader and TUI

The watchdog is intended for **automatic connectivity monitoring and recovery**, not general register editing. For interactive diagnostics, use the companion register tools:

- **`noah_registers.py`** — command-line register reader and diagnostics tool.
- **`noah_tui.py`** — interactive terminal user interface for viewing registers and performing supported register/configuration edits.

The TUI uses `noah_registers.py`, `protocol.py`, `growatt_ble.py`, and a register definition file such as `registers.txt`. It requires additional packages:

```bash
python -m pip install -r requirements.txt
python noah_tui.py
```

For register definitions, supported input/holding/configuration access, data conversions, write limitations, and TUI operation, see the [BLE tools guide](README_noah_ble_tools.md).

**Safety:** Do not edit unknown registers merely because they are visible in the TUI. Register write behavior is experimental and may affect battery or communication settings.

## 7. Troubleshooting

| Symptom | Check |
| --- | --- |
| Windows process management or setup dependency missing | Install the current `requirements.txt` in the active virtual environment. |
| Multiple independent watchdogs detected | Inspect the running processes and resolve duplicate instances manually before using Stop or Restart. |
| No devices appear during setup | BLE adapter, permissions, power, range; increase `--timeout`. |
| Device is not monitored | It must be selected in setup and visible in the BLE scan. |
| `credentials.txt` error | Confirm the file is beside the script, contains the enabled sections, and has mode `600` on Linux. |
| No `MQTT PRÜFUNG OK` entries | Enable both `Logging` and `MQTT-Daten neu schreiben` in setup; confirm the poll is succeeding. |
| MQTT repair fails | Inspect `MQTT RECOVERY FEHLGESCHLAGEN`; check BLE stability and whether the device accepts the configuration write. |
| `connection_state=16` but MQTT messages are missing | The watchdog does not validate message delivery or broker authentication; investigate the broker, network, and publishing path separately. |
| Log shows only cycle completion | Check device selection, BLE visibility, and logging configuration. |

## 8. Security and limitations

- All recovery options are **opt-in**. Review the setup selections before leaving the program unattended.
- Protect `credentials.txt` and configuration backups. Avoid sharing unredacted BLE traces.
- The watchdog compares MQTT **host/port** only; it does not inspect message payloads, subscriptions, credentials beyond these fields, or broker-side availability.
- Wi-Fi credentials are reapplied during status-triggered recovery without a preceding equality check.
- BLE visibility and status values alone cannot establish full cloud/MQTT functionality.
- This is an independent experimental utility, not an official Growatt product. Test changes carefully on your own equipment.

## 9. Version notes

**Watchdog UI v0.1.8 (project release 1.1)**

- Added Windows process management using `psutil`, including virtual-environment launcher detection and windowless background startup.
- Preserved the existing Linux `/proc` backend and interactive interface.
- Windows start, stop and rediscovery after restarting the TUI were tested; Linux operation was regression-tested.
- The standalone watchdog remains at v0.4.2.

**Watchdog v0.4.2**

- Check MQTT broker host/port on every successful poll when enabled.
- Repair only mismatching MQTT fields and verify by reading them back.
- Distinguish MQTT check, recovery start, recovery success, and recovery failure in the log.
- Restore routine per-device connection-status logging while filtering raw MQTT responses.
- Log a successful MQTT comparison as `MQTT PRÜFUNG OK` when logging is enabled.

---

**Related documentation:** [BLE tools guide](README_noah_ble_tools.md)
