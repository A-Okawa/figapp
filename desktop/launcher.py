# -*- coding: utf-8 -*-
"""figapp の PC 版（Mac・Windows のアプリ）を起動する。

ダブルクリックで開くと、アプリの窓の中に Web 版と同じ画面が出る。画面を作る Streamlit のサーバーはこの PC の中
（127.0.0.1）だけで動き、ほかの PC からはつながらない。窓を閉じるとサーバーも止まる。

同じ実行ファイルが 3 通りに動く:
  figapp                   アプリの窓（pywebview）。窓を作れない PC（Windows で Edge WebView2 が無いなど）ではブラウザで開く
  figapp --browser         いつものブラウザで開く（タブを閉じてしばらくするとサーバーが止まる。画面の「アプリを終了」でも止まる）
  figapp --server PORT     Streamlit のサーバー（窓の側が起動する。ユーザーが直接使うことはない）

設定（色の一覧・画面の言語）とログは、Mac は ~/Library/Application Support/figapp、Windows は %APPDATA%\\figapp
（FIGAPP_DATA_DIR で変更可）。開発中は `python desktop/launcher.py` でも動く（そのときはリポジトリの app.py を使う）。
"""
import json
import locale
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

APP = 'figapp'
FROZEN = getattr(sys, 'frozen', False)
HERE = os.path.dirname(os.path.abspath(__file__))
LANGS = ('ja', 'en', 'vi', 'th')

TEXT = {
    'loading': {'ja': '起動しています…', 'en': 'Starting…', 'vi': 'Đang khởi động…', 'th': 'กำลังเริ่มต้น…'},
    'first': {'ja': '初めて開くときは 1 分ほどかかることがあります。',
              'en': 'The first start can take about a minute.',
              'vi': 'Lần mở đầu tiên có thể mất khoảng một phút.',
              'th': 'การเปิดครั้งแรกอาจใช้เวลาประมาณหนึ่งนาที'},
    'failed': {'ja': '起動できませんでした。もう一度開いてみてください。続くときは、次のフォルダの logs の中身を管理者に送ってください。',
               'en': 'figapp could not start. Try opening it again. If it keeps failing, send the files in the logs folder '
                     'below to the administrator.',
               'vi': 'Không khởi động được figapp. Hãy thử mở lại. Nếu vẫn lỗi, hãy gửi các tệp trong thư mục logs dưới '
                     'đây cho người quản lý.',
               'th': 'ไม่สามารถเริ่ม figapp ได้ ลองเปิดอีกครั้ง หากยังไม่ได้ โปรดส่งไฟล์ในโฟลเดอร์ logs ด้านล่างให้ผู้ดูแล'},
    'running': {'ja': 'figapp はすでに起動しています。タスクバーの figapp の窓を使ってください。',
                'en': 'figapp is already running. Use its window in the taskbar.',
                'vi': 'figapp đang chạy. Hãy dùng cửa sổ figapp trên thanh tác vụ.',
                'th': 'figapp กำลังทำงานอยู่แล้ว โปรดใช้หน้าต่าง figapp บนแถบงาน'},
    'quit': {'ja': 'figapp を終了しますか？保存していない図や設定は消えます。',
             'en': 'Quit figapp? Figures and settings you have not saved will be lost.',
             'vi': 'Thoát figapp? Hình và thiết lập chưa lưu sẽ bị mất.',
             'th': 'ออกจาก figapp หรือไม่? รูปและการตั้งค่าที่ยังไม่ได้บันทึกจะหายไป'},
}
# Mac のメニューと保存の窓（pywebview の表示）。日本語以外は pywebview の英語のまま
MENU_JA = {
    'global.ok': 'OK', 'global.quit': '終了', 'global.cancel': 'キャンセル', 'global.saveFile': 'ファイルを保存',
    'cocoa.menu.about': 'figapp について', 'cocoa.menu.services': 'サービス', 'cocoa.menu.view': '表示',
    'cocoa.menu.edit': '編集', 'cocoa.menu.hide': 'figapp を隠す', 'cocoa.menu.hideOthers': 'ほかを隠す',
    'cocoa.menu.showAll': 'すべてを表示', 'cocoa.menu.quit': 'figapp を終了', 'cocoa.menu.fullscreen': 'フルスクリーンにする',
    'cocoa.menu.cut': 'カット', 'cocoa.menu.copy': 'コピー', 'cocoa.menu.paste': 'ペースト', 'cocoa.menu.selectAll': 'すべてを選択',
    'windows.fileFilter.allFiles': 'すべてのファイル', 'windows.fileFilter.otherFiles': 'その他のファイル',
}


