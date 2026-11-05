# -*- coding: utf-8 -*-
"""Check the built PC version's server in a real browser (Chromium through Playwright).

Every preset opens without an error and draws; PDF, SVG, PNG, TIFF, settings (.json) and the work file (.zip) download
with the right contents; a CSV uploads; the colour list is saved to the user's folder (not into the app); the chosen
screen language is remembered; "Quit the app" is offered only in the browser mode and stops the server.

usage: python smoke_server.py BASE_URL OUT_DIR DATA_DIR MODE(window|browser)
"""
import json
import os
import re
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

BASE, OUT, DATA, MODE = sys.argv[1].rstrip('/'), sys.argv[2], sys.argv[3], sys.argv[4]
os.makedirs(OUT, exist_ok=True)
EXC = '[data-testid="stException"]'
PRESETS = ['TwoFiles', 'XRD', 'Bar', 'Scatter', 'TwinAxis', 'Closure', 'Sieve']
MAGIC = {'pdf': b'%PDF', 'png': b'\x89PNG', 'tif': (b'II*\x00', b'MM\x00*'), 'zip': b'PK\x03\x04'}
fails = []
report = []


REPORT_FILE = os.path.join(OUT, f'smoke_server_{MODE}.txt')
open(REPORT_FILE, 'w', encoding='utf-8').close()


def ok(cond, what):
    line = ('OK   ' if cond else 'FAIL ') + what
    print(line, flush=True)
    report.append(line)
    with open(REPORT_FILE, 'a', encoding='utf-8') as fh:            # 途中で止まっても、そこまでの結果が残る
        fh.write(line + '\n')
    if not cond:
        fails.append(what)


def settle(pg, ms=2000):
    pg.wait_for_timeout(ms)
    for _ in range(120):
        if pg.locator('[data-testid="stStatusWidget"]').count() == 0:
            break
        pg.wait_for_timeout(500)
    pg.wait_for_timeout(500)


def healthy():
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(BASE + '/_stcore/health', timeout=3) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def download(pg, label, ext):
    btn = pg.locator('[data-testid="stDownloadButton"] button').filter(has_text=re.compile('^' + re.escape(label) + '$'))
    if btn.count() == 0:
        btn = pg.locator('[data-testid="stDownloadButton"] button').filter(has_text=label)
    with pg.expect_download(timeout=120000) as dl:
        btn.first.click()
    path = os.path.join(OUT, 'dl_' + dl.value.suggested_filename)
    dl.value.save_as(path)
    data = open(path, 'rb').read()
    magic = MAGIC.get(ext)
    good = (data.startswith(magic) if isinstance(magic, bytes) else any(data.startswith(m) for m in magic)) if magic else True
    if ext == 'svg':
        good = b'<svg' in data[:2000]
    if ext == 'json':
        good = bool(json.loads(data.decode('utf-8')).get('layers'))
    ok(good and len(data) > 100, f'download {label}: {dl.value.suggested_filename}, {len(data)} bytes')


