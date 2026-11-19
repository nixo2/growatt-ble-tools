# Growatt NOAH BLE Register Reader

**Experimental register diagnostic and exploration tool — v0.6.5**

Read and explore Growatt NOAH registers locally over Bluetooth Low Energy (BLE), without depending on Growatt cloud access. This utility complements the [NOAH BLE Watchdog](../README.md).

> **Experimental / use at your own risk.** Register addresses and meanings may vary between devices and firmware versions. Reading is intended to be non-destructive, but manual **holding-register writes** and **datalogger restarts** can change device behavior or interrupt connectivity. Use only on equipment you own or are authorized to manage. This is an unofficial project, not affiliated with Growatt.

## What's new in v0.6.5

The register reader has received several improvements since v0.5.7:

- **v0.5.8:** Binary bit-field formatting (`bit9-0`, `bit3-0`) with the least-significant bit on the right; units such as `Uhr` are preserved for `TIME` values.
- **v0.5.9:** Numeric register ranges are supported, not just ASCII ranges; `HEX` formatting was added.
- **v0.6.0:** Failed block reads can fall back to smaller ranges and preserve readable values when some registers fail.
- **v0.6.1:** Register ranges are displayed as aligned **decimal and hexadecimal rows**, rather than one very long combined value.
- **v0.6.2:** **Adaptive fallback** probes the first and last register of a failed block. If both fail, the remaining registers are queried individually; otherwise the block is split. A range with zero successful values is marked `NICHT LESBAR`, not `TEILWEISE`.
- **v0.6.3:** New `start->count` register syntax, e.g. `100->16` is equivalent to `100-115`.
- **v0.6.4:** corrected Display Issues
- **v0.6.5:** corrected Display Issues

These changes primarily improve register-list parsing, diagnostic reads and display. The existing manual write/restart functions remain available.

## Features

- Discover nearby NOAH devices over BLE and select a device interactively.
- Read **input registers** (Modbus function `0x04`) and **holding registers** (`0x03`) through Growatt BLE passthrough function `0x17`.
- Read a user-defined `registers.txt` list, including individual registers, ranges and overlapping ranges.
- Decode decimal/scaled values, hexadecimal bytes, ASCII text, time values, enumerations and bit fields.
- Preserve successful register values even when neighboring registers are unreadable.
- Manually read an individual register; optionally write a holding register (Modbus `0x06`) after explicit confirmation.
- Request a datalogger restart and query Growatt configuration parameters via BLE.

## Installation

The script uses the existing `protocol.py` and `growatt_ble.py` from the watchdog project. Suggested repository structure:

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

Install the repository's Python requirements and use a working BLE adapter. If the reader is in `register-reader/`, Python must be able to import modules from the repository root:

**Linux / Raspberry Pi:**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=. python3 register-reader/noah_registers.py
```

**Windows PowerShell:**

```powershell
py -m pip install -r requirements.txt
$env:PYTHONPATH = (Get-Location).Path
py .\register-reader\noah_registers.py
```

If you keep `noah_registers.py`, `protocol.py` and `growatt_ble.py` in the **same directory**, you can run the script directly without setting `PYTHONPATH`. By default, `registers.txt` must be beside `noah_registers.py`.

## Interactive menu

Start without arguments:

```bash
PYTHONPATH=. python3 register-reader/noah_registers.py
```

The interface currently uses German prompts:

```text
1) Register aus registers.txt auslesen
2) Register manuell lesen / schreiben
3) Datenlogger über BLE neu starten
4) Konfigurationsparameter lesen (Growatt 0x19)
g) Anderes Gerät auswählen
q) Beenden
```

## `registers.txt` format

Each non-comment line has five pipe-separated columns:

```text
Register|Bezeichnung|Faktor|Einheit|Registertyp
```

- **Register:** single address, inclusive range, or `start->count` expression.
- **Bezeichnung:** display label.
- **Faktor:** conversion or output format.
- **Einheit:** unit or enumerated value labels.
- **Registertyp:** `I` (input) or `H` (holding); defaults to `I` if omitted.

Example:

```text
# Single registers and calculations
12|Anzahl Akkupacks|1|Stück|I
29|SOC Bat.1|1|%|I
30|Temp. Bat.1|*0.1 -273.1|°C|I
92|PV1-Spannung|0.01|V|I

