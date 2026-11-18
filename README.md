# Growatt NOAH BLE Watchdog

Experimental standalone Bluetooth Low Energy (BLE) monitoring and **opt-in recovery** for Growatt NOAH battery storage systems.

Built on the BLE protocol implementation by [Peter Meiser](https://github.com/meiser79/growatt_noah_ble).

> **v0.3.0 — Experimental release; first observed automatic Wi-Fi recovery.** On November 18, 2026, the watchdog detected `connection_state=0`, reapplied the configured Wi-Fi credentials and subsequently observed `connection_state=16` during its follow-up BLE check. This is one documented successful recovery sequence, not proof that the Wi-Fi action alone caused the reconnection or that recovery is reliable in all conditions. MQTT reconfiguration and datalogger restart have not been similarly validated. Recovery actions remain disabled by default.

## Features

- Discover NOAH devices advertising the Growatt BLE service.
- Select devices using an interactive terminal menu.
- Scan and query selected, BLE-visible devices **sequentially**, normally every five minutes.
- Read `wifi_status` and `connection_state` via BLE.
- Optionally log routine queries and status information.
- Optionally retry recovery by reapplying Wi-Fi credentials, correcting MQTT broker host/port, and/or requesting a datalogger restart.
- Choose a threshold of **1, 2, or 3 consecutive abnormal polling cycles**, counted separately for each device.
- Check `connection_state` again **30 seconds** after recovery actions.
- Keep devices that are not visible in a BLE scan from being treated as network failures.

## Additional tool: NOAH BLE Register Reader

The [`register-reader/`](register-reader/) directory contains a **separate, experimental** diagnostic utility (`noah_registers.py`) and its own [README](register-reader/README.md). It supports:

- Reading selected Modbus registers defined in `registers.txt`.
- Manually reading input/holding registers and experimentally writing holding registers.
- Sending a BLE datalogger restart request.
- Querying Growatt configuration parameters via BLE function `0x19` (distinct from Modbus registers).

The reader uses the shared `protocol.py` and `growatt_ble.py` from this repository. Some responses and write effects remain unverified; configuration query output may include secrets. **Do not share unredacted diagnostic output.** See its own README for usage and limitations.

## Requirements

- Python **3.11+**.
- Working BLE adapter and Bluetooth permissions.
- Linux with BlueZ (tested on Raspberry Pi / Debian), or Windows with a working BLE adapter.
- Python packages listed in `requirements.txt`.

Example installation:

```bash
git clone https://github.com/nixo2/growatt-noah-ble-watchdog.git
cd growatt-noah-ble-watchdog
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

On Windows, activate the virtual environment using the appropriate PowerShell or Command Prompt command. The requirements include `windows-curses` for Windows.

## Configure devices and recovery

```bash
python noah_watchdog.py setup --timeout 30
```

Use **↑/↓** to move, **Space** to toggle a device or action, **Enter** to save, and **q** to cancel. On the recovery threshold line, each press of Space cycles **1 → 2 → 3 → 1**.

The menu provides these options:

```text
Maßnahmen:
[x] Logging
[ ] WLAN-Daten neu schreiben
[ ] MQTT-Daten neu schreiben
[ ] Datalogger NOAH neu starten
    Automatischer Recoveryversuch nach [3] Zyklen
```

The selected recovery actions apply to all selected devices. Error counters are maintained per device. The configuration is saved in `noah_watchdog_devices.json`, which is excluded from Git. Existing v0.1.x device-list configurations are read and converted to the new format when saved.

**Monitoring-only setup:** Leave Wi-Fi, MQTT and restart actions unchecked.

## Credentials file

When Wi-Fi or MQTT recovery is enabled, create `credentials.txt` in the same directory as `noah_watchdog.py`. Copy `credentials.example.txt` and edit it:

```ini
[WIFI]
ssid = YourWiFiSSID
password = YourWiFiPassword

[MQTT]
host = 192.168.2.10
port = 1883
```

Wi-Fi credentials are needed only when Wi-Fi recovery is selected; MQTT host and port are needed only when MQTT recovery is selected. The same target values apply to all devices.

On Linux:

```bash
chmod 600 credentials.txt
ls -l credentials.txt
```

The file must be **owned by the user running the watchdog** and readable by that user. For example, if it was created using `sudo` and belongs to `root`, correct its ownership (substitute your username):

```bash
sudo chown "$USER":"$USER" credentials.txt
chmod 600 credentials.txt
```

The program rejects credentials files accessible to group/other users on non-Windows systems. It does not currently provide a distinct diagnostic for every file permission failure.

**Never commit `credentials.txt`.** The supplied `.gitignore` excludes it, as well as logs and local device configuration. Check `git status` before publishing. Do not include credentials in screenshots, issue reports or diagnostic output.

## Run

```bash
python noah_watchdog.py run
```

Defaults: five-minute polling cycle and eight-second BLE discovery scan. Optional arguments:

```bash
python noah_watchdog.py run --minutes 5 --scan-timeout 15
```

Output is written to the console and `noah_watchdog.log`. With the **Logging** option disabled, routine per-device lines are suppressed, but recovery outcomes and errors remain logged.

To run in the background after an SSH session closes:

```bash
nohup python -u noah_watchdog.py run >> watchdog_console.log 2>&1 < /dev/null &
```

Inspect the output:

```bash
tail -f watchdog_console.log
```

Find a running instance:

```bash
pgrep -af 'python.*noah_watchdog'
```

Stop an existing instance using `kill <PID>` before starting a replacement. Avoid multiple watchdog instances competing for BLE connections. `nohup` does **not** automatically restart the program after a reboot or crash; use a systemd service for unattended operation after testing.

## Experimental recovery behavior

The current implementation treats a poll as **abnormal** when:

```text
wifi_status == "0" AND connection_state is neither "16" nor empty
```

These are **provisional project heuristics**, not confirmed Growatt diagnostic specifications. In particular, `wifi_status=0` has also been observed during normal operation.

- Each abnormal poll increments that device's consecutive-error counter.
- A healthy or inconclusive poll resets its counter.
- If a device is not BLE-visible, its counter is reset and no recovery is attempted.
- When the counter reaches the configured threshold, selected actions execute **in the same polling cycle**.
- With threshold **1**, the first abnormal poll triggers recovery immediately; with threshold **2** or **3**, two or three consecutive abnormal polls are required.
- Wi-Fi recovery writes the configured SSID and password.
- MQTT recovery reads the current host and port, and writes only values that differ from the configured targets.
- Datalogger restart sends the restart command via BLE.
- The program waits 30 seconds, reconnects via BLE, and reads `connection_state` again. A value of `16` is reported as success; other values or a failed control query are reported as unsuccessful.
- After an attempt, the error counter resets; another attempt cannot occur before the next polling cycle.

Actions are performed in this order: **Wi-Fi → MQTT → restart**. Only checked actions are executed. The watchdog cannot restore connectivity while the access point or broker remains unavailable.

**Important limitation:** The current success criterion checks only the NOAH's BLE-reported `connection_state`; it does **not** independently verify Wi-Fi association, MQTT broker reachability, or message delivery. Also, a recovery sequence can be interrupted by BLE disconnects or timeouts.

## Observed recovery event (2026-11-18)

One real-device log sequence showed the following behavior (times are local):

| Time | Watchdog observation |
| --- | --- |
| 14:21:23 | BLE query reported `wifi_status=0`, `connection_state=0`. |
| 14:21:23 | `RECOVERY START` after one abnormal polling cycle. |
| 14:21:25 | Wi-Fi credentials were retransmitted. |
| 14:21:27 | Watchdog logged the Wi-Fi recovery action and started its 30-second wait. |
| 14:22:02 | Follow-up BLE query reported `connection_state=16`; watchdog logged `RECOVERY ERFOLGREICH`. |
| 14:26:24 | Subsequent routine query still reported `connection_state=16`. |

This supports that **detection, configured recovery execution, and the BLE-based success check operated as designed in this case**. It does not establish causation (the device could also have recovered independently), independent MQTT connectivity, or long-term reliability.

## Safety and testing

1. Start with **Logging** enabled and all recovery actions disabled.
2. Confirm that the selected NOAHs are queried correctly.
3. Enable **one** recovery action at a time for controlled tests.
4. Observe the recovery logs and check the NOAH's actual behavior separately.
5. Keep backups of `noah_watchdog_devices.json` and existing scripts before upgrading.

Do not rely on this experimental software as a safety system or a guaranteed connectivity repair mechanism.

## Repository files

| File | Purpose |
| --- | --- |
| `noah_watchdog.py` | Device selection, monitoring and experimental recovery |
| `register-reader/` | Separate experimental BLE register/configuration diagnostic and documentation |
| `growatt_ble.py` | BLE client / CLI based on Peter Meiser's work |
| `protocol.py` | Reverse-engineered Growatt BLE protocol |
| `credentials.example.txt` | Template for local Wi-Fi/MQTT target settings |
| `requirements.txt` | Python dependencies |
| `.gitignore` | Excludes credentials, local configuration, logs and environment |
| `LICENSE` / `NOTICE` | License and attribution notices |

## Known limitations / planned improvements

- Further validate the meaning of status values and recovery behavior across more devices and failure scenarios; one Wi-Fi recovery sequence has been observed, while MQTT recovery and datalogger restart still need independent validation.
- Improve credentials-file error messages, especially when file ownership or permissions are incorrect.
- Consider recovery cooldowns/backoff and safer behavior during prolonged network outages.
- Add tests for status handling, configuration parsing and recovery thresholds.
- Consider a systemd service for reliable unattended startup.

## Credits and license

The reverse-engineered BLE protocol and bundled `protocol.py` and `growatt_ble.py` originate from **Peter Meiser's** [Growatt NOAH (Bluetooth) for Home Assistant](https://github.com/meiser79/growatt_noah_ble) project, copyright 2026 Peter Meiser. The watchdog application is an independent program built on those components.

Keep the repository's **Apache License 2.0** `LICENSE` and its `NOTICE` file when publishing the update. Retain the original attribution and any notices of modifications where required. The original NOTICE references artwork from the upstream Home Assistant integration; that artwork is not distributed here.

This project is independently maintained and is not affiliated with, endorsed by, or sponsored by Growatt New Energy Co., Ltd. Product names and trademarks belong to their respective owners.