def checks(p):
    browser = p.chromium.launch()
    pg = browser.new_page(viewport={'width': 1500, 'height': 1000}, accept_downloads=True)

    for preset in PRESETS:
        t0 = time.time()
        pg.goto(f'{BASE}/?demo={preset}&lang=en', wait_until='domcontentloaded', timeout=120000)
        pg.locator('[data-testid="stSidebar"]').first.wait_for(timeout=120000)
        settle(pg, 2500)
        n_exc = pg.locator(EXC).count()
        n_img = pg.locator('[data-testid="stImage"] img').count()
        ok(n_exc == 0 and n_img >= 1, f'{preset}: drawn in {time.time() - t0:.1f} s (images {n_img}, errors {n_exc})')
        if n_exc:
            print(pg.locator(EXC).first.inner_text()[:1500])

    pg.goto(f'{BASE}/?demo=Bar&lang=en', wait_until='domcontentloaded', timeout=120000)
    pg.locator('[data-testid="stSidebar"]').first.wait_for(timeout=120000)
    settle(pg, 3000)
    side = pg.locator('[data-testid="stSidebar"]').inner_text()
    ok('(desktop)' in side, 'sidebar says this is the desktop version')
    ok('Your data stay on this PC' in side, 'sidebar says the data stay on this PC')
    ok(('Quit the app' in side) == (MODE == 'browser'), f'"Quit the app" shown only in the browser mode ({MODE})')
    ok('Password' not in pg.locator('body').inner_text(), 'no password screen')
    pg.screenshot(path=os.path.join(OUT, f'server_{MODE}_bar.png'))

    for label, ext in (('PDF', 'pdf'), ('SVG', 'svg'), ('PNG', 'png'), ('TIFF', 'tif'),
                       ('Settings of this figure (.json)', 'json'), ('Save work file', 'zip')):
        download(pg, label, ext)

    csv = os.path.join(OUT, 'upload_test.csv')
    with open(csv, 'w', encoding='utf-8') as fh:
        fh.write('x,y\n1,2\n2,4\n3,9\n')
    pg.locator('[data-testid="stSidebar"] input[type="file"]').first.set_input_files(csv)
    settle(pg, 3000)
    ok('upload_test.csv' in pg.locator('body').inner_text(), 'CSV uploaded')
    ok(pg.locator(EXC).count() == 0, 'no error after the upload')

    # colour list → the user's folder
    pg.get_by_role('tab', name='Series & colours').click()
    settle(pg, 1500)
    pg.get_by_text('Colour list (colours chosen by name)').first.click()
    settle(pg, 1000)
    exp = pg.locator('[data-testid="stExpander"]').filter(has_text='Colour list (colours chosen by name)').first
    path_note = exp.inner_text()
    ok(os.path.normcase(os.path.join(DATA, 'colors.csv')) in os.path.normcase(path_note.replace('`', '')),
       'colour list shows its place in the user folder')
    ta = exp.locator('textarea').first
    ta.fill(ta.input_value() + '\nselftest,#123456')
    exp.get_by_role('button', name='Save').click()
    settle(pg, 2000)
    saved = open(os.path.join(DATA, 'colors.csv'), encoding='utf-8').read() if os.path.exists(os.path.join(DATA, 'colors.csv')) else ''
    ok('selftest,#123456' in saved, 'colour list saved to the user folder')

    # language is remembered
    pg.get_by_text('日本語', exact=True).first.click()
    settle(pg, 2500)
    prefs = json.load(open(os.path.join(DATA, 'prefs.json'), encoding='utf-8')) if os.path.exists(os.path.join(DATA, 'prefs.json')) else {}
    ok(prefs.get('lang') == 'ja', f'language remembered: {prefs}')
    ok('（PC 版）' in pg.locator('[data-testid="stSidebar"]').inner_text(), 'Japanese screen says PC 版')
    pg.screenshot(path=os.path.join(OUT, f'server_{MODE}_ja.png'))
    pg.get_by_text('English', exact=True).first.click()
    settle(pg, 2000)

    if MODE == 'browser':
        pg.get_by_role('button', name='Quit the app').click()
        pg.wait_for_timeout(1500)
        ok('figapp has stopped' in pg.locator('[data-testid="stSidebar"]').inner_text(), 'quit message shown')
        for _ in range(20):
            if not healthy():
                break
            time.sleep(0.5)
        ok(not healthy(), 'server stopped after "Quit the app"')
    browser.close()


with sync_playwright() as p:
    try:
        checks(p)
    except Exception as e:  # noqa: BLE001
        ok(False, f'stopped by an error: {e!r}'[:600])

with open(REPORT_FILE, 'w', encoding='utf-8') as fh:
    fh.write('\n'.join(report) + '\n\nFAILED: ' + (', '.join(fails) if fails else 'none') + '\n')
print('\nFAILED:', fails if fails else 'none')
sys.exit(1 if fails else 0)
