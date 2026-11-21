# NOAH BLE TUI — Version 0.8.0

**Interactive register reader and experimental configuration tool for Growatt NOAH devices over Bluetooth Low Energy (BLE).**

The TUI builds on `noah_registers.py`, `growatt_ble.py`, and `protocol.py`. It allows you to read device data locally and, after explicit confirmation, write configuration values without using the Growatt app for these operations. This is an **unofficial** project and is not affiliated with Growatt.

> **Safety warning:** Writing registers and restarting the data logger are experimental features. Incorrect values may affect schedules, power limits, network connectivity, or other device functions. Use this tool only on devices you own or are authorized to manage. Record original values before making changes. Not every readable register is writable, and register meanings may differ between firmware versions.

## Features

### Register overview and diagnostics

- Interactive two-column terminal interface with independent navigation in either column.
- Displays **input (`I`), holding (`H`), and configuration (`C`)** registers defined in `registers.txt`.
- Navigate using arrow keys; press **Enter** to view a register's address, type, and interpretation.
- Supports individual registers, register ranges, and non-contiguous register lists.
- Displays numerical values, scaling factors, units, enumerations, bit fields, ASCII strings, and time values according to the register definitions.
- Shows progress and diagnostics for the initial BLE read in the console before opening the overview.
- Press **R** to refresh the complete register list when needed.

### Controlled write access

- Press **W** from a **register details dialog** to open the write dialog; writing is not triggered directly from the main overview.
- **Configuration (`C`) parameters:** Write individual ASCII configuration values using Growatt BLE command `0x18`, followed by readback using `0x19`. MQTT server (parameter 17) and MQTT port (parameter 18) have been tested on real hardware.
- **Holding (`H`) registers:** Write individual 16-bit holding registers through Growatt BLE passthrough `0x17` using Modbus function `0x06` (FC06).
- **Register groups:** For entries such as `45–50`, choose the **specific register address** and then enter its **new raw value**. Only the selected register is written, not the entire group.
- Supports decimal raw values and `0x` hexadecimal values; individual holding registers defined as `TIME` also accept `HH:MM` input.
- Before writing: validates the input, reads the previous value again, and requires explicit **confirmation**.
- After writing: reads the actual value back. The display uses the **readback value**, not an unverified requested value.
- **Targeted table updates:** Only the affected table entry is updated. For grouped holding registers, the remaining group members are read again; a complete refresh is unnecessary.
- Fixed column alignment is preserved after targeted updates.

**Note on configuration parameters:** Readable status parameters (for example, 55 and 60) may still be offered for editing by this experimental interface. This **does not** mean that writing to them is supported or advisable.

**Note on holding registers:** All registers marked `H` in `registers.txt` can be accessed from the write dialog, including unknown registers. The TUI does not know every register's permitted range or side effects. For groups displayed as ASCII, writes operate on **individual 16-bit raw values**, not complete ASCII strings.

### Data logger restart

- Press **N** to open the restart confirmation dialog.
- The restart command is sent only after explicit confirmation.
- Restarting may temporarily interrupt BLE or MQTT connectivity.

### Multiple-device management

- On first launch, **if `noah_devices.txt` does not exist**, the device manager opens automatically.
- Scan for nearby NOAH devices and add or remove them from the saved list.
- On subsequent launches, choose a device from the saved list before reading its registers.
- Press **G** to select another saved device or **K** to open device management; both actions are also available from register details.
- Devices are stored in `noah_devices.txt` (UTF-8 text containing JSON).
- The **`--address`** option selects a BLE address directly, bypassing the device selection menu.

## Requirements

- Python 3.11 or later recommended.
- A working Bluetooth Low Energy adapter and access to the target NOAH device.
- A terminal wide enough for the two-column layout; **Windows Terminal** is recommended on Windows.
- On Linux, the BLE adapter must be accessible through BlueZ. On Windows, Bleak uses the native Windows Bluetooth interface. BLE addresses/identifiers can differ between operating systems; scan again when necessary.

## Installation

Place the following project files in the **same directory**:

```text
noah-ble/
├── noah_tui.py
├── noah_registers.py
├── growatt_ble.py
├── protocol.py
├── registers.txt
├── requirements.txt
└── noah_devices.txt      # created when needed
```

The last tested development file, `noah_tui_v0_8_2.py`, should be published as **`noah_tui.py`**. **0.8.0** is the public release version; update any internal development version string before publishing if necessary.

Suggested `requirements.txt` (preserving existing minimum versions):

```text
bleak>=0.21
cryptography>=42
windows-curses; sys_platform == "win32"
textual
rich
```

