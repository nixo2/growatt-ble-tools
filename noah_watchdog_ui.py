"""NOAH Watchdog management v0.1.8 (Linux and Windows process control)."""
import asyncio
import json
import math
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from rich.text import Text
from textual.screen import ModalScreen
from textual.binding import Binding
from textual.containers import VerticalScroll, Horizontal, Vertical
from textual.widgets import Button, Input, Label, Static, Select, OptionList, TextArea
from textual.widgets.option_list import Option

BASE = Path(__file__).resolve().parent
SCRIPT = BASE / 'noah_watchdog.py'
CONFIG = BASE / 'noah_watchdog_devices.json'
LOG = BASE / 'noah_watchdog.log'
CONSOLE = BASE / 'watchdog_console.log'
DEFAULTS = dict(logging=True, wifi=False, mqtt=False, restart=False, cycles=3)


def linux_processes():
    if not Path('/proc/uptime').exists():
        raise RuntimeError('Prozessverwaltung benötigt Linux /proc.')
    result = {}
    for p in Path('/proc').iterdir():
        if not p.name.isdecimal():
            continue
        try:
            args = (p / 'cmdline').read_bytes().decode(errors='replace').rstrip('\0').split('\0')
            for i, arg in enumerate(args[:-1]):
                if Path(arg).name != SCRIPT.name or args[i+1] != 'run':
                    continue
                if i == 0 or not Path(args[0]).name.startswith('python'):
                    continue
                if (Path(os.readlink(p / 'cwd')) / arg).resolve() != SCRIPT.resolve():
                    continue
                stat = (p / 'stat').read_text().rsplit(')', 1)[1].split()
                if stat[0] != 'Z':
                    result[int(p.name)] = (int(stat[19]), args[i+2:])
                break
        except (OSError, ValueError, IndexError):
            continue
    return result


# Windows process backend. Keep the existing Linux backend independent.
def windows_psutil():
    try:
        import psutil
    except ImportError as exc:
        raise RuntimeError('Windows-Prozessverwaltung benötigt psutil (pip install psutil).') from exc
    return psutil


def windows_process_records():
    psutil = windows_psutil()
    result = {}
    for proc in psutil.process_iter():
        try:
            args = proc.cmdline()
            if not args or not re.fullmatch(r'python(?:w|[0-9.]+)?(?:\.exe)?', Path(args[0]).name, re.I):
                continue
            for i, arg in enumerate(args[1:-1], 1):
                if Path(arg).name.casefold() != SCRIPT.name.casefold() or args[i+1] != 'run':
                    continue
                path = Path(arg)
                if not path.is_absolute():
                    path = Path(proc.cwd()) / path
                if path.resolve() == SCRIPT.resolve():
                    result[proc.pid] = (proc.create_time(), args[i+2:], proc.ppid(), args)
                break
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError, ValueError):
            continue
    return result


def windows_groups(records):
    """Collapse matching launcher/worker chains, not independent instances."""
    parents = {}
    for pid, (started, args, ppid, command) in records.items():
        parent = records.get(ppid)
        if parent and ppid != pid and parent[0] <= started and parent[3] == command:
            parents[pid] = ppid
    # Only collapse unambiguous chains; branching remains a multiple-instance warning.
    counts = Counter(parents.values())
    parents = {pid: ppid for pid, ppid in parents.items() if counts[ppid] == 1}
    groups = {}
    for pid in records.keys() - parents.values():
        chain = [pid]
        while chain[-1] in parents and parents[chain[-1]] not in chain:
            chain.append(parents[chain[-1]])
        groups[pid] = chain
    return groups


def windows_processes():
    records = windows_process_records()
    return {pid: records[pid][:2] for pid in windows_groups(records)}


def processes():
    return windows_processes() if sys.platform == 'win32' else linux_processes()


