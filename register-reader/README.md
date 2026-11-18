# Growatt NOAH BLE Register Reader

**Experimental diagnostic and register-access tool — v0.5.7**

A command-line utility for exploring Growatt NOAH devices locally over Bluetooth Low Energy (BLE), without requiring Growatt cloud access. This tool complements the [NOAH BLE Watchdog](../README.md) in the same repository.

> **Experimental software.** Register definitions and configuration parameters may vary by model and firmware. Reading is generally non-destructive, but writing registers or restarting the datalogger can change device behavior or interrupt connectivity. Use only with devices you own or are authorized to manage. Not affiliated with Growatt.

## Features

- Discover nearby NOAH devices over BLE and select one interactively.
- Read configured **input** and **holding** registers from `registers.txt`, with grouped/block reads to reduce BLE traffic.
- Read individual Modbus registers manually and, with explicit user input, write supported holding registers.
- Send a **datalogger restart** request over BLE, with a confirmation prompt. Actual reboot behavior has not been fully verified across devices.
- Query **Growatt configuration parameters** by decimal or hexadecimal ID using protocol function `0x19` (read-only). Display response length, hexadecimal bytes and escaped text.
- Use the existing `protocol.py` and `growatt_ble.py` BLE implementation rather than duplicating the protocol.

## Menu

Run `noah_registers.py` without arguments to enter interactive mode:

```text
1) Register aus registers.txt auslesen
2) Register manuell lesen / schreiben
3) Datenlogger über BLE neu starten
4) Konfigurationsparameter lesen (Growatt 0x19)
g) Anderes Gerät auswählen
q) Beenden
```

The interactive prompts are currently in German.

## Installation and repository layout

The reader imports `protocol.py` and `growatt_ble.py` from the repository root. The suggested layout is:

```text
growatt-noah-ble-watchdog/
├── protocol.py
├── growatt_ble.py
├── requirements.txt
├── README.md
└── register-reader/
    ├── README.md
    ├── noah_registers.py
    └── registers.txt
```

**Important:** When `noah_registers.py` is placed in a subdirectory, Python does not automatically search the parent directory for imports. Until the import paths are adapted in the script, run it from the repository root with the root directory on `PYTHONPATH`:

**Linux / macOS:**

```bash
python -m pip install -r requirements.txt
PYTHONPATH=. python register-reader/noah_registers.py
```

**Windows PowerShell:**

```powershell
py -m pip install -r requirements.txt
$env:PYTHONPATH = (Get-Location).Path
py .\register-reader\noah_registers.py
```

A working BLE adapter is required. Python dependencies include `bleak` and `cryptography` through the underlying protocol implementation. Use the repository's dependency instructions for the Watchdog as a starting point; its interactive terminal dependencies are not necessarily required by this reader.

`registers.txt` is loaded relative to `noah_registers.py` by default, so keep the file beside the script. It is **not** included in the v0.5.6 Python-only ZIP release and must be added separately.

## Command-line examples

Run from the repository root, with `PYTHONPATH=.` set on Linux/macOS (or the equivalent environment variable on Windows):

```bash
python register-reader/noah_registers.py scan
python register-reader/noah_registers.py read --address <BLE_ADDRESS> --type holding --register 255
python register-reader/noah_registers.py read --address <BLE_ADDRESS> --type input --register 29
python register-reader/noah_registers.py list --address <BLE_ADDRESS>
```

For the full command-line options:

```bash
python register-reader/noah_registers.py --help
python register-reader/noah_registers.py list --help
```

The datalogger restart and configuration-parameter query are available in the **interactive menu**, not as separate CLI subcommands in v0.5.6.

## Two distinct address spaces

**Modbus registers** and **Growatt configuration parameters** are not interchangeable:

| Access | Growatt BLE function | Example | Purpose |
| --- | --- | --- | --- |
| Modbus passthrough | `0x17` | Holding register `255` | Operating registers and schedules |
| Configuration query | `0x19` | Parameter `80` (`0x50`) | Read datalogger configuration |
| Configuration set | `0x18` | Parameter `0x20` | Datalogger restart command |

### Example: firmware-update URL (configuration parameter 80)

In interactive mode, select option **4** and enter `80` (or `0x50`). On at least one tested NOAH device, the response contained a firmware-update URL.

**This is not Holding register 80.** Reading Holding registers 80–84 on the same device produced empty Modbus passthrough replies, not a URL.

The stored URL represents an update location previously supplied to the device. **It does not establish the currently installed firmware version, prove that a download occurred, or mean that the URL remains valid.** This query does not start an update.

Other parameter IDs shown by the program include `55` (router status), `60` (server status) and `61` (SDK version). Their availability and semantics may differ by firmware. The reader intentionally blocks display of selected sensitive parameter IDs (`54` and `57`); do not publish unredacted outputs that might contain credentials or identifiers.

## Register definitions

The `registers.txt` file supplies the registers displayed by menu option 1 and the `list` command. The reader supports input/holding register definitions, optional formatting/scaling, and optimized block reads. Keep the register list aligned with your own tested device and firmware; do not assume every address works on every NOAH.

## Datalogger restart

Menu option **3** uses the original protocol helper `restart_request()` from `protocol.py`, which constructs a Growatt `SET` (`0x18`) request with parameter `0x20` and ASCII value `"1"`. The command is sent to the BLE characteristic after authentication.

A successful BLE/GATT write **does not prove** the datalogger restarted. Expect temporary Wi-Fi/MQTT disruption if the restart is accepted, and verify the outcome independently. This is intended for manual experimentation, not unattended recovery.

## Troubleshooting

- **`ModuleNotFoundError: protocol` or `growatt_ble`:** Run from the repository root with `PYTHONPATH` set as shown above, or adapt the script's import path.
- **`registers.txt` not found:** Place the file alongside `register-reader/noah_registers.py`.
- **No BLE devices found:** Check adapter permissions, proximity, and whether the device is advertising.
- **Empty Modbus response:** A Growatt passthrough frame can be received without an embedded Modbus reply. This does not identify a register value.
- **Connection lost during restart:** A reboot may interrupt BLE; a GATT acknowledgment is not a device-level reboot confirmation.

## Credits and licensing

The BLE framing, encryption, authentication and configuration protocol implementation is based on **Peter Meiser's** [growatt_noah_ble](https://github.com/meiser79/growatt_noah_ble), licensed under the **Apache License 2.0**. See the repository's root `LICENSE`, `NOTICE`, and [README](../README.md) for attribution and applicable notices. Preserve the original license and attribution notices when redistributing the underlying files.

The [Growatt Nexa 2000 protocol notes by longsleep](https://gist.github.com/longsleep/cf5bc2eff0def99ad411b22abf7c2b33) provided additional research context. NEXA behavior is not automatically guaranteed to apply to NOAH; validate findings on the target device.

Contributions, reproducible test results, and corrections are welcome. Please redact device serial numbers, BLE addresses, network credentials, and private URLs from shared logs.
