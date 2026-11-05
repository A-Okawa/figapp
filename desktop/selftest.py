# -*- coding: utf-8 -*-
"""Checks of the app window on the build machines (GitHub Actions). Used only when FIGAPP_SELFTEST is set.

In the real window (WKWebView on the Mac, Edge WebView2 on Windows) this waits for the app, then uploads a CSV through the
page's file input, clicks the PDF, SVG and settings (.json) download buttons and records what happened in
<FIGAPP_SELFTEST>/selftest.json. The build machine takes screenshots in between (marker files *.ready).

FIGAPP_SELFTEST_SAVE=<folder>: downloads go straight into that folder (no save dialog), so the saved files can be checked.
Without it the normal save dialog opens; the build machine photographs it, this presses its Save button, checks that the
PDF arrived in the Downloads folder, and the build machine then ends the app.
"""
import json
import os
import sys
import threading
import time

CSV = 'x,y\\n1,2\\n2,4\\n3,9\\n'
RENAMED = 'selftest-renamed'                               # 保存の窓で打つ名前（拡張子なし）

UPLOAD_JS = """(function () {
  var inputs = document.querySelectorAll('[data-testid="stSidebar"] input[type="file"]');
  if (!inputs.length) return 'no file input';
  var dt = new DataTransfer();
  dt.items.add(new File(['%s'], 'selftest.csv', {type: 'text/csv'}));
  inputs[0].files = dt.files;
  inputs[0].dispatchEvent(new Event('input', {bubbles: true}));
  inputs[0].dispatchEvent(new Event('change', {bubbles: true}));
  return 'ok';
})()""" % CSV

CLICK_JS = """(function (label) {
  var b = Array.prototype.slice.call(document.querySelectorAll('[data-testid="stDownloadButton"] button'))
    .filter(function (e) { return e.innerText.trim() === label; })[0];
  if (!b) return false;
  b.scrollIntoView({block: 'center'});
  b.click();
  return true;
})(%s)"""


def _poll(fn, timeout, step=0.5):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            v = fn()
        except Exception:  # noqa: BLE001
            v = None
        if v:
            return v
        time.sleep(step)
    return None


def _saved(folder, name):
    """Size and first bytes of a downloaded file once it stops growing."""
    path = os.path.join(folder, name)
    if not os.path.exists(path):
        return None
    size = os.path.getsize(path)
    time.sleep(0.6)
    if size == 0 or size != os.path.getsize(path):
        return None
    with open(path, 'rb') as fh:
        return {'bytes': size, 'head': fh.read(16).decode('latin-1')}


def run(window, url, log):
    out = os.environ['FIGAPP_SELFTEST']
    os.makedirs(out, exist_ok=True)
    save = os.environ.get('FIGAPP_SELFTEST_SAVE')
    res = {'url': url, 'platform': sys.platform, 'save_dir': save, 'errors': []}

    def js(code):
        try:
            return window.evaluate_js(code)
        except Exception as e:  # noqa: BLE001
            res['errors'].append(repr(e)[:300])
            return None

    def mark(name):
        with open(os.path.join(out, name + '.ready'), 'w') as fh:
            fh.write(str(time.time()))

    def dump():
        with open(os.path.join(out, 'selftest.json'), 'w', encoding='utf-8') as fh:
            json.dump(res, fh, ensure_ascii=False, indent=1)

    res['sidebar'] = bool(_poll(lambda: js('!!document.querySelector(\'[data-testid="stSidebar"]\')'), 180))
    res['figure'] = bool(_poll(lambda: js('document.querySelectorAll(\'[data-testid="stImage"] img\').length'), 180))
    time.sleep(2)
    res['user_agent'] = js('navigator.userAgent')
    res['title'] = js('document.title')
    res['exceptions'] = js('document.querySelectorAll(\'[data-testid="stException"]\').length')
    res['sidebar_text'] = (js('document.querySelector(\'[data-testid="stSidebar"]\').innerText') or '')[:400]
    dump()
    mark('loaded')
    log('selftest: loaded', res['sidebar'], res['figure'])
    time.sleep(6)                                          # 写真を撮る間

    res['upload'] = js(UPLOAD_JS)
    res['uploaded'] = bool(_poll(lambda: js("document.body.innerText.indexOf('selftest.csv') >= 0"), 60))
    time.sleep(3)
    dump()
    if not save:                                           # 本物の保存の窓：写真を撮ってもらい、名前を変えて「保存」を押す
        target = os.path.join(downloads_folder(), RENAMED + '.pdf')   # 拡張子なしの名前を打っても .pdf になる
        res['download_target'] = target
        for old in (target, os.path.join(downloads_folder(), 'figure.pdf'), os.path.join(downloads_folder(), RENAMED)):
            if os.path.exists(old):
                os.remove(old)
        res['clicked_pdf'] = js(CLICK_JS % "'PDF'")
        dump()
        mark('dialog')
        log('selftest: save dialog requested')
        time.sleep(9)
        res['dialog'] = press_save(RENAMED)
        res['saved_by_dialog'] = _poll(lambda: _saved(os.path.dirname(target), RENAMED + '.pdf'), 40)
        res['files_in_downloads'] = sorted(os.listdir(os.path.dirname(target)))[:30]
        dump()
        mark('dialog_done')
        log('selftest: dialog', res['dialog'], res['saved_by_dialog'])
        return
    res['downloads'] = {}
    for label, name in (('PDF', 'figure.pdf'), ('SVG', 'figure.svg'), ('PNG', 'figure.png'),
                        ('この図の設定（.json）', 'figure.json'), ('Settings of this figure (.json)', 'figure.json')):
        if res['downloads'].get(name):
            continue
        clicked = js(CLICK_JS % json.dumps(label))
        if not clicked:
            continue
        res['downloads'][name] = _poll(lambda: _saved(save, name), 60)
        dump()
    res['files_in_save_dir'] = sorted(os.listdir(save))
    res['exceptions_end'] = js('document.querySelectorAll(\'[data-testid="stException"]\').length')
    dump()
    mark('done')
    log('selftest: done', res['downloads'])
    time.sleep(4)
    if os.environ.get('FIGAPP_SELFTEST_CLOSE') == '1':
        window.destroy()