async def windows_stop(pid, identity):
    psutil = windows_psutil()
    records = windows_process_records()
    groups = windows_groups(records)
    if len(groups) != 1 or pid not in groups or records[pid][:2] != identity:
        raise RuntimeError('Watchdog-Prozess hat sich geändert; bitte Status aktualisieren.')
    # Stop worker first, then any surviving matching launcher(s).
    for member in groups[pid]:
        try:
            proc = psutil.Process(member)
            if proc.create_time() != records[member][0]:
                continue
            if windows_process_records().get(member) != records[member]:
                continue
            proc.terminate()
            try:
                await asyncio.to_thread(proc.wait, timeout=2)
            except psutil.TimeoutExpired:
                raise RuntimeError('Watchdog-Prozess läuft weiterhin.')
        except psutil.NoSuchProcess:
            pass


def load_config():
    data = json.loads(CONFIG.read_text(encoding='utf-8') if sys.platform == 'win32' else CONFIG.read_text()) if CONFIG.exists() else {}
    if isinstance(data, list):
        data = {'devices': data}
    if not isinstance(data, dict) or not isinstance(data.get('devices', []), list) or not isinstance(data.get('options', {}), dict):
        raise ValueError('Ungültige Konfiguration')
    for d in data.get('devices', []):
        if not isinstance(d, dict) or not isinstance(d.get('address'), str) or not d['address']:
            raise ValueError('Ungültiger Geräteeintrag')
    data.setdefault('devices', [])
    data['options'] = {**DEFAULTS, **data.get('options', {})}
    return data


