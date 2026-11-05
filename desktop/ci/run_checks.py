# -*- coding: utf-8 -*-
"""Checks of the built PC app on the build machines (GitHub Actions, Mac and Windows). Everything goes to $REPORT.

  python run_checks.py server              the app's server in Chromium (smoke_server.py)
  python run_checks.py window              the app window: page, CSV upload, downloads (saved without the dialog)
  python run_checks.py dialog              the app window: the save dialog opens (photo); killing the app stops the server
  python run_checks.py installer SETUP     Windows: install without questions, start, uninstall
  python run_checks.py minos APP           Mac: the oldest macOS that every program in the app supports
Exit code 1 when a check fails.
"""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPORT = os.path.abspath(os.environ.get('REPORT') or 'report')
TMP = os.environ.get('RUNNER_TEMP') or os.path.abspath('tmp')
MAC = sys.platform == 'darwin'
EXE = os.path.abspath('dist/figapp.app/Contents/MacOS/figapp' if MAC else 'dist/figapp/figapp.exe')
os.makedirs(REPORT, exist_ok=True)
fails, lines = [], []


def ok(cond, what):
    line = ('OK   ' if cond else 'FAIL ') + what
    print(line, flush=True)
    lines.append(line)
    if not cond:
        fails.append(what)


def finish(name):
    with open(os.path.join(REPORT, f'checks-{name}.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(lines) + '\n\nFAILED: ' + (', '.join(fails) if fails else 'none') + '\n')
    print('\nFAILED:', fails if fails else 'none')
    sys.exit(1 if fails else 0)


def health(port):
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                f'http://127.0.0.1:{port}/_stcore/health', timeout=3) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def alive(pid):
    if os.name == 'nt':
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.OpenProcess.restype = ctypes.c_void_p
        k32.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
        k32.GetExitCodeProcess.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong))
        k32.CloseHandle.argtypes = (ctypes.c_void_p,)
        h = k32.OpenProcess(0x1000, False, pid)                    # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return code.value == 259                                   # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def wait_for(fn, timeout, step=0.5):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if fn():
            return time.time() - t0
        time.sleep(step)
    return None


def photo(name):
    try:
        from PIL import ImageGrab
        im = ImageGrab.grab()
        im.save(os.path.join(REPORT, name))
        print('photo', name, im.size, flush=True)
    except Exception as e:  # noqa: BLE001
        print('photo failed', name, repr(e), flush=True)


def copy_logs(data, tag):
    d = os.path.join(data, 'logs')
    if os.path.isdir(d):
        for f in os.listdir(d):
            shutil.copy(os.path.join(d, f), os.path.join(REPORT, f'{tag}-{f}'))


# ---------------------------------------------------------------------------------------------- checks
def server():
    data = os.path.join(TMP, 'data-server')
    os.makedirs(data, exist_ok=True)
    p = subprocess.Popen([EXE, '--server', '8765', '--mode', 'window', '--parent', '0'],
                         env=dict(os.environ, FIGAPP_DATA_DIR=data))
    t = wait_for(lambda: health(8765), 300)
    ok(t is not None, f'server answers ({t and round(t, 1)} s after start)')
    if t is not None:
        r = subprocess.run([sys.executable, os.path.join(HERE, 'smoke_server.py'), 'http://127.0.0.1:8765',
                            os.path.join(REPORT, 'server'), data, 'window'], timeout=1800)
        ok(r.returncode == 0, 'browser checks of the server (see server/smoke_server_window.txt)')
    p.terminate()
    try:
        p.wait(20)
    except subprocess.TimeoutExpired:
        p.kill()
    copy_logs(data, 'server')
    finish('server')