# ---------------------------------------------------------------------------------------------- places
def bundle_dir():
    """The folder with app.py: inside the app when frozen, else the repository."""
    return sys._MEIPASS if FROZEN else os.path.dirname(HERE)


def data_dir():
    d = os.environ.get('FIGAPP_DATA_DIR')
    if not d:
        if sys.platform == 'darwin':
            d = os.path.join(os.path.expanduser('~'), 'Library', 'Application Support', APP)
        elif os.name == 'nt':
            d = os.path.join(os.environ.get('APPDATA') or os.path.expanduser('~'), APP)
        else:
            d = os.path.join(os.environ.get('XDG_DATA_HOME') or os.path.join(os.path.expanduser('~'), '.local', 'share'), APP)
    os.makedirs(os.path.join(d, 'logs'), exist_ok=True)
    return d


def open_log(name):
    """A fresh log for this run; the previous run's log is kept as <name>.1."""
    path = os.path.join(data_dir(), 'logs', name)
    try:
        if os.path.exists(path):
            os.replace(path, path + '.1')
    except OSError:
        pass
    return open(path, 'a', encoding='utf-8', errors='replace', buffering=1)


def log(*a):
    print(time.strftime('%Y-%m-%d %H:%M:%S'), *a, flush=True)


def arg(argv, name, default=None):
    return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else default


# ---------------------------------------------------------------------------------------------- language
def system_language():
    """ja, vi, th or en, from the language of the OS."""
    tag = ''
    try:
        if sys.platform == 'darwin':
            from Foundation import NSLocale
            langs = NSLocale.preferredLanguages()
            tag = str(langs[0]) if langs else ''
        elif os.name == 'nt':
            import ctypes
            primary = ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF
            tag = {0x11: 'ja', 0x2A: 'vi', 0x1E: 'th'}.get(primary, 'en')
        else:
            tag = locale.getlocale()[0] or os.environ.get('LANG', '')
    except Exception:  # noqa: BLE001
        pass
    tag = tag.lower()
    return next((c for c in ('ja', 'vi', 'th') if tag.startswith(c)), 'en')


def start_language():
    """The language chosen last time in the app (prefs.json, written by app.py), else the OS language."""
    try:
        with open(os.path.join(data_dir(), 'prefs.json'), encoding='utf-8') as fh:
            lang = json.load(fh).get('lang')
        if lang in LANGS:
            return lang
    except (OSError, ValueError, AttributeError):
        pass
    return system_language()


# ---------------------------------------------------------------------------------------------- server
_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 127.0.0.1 にはプロキシを通さない


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def is_ready(port):
    try:
        with _NO_PROXY.open(f'http://127.0.0.1:{port}/_stcore/health', timeout=2) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def wait_ready(proc, port, timeout):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc.poll() is not None:
            log('server stopped with code', proc.returncode)
            return False
        if is_ready(port):
            log(f'server ready after {time.time() - t0:.1f} s')
            return True
        time.sleep(0.3)
    log('server did not answer in', timeout, 's')
    return False