def save_config(data):
    fd, path = tempfile.mkstemp(dir=BASE, prefix='.watchdog-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write('\n')
        os.replace(path, CONFIG)
    finally:
        if os.path.exists(path):
            os.unlink(path)


def log_lines(path, today=False, term=''):
    if not path.exists():
        return []
    prefix = date.today().isoformat()
    with path.open(encoding='utf-8', errors='replace') as f:
        return [line.rstrip('\n') for line in f if (not today or line.startswith(prefix)) and term.casefold() in line.casefold()]


def statistics():
    counts = Counter()
    names = {}
    today = date.today()
    for line in log_lines(LOG, term='RECOVERY START:'):
        m = re.match(r'(\d{4}-\d\d-\d\d) .*? (\S+) ((?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}) \| (MQTT RECOVERY|RECOVERY) START:', line)
        if not m:
            continue
        day, name, addr, kind = m.groups()
        day = date.fromisoformat(day)
        names[addr] = name
        counts[addr, kind, 'gesamt'] += 1
        if today-timedelta(days=6) <= day <= today:
            counts[addr, kind, '7 Tage'] += 1
        if day == today:
            counts[addr, kind, 'heute'] += 1
    rows = ['Recovery-Starts (keine Erfolgszählung)', 'Gerät                 Art          Heute  7 Tage  Gesamt']
    for addr, name in sorted(names.items()):
        for kind in ('RECOVERY', 'MQTT RECOVERY'):
            rows.append(f'{name:20} {kind:13} ' + ' '.join(f'{counts[addr,kind,p]:7}' for p in ('heute','7 Tage','gesamt')))
    return '\n'.join(rows)


class ServiceToggle(Button):
    """Explicit ASCII checkmark, independent of terminal colors."""
    def __init__(self, caption, **kwargs):
        self.caption = caption
        self._checked = False
        super().__init__(Text(f'[ ] {caption}'), **kwargs)

    @property
    def value(self):
        return self._checked

    @value.setter
    def value(self, checked):
        self._checked = bool(checked)
        self.label = Text(f'[{"X" if self._checked else " "}] {self.caption}')


class RestartNotice(ModalScreen):
    """Require acknowledgement without restarting the watchdog."""
    CSS = """
    RestartNotice { align: center middle; background: $background 70%; }
    #wd_notice_box { align-horizontal: center; width: 64; max-width: 95%; height: auto;
                     border: round $accent; background: $panel; padding: 1 2; }
    #wd_notice_text { height: auto; margin: 1 0; }
    #wd_notice_ok { width: 18; color: #ffffff; background: #303030; }
    #wd_notice_ok:focus { color: #000000; background: #ffffff; text-style: bold; }
    """

    def compose(self):
        with Vertical(id='wd_notice_box'):
            yield Label('Hinweis – Watchdog-Neustart')
            yield Static('Damit die Änderungen wirksam werden, muss der Watchdog neu gestartet werden.\n\nDer laufende Prozess bleibt unverändert aktiv.', id='wd_notice_text', markup=False)
            yield Button('OK (Enter)', id='wd_notice_ok', variant='default')

    def on_mount(self):
        self.query_one('#wd_notice_ok', Button).focus()

    def on_button_pressed(self, event):
        if event.button.id == 'wd_notice_ok':
            event.stop()
            self.dismiss()


class WatchdogScreen(ModalScreen):
    BINDINGS = [Binding('escape', 'close', 'Zurück')]
    CSS = '''
    WatchdogScreen { align: center middle; }
    #wd_box { width: 96%; height: 96%; border: round $accent; background: $panel; padding: 0 1; }
    #wd_box Horizontal { height: auto; }
    #wd_box Button { min-width: 10; margin-right: 1; }
    #wd_box Input { width: 1fr; }
    #wd_box Select { width: 1fr; }
    #wd_box #wd_cycles { width: 7; min-width: 7; margin-right: 1; }
    #wd_cycles_label { width: auto; height: 3; content-align: left middle; margin-right: 1; }
    #wd_devices { height: 6; }
    #wd_output { height: 1fr; min-height: 18; }
    #wd_status, #wd_message { height: auto; }
    '''
    def __init__(self):
        super().__init__()
        self.data = None
        self.child = None
        self.working = False
        self.recovery_filter = False
        self.manual_filter = ""

    def compose(self):
        with VerticalScroll(id='wd_box'):
            yield Label('NOAH Watchdog – Verwaltung')
            yield Static('', id='wd_status', markup=False)
            with Horizontal():
                yield Button('Starten', id='wd_start')
                yield Button('Stoppen', id='wd_stop')
                yield Button('Neu starten', id='wd_restart')
                yield Button('Status', id='wd_refresh')
                yield Button('Zurück', id='wd_close')
            with Horizontal():
                yield Label('Intervall (min)')
                yield Input('5', id='wd_minutes')
                yield Label('Scan (s)')
                yield Input('8', id='wd_scan')
            yield Label('Überwachte Geräte')
            yield OptionList(id='wd_devices')
            yield Button('Geräte verwalten (BLE-Scan)', id='wd_setup')
            with Horizontal():
                for key, label in [('logging','Logging'),('wifi','WLAN'),('mqtt','MQTT'),('restart','Logger-Neustart')]:
                    yield ServiceToggle(label, id='wd_'+key+'_opt')
                yield Label('Recovery nach Zyklen', id='wd_cycles_label')
                yield Select([(str(i),i) for i in (1,2,3)], value=3, allow_blank=False, id='wd_cycles')
                yield Button('Speichern', id='wd_save')
            yield Static('', id='wd_message', markup=False)
            yield Label('Protokolle und Statistik')
            with Horizontal():
                yield Select([('Watchdog','log'),('Konsole','console')], value='log', allow_blank=False,id='wd_source')
                yield Select([('Heute','today'),('Gesamt','all')], value='today', allow_blank=False,id='wd_period')
                yield Input(placeholder='Filter (z. B. RECOVERY)',id='wd_filter')
            with Horizontal():
                yield Button('Anzeigen',id='wd_show')
                yield Button('Recovery heute',id='wd_today')
                yield Button('Recovery gesamt',id='wd_all')
                yield Button('Statistik',id='wd_stats')
                yield Button('Zum Ende',id='wd_end')
            yield TextArea('',read_only=True,id='wd_output')

    def msg(self, text):
        self.query_one('#wd_message',Static).update(text)

    def field(self, key):
        return self.query_one('#wd_'+key,Input)

    def on_mount(self):
        try:
            self.reload_config()
        except Exception as exc:
            self.msg(str(exc))
        self.refresh_status(initial=True)
        self.set_interval(5,self.refresh_status)

    def refresh_devices(self):
        listing = self.query_one('#wd_devices',OptionList)
        listing.clear_options()
        for i,d in enumerate(self.data['devices']):
            listing.add_option(Option(f"{d.get('name','')}  {d['address']}",id=str(i)))

    def reload_config(self):
        self.data = load_config()
        self.refresh_devices()
        for key in ('logging','wifi','mqtt','restart'):
            self.query_one('#wd_'+key+'_opt',ServiceToggle).value = bool(self.data['options'][key])
        cycles = self.data['options']['cycles']
        self.query_one('#wd_cycles',Select).value = cycles if cycles in (1,2,3) else 3
    def selected_options(self):
        return {**self.data['options'],
                **{opt: self.query_one('#wd_'+opt+'_opt', ServiceToggle).value
                   for opt in ('logging','wifi','mqtt','restart')},
                'cycles': self.query_one('#wd_cycles', Select).value}

    def setup_devices(self):
        if self.data is not None and self.selected_options() != self.data['options']:
            raise ValueError('Bitte geänderte Maßnahmen zuerst speichern.')
        if not SCRIPT.is_file():
            raise ValueError('Watchdog-Skript fehlt.')
        with self.app.suspend():
            result = subprocess.run([sys.executable, str(SCRIPT), 'setup'], cwd=BASE)
        self.reload_config()
        if result.returncode:
            raise RuntimeError(f'Setup fehlgeschlagen (Exit {result.returncode}).')
        if processes():
            self.msg('Konfiguration neu eingelesen. Neustart erforderlich.')
            self.app.push_screen(RestartNotice())
        else:
            self.msg('Konfiguration neu eingelesen. Watchdog bei Bedarf starten.')

    def refresh_status(self, initial=False):
        try:
            if self.child is not None and self.child.poll() is not None:
                self.child = None
            found = processes()
            if sys.platform == 'win32':
                lines = [f'PID {pid} | Laufzeit {int(max(0,time.time()-start))//60} min | {" ".join(args)}' for pid,(start,args) in found.items()]
            else:
                uptime = float(Path('/proc/uptime').read_text().split()[0])
                hz = os.sysconf('SC_CLK_TCK')
                lines = [f'PID {pid} | Laufzeit {int(max(0,uptime-start/hz))//60} min | {" ".join(args)}' for pid,(start,args) in found.items()]
            self.query_one('#wd_status',Static).update('\n'.join(lines) or 'Watchdog nicht aktiv')
            if initial and len(found)==1:
                args = next(iter(found.values()))[1]
                for flag,key in [('--minutes','minutes'),('--scan-timeout','scan')]:
                    for i,arg in enumerate(args):
                        if arg.startswith(flag+'='):
                            self.field(key).value=arg.split('=',1)[1]
                        elif arg==flag and i+1<len(args):
                            self.field(key).value=args[i+1]
        except Exception as exc:
            self.query_one('#wd_status',Static).update(str(exc))

    def action_close(self):
        if not self.working:
            self.dismiss()

    async def stop(self):
        found = processes()
        if len(found)>1:
            raise ValueError('Mehrere Watchdogs aktiv; bitte manuell bereinigen.')
        for pid,identity in found.items():
            if processes().get(pid)==identity:
                if sys.platform == 'win32':
                    await windows_stop(pid, identity)
                else:
                    os.kill(pid,signal.SIGTERM)
        for _ in range(50):
            if not processes():
                return
            await asyncio.sleep(.1)
        raise RuntimeError('Watchdog läuft weiterhin; kein erzwungenes Beenden.')

    def command(self):
        minutes,scan = (float(self.field(k).value) for k in ('minutes','scan'))
        if not all(math.isfinite(x) and x>0 for x in (minutes,scan)):
            raise ValueError('Intervalle müssen positiv und endlich sein.')
        if not SCRIPT.is_file() or not load_config()['devices']:
            raise ValueError('Watchdog-Skript oder gespeicherte Geräte fehlen.')
        return [sys.executable,'-u',str(SCRIPT),'run','--minutes',str(minutes),'--scan-timeout',str(scan)]

    async def start_process(self,cmd):
        if processes():
            raise RuntimeError('Watchdog läuft bereits.')
        with CONSOLE.open('ab') as out:
            if sys.platform == 'win32':
                self.child = subprocess.Popen(cmd,cwd=BASE,stdin=subprocess.DEVNULL,stdout=out,stderr=subprocess.STDOUT,
                                              creationflags=subprocess.CREATE_NO_WINDOW,
                                              env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
            else:
                self.child = subprocess.Popen(cmd,cwd=BASE,stdin=subprocess.DEVNULL,stdout=out,stderr=subprocess.STDOUT,start_new_session=True)
        await asyncio.sleep(1)
        if self.child.poll() is not None:
            raise RuntimeError(f'Start fehlgeschlagen (Exit {self.child.returncode}); Konsolenlog prüfen.')
        self.msg('Watchdog gestartet; Konfiguration von Festplatte geladen.')

    async def on_button_pressed(self,event):
        event.stop()
        key = event.button.id.removeprefix('wd_')
        if self.working:
            return
        self.working=True
        try:
            if key=='close':
                self.dismiss()
                return
            if key in ('start','stop','restart'):
                cmd = self.command() if key!='stop' else None
                if key!='start':
                    await self.stop()
                    self.msg('Watchdog gestoppt.')
                if cmd:
                    await self.start_process(cmd)
            elif isinstance(event.button, ServiceToggle):
                event.button.value = not event.button.value
            elif key == 'setup':
                self.setup_devices()
            elif key == 'save':
                if self.data is None:
                    raise ValueError('Konfiguration konnte nicht geladen werden.')
                self.data['options'] = self.selected_options()
                save_config(self.data)
                if processes():
                    self.msg('Gespeichert. Neustart erforderlich.')
                    self.app.push_screen(RestartNotice())
                else:
                    self.msg('Gespeichert. Watchdog bei Bedarf starten.')
            elif key in ('show','today','all','stats','end'):
                output=self.query_one('#wd_output',TextArea)
                if key=='end':
                    output.move_cursor((output.document.line_count-1,0))
                elif key=='stats':
                    output.load_text(statistics())
                else:
                    if key in ('today','all'):
                        self.query_one('#wd_source',Select).value='log'
                        self.query_one('#wd_period',Select).value='today' if key=='today' else 'all'
                        if not self.recovery_filter or self.field('filter').value != 'RECOVERY':
                            self.manual_filter = self.field('filter').value
                        self.recovery_filter = True
                        self.field('filter').value='RECOVERY'
                    elif self.recovery_filter:
                        if self.field('filter').value == 'RECOVERY':
                            self.field('filter').value = self.manual_filter
                        self.recovery_filter = False
                    path=LOG if self.query_one('#wd_source',Select).value=='log' else CONSOLE
                    lines=await asyncio.to_thread(log_lines,path,self.query_one('#wd_period',Select).value=='today',self.field('filter').value)
                    output.load_text('\n'.join(lines) if lines else 'Keine passenden Einträge.' if path.exists() else 'Logdatei fehlt.')
                    self.msg(f'{len(lines)} Einträge | {path.name}')
        except Exception as exc:
            self.msg(str(exc))
        finally:
            self.working=False
            if key!='close': self.refresh_status()
