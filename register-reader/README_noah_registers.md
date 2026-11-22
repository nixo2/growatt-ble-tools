# Growatt NOAH – BLE Register Diagnostics and Terminal Interface

**Updated: November 22, 2026**  
**`noah_registers.py` v0.6.22 · `noah_tui.py` v0.8.7**

Two Python applications for reading and diagnosing Growatt NOAH battery systems directly over Bluetooth Low Energy (BLE):

- **`noah_registers.py`**: Command-line utility for device discovery, individual reads, register lists, and diagnostics.
- **`noah_tui.py`**: Interactive Textual-based terminal interface with device management, a two-column register display, detail views, and selected write operations.

Both applications use the existing `protocol.py` and `growatt_ble.py` modules. BLE access does not require the Growatt cloud or MQTT. This project is **unofficial and experimental**; in particular, register meanings and write operations have not been approved by Growatt for this use.

## 1. Requirements and Files

You need a computer with a working BLE adapter, Python 3, and access to the Bluetooth interface. Both applications import `bleak`; the TUI also requires `textual` and `rich` (Rich is normally installed with Textual).

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install bleak textual rich
```

Place these files in the same project directory:

```text
project/
├── noah_registers.py       # v0.6.22
├── noah_tui.py             # v0.8.7
├── protocol.py             # BLE protocol module
├── growatt_ble.py          # BLE device communication
├── registers.txt           # Register selection, conversion, layout
├── register_info.txt       # Optional explanations in the detail view
└── noah_devices.txt        # Device list managed by the TUI
```

**Note on versioned filenames:** Rename the supplied `noah_registers_v0_6_22.py` and `noah_tui_v0_8_7.py` files to `noah_registers.py` and `noah_tui.py` before running them. The TUI explicitly imports `noah_registers`.

## 2. Quick Start

**TUI with device selection:**

```bash
python noah_tui.py
```

**TUI with a specific BLE address:**

```bash
python noah_tui.py --address 'AA:BB:CC:DD:EE:FF'
```

**Use a different register file:**

```bash
python noah_tui.py --file registers.txt
```

**CLI – discover devices:**

```bash
python noah_registers.py scan --seconds 8
```

**CLI – display a register list:**

```bash
python noah_registers.py list --address 'AA:BB:CC:DD:EE:FF' --file registers.txt
```

**Read an individual input register:**

```bash
python noah_registers.py read --address 'AA:BB:CC:DD:EE:FF' --type input --register 120
```

**Read holding registers:**

```bash
python noah_registers.py read --address 'AA:BB:CC:DD:EE:FF' --type holding --register 254 --count 5
```

Running `noah_registers.py` without a subcommand opens its interactive diagnostics menu.

## 3. Register Types and Transport

| Code | Data source | Read mechanism | Data type |
|---|---|---|---|
| `I` | Input registers | Growatt BLE `0x17` carrying Modbus FC04 | 16-bit words |
| `H` | Holding registers | Growatt BLE `0x17` carrying Modbus FC03 | 16-bit words |
| `C` | Growatt configuration parameters | Growatt BLE `0x19` (QUERY) | Variable-length byte sequences, often ASCII |
| `I,C` / `H,C` | Mixed sources | Both corresponding read mechanisms | Combined **display value**, not a shared physical register |

A holding register is not automatically a datalogger parameter. The `C` address space uses a separate Growatt configuration interface.

### Optimized Input Reads

For input registers **I:0–123**, the current implementation uses experimentally verified 16-word read windows:

```text
0–15, 16–31, 32–47, 48–63,
64–79, 80–95, 96–111, 108–123
```

The final window overlaps intentionally. This optimization matters because individual reads of I:119/I:120 were unreliable in testing, whereas the 16-word block succeeded. Holding and configuration reads continue to use their own methods. Registers outside the verified windows are handled by the existing read logic.

## 4. The `registers.txt` Format

Basic format (UTF-8), one entry per line:

```text
Register|Label|Factor|Unit|RegisterType
```

Examples:

```text
101|Charge cycles|1|cycles|I
102|SOH|1|%|I
119-120|Firmware components|CombLBHBLB|.|I
21|Firmware suffix|ASCII||C
119-120,21|Firmware|CombLBHBLB+ASCIIstrip(.)|.|I,C
```

The fifth column is optional; if omitted, `I` is assumed. Lines beginning with `#` are comments; blank lines are ignored.

### Register Specifications

- `119`: One register.
- `119-120`: Inclusive contiguous range.
- `100->16`: 16 registers starting at address 100 (100 through 115).
- `30,42`: Explicit list of individual registers.
- `119-120,21` with type `I,C`: **First group** = input registers 119–120; **second group** = configuration parameter 21.

The current mixed-source syntax specifically supports `I,C` and `H,C`, each with one Modbus register range and **one** C parameter ID. It is not a general-purpose expression parser.

### Factors and Display Formats

