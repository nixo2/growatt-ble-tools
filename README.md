# Growatt BLE Tools

**Local Bluetooth LE diagnostics, register access, configuration, and connectivity monitoring for Growatt battery systems.**

This independent, community-developed project provides Python tools for working directly with **Growatt NOAH** devices over Bluetooth Low Energy (BLE), without requiring the Growatt cloud for BLE operations.

The **Register Reader and interactive TUI** are the main tools. A separate **Watchdog** monitors connectivity and can restore selected settings automatically.

> **Device support:** The tools described below currently target **NOAH**. Support for **NEXA** is a future goal; do not assume the existing commands, register maps, or write operations are compatible with NEXA.
>
> **Disclaimer:** Unofficial and experimental software, not affiliated with or endorsed by Growatt. Register meanings and write operations may vary by model and firmware. Use write/recovery functions carefully and at your own risk.

## Tools

| Tool | Purpose | Documentation |
| --- | --- | --- |
| **Register Reader** (`noah_registers.py`) | BLE device discovery, command-line register reads, register lists, and diagnostics. | [Register Reader & TUI guide](docs/README_noah_ble_tools.md) |
| **Register TUI** (`noah_tui.py`) | Interactive terminal interface to inspect registers, view details, and perform supported configuration changes. | [Register Reader & TUI guide](docs/README_noah_ble_tools.md) |
| **BLE Watchdog** (`noah_watchdog.py`) | Periodically check connectivity, compare MQTT host/port with local credentials, and optionally recover settings or restart the datalogger. | [Watchdog guide](docs/README_watchdog.md) |

A [German guide for the Register Reader and TUI](docs/README_noah_ble_tools_DE.md) is also available.

## Quick start

### Requirements

- Python **3.11+** (required by the watchdog).
- A working Bluetooth LE adapter and appropriate Bluetooth permissions.
- Python dependencies from `requirements.txt` (the TUI uses Textual; the BLE tools use Bleak).

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

On Windows, activate the virtual environment with `.venv\Scripts\activate` instead. The watchdog's curses-based setup may need additional Windows support; see its guide.

### Register Reader / TUI

Launch the interactive terminal interface:

```bash
python noah_tui.py
```

Scan for nearby supported devices using the command-line reader:

```bash
python noah_registers.py scan --seconds 8
```

The register tools use `registers.txt` for register definitions and display configuration. See the [detailed reader documentation](docs/README_noah_ble_tools.md) before attempting write operations.

### Watchdog

Select devices and recovery options:

```bash
python noah_watchdog.py setup
```

Start monitoring (default: five-minute cycles):

```bash
python noah_watchdog.py run
```

The watchdog's optional Wi-Fi/MQTT recovery uses a local `credentials.txt` file. Consult the [watchdog documentation](docs/README_watchdog.md) and `credentials.example.txt` before enabling recovery. **Never commit real credentials, device secrets, or private logs.**

## Repository layout

```text
growatt-noah-ble-watchdog/    # Current repository name; rename is optional
├── README.md                # This project overview
├── noah_registers.py        # CLI register reader
├── noah_tui.py              # Interactive register interface
├── noah_watchdog.py         # Connectivity watchdog
├── protocol.py             # Shared protocol implementation
├── growatt_ble.py          # Shared BLE transport
├── registers.txt           # Register definitions
├── register_info.txt       # Supplementary register descriptions
├── credentials.example.txt # Example configuration; no real secrets
├── requirements.txt
├── LICENSE
├── NOTICE
└── docs/
    ├── README_noah_registers.md
    ├── README_noah_registers_DE.md
    └── README_watchdog.md
```

All paths in this README are relative to the repository root. The documentation links match the filenames currently shown in the `develop` branch.

## Development and releases

The Register Reader, TUI, and Watchdog have **separate internal version numbers** and may evolve at different rates. Check the respective guides and release notes for component-specific changes. Existing Watchdog releases remain part of the project's history even as the repository expands beyond monitoring.

NEXA support is **planned, not implemented or verified** in the current NOAH tools. Contributions, reproducible diagnostic results, and careful protocol research are welcome; please avoid publishing credentials or personally identifying device data in issues.

## License

See [LICENSE](LICENSE) and [NOTICE](NOTICE) for licensing and attribution information.
