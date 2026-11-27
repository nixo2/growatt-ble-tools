# Growatt BLE Tools

**Local Bluetooth LE diagnostics, register access, configuration, and connectivity monitoring for Growatt battery systems.**

This independent, community-developed project provides Python tools for working directly with **Growatt NOAH** devices over Bluetooth Low Energy (BLE), without requiring the Growatt cloud for BLE operations.

**Release 1.1 · November 27, 2026**

The interactive **terminal user interfaces (TUI)** are the recommended entry point: register diagnostics and configuration, plus integrated watchdog management. Command-line tools remain available for scripting and unattended monitoring.

> **Device support:** The tools described below currently target **NOAH**. Support for **NEXA** is a future goal; do not assume the existing commands, register maps, or write operations are compatible with NEXA.
>
> **Disclaimer:** Unofficial and experimental software, not affiliated with or endorsed by Growatt. Register meanings and write operations may vary by model and firmware. Use write/recovery functions carefully and at your own risk.

## Tools

| Tool | Purpose | Documentation |
| --- | --- | --- |
| **Register TUI 1.1** (`noah_tui.py`) | Interactive register diagnostics, device management, timeslots and supported configuration changes. | [BLE tools guide](docs/README_noah_ble_tools.md) |
| **Watchdog UI 0.1.8** (`noah_watchdog_ui.py`) | Integrated Linux and Windows process management, device setup, recovery options, logs and statistics. | [Watchdog guide](docs/README_watchdog.md) |
| Register Reader 0.8.2 (`noah_registers.py`) | CLI discovery, register reads and diagnostics. | [BLE tools guide](docs/README_noah_ble_tools.md) |
| BLE Watchdog 0.4.2 (`noah_watchdog.py`) | Standalone connectivity monitoring and optional recovery. | [Watchdog guide](docs/README_watchdog.md) |

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

On Windows (PowerShell), from the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

The requirements file installs `windows-curses` and `psutil` only on Windows. Linux retains its existing `/proc`-based process management without `psutil`.

### Interactive interfaces (recommended)

```bash
python noah_tui.py
```

Select a NOAH for register diagnostics, or open **Watchdog (W)** from the start screen or register overview. On Linux and Windows, the integrated watchdog interface supports starting, stopping and restarting the monitoring process, configuring devices and recovery options, and viewing filtered logs and recovery statistics. Configuration changes require a manual watchdog restart to take effect; a confirmation dialog explains this without stopping the process automatically.

### Command-line tools (CLI)

```bash
python noah_registers.py scan --seconds 8
python noah_watchdog.py setup
python noah_watchdog.py run
```

The default watchdog interval is five minutes and the default BLE scan duration is eight seconds. The register tools use `registers.txt`; consult the [BLE tools guide](docs/README_noah_ble_tools.md) before writing registers.

The watchdog's optional Wi-Fi/MQTT recovery uses a local `credentials.txt` file. Consult the [watchdog documentation](docs/README_watchdog.md) and `credentials.example.txt` before enabling recovery. **Never commit real credentials, device secrets, or private logs.**

## Repository layout

```text
growatt-noah-ble-watchdog/    # Current repository name; rename is optional
├── README.md                # This project overview
├── noah_registers.py        # CLI register reader
├── noah_tui.py              # Interactive register interface
├── noah_watchdog_ui.py      # Integrated watchdog management
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
    ├── README_noah_ble_tools.md
    ├── README_noah_ble_tools_DE.md
    └── README_watchdog.md
```

All paths in this README are relative to the repository root.

## Development and releases

Release 1.1 adds integrated Windows watchdog process management alongside the unchanged Linux backend. Windows monitoring can run in the background without opening an additional console window and is recognized again after restarting the TUI. The standalone watchdog and register reader are unchanged.

Starting with release 1.0, the project release version and TUI version are aligned. The Register Reader, Watchdog and Watchdog UI retain independent internal versions. Check the respective guides and release notes for component-specific changes. Existing Watchdog releases remain part of the project's history even as the repository expands beyond monitoring.

NEXA support is **planned, not implemented or verified** in the current NOAH tools. Contributions, reproducible diagnostic results, and careful protocol research are welcome; please avoid publishing credentials or personally identifying device data in issues.

## License

See [LICENSE](LICENSE) and [NOTICE](NOTICE) for licensing and attribution information.