| Factor | Meaning |
|---|---|
| `1`, `0.1`, `0.01` | Multiply the raw value |
| `*0.1 -273.1` | Arithmetic conversion |
| `ASCII` | Interpret 16-bit words as ASCII, or a C parameter as a string |
| `HEX` | Display bytes in hexadecimal |
| `TIME` | Time encoded as high byte (hours) and low byte (minutes) |
| `bit3-0` | Display selected bits as a bit sequence |
| `CombLBHBLB` | Combine selected bytes from multiple 16-bit words |
| `CombLBHBLB+ASCIIstrip(.)` | Combine selected bytes with a cleaned C-parameter ASCII string |

Additional options for numeric formulas include `NULL0`, `NULL65535`, and `NOHEX`. For numeric status values, the unit column may also contain a legend, for example `0 Off, 1 On`.

### Reconstructing the Complete Firmware Version

**Recommended entry:**

```text
119-120,21|Firmware|CombLBHBLB+ASCIIstrip(.)|.|I,C
```

Observed raw values:

```text
I:119 = 0x0013  → Low byte  = 19
I:120 = 0x140E  → High byte = 20; Low byte = 14
C:21  = "4.0.2.6" (ASCII)
```

Each numeric component receives the suffix from the unit column (`.`), **including the final numeric component**, producing `19.20.14.`. `ASCIIstrip(.)` then removes **all** periods from the C string (`4026`) before appending it.

**Result: `19.20.14.4026`**, matching the firmware version observed in the Growatt app.

`HB` means high byte (bits 15–8); `LB` means low byte (bits 7–0). This operation **formats values that have already been read**; it does not introduce a new BLE command. Despite its name, `ASCIIstrip(.)` removes all periods (equivalent to `replace`), not just leading or trailing ones.

### Layout Directives

Standalone lines enclosed in double asterisks control the display:

```text
**[Battery]**
**LEER**
**---**
**===40===**
**SPALTE**
**[Datalogger]**
```

`**SPALTE**` or `**SEITENUMBRUCH**` starts a new display column. These directive keywords remain in German because they are part of the parser syntax. Headings and separators do not trigger register reads.

## 5. TUI Controls

| Key | Action |
|---|---|
| `↑` / `↓` | Select previous / next entry |
| `←` / `→` | Select left / right column |
| `Enter` | Show details for the selected entry |
| `R` | Refresh all registers |
| `G` | Switch device |
| `K` | Open device management |
| `N` | Trigger datalogger restart (**active operation**) |
| `Q` | Quit or close the current dialog |

The TUI reads registers through `noah_registers.py`, displays the results in two columns, and opens a detail view with Enter. The optional `register_info.txt` adds explanatory notes under section headings such as `[I:120]`, `[H:254]`, or `[C:21]`.

### Write Operations – Use Caution

The TUI includes experimental functions for modifying individual holding registers, certain ASCII configuration parameters, and the nine timeslots (five holding registers each). Writes use separate Growatt/Modbus commands; the implementation then attempts to read back the actual values. A timeout **does not prove** that a write had no effect.

The combined `I,C` firmware entry is **read-only**. Never send untested write values to a device: changes to operating modes, output power, schedules, or communications can affect operation immediately.

## 6. CLI Options

```bash
python noah_registers.py --help
python noah_registers.py scan --help
python noah_registers.py read --help
python noah_registers.py list --help
python noah_tui.py --help
```

The `list` command supports options including `--timeout`, `--block-size` (default 16), `--max-gap` (default 2), and `--verbose`. The older `list --type` switch is retained for compatibility; the register types specified in the file take precedence.

## 7. Troubleshooting

**`ModuleNotFoundError: noah_registers`** – The versioned file must be named `noah_registers.py` and placed next to `noah_tui.py`.

**`ModuleNotFoundError: textual` / `bleak`** – Activate the virtual environment and install the dependencies.

**No BLE devices found** – Check the Bluetooth adapter, permissions, signal range, and possible simultaneous connections. A device not found by scanning is not necessarily defective.

**Individual register returns no values** – Do not automatically conclude that the register does not exist. Some NOAH registers only respond when read in a suitable block; this is why the optimized 16-word windows matter.

**Firmware combination is rejected** – Verify that the entry is exactly `119-120,21|Firmware|CombLBHBLB+ASCIIstrip(.)|.|I,C` and that both applications use the versions listed above.

**Display is partially incomplete** – All required sources must be read successfully for a combined value. A missing C parameter must not silently be treated as a valid firmware version.

## 8. Project Status and Limitations

BLE register reads, including the optimized input windows, have previously been used successfully on **four NOAH devices**. Mixed register/configuration support in `noah_registers.py` v0.6.22 and `noah_tui.py` v0.8.7 is newer and still requires a complete real-device test. The observed match between firmware components and the app is strong evidence, but it does not replace official NOAH register documentation.

The SOH value at I:102 should also be interpreted cautiously: for BMS version 14, the related NEXA documentation describes a constant value of 100. Whether and to what extent the NOAH behaves identically should be verified separately.

## 9. Attribution and Disclaimer

The BLE protocol foundation (`protocol.py`, `growatt_ble.py`) builds on the community project **meiser79/growatt_noah_ble**. When publishing, respect its license and copyright notices.

**Not affiliated with Growatt. No warranty of correctness or operational safety.** Read operations are intended for diagnostics. Use write and restart functions only deliberately and at your own risk.