# ASCII across several 16-bit registers
21-28|Seriennummer|ASCII||I

# Bit fields: bit 0 is the rightmost character
15|Akkupack Fehler|bit9-0|Pack10-1|I
14|Heizung|bit3-0|Akku4-1|I

# Enumerations
8|Modus|1|0 Load First, 1 Battery First, 2 Smart Self-Use|I

# Time values (unit is preserved)
254|Slot1 Startzeit|TIME|Uhr|H
255|Slot1 Endzeit|TIME|Uhr|H

# Exploratory reads
100->16|Suche|1||I
116->16|Suche|HEX||I
```

### Register-address syntax

| Expression | Meaning |
| --- | --- |
| `100` | One register: 100 |
| `100-115` | Inclusive range: 100 through 115 |
| `100->16` | Start at 100, read **16** registers (100–115) |
| `100,105,110` | Three specified registers |
| `100->16,200->8` | Two ranges in one definition |

The parser rejects duplicate addresses **within a single definition**, invalid counts such as `100->0`, and ranges beyond register `65535`. Overlapping ranges in **different lines** are allowed; the reader collects distinct addresses for acquisition and renders each requested row separately.

**Note:** A range in `registers.txt` describes the desired output, not necessarily one BLE request. Acquisition is divided into blocks according to `--block-size` and `--max-gap`.

### Formatting and conversion

| `Faktor` | Behavior |
| --- | --- |
| `1`, `0.01` | Multiply raw register value by the specified factor |
| `*0.1 -273.1` | Arithmetic expression applied to the raw value |
| `ASCII` | Interpret register bytes as ASCII text |
| `HEX` | Show bytes in hexadecimal, high byte first |
| `TIME` | Decode one register as `HH:MM` (high byte = hour, low byte = minute) |
| `bit9-0`, `bit3-0` | Show the specified bit range in binary, most significant bit left and LSB right |

Bit masks operate on a **single 16-bit register**, with bit numbers 0–15. `TIME` also requires a single register. For example, raw value `5` with `bit3-0` displays `0101`; raw value `536` (`0x0218`) appears as bytes `02 18`.

The reader also recognizes enum labels in the unit column, such as `0 Aus, 1 Ein`. An alternative six-column bit-mask notation is accepted for compatibility, e.g. `14|Heizung|1|bit3-0|Akku4-1|I`; the five-column form above is recommended.

## Register-range display

For numeric register ranges, values are displayed horizontally in **two aligned rows**: decimal (or scaled) values above, hexadecimal bytes below. Each decimal/hex pair refers to the same register position.

Example (illustrative values):

```text
Reg       Bezeichnung  Werte / Status
90-93     Suche          100    10   536    13
                         00 64 00 0A 02 18 00 0D  [OK]