def start_server(port, mode):
    cmd = [sys.executable] if FROZEN else [sys.executable, os.path.abspath(__file__)]
    cmd += ['--server', str(port), '--mode', mode, '--parent', str(os.getpid())]
    # PYINSTALLER_RESET_ENVIRONMENT: 自分自身を別のプロセスとして起動するときの PyInstaller の決まり
    env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT='1', FIGAPP_DATA_DIR=data_dir(), PYTHONDONTWRITEBYTECODE='1')
    kw = {'creationflags': 0x08000000} if os.name == 'nt' else {}        # CREATE_NO_WINDOW: 黒い窓を出さない
    log('starting the server:', cmd)
    return subprocess.Popen(cmd, env=env, cwd=bundle_dir(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, **kw)


def stop_server(proc):
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(8)
        except subprocess.TimeoutExpired:
            proc.kill()
    log('server stopped')


def watch_parent(pid):
    """Stop this server when the window process is gone (also when it crashed or was killed)."""
    if not pid:
        return
    if os.name == 'nt':
        import ctypes
        k32 = ctypes.WinDLL('kernel32', use_last_error=True)
        k32.OpenProcess.restype = ctypes.c_void_p
        k32.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
        k32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
        handle = k32.OpenProcess(0x00100000, False, pid)                      # SYNCHRONIZE
        if not handle:
            return

        def wait():
            k32.WaitForSingleObject(handle, 0xFFFFFFFF)
            log('the window process is gone: quitting')
            os._exit(0)
    else:
        def wait():
            while os.getppid() == pid:
                time.sleep(1.5)
            log('the window process is gone: quitting')
            os._exit(0)
    threading.Thread(target=wait, name='figapp-parent', daemon=True).start()


def watch_tabs(grace=None, first=900):
    """Browser mode: stop when no tab has been open for `grace` s (or none opened within `first` s)."""
    grace = grace or int(os.environ.get('FIGAPP_TAB_GRACE', '120'))
    def sessions():
        try:
            from streamlit.runtime import Runtime
            if not Runtime.exists():
                return None
            return Runtime.instance()._session_mgr.num_active_sessions()
        except Exception:  # noqa: BLE001
            return None

    def loop():
        t0 = last = time.time()
        seen = False
        while True:
            time.sleep(5)
            n = sessions()
            if n:
                seen, last = True, time.time()
            elif n == 0 and ((seen and time.time() - last > grace) or (not seen and time.time() - t0 > first)):
                log('no browser tab for a while: quitting')
                os._exit(0)
    threading.Thread(target=loop, name='figapp-tabs', daemon=True).start()


def run_server(argv):
    port = int(arg(argv, '--server'))
    mode = arg(argv, '--mode', 'window')
    out = open_log('server.log')
    sys.stdout = sys.stderr = out                    # 窓のアプリには標準出力が無いので、ログに書く（streamlit を読み込む前に）
    os.environ['FIGAPP_DESKTOP'] = mode
    os.environ.setdefault('FIGAPP_DATA_DIR', data_dir())
    os.environ.setdefault('MPLCONFIGDIR', os.path.join(data_dir(), 'matplotlib'))
    sys.dont_write_bytecode = True                   # アプリの中にファイルを書かない（Mac では署名が壊れる）
    watch_parent(int(arg(argv, '--parent', '0')))
    if mode == 'browser':
        watch_tabs()
    base = bundle_dir()
    os.chdir(base)
    flags = [f'--server.port={port}', '--server.address=127.0.0.1', '--server.headless=true',
             '--global.developmentMode=false', '--browser.gatherUsageStats=false', '--server.fileWatcherType=none',
             '--server.runOnSave=false', '--server.maxUploadSize=200', '--client.toolbarMode=minimal',
             '--theme.base=light', '--theme.primaryColor=#0072B2']
    log('figapp server', sys.version.split()[0], base, flags)
    from streamlit.web import cli
    cli.main(args=['run', os.path.join(base, 'app.py')] + flags, prog_name='streamlit', windows_expand_args=False)


# ---------------------------------------------------------------------------------------------- window
def load_webview():
    """pywebview, or None when this PC cannot show the app in its own window."""
    if sys.platform not in ('darwin', 'win32'):
        return None
    try:
        import webview
        if os.name == 'nt':
            from webview.platforms import winforms
            if not getattr(winforms, 'is_chromium', False):
                log('Microsoft Edge WebView2 is not installed: opening the browser instead')
                return None
        else:
            from webview.platforms import cocoa  # noqa: F401
        return webview
    except Exception as e:  # noqa: BLE001
        log('no app window:', repr(e))
        return None


def window_size(webview):
    try:
        s = webview.screens[0]
        return max(960, min(1600, int(s.width * 0.9))), max(640, min(1000, int(s.height * 0.88)))
    except Exception:  # noqa: BLE001
        return 1400, 900


def page(lang, body):
    return ('<!doctype html><html><head><meta charset="utf-8"><style>'
            'body{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;background:#fff;color:#31333F;'
            'font-family:-apple-system,"Segoe UI","Hiragino Sans","Yu Gothic UI","Noto Sans",sans-serif}'
            '.box{max-width:560px;padding:24px;text-align:center;line-height:1.6}'
            '.spin{width:36px;height:36px;margin:0 auto 18px;border:4px solid #dbe9f4;border-top-color:#0072B2;'
            'border-radius:50%;animation:s 0.9s linear infinite}@keyframes s{to{transform:rotate(360deg)}}'
            'h1{font-size:20px;font-weight:600;margin:0 0 6px}p{margin:6px 0;color:#555;font-size:14px}'
            'code{font-size:12px;word-break:break-all;color:#31333F}</style></head>'
            f'<body><div class="box" lang="{lang}">{body}</div></body></html>')


def loading_page(lang):
    return page(lang, f'<div class="spin"></div><h1>{TEXT["loading"][lang]}</h1><p>{TEXT["first"][lang]}</p>')


def error_page(lang):
    import html
    return page(lang, f'<h1>figapp</h1><p>{TEXT["failed"][lang]}</p><p><code>{html.escape(data_dir())}</code></p>')


def message(text):
    """A message box without the app window (Windows); elsewhere only the log."""
    log(text)
    if os.name == 'nt':
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, APP, 0x40)
        except Exception:  # noqa: BLE001
            pass