`windows-curses` is retained for any older curses-based components in the project; the new TUI itself uses **Textual** and **Rich**.

### Linux

```bash
cd ~/growatt-ble
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python noah_tui.py
```

### Windows (PowerShell)

```powershell
cd C:\NOAH-BLE
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python noah_tui.py
```

If PowerShell blocks virtual-environment activation, call its Python executable directly:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe noah_tui.py
```

## Keyboard controls

| Key | Register overview | Register details |
|---|---|---|
| `↑` / `↓` | Select register | — |
| `←` / `→` | Switch column | — |
| `Enter` | Open details and editing options | — |
| `W` | — | Open write dialog for `H` or supported `C` parameters |
| `G` | Select device | Select device |
| `K` | Manage devices | Manage devices |
| `N` | Restart data logger (with confirmation) | — |
| `R` | Refresh all registers | — |
| `Q` | Quit application | Close details |
| `Esc` | — | Close details |

**Typical write workflow:** Select a register → `Enter` → `W` → enter a value → **Check** → review old and new raw values → **confirm the write** → wait for readback → return to the overview. The affected table row updates automatically.

### Example: Single holding register

Holding register `251` represents the discharge limit. Enter an appropriate raw value and confirm the change; the application reads it back. Changes to charge or discharge limits can affect device operation.

### Example: Register group 45–50

Based on observations, this group **appears** to represent the device clock (year, month, day, hour, minute, second). This mapping is **not officially confirmed**.

To edit only the suspected day register, select **register address `47`** in the write dialog and enter the desired **16-bit raw value**. The other registers in the group are not written. Incorrect values may affect the internal clock or time-dependent behavior.

## Device file: `noah_devices.txt`

The device manager creates a text file containing JSON, for example:

```json
[
  {"address": "AA:BB:CC:DD:EE:01", "name": "NOAH Patio"},
  {"address": "AA:BB:CC:DD:EE:02", "name": "NOAH Garage"}
]
```

The addresses above are **placeholders**. You may edit this file manually, provided the JSON remains valid. The TUI also saves changes through its device manager.

## Custom register definitions

`registers.txt` controls which registers are read and displayed. It supports register types `I`, `H`, and `C`, individual addresses, ranges, lists, formatting instructions, and section headers. The TUI uses the existing `noah_registers.py` reader to interpret these definitions.

Before attempting writes to undocumented registers, observe their values over time and compare them with known operating states.

## Command-line options

```bash
python noah_tui.py
python noah_tui.py --address AA:BB:CC:DD:EE:FF
python noah_tui.py --file /path/to/registers.txt
```

The address passed to `--address` must be valid for the operating system and BLE backend in use.

## Technical background

- **BLE:** Communication via `bleak` and the project's `growatt_ble.py` / `protocol.py` modules.
- **Register reads:** Modbus function `0x03` (holding) and `0x04` (input) over Growatt BLE passthrough `0x17`.
- **Holding-register writes:** Modbus `0x06` (single register) over passthrough `0x17`.
- **Configuration parameters:** Growatt commands `0x18` (write) and `0x19` (read).
- **TUI:** `textual` and `rich`.
- **Formatting:** Reuses register definitions and conversions from `noah_registers.py`.

Reading and writing MQTT configuration has been tested on a real NOAH: after changing the MQTT server address, messages were received by a local MQTT/Node-RED setup. This **does not guarantee** compatibility with every firmware version or register.

## What's new in 0.8.0

Compared with the earlier command-line register reader, this release adds:

1. Full-screen TUI with two independently navigable register columns.
2. Register details with integrated write access (`Enter` → `W`).
3. Hardware-tested ASCII writes to Growatt configuration parameters.
4. Experimental write access to all defined holding registers, including individual registers within groups.
5. Pre-write readback, explicit confirmation, and post-write verification.
6. Data logger restart with a confirmation dialog.
7. Persistent device management, BLE scanning, device selection, and device switching.
8. Targeted table updates after writes without a complete BLE refresh.
9. Fixed column alignment during targeted updates.
10. Full manual refresh still available at any time (`R`).

## Limitations and known risks

- Not every `H` or `C` register defined in `registers.txt` is necessarily writable.
- Unknown holding registers do not have comprehensive device-specific range validation.
- Writing to a register group changes **only one 16-bit register** per operation.
- If BLE disconnects during a write, the final device state may be unknown; read the value again before retrying.
- Successful readback does not prove that a setting will persist after every reboot.
- Bluetooth discovery and permissions differ between Linux and Windows.

## License

The license and notice files (`LICENSE`, `NOTICE`) from the relevant project repository apply. Before publishing, ensure these files are distributed alongside any reused project modules.
