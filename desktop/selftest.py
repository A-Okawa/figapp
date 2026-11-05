# -*- coding: utf-8 -*-
"""Checks of the app window on the build machines (GitHub Actions). Used only when FIGAPP_SELFTEST is set.

In the real window (WKWebView on the Mac, Edge WebView2 on Windows) this waits for the app, then uploads a CSV through the
page's file input, clicks the PDF, SVG and settings (.json) download buttons and records what happened in
<FIGAPP_SELFTEST>/selftest.json. The build machine takes screenshots in between (marker files *.ready).

FIGAPP_SELFTEST_SAVE=<folder>: downloads go straight into that folder (no save dialog), so the saved files can be checked.
Without it the normal save dialog opens; the build machine photographs it and then ends the app.
"""
import json
import os
import sys
import time

CSV = 'x,y\\n1,2\\n2,4\\n3,9\\n'

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
    if not save:                                           # 保存の窓を出して写真を撮ってもらう（窓は閉じない）
        res['clicked_pdf'] = js(CLICK_JS % "'PDF'")
        dump()
        mark('dialog')
        log('selftest: save dialog requested')
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