def window(save):
    tag = 'window' if save else 'dialog'
    st = os.path.join(REPORT, tag)
    data = os.path.join(TMP, 'data-' + tag)
    shutil.rmtree(st, ignore_errors=True)
    os.makedirs(st)
    env = dict(os.environ, FIGAPP_DATA_DIR=data, FIGAPP_SELFTEST=st, FIGAPP_SELFTEST_QUERY='demo=Bar',
               FIGAPP_SELFTEST_CLOSE='1')
    if save:
        env['FIGAPP_SELFTEST_SAVE'] = os.path.join(st, 'saved')
    t0 = time.time()
    p = subprocess.Popen([EXE], env=env)
    t = wait_for(lambda: os.path.exists(os.path.join(st, 'loaded.ready')), 420)
    ok(t is not None, f'{tag}: page shown in the app window ({t and round(t, 1)} s after start)')
    time.sleep(1.5)
    photo(f'{tag}-1-loaded.png')
    running = {}
    try:
        running = json.load(open(os.path.join(data, 'running.json'), encoding='utf-8'))
    except (OSError, ValueError):
        pass
    ok(running.get('mode') == 'window', f'{tag}: runs in its own window (mode {running.get("mode")})')
    spid = running.get('server_pid')
    if save:
        t = wait_for(lambda: os.path.exists(os.path.join(st, 'done.ready')), 420)
        ok(t is not None, f'{tag}: upload and downloads finished')
        time.sleep(1)
        photo(f'{tag}-2-done.png')
        t = wait_for(lambda: p.poll() is not None, 90)
        ok(t is not None, f'{tag}: the app ends when its window closes (code {p.poll()})')
    else:
        t = wait_for(lambda: os.path.exists(os.path.join(st, 'dialog.ready')), 420)
        ok(t is not None, f'{tag}: PDF button pressed')
        time.sleep(5)
        photo(f'{tag}-2-save-dialog.png')
        t = wait_for(lambda: os.path.exists(os.path.join(st, 'dialog_done.ready')), 120)
        ok(t is not None, f'{tag}: Save pressed in the dialog')
        time.sleep(1)
        photo(f'{tag}-3-after-save.png')
        p.kill()                                                    # like a crash: the server must stop by itself
        p.wait(30)
    if spid:
        t = wait_for(lambda: not alive(spid), 30)
        ok(t is not None, f'{tag}: the server stopped too ({t and round(t, 1)} s)')
    if p.poll() is None:
        p.kill()
    res = {}
    try:
        res = json.load(open(os.path.join(st, 'selftest.json'), encoding='utf-8'))
    except (OSError, ValueError):
        pass
    ok(res.get('sidebar') and res.get('figure'), f'{tag}: app and figure drawn')
    ok(res.get('exceptions') == 0, f'{tag}: no error on the page ({res.get("exceptions")})')
    ok(res.get('upload') == 'ok' and res.get('uploaded'), f'{tag}: CSV uploaded in the window ({res.get("upload")})')
    if save:
        dl = res.get('downloads') or {}
        for name, head in (('figure.pdf', '%PDF'), ('figure.svg', '<'), ('figure.png', '\x89PNG'), ('figure.json', '{')):
            got = dl.get(name) or {}
            ok(str(got.get('head', '')).startswith(head) and got.get('bytes', 0) > 100,
               f'{tag}: {name} saved from the window ({got.get("bytes")} bytes)')
        ok(res.get('exceptions_end') == 0, f'{tag}: no error after the downloads')
    else:
        ok(res.get('clicked_pdf') is True, f'{tag}: PDF button found')
        ok((res.get('dialog') or {}).get('pressed'), f'{tag}: the save dialog was there ({res.get("dialog")})')
        got = res.get('saved_by_dialog') or {}
        ok(str(got.get('head', '')).startswith('%PDF') and got.get('bytes', 0) > 100,
           f'{tag}: figure.pdf saved to {res.get("download_target")} through the dialog ({got.get("bytes")} bytes)')
    print(json.dumps(res, ensure_ascii=False, indent=1)[:3000])
    print('took', round(time.time() - t0, 1), 's')
    copy_logs(data, tag)
    finish(tag)


def installer(setup):
    log = os.path.join(REPORT, 'installer-setup.log')
    r = subprocess.run([setup, '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/CURRENTUSER', f'/LOG={log}'], timeout=900)
    ok(r.returncode == 0, f'installer ran without questions (code {r.returncode})')
    home = os.path.join(os.environ['LOCALAPPDATA'], 'Programs', 'figapp')
    exe = os.path.join(home, 'figapp.exe')
    ok(os.path.exists(exe), f'installed to {home}')
    menu = os.path.join(os.environ['APPDATA'], 'Microsoft', 'Windows', 'Start Menu', 'Programs', 'figapp.lnk')
    desk = os.path.join(os.environ['USERPROFILE'], 'Desktop', 'figapp.lnk')
    ok(os.path.exists(menu), 'Start menu shortcut')
    ok(os.path.exists(desk), 'desktop shortcut')
    data = os.path.join(TMP, 'data-installed')
    p = subprocess.Popen([exe, '--server', '8790', '--mode', 'window', '--parent', '0'], env=dict(os.environ, FIGAPP_DATA_DIR=data))
    t = wait_for(lambda: health(8790), 300)
    ok(t is not None, f'installed app starts ({t and round(t, 1)} s)')
    p.kill()
    p.wait(30)
    time.sleep(2)
    r = subprocess.run([os.path.join(home, 'unins000.exe'), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART'], timeout=600)
    t = wait_for(lambda: not os.path.exists(exe), 120)
    ok(t is not None, 'uninstalled (program removed)')
    ok(not os.path.exists(menu), 'uninstalled (Start menu shortcut removed)')
    finish('installer')


def minos(app):
    """The newest 'minimum macOS' among all Mach-O files (otool -l), i.e. the oldest macOS the app runs on."""
    worst, rows = (0, 0), []
    for root, _, files in os.walk(app):
        for f in files:
            path = os.path.join(root, f)
            if os.path.islink(path):
                continue
            with open(path, 'rb') as fh:
                magic = fh.read(4)
            if magic not in (b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xce\xfa\xed\xfe'):
                continue
            out = subprocess.run(['otool', '-l', path], capture_output=True, text=True).stdout.split('\n')
            for i, line in enumerate(out):
                s = line.strip()
                if s.startswith('minos ') or (s.startswith('version ') and 'LC_VERSION_MIN_MACOSX' in ''.join(out[max(0, i - 3):i])):
                    v = tuple(int(x) for x in s.split()[1].split('.')[:2])
                    rows.append((v, os.path.relpath(path, app)))
                    worst = max(worst, v)
                    break
    rows.sort(reverse=True)
    with open(os.path.join(REPORT, 'min-macos.txt'), 'w') as fh:
        fh.write(f'oldest macOS the app supports: {worst[0]}.{worst[1]}\n\nnewest requirements:\n')
        fh.writelines(f'{v[0]}.{v[1]}  {p}\n' for v, p in rows[:25])
    print(f'oldest macOS the app supports: {worst[0]}.{worst[1]} ({len(rows)} programs)')
    ok(worst <= (12, 0), f'runs on macOS 12 or later (needs {worst[0]}.{worst[1]})')
    finish('minos')


if __name__ == '__main__':
    what = sys.argv[1]
    if what == 'server':
        server()
    elif what == 'window':
        window(True)
    elif what == 'dialog':
        window(False)
    elif what == 'installer':
        installer(sys.argv[2])
    elif what == 'minos':
        minos(sys.argv[2])