def downloads_folder():
    """The folder the save dialog starts in (the user's Downloads)."""
    if os.name == 'nt':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r'Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders') as key:
                return winreg.QueryValueEx(key, '{374DE290-123F-4565-9164-39C4925E467B}')[0]
        except OSError:
            pass
    return os.path.join(os.path.expanduser('~'), 'Downloads')


def press_save(new_name):
    """Type `new_name` (without an extension) into the open save dialog of this app and press Save, as a user would."""
    out = {'pressed': False}
    if sys.platform == 'darwin':
        import AppKit
        from PyObjCTools import AppHelper
        done = threading.Event()

        def press():
            try:
                w = AppKit.NSApp.modalWindow() or AppKit.NSApp.keyWindow()
                out['window'] = str(w.className()) if w is not None else None
                if w is not None and w.isKindOfClass_(AppKit.NSSavePanel):
                    out['name'] = str(w.nameFieldStringValue())
                    out['extension_hidden'] = bool(w.isExtensionHidden())
                    out['types'] = [str(t.identifier()) for t in (w.allowedContentTypes() or [])]
                    w.setNameFieldStringValue_(new_name)
                    w.ok_(None)
                    out['pressed'] = True
            except Exception as e:  # noqa: BLE001
                out['error'] = repr(e)
            done.set()

        AppHelper.callAfter(press)
        done.wait(20)
    elif os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        found = []
        proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def each(hwnd, _):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == os.getpid() and user32.IsWindowVisible(hwnd):
                cls = ctypes.create_unicode_buffer(64)
                user32.GetClassNameW(hwnd, cls, 64)
                if cls.value == '#32770':                  # the save dialog
                    found.append(hwnd)
            return True

        user32.EnumWindows(proc(each), 0)
        if found:
            title = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(found[0], title, 256)
            out['window'] = title.value
            boxes = []

            def child(hwnd, _):                            # the file name box: the Edit inside a ComboBox
                cls = ctypes.create_unicode_buffer(64)
                user32.GetClassNameW(hwnd, cls, 64)
                if cls.value == 'Edit':
                    up = user32.GetParent(hwnd)
                    c1 = ctypes.create_unicode_buffer(64)
                    if up:
                        user32.GetClassNameW(up, c1, 64)
                    out.setdefault('edits', []).append(c1.value)
                    if c1.value == 'ComboBox':
                        boxes.append(hwnd)
                return True

            user32.EnumChildWindows(found[0], proc(child), 0)
            if boxes:
                text = ctypes.create_unicode_buffer(260)
                user32.GetWindowTextW(boxes[0], text, 260)
                out['name'] = text.value
                user32.SendMessageW(boxes[0], 0x000C, 0, ctypes.c_wchar_p(new_name))   # WM_SETTEXT
            user32.PostMessageW(found[0], 0x0111, 1, 0)    # WM_COMMAND IDOK: the Save button
            out['pressed'] = True
    return out


def save_without_dialog(folder):
    """Downloads of the window go into `folder` without the save dialog (only for the checks)."""
    os.makedirs(folder, exist_ok=True)
    if sys.platform == 'darwin':
        import Foundation
        from webview.platforms import cocoa

        class FigappSelftestDownloadDelegate(cocoa.BrowserView.DownloadDelegate):
            def download_decideDestinationUsingResponse_suggestedFilename_completionHandler_(
                    self, download, response, suggested, handler):
                handler(Foundation.NSURL.fileURLWithPath_(os.path.join(folder, str(suggested))))

        cocoa.BrowserView.DownloadDelegate = FigappSelftestDownloadDelegate
    elif os.name == 'nt':
        from webview.platforms import edgechromium

        def on_download_starting(self, sender, args):
            args.ResultFilePath = os.path.join(folder, os.path.basename(str(args.ResultFilePath)))
            args.Handled = True

        edgechromium.EdgeChrome.on_download_starting = on_download_starting