_MUTEX = None


def first_instance():
    """Windows starts a second copy on a second double-click; keep one (the Mac does this by itself)."""
    global _MUTEX
    if os.name != 'nt' or os.environ.get('FIGAPP_SELFTEST'):
        return True
    import ctypes
    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    k32.CreateMutexW.restype = ctypes.c_void_p
    _MUTEX = k32.CreateMutexW(None, False, 'Local\\figapp-desktop')
    return ctypes.get_last_error() != 183                                     # ERROR_ALREADY_EXISTS


def run_window(webview, proc, port, url, lang):
    w, h = window_size(webview)
    loc = dict(MENU_JA) if lang == 'ja' else {}
    loc['global.quitConfirmation'] = TEXT['quit'][lang]
    window = webview.create_window(APP, html=loading_page(lang), width=w, height=h, min_size=(960, 640),
                                   confirm_close=not os.environ.get('FIGAPP_SELFTEST'), zoomable=True, text_select=True,
                                   background_color='#FFFFFF')

    def boot():
        if wait_ready(proc, port, 300):
            window.load_url(url)
            log('window shows', url)
            if os.environ.get('FIGAPP_SELFTEST'):
                import selftest
                selftest.run(window, url, log)
        else:
            window.load_html(error_page(lang))

    webview.settings['ALLOW_DOWNLOADS'] = True             # ダウンロード（PDF などの保存）は保存先を聞く窓を出す
    if os.environ.get('FIGAPP_SELFTEST_SAVE'):
        import selftest
        selftest.save_without_dialog(os.environ['FIGAPP_SELFTEST_SAVE'])
    webview.start(boot, gui='edgechromium' if os.name == 'nt' else None, localization=loc, private_mode=True)
    log('window closed')


def run_browser(proc, port, url, lang):
    if wait_ready(proc, port, 300):
        webbrowser.open(url)
        log('browser opened', url)
        proc.wait()                                         # 画面の「アプリを終了」か、タブを閉じてしばらくすると止まる
    else:
        message(TEXT['failed'][lang] + '\n\n' + data_dir())


def run_app(argv):
    if FROZEN or not sys.stdout:
        sys.stdout = sys.stderr = open_log('launcher.log')
    lang = start_language()
    log('figapp desktop', sys.platform, sys.version.split()[0], 'frozen' if FROZEN else 'source', 'lang', lang)
    if not first_instance():
        message(TEXT['running'][lang])
        return
    webview = None if ('--browser' in argv or os.environ.get('FIGAPP_BROWSER') == '1') else load_webview()
    mode = 'window' if webview else 'browser'
    port = free_port()
    proc = start_server(port, mode)
    url = f'http://127.0.0.1:{port}/?lang={lang}'
    if os.environ.get('FIGAPP_SELFTEST_QUERY'):
        url += '&' + os.environ['FIGAPP_SELFTEST_QUERY']
    try:
        with open(os.path.join(data_dir(), 'running.json'), 'w', encoding='utf-8') as fh:
            json.dump({'pid': os.getpid(), 'server_pid': proc.pid, 'port': port, 'mode': mode, 'url': url}, fh)
    except OSError:
        pass
    try:
        if webview:
            run_window(webview, proc, port, url, lang)
        else:
            run_browser(proc, port, url, lang)
    finally:
        stop_server(proc)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if '--server' in argv:
        run_server(argv)
    else:
        run_app(argv)


if __name__ == '__main__':
    main()