```

Each hexadecimal register consists of **two bytes**, high byte first. Missing values are shown as `?` in the decimal row and `?? ??` in the hex row. Results are classified as:

- `OK`: every requested register was read.
- `TEILWEISE (n/N Register)`: at least one, but not all, registers were read.
- `NICHT LESBAR (0/N Register)`: none of the requested registers could be read.

The placeholders remain visible even for entirely unreadable ranges. **Very wide ranges may still wrap in narrow terminals**; consider shorter rows (e.g. `100->8`) or a wider terminal window.

## Block reads and adaptive fallback

The reader first groups requested addresses into blocks, separately for input and holding registers. Defaults:

- `--block-size 16`: at most 16 registers per initial block (allowed: 1–32).
- `--max-gap 2`: bridge up to two unrequested registers between nearby requested addresses (allowed: 0–8).

When a block cannot be decoded, v0.6.3:

1. Queries the **first** and **last** register individually.
2. If **both** are unreadable, queries the remaining registers individually.
3. If **at least one** is readable, splits the block and tries smaller blocks recursively.
4. Reuses cached individual probe results rather than repeating those probes.

This strategy can reduce requests in fully unreadable regions. For a completely unreadable 16-register block, it requires **17 requests** (one initial block request plus 16 individual reads), instead of 31 with full recursive splitting. It does **not** guarantee the fewest requests for every possible failure pattern.

A failed block read does **not** necessarily mean the addresses do not exist: the device may reject a range, return a Modbus exception, time out, or use an as-yet-unrecognized reply format. The tool reports what it can decode; it does not prove register absence.

## Command-line usage

Run from the repository root with the root directory on `PYTHONPATH` if using the layout above:

```bash
PYTHONPATH=. python3 register-reader/noah_registers.py scan
PYTHONPATH=. python3 register-reader/noah_registers.py read --address <BLE_ADDRESS> --type input --register 92
PYTHONPATH=. python3 register-reader/noah_registers.py read --address <BLE_ADDRESS> --type holding --register 255
PYTHONPATH=. python3 register-reader/noah_registers.py list --address <BLE_ADDRESS>
PYTHONPATH=. python3 register-reader/noah_registers.py list --address <BLE_ADDRESS> --file register-reader/registers.txt --block-size 8 --max-gap 0
```

Use `--verbose` to inspect raw BLE traffic when troubleshooting, and `--timeout` to adjust response waiting time. The `read` subcommand supports `--count` (1–32) for a direct block request. Run `--help`, `list --help`, or `read --help` for the complete options.

**Privacy:** Raw traffic, configuration parameters and diagnostic logs may include serial numbers, BLE addresses, network credentials, server addresses or private URLs. Redact them before sharing.

## Manual writes and configuration queries

These are separate operations from reading `registers.txt`:

| Operation | Protocol | Notes |
| --- | --- | --- |
| Input/holding register read | Growatt BLE `0x17`, inner Modbus `0x04`/`0x03` | Diagnostic read |
| Holding register write | Growatt BLE `0x17`, inner Modbus `0x06` | Experimental; explicit `JA` confirmation and read-back attempt |
| Configuration parameter query | Growatt BLE `0x19` | Read-only; may reveal sensitive values |
| Datalogger restart | Growatt BLE `0x18`, parameter `0x20` | Explicit confirmation; reboot not guaranteed by GATT ACK |

Configuration parameter IDs are **not** Modbus register addresses. For example, parameter `80` has returned a firmware-update URL on a tested device; that does **not** prove the linked firmware was installed or downloaded. The reader permits queries of all parameter IDs, including potentially sensitive ones; take care when sharing output.

The datalogger restart and configuration-parameter query are available from the **interactive menu**, not separate CLI subcommands.

## Troubleshooting

- **`ModuleNotFoundError: protocol` / `growatt_ble`:** Place the files together or set `PYTHONPATH` to the repository root.
- **`registers.txt` missing:** Keep it next to `noah_registers.py` or supply `list --file ...`.
- **No device found:** Check BLE adapter availability, permissions, distance and device advertising.
- **`Antwortformat nicht sicher dekodierbar`:** A BLE response was received, but its embedded Modbus reply could not be validated. Try a single register and `--verbose`; this is not proof that the address is unused.
- **Many slow failures:** Reduce the search range, try `--block-size 8` or `--max-gap 0`, and avoid very large speculative scans. Individual requests may still be slow.
- **`NICHT LESBAR (0/N Register)`:** All requested values in that row were unavailable; placeholders are intentional.
- **Unexpected line wraps:** Widen the terminal or define shorter display ranges.
- **Restart or write uncertainty:** A successful GATT write is not proof that a device-level operation completed. Verify device state independently.

## Credits and licensing

The BLE protocol implementation builds on **Peter Meiser's** [growatt_noah_ble](https://github.com/meiser79/growatt_noah_ble), licensed under **Apache License 2.0**. Preserve the repository's `LICENSE`, `NOTICE` and upstream attribution when redistributing derived files.

The unofficial [NEXA protocol notes by longsleep](https://gist.github.com/longsleep/cf5bc2eff0def99ad411b22abf7c2b33) provided research context; NEXA behavior should not be assumed to apply identically to every NOAH firmware.

Developed and maintained by **nixo2**, with development assistance from **OpenAI's ChatGPT**. Contributions and reproducible findings are welcome; please remove personal identifiers and credentials from logs before publishing them.
