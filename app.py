# -*- coding: utf-8 -*-
"""論文用グラフ作成アプリ（Streamlit）

CSV をアップロード → レイヤー（描き方と列）を選ぶ → 軸・色・文字を整える → PDF／SVG／PNG／TIFF と描いた値を保存。
設定は JSON で保存・読み込みでき、同じ見た目を別のデータに使い回せる。描画は figcore.py（コマンドラインでも同じ結果）。
起動：streamlit run app.py
"""
import copy, hmac, html, inspect, json, os, re, sys, threading, unicodedata
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:          # figcore.py と demo_data.py は app.py と同じフォルダ
    sys.path.insert(0, HERE)
import figcore as fc  # noqa: E402
import demo_data  # noqa: E402
import i18n  # noqa: E402
from i18n import ui  # noqa: E402

PRESET_DIR = os.path.join(HERE, 'presets')
# matplotlib の rcParams と pyplot は全員で共有なので、描画は 1 つずつ。app.py は実行のたびに読み直されるので、
# 錠は一度しか読み込まれない figcore に置く（ここで作ると実行ごとに別の錠になり、同時に描く人どうしを止められない）
LOCK = fc.RENDER_LOCK

# 画面の言語（コード ja・en・vi・th）は st.session_state['lang']。URL の ?lang=en などで最初の言語を選べる
i18n.use_streamlit_session('lang')
if 'lang' not in st.session_state:
    _ql = str(st.query_params.get('lang', '')).lower()
    st.session_state['lang'] = _ql if _ql in i18n.CODES else 'ja'

# 共有のサーバー（Streamlit Community Cloud など）で動いているか。そこでは、アプリのフォルダに書く操作（色の一覧の保存）を
# その人の画面だけのものにする。Community Cloud はリポジトリを /mount/src/ に置く。ほかのサーバーでは FIGAPP_SHARED=1 にする
SHARED = HERE.replace('\\', '/').startswith('/mount/src/') or os.environ.get('FIGAPP_SHARED') == '1'

TYPE_LABEL = {'mean_error': ui('平均 ± 誤差', 'Mean ± error'), 'bar': ui('棒グラフ', 'Bar chart'), 'line': ui('線（スペクトル・曲線）', 'Line (spectra, curves)'), 'scatter': ui('散布図', 'Scatter plot'), 'ticks': ui('ティック（ICDD など）', 'Ticks (ICDD etc.)')}
ERR_LABEL = {'sd': 'SD', 'se': 'SE', 'ci95': ui('95% 信頼区間', '95% confidence interval'), 'minmax': ui('最小〜最大', 'Min–max'), 'column': ui('列の値を使う', 'Values from a column'), 'none': ui('なし', 'None')}
NORM_LABEL = {'none': ui('しない', 'None'), 'max': ui('最大値を 1 に', 'Maximum to 1'), 'minmax': ui('0〜1 に', 'Scale to 0–1'), 'sub_x0': ui('x0 での値を引く', 'Subtract the value at x0'), 'div_x0': ui('x0 での値で割る', 'Divide by the value at x0')}
TICK_LABEL = {'auto': ui('自動', 'Auto'), 'step': ui('間隔を指定', 'Set the interval'), 'list': ui('値を列挙', 'List the values')}
MINOR_LABEL = {'auto': ui('自動', 'Auto'), 'n': ui('本数を指定', 'Set the number'), 'off': ui('なし', 'None')}
FMT_LABEL = {'auto': ui('自動', 'Auto'), 'g': ui('簡潔（9.8, 100）', 'Short (9.8, 100)'), '0': ui('小数 0 桁', '0 decimals'), '1': ui('小数 1 桁', '1 decimal'), '2': ui('小数 2 桁', '2 decimals'), '3': ui('小数 3 桁', '3 decimals'), 'sci': ui('10 のべき', 'Powers of 10')}
LOC_LABEL = {'best': ui('自動', 'Auto'), 'upper left': ui('左上', 'Upper left'), 'upper right': ui('右上', 'Upper right'), 'lower left': ui('左下', 'Lower left'), 'lower right': ui('右下', 'Lower right'),
             'center left': ui('左中央', 'Centre left'), 'center right': ui('右中央', 'Centre right'), 'upper center': ui('上中央', 'Upper centre'), 'lower center': ui('下中央', 'Lower centre'),
             'outside right': ui('枠の外（右）', 'Outside (right)'), 'outside top': ui('枠の外（上）', 'Outside (top)')}
LS_LABEL = {'-': ui('実線', 'Solid'), '--': ui('破線', 'Dashed'), ':': ui('点線', 'Dotted'), '-.': ui('一点鎖線', 'Dash-dot'), 'none': ui('なし', 'None')}
MARKER_LABEL = {'o': ui('● 丸', '● Circle'), 's': ui('■ 四角', '■ Square'), '^': ui('▲ 三角', '▲ Triangle'), 'D': ui('◆ ひし形', '◆ Diamond'), 'v': ui('▼ 逆三角', '▼ Inverted triangle'), 'p': ui('五角形', 'Pentagon'), 'h': ui('六角形', 'Hexagon'),
                '<': '◀', '>': '▶', '*': '★', 'x': '×', '+': ui('＋', '+'), 'none': ui('なし', 'None')}
N_SHOW_LABEL = {'none': ui('書かない', 'Off'), 'above': ui('各点の上', 'Above each point'), 'below': ui('各点の下', 'Below each point'), 'legend': ui('凡例に（範囲）', 'In the legend (range)')}
N_STYLE_LABEL = {'eq': 'n = 5', 'paren': '(5)', 'num': '5'}
NOTE_LOC_LABEL = {'lower right': ui('右下', 'Lower right'), 'lower left': ui('左下', 'Lower left'), 'upper right': ui('右上', 'Upper right'), 'upper left': ui('左上', 'Upper left')}
PRESETS = {
    ui('Fig. 1(a)：亀裂長さと荷重（平均 ± SD、圧痕でまとめる）', 'Fig. 1(a): crack length vs load (mean ± SD, grouped by indentation)'): ('Fig1a.json', []),
    ui('2 つの CSV を色分け（5 vol% と 10 vol%、平均 ± SD と n）', 'Two CSVs in two colours (5 vol% and 10 vol%, mean ± SD and n)'): ('TwoFiles.json', ['demo_5vol.csv', 'demo_10vol.csv']),
    ui('スペクトル（XRD・Raman、ずらして重ねる＋ICDD ティック）', 'Spectra (XRD, Raman: offset and stacked, with ICDD ticks)'): ('XRD.json', ['demo_spectra.csv', 'demo_ticks.csv']),
    ui('棒グラフ（領域ごとの平均 ± SE と個々の点）', 'Bar chart (mean ± SE per region with individual points)'): ('Bar.json', ['demo_bars.csv']),
    ui('散布図（系列ごとの色）', 'Scatter plot (one colour per series)'): ('Scatter.json', ['demo_scatter.csv']),
    ui('第 2 縦軸（左 Hv・右 K）', 'Second y axis (Hv on the left, K on the right)'): ('TwinAxis.json', ['demo_twin.csv']),
    ui('治癒率 H と焼鈍温度（亀裂ごとの CSV から計算列で h）', 'Healing ratio H vs annealing temperature (h from a calculated column, one CSV row per crack)'): ('HealingRatio.json', []),
    ui('き裂の閉じた量と初期き裂長さ（荷重ごとに x・y の平均 ± SD）', 'Crack closure vs initial crack length (mean ± SD of x and y for each load)'): ('Closure.json', ['demo_closure.csv']),
    ui('粒度の内訳（ふるい分けの質量%、100% 積み上げ棒）', 'Size fractions (sieved mass %, 100% stacked bars)'): ('Sieve.json', ['demo_sieve.csv']),
}
TEMPLATES = {ui('Fig. 1(a) の入力テンプレート（fig1a.csv）', 'Input template for Fig. 1(a) (fig1a.csv)'): 'fig1a_template.csv',
             ui('き裂の閉じた量の入力テンプレート（closure.csv）', 'Input template for crack closure (closure.csv)'): 'closure_template.csv',
             ui('粒度の内訳の入力テンプレート（sieve.csv）', 'Input template for size fractions (sieve.csv)'): 'sieve_template.csv'}
PTS_LABEL = {'unit': ui('単位ごとの平均', 'Mean of each unit'), 'row': ui('1 行ずつ', 'Each row')}
STACK_LABEL = {'none': ui('横に並べる', 'Side by side'), 'stack': ui('積み上げ', 'Stacked'), 'percent': ui('100% 積み上げ', '100% stacked')}
FIT_LABEL = {'none': ui('なし', 'None'), 'linear': ui('直線 y = a + bx', 'Straight line y = a + bx'), 'origin': ui('原点を通る直線 y = bx', 'Line through the origin y = bx'), 'poly2': ui('2 次式 y = a + bx + cx²', 'Quadratic y = a + bx + cx²'),
             'power': ui('べき乗 y = a·x^b', 'Power y = a·x^b'), 'exp': ui('指数 y = a·e^(bx)', 'Exponential y = a·e^(bx)'), 'log': ui('対数 y = a + b·ln x', 'Logarithmic y = a + b·ln x')}
FIT_ON_LABEL = {'points': ui('個々の値（すべての点）', 'Individual values (all points)'), 'means': ui('平均の点', 'Mean points')}

st.set_page_config(page_title='Figure app', page_icon='📈', layout='wide')
# 古い Streamlit（1.32 など）でも動くように：ボタンの幅の指定と popover の有無を吸収する
WIDE = {'width': 'stretch'} if 'width' in inspect.signature(st.button).parameters else {'use_container_width': True}
popover = getattr(st, 'popover', None) or st.expander
OLD_ST = tuple(int(x) for x in re.findall(r'\d+', st.__version__)[:2]) < (1, 40)   # 古い版は画像を列の幅に収めない
ss = st.session_state


def secret(name, default=''):
    """A value from Streamlit's secrets (the app's Secrets on Streamlit Cloud, or .streamlit/secrets.toml); '' when none."""
    try:
        if not st.secrets.load_if_toml_exists():
            return default
        v = st.secrets.get(name, default)
    except Exception:  # noqa: BLE001
        return default
    return default if v is None else str(v)


SHARED = SHARED or secret('shared').strip().lower() in ('1', 'true', 'yes')
# 画面の言語（いちばん上）。図に入る文字は入力したままで、画面の表示だけが替わる
st.sidebar.radio('🌐 Language / 言語', i18n.CODES, format_func=i18n.NAMES.get, key='lang', horizontal=True)
# パスワード（Streamlit Cloud の Secrets に password = "…" を書いたときだけ。手元で動かすときは聞かない）
PASSWORD = secret('password')
if PASSWORD and not ss.get('authenticated'):
    st.title(ui('📈 グラフ作成', '📈 Graph maker'))
    with st.form('login'):
        _pw = st.text_input(ui('パスワード', 'Password'), type='password')
        _go = st.form_submit_button(ui('入る', 'Enter'))
    if _go:
        if hmac.compare_digest(_pw.encode('utf-8'), PASSWORD.encode('utf-8')):
            ss.authenticated = True
            st.rerun()
        st.error(ui('パスワードが違います。', 'Wrong password.'))
    st.stop()
if 'cfg' not in ss:
    ss.cfg = fc.default_settings()
if 'demo_files' not in ss:
    ss.demo_files = {}
if 'saved_files' not in ss:       # 作業ファイル（.zip）から開いた CSV
    ss.saved_files = {}
# ?demo=XRD などで、ひな形とデモデータを読み込んだ状態で開く（共有・確認用）
_qp = st.query_params.get('demo')
if _qp and not ss.get('_demo_done'):
    for _label, (_js, _demos) in PRESETS.items():
        if os.path.splitext(_js)[0].lower() == str(_qp).lower():
            ss.cfg = fc.complete(json.load(open(os.path.join(PRESET_DIR, _js), encoding='utf-8')))
            ss.demo_files = {n: demo_data.DEMOS[n]() for n in _demos}
    ss._demo_done = True
cfg = ss.cfg
fc.assign_uids(cfg)


# ---------------------------------------------------------------------------------------------- helpers
def clear_widgets(prefix='w|'):
    for k in list(ss.keys()):
        if str(k).startswith(prefix):
            del ss[k]


# 値は毎回セッションに入れ直す。Streamlit 1.32 などの古い版は、選択肢などが変わったウィジェットを作り直すときに
# 値を初期値（選択肢の先頭）に戻してしまう（例：CSV を足す・外すと、レイヤーの「データ（CSV）」が 1 つ目の CSV に戻る）。
def W(fn, label, key, cur, **kw):
    """Widget bound to a settings value: first run takes the value from the settings, later runs from the widget."""
    k = 'w|' + key
    ss[k] = ss[k] if k in ss else cur
    return fn(label, key=k, **kw)


def S(label, key, options, cur, fmt=None, where=None, **kw):
    """Selectbox that keeps its value when the options change; a value no longer among them falls back to `cur`,
    or to the first option."""
    k = 'w|' + key
    v = ss[k] if k in ss else cur
    if v not in options:
        v = cur if cur in options else options[0]
    ss[k] = v
    return (where or st).selectbox(label, options, key=k, format_func=fmt or (lambda v: str(v)), **kw)


def MS(label, key, options, cur, where=None, **kw):
    k = 'w|' + key
    if k not in ss:
        ss[k] = list(cur or [])
    ss[k] = [v for v in ss[k] if v in options]
    return (where or st).multiselect(label, options, key=k, **kw)


def numtext(label, key, cur, where=None, **kw):
    return W((where or st).text_input, label, key, '' if cur is None else str(cur), **kw)


SER_PLACES = ('SR', 'LS')        # 「系列・色」タブと、レイヤーの中（系列の列がないレイヤー）の 2 か所に同じ設定が出る


def _ser_sync(sk, field, key):
    v = ss['w|' + key]
    ss.cfg['series'].setdefault(sk, {})[field] = v
    for p in SER_PLACES:
        k2 = f'w|{p}|{sk}|{field}'
        if k2 != 'w|' + key:
            ss[k2] = v


def SW(fn, label, sk, field, place, options=None, **kw):
    """Widget for one field of a series style (label, color, marker, ls, filled). The copies in the layer tab and the
    series tab stay in step: changing one updates the other and the settings."""
    key = f'{place}|{sk}|{field}'
    k = 'w|' + key
    cur = cfg['series'][sk].get(field, fc.default_label(cfg, sk) if field == 'label' else fc.SERIES_FIELDS[field])
    if k not in ss:
        ss[k] = cur
    if options is not None:
        if ss[k] not in options:
            ss[k] = cur if cur in options else options[0]
        val = fn(label, options, key=k, on_change=_ser_sync, args=(sk, field, key), **kw)
    else:
        val = fn(label, key=k, on_change=_ser_sync, args=(sk, field, key), **kw)
    cfg['series'][sk][field] = val
    return val


def _palette_changed():
    """Choosing another palette (or reversing it) recolours every series at once (done after the keys are known)."""
    ss.cfg['palette'] = ss.get('w|pal', ss.cfg.get('palette', 'paper'))
    ss.cfg['palette_reverse'] = bool(ss.get('w|palrev', False))
    ss._apply_palette = True


def _label_typed(a):
    ss.cfg[a]['auto_label'] = False          # 書き換えたラベルは、列を替えてもそのまま


def _label_auto(a):
    ss.cfg[a]['auto_label'] = True
    ss.pop(f'w|A|{a}|lab', None)


# 説明の文は Markdown として表示されるので、記号の例は ` で囲む（_ が斜体、$ が数式にならないように）
SUBSUP_HELP = (ui('文字を選んで Ctrl+I で斜体、Ctrl+B で太字、Ctrl+,（カンマ）で下付き、Ctrl+.（ピリオド）で上付き（Mac は ⌘ でも可）。入力欄では `<i>l</i>`、'
                  '`_{i}`、`^{2}` のような記号で出て、Enter を押すと図に反映されます。直接書いても同じ（例：`<i>K</i>_{IC} (MPa m^{1/2})`、`Yb_{2}Si_{2}O_{7}`、'
                  '`cm^-1`）。`$...$` の中は数式。', 'Select text and press Ctrl+I for italic, Ctrl+B for bold, Ctrl+, (comma) for subscript, Ctrl+. '
                  '(period) for superscript (⌘ also works on a Mac). The box shows codes such as `<i>l</i>`, `_{i}`, '
                  '`^{2}`; press Enter to update the figure. Typing the codes works too (e.g. `<i>K</i>_{IC} (MPa '
                  'm^{1/2})`, `Yb_{2}Si_{2}O_{7}`, `cm^-1`). Text inside `$...$` is a formula.'))


COLORS_FILE = os.path.join(HERE, 'colors.csv')
OTHER = ui('その他（ピッカーで選ぶ）', 'Other (use the picker)')


def named_colors():
    """The colours that can be chosen by name: colors.csv in the app folder, or on a shared server the list this
    user set for their own screen ("この画面で使う")."""
    if SHARED and ss.get('colors_session'):
        return dict(ss['colors_session'])
    return fc.load_colors(COLORS_FILE)


def colors_csv(cols):
    return ('名前,色\n' + ''.join(f'{n},{h}\n' for n, h in cols.items())).encode('utf-8')


def _cname_changed(sk, key):
    name = ss.get(key)
    hexcol = named_colors().get(name)
    if hexcol:
        ss.cfg['series'].setdefault(sk, {}).update(color=hexcol, cname=name)
        for p in SER_PLACES:
            ss[f'w|{p}|{sk}|color'] = hexcol


def color_menu(where, sk, place, **kw):
    """A box of named colours (colors.csv) next to the colour picker; shows the name of the current colour."""
    cols = named_colors()
    key = f'w|{place}|{sk}|cname'
    cur = str(cfg['series'][sk].get('color', '')).upper()
    nm = cfg['series'][sk].get('cname')
    ss[key] = nm if cols.get(nm, '').upper() == cur else next((n for n, h in cols.items() if h.upper() == cur), OTHER)
    return where.selectbox(ui('色の名前', 'Colour name'), [OTHER] + list(cols), key=key, on_change=_cname_changed, args=(sk, key),
                           format_func=lambda n: n if n == OTHER else f'{fc.color_emoji(cols[n])} {i18n.color_name(n)}', **kw)


def contrast_note(sk):
    """A short note under the colour row when the colour is too light to see as a thin line or a small marker on white."""
    r = fc.contrast_on_white(cfg['series'][sk].get('color', ''))
    if r < fc.MIN_CONTRAST:
        st.caption(ui('⚠ 白地とのコントラストが {r:.1f}:1 で、細い線や小さな記号の目安（{MIN_CONTRAST:g}:1）より薄い色です。印刷やプロジェクターでは見えにくくなります。', '⚠ The contrast with white is {r:.1f}:1, lighter than the guide for thin lines and small markers '
                      '({MIN_CONTRAST:g}:1). It will be hard to see in print or on a projector.', r=r, MIN_CONTRAST=fc.MIN_CONTRAST))


def series_title(sk):
    L = fc.layer_of(cfg, sk) if str(sk).startswith('@') else None
    if L is not None:
        return f"`{sk}` {L.get('source') or L.get('name', '')}"
    return f'`{sk}`'


@st.cache_data(show_spinner=False)
def parse(data: bytes, name: str):
    return fc.read_table(data, name)


def data_cols(df):
    return [c for c in df.columns if c not in ('_row', '_col', '_excluded')]


# ---------------------------------------------------------------------------------------------- sidebar
sb = st.sidebar
sb.title(ui('📈 グラフ作成', '📈 Graph maker'))


def _drop_all_csv():
    ss.up_gen = ss.get('up_gen', 0) + 1      # 別のキーの欄に替えると、アップロード欄が空になる
    ss.demo_files = {}
    ss.saved_files = {}


def _drop_kept(name):
    """Remove one CSV that is not in the upload box (demo data, or a CSV from a work file)."""
    ss.demo_files.pop(name, None)
    ss.saved_files.pop(name, None)


def _key_identity():
    """True when a widget with a key keeps its value although its label changes (new Streamlit). On older versions
    (1.32), a label in another language makes a new upload box, so the CSV files in it would be lost."""
    try:
        from streamlit.elements.lib.utils import compute_and_register_element_id
        return 'key_as_main_identity' in inspect.signature(compute_and_register_element_id).parameters
    except Exception:  # noqa: BLE001
        return False


# 言語を切り替えた直後（古い Streamlit）：前の回にアップロード欄にあった CSV を、作業ファイルの CSV と同じ扱いで残す
if not _key_identity() and ss.get('_last_lang') not in (None, ss.get('lang')) and ss.get('_last_uploaded'):
    ss.saved_files.update(ss._last_uploaded)
    ss._last_uploaded = {}
    ss._up_sig = {}
ss._last_lang = ss.get('lang')
up = sb.file_uploader(ui('CSV をアップロード（複数可）', 'Upload CSV files (several at once)'), type=['csv', 'tsv', 'txt'], accept_multiple_files=True,
                      key=f"csv_up{ss.get('up_gen', 0)}",
                      help=ui('1 行目が見出し。UTF-8／Shift_JIS、コンマ・タブ区切りのどちらでも読めます。', 'The first row holds the headers. UTF-8 or Shift_JIS, comma- or tab-separated.'))
uploaded, dups = {}, []
for f in up or []:                  # 古い順に並んでいるので、同じ名前なら後から入れた方が残る
    if f.name in uploaded:
        dups.append(f.name)
    uploaded[f.name] = f.getvalue()
ss._last_uploaded = dict(uploaded)
_sig = {n: (len(b), hash(b)) for n, b in uploaded.items()}
_prev = ss.get('_up_sig')
_new_names = [n for n in _sig if _prev is not None and _prev.get(n) != _sig[n]]    # この回に入れた（入れ直した）CSV
ss._up_sig = _sig
_seen = ss.setdefault('_seen', {})   # 名前ごとに、前に読んだ中身（外した後に入れ直したときも比べる）
_changed = [n for n in _sig if n in _seen and _seen[n] != _sig[n]]
_seen.update(_sig)
if _new_names and ss.demo_files:     # 自分の CSV を入れたら、デモデータは外す
    ss.demo_files = {}
    st.toast(ui('デモデータを外して、アップロードした CSV に切り替えました。', 'Removed the demo data and switched to the uploaded CSV files.'))
files = dict(ss.demo_files)
files.update(ss.saved_files)
files.update(uploaded)                # 同じ名前なら、アップロードした方を使う
if dups:
    sb.warning(ui('同じ名前の CSV が 2 つ以上あります：', 'Two or more CSV files have the same name: ') + ui('、', ', ').join(dict.fromkeys(dups)) + ui('。後から入れた方を使っています。古い方を × で外すか、「CSV をすべて外す」を押してから入れ直してください。', '. The one added last is used. Remove the older one with × or press “Remove all CSV files” and '
                                                                                                           'upload again.'))
files_box = sb.container()           # 読み込んでいる CSV の一覧（レイヤーが決まった後、最後に書く）
if files:
    sb.button(ui('CSV をすべて外す', 'Remove all CSV files'), on_click=_drop_all_csv, help=ui('アップロードした CSV とデモデータを外します。レイヤー・軸などの設定は残ります。', 'Removes the uploaded CSV files and the demo data. Layers, axes and other settings stay.'))

sb.divider()
sb.subheader(ui('ひな形', 'Presets'))
pname = sb.selectbox(ui('設定のひな形', 'Settings preset'), list(PRESETS), index=None, placeholder=ui('選ぶ…', 'Choose…'), key='preset_pick')
c1, c2 = sb.columns(2)
if c1.button(ui('設定だけ読む', 'Settings only'), disabled=pname is None, help=ui('今の CSV にこの見た目を当てはめる', 'Apply this look to the current CSV files')):
    ss.cfg = fc.complete(json.load(open(os.path.join(PRESET_DIR, PRESETS[pname][0]), encoding='utf-8')))
    clear_widgets()
    st.rerun()
if c2.button(ui('デモデータも', 'With demo data'), disabled=pname is None or not PRESETS[pname][1], help=ui('合成データで試す', 'Try it with synthetic data')):
    ss.cfg = fc.complete(json.load(open(os.path.join(PRESET_DIR, PRESETS[pname][0]), encoding='utf-8')))
    ss.demo_files = {n: demo_data.DEMOS[n]() for n in PRESETS[pname][1]}
    clear_widgets()
    st.rerun()
tname = sb.selectbox(ui('入力用テンプレート', 'Input template'), list(TEMPLATES), index=None, placeholder=ui('選ぶ…', 'Choose…'), key='tpl_pick')
if tname:
    sb.download_button(ui('テンプレートを保存', 'Save the template'), open(os.path.join(PRESET_DIR, TEMPLATES[tname]), 'rb').read(),
                       file_name=TEMPLATES[tname].replace('_template', ''), mime='text/csv')

sb.divider()
sb.subheader(ui('保存・読み込み', 'Save and open'))
save_box = sb.container()            # 保存のボタン（最後に書く。先に書くと、直前に変えた設定が入らない）
js = sb.file_uploader(ui('作業ファイル（.zip）または設定（.json）を開く', 'Open a work file (.zip) or settings (.json)'), type=['zip', 'json'], key='json_up',
                      help=ui('作業ファイル：設定と CSV。今の CSV と設定に置き換わります。設定（.json）：見た目だけ。今の CSV に当てはめます。', 'Work file: settings and CSV files; they replace the current CSV files and settings. Settings '
                              '(.json): the look only, applied to the current CSV files.'))
if js is not None:
    is_zip = js.name.lower().endswith('.zip')
    if sb.button(ui('この作業ファイルを開く', 'Open this work file') if is_zip else ui('この設定を適用', 'Apply these settings'), help=ui('今の CSV と設定は置き換わります', 'Replaces the current CSV files and settings') if is_zip else None):
        try:
            if is_zip:
                ss.cfg, ss.saved_files = fc.read_work_zip(js.getvalue())
                ss.demo_files = {}
                ss.up_gen = ss.get('up_gen', 0) + 1     # 今アップロードしている CSV は外す
                ss._excl_check = {}
                ss._opened = js.name
            else:
                ss.cfg = fc.complete(json.loads(js.getvalue().decode('utf-8-sig')))
            clear_widgets()
            st.rerun()
        except Exception as e:  # noqa: BLE001
            sb.error(ui('読めませんでした: {e}', 'Could not read it: {e}', e=e))
if sb.button(ui('設定を初期化', 'Reset settings'), help=ui('レイヤー・軸・色をすべて初期状態に戻す（CSV はそのまま。CSV ごとのレイヤーを作り直す）', 'Reset layers, axes and colours (the CSV files stay; one layer per CSV file is made again)')):
    ss.cfg = fc.default_settings()
    ss._pending_auto = list(files)
    clear_widgets()
    st.rerun()
sb.caption(ui('データはこの画面を開いている間だけ使われ、サーバーには残りません。ページを閉じる・再読み込みする前に「作業ファイルを保存」で手元に保存しておくと、続きから再開できます。', 'Your data are used only while this page is open and are not kept on the server. Before you close '
              'or reload the page, keep a copy with “Save work file” to carry on later.'))
if ss.get('_copied'):
    _d, _k = ss.pop('_copied')
    if _d:
        st.toast(ui('同じ設定にしました：', 'Same settings applied to: ') + ui('、', ', ').join(_d))
    if _k:
        st.toast(ui('同じ名前の列がないので変えませんでした：', 'Left unchanged (no column with the same name): ') + ui('、', ', ').join(_k))
if ss.get('_formula_added'):
    _s, _c, _y = ss.pop('_formula_added')
    st.toast(ui('「{_s}」に計算列「{_c}」を足しました', 'Added the calculated column “{_c}” to “{_s}”', _s=_s, _c=_c) + (ui('。この CSV のレイヤーの y にしました。', '. It is now the y of the layers using this CSV.') if _y else ui('。', '.')))
if ss.get('_opened'):
    st.toast(ui('「{v}」を開きました（CSV {v2} 個と設定）。', 'Opened “{v}” ({v2} CSV files and the settings).', v=ss.pop('_opened'), v2=len(ss.saved_files)))
sb.caption(f'figapp v{fc.APP_VERSION}')

# ---------------------------------------------------------------------------------------------- data
raws = {}
for n, b in files.items():
    try:
        raws[n] = parse(b, n)
    except Exception as e:  # noqa: BLE001
        sb.error(ui('{n} を読めませんでした: {e}', 'Could not read {n}: {e}', n=n, e=e))
_excl_check = ss.setdefault('_excl_check', {})    # 中身が変わったのに「除外する行」が前のままの CSV → その除外の文字列
for n in _changed:
    if n in raws and (cfg['sources'].get(n) or {}).get('exclude'):
        _excl_check[n] = cfg['sources'][n]['exclude']


def _default_src(sc):
    return not sc or (not sc.get('derived') and not sc.get('exclude') and not (sc.get('reshape') or {}).get('enabled'))


def SB(where, label, options, key, default=None, **kw):
    """Selectbox not tied to the settings that keeps its choice when the options change (old Streamlit resets it)."""
    v = ss.get(key)
    if v not in options:
        v = default if default in options else options[0]
    ss[key] = v
    return where.selectbox(label, options, key=key, **kw)


def pretty_formula(line):
    """'h = 1 - `l_after` / `l_before`' → 'h = 1 − l_after ÷ l_before' (for showing, not for computing)."""
    return line.replace('`', '').replace(' ** ', '^').replace(' * ', ' × ').replace(' / ', ' ÷ ').replace(' - ', ' − ')


def _formula_lines(src_name):
    return [x for x in str((ss.cfg['sources'].get(src_name) or {}).get('derived') or '').splitlines() if x.strip()]


def _add_formula(src_name, line, col, use_y):
    """Add one calculated column (a line with the same new name is replaced) and, if asked, plot it as y."""
    # 値は消さずに入れ直す（消すだけだと、新しい Streamlit では画面に残った前の値が次の操作で戻ってくる）
    lines = [x for x in _formula_lines(src_name) if x.split('=', 1)[0].strip() != col] + [line]
    ss.cfg['sources'].setdefault(src_name, {})['derived'] = '\n'.join(lines)
    ss[f'w|src|{src_name}|der'] = '\n'.join(lines)
    ss[f'fn|{src_name}'] = ''
    if use_y:
        for i, L in enumerate(ss.cfg['layers']):
            if L.get('source') == src_name and L.get('type') != 'ticks':
                L['y'] = col
                ss[f'w|L|{i}|y'] = col
    ss._formula_added = (src_name, col, use_y)


def _del_formula(src_name, j):
    lines = _formula_lines(src_name)
    if 0 <= j < len(lines):
        lines.pop(j)
    ss.cfg['sources'].setdefault(src_name, {})['derived'] = '\n'.join(lines)
    ss[f'w|src|{src_name}|der'] = '\n'.join(lines)


def _exclude_rows(picks):
    """Add the chosen (CSV, row) pairs from the checks to 「除外する行」."""
    for n, tok in picks:
        sc = ss.cfg['sources'].setdefault(n, {})
        toks = fc.exclusion_tokens(sc.get('exclude', ''))
        if tok not in toks:
            toks.append(tok)
        sc['exclude'] = ', '.join(toks)
        ss[f'w|src|{n}|exc'] = sc['exclude']
    ss['chk_pick'] = []
    ss._excl_msg = ui('{len_picks} 行を除外しました。図と平均の計算から外れています（「データ」タブの「除外する行」にも入っています）。', '{len_picks} rows excluded. They are left out of the figure and the means (they are also listed '
                      'under “Rows to exclude” in the Data tab).', len_picks=len(picks))


def _include_rows(items):
    for n, tok in items:
        sc = ss.cfg['sources'].setdefault(n, {})
        sc['exclude'] = ', '.join(t for t in fc.exclusion_tokens(sc.get('exclude', '')) if t != tok)
        ss[f'w|src|{n}|exc'] = sc['exclude']
    ss['chk_back'] = []
    ss._excl_msg = ui('{len_items} 行を戻しました。', '{len_items} rows restored.', len_items=len(items))


COPY_KEYS = ('type', 'x', 'y', 'series', 'unit', 'group', 'err', 'err_col', 'xerr_col', 'points', 'pts', 'connect', 'xbars', 'dodge',
             'n_show', 'axis', 'norm', 'x0', 'offset', 'xmin', 'xmax', 'sort', 'reverse_offset', 'trace_labels', 'bar_width',
             'tick_style', 'tick_y', 'tick_h', 'tick_gap', 'fit', 'fit_on', 'fit_ls', 'fit_eq', 'fit_extend', 'stack', 'seg_values', 'seg_decimals')


def same_col(c, cols):
    """The column in `cols` that is `c`, allowing µ/μ, full-width characters and spaces to differ between files."""
    if c in cols:
        return c
    key = lambda t: re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(t))).casefold()  # noqa: E731
    return next((x for x in cols if key(x) == key(c)), None)


def _copy_layer(i):
    """Give the other layers this layer's way of drawing (columns, grouping, error bars …), when their CSV has those
    columns. Each keeps its own CSV, name and colours."""
    L = ss.cfg['layers'][i]
    done, skipped = [], []
    for j, M in enumerate(ss.cfg['layers']):
        if j == i:
            continue
        tcols = ss.get('_cols', {}).get(M.get('source'), [])
        mapped = {f: same_col(L[f], tcols) for f in ('x', 'y', 'series', 'unit', 'group', 'err_col', 'xerr_col', 'filter_col') if L.get(f)}
        if all(v is not None for v in mapped.values()):
            for k in COPY_KEYS:
                if k in L:
                    M[k] = copy.deepcopy(L[k])
            M.update(mapped)
            done.append(M.get('name', ''))
        else:
            skipped.append(M.get('name', ''))
    clear_widgets('w|L|')
    ss._copied = (done, skipped)


AUTO_KEYS = ('name', 'type', 'source', 'x', 'y', 'series', 'unit', 'err', 'points', 'connect', 'dodge', 'n_show', 'filter_col',
             'axis', 'norm', 'offset', 'in_legend', 'label_suffix', 'ov_color', 'ov_ls', 'ov_fill', 'ov_marker', 'lw', 'ms', 'ma',
             'group', 'xbars', 'pts', 'xerr_col', 'err_col', 'fit', 'fit_on', 'fit_ls', 'fit_eq', 'fit_extend', 'stack')


def layer_sig(L):
    def norm(v):
        if isinstance(v, bool):
            return str(v)
        if isinstance(v, (int, float)):
            return f'{float(v):g}'
        return '' if v is None else str(v)
    return '|'.join(norm(L.get(k)) for k in AUTO_KEYS)


def untouched_auto(L):
    """A layer made automatically for a CSV and not changed since."""
    return bool(L.get('auto_sig')) and L['auto_sig'] == layer_sig(L)


def next_seq():
    ss.layer_seq = max([ss.get('layer_seq', 0)] + [int(L.get('created') or 0) for L in cfg['layers']]) + 1
    return ss.layer_seq


def add_auto_layers(names):
    """One layer per CSV just loaded (x = 1st numeric column, y = 2nd …, see figcore.auto_layer)."""
    made, skipped = [], []
    for n in names:
        if n not in raws or any(L.get('source') == n for L in cfg['layers']):
            continue
        L, text_x = fc.auto_layer(n, raws[n], len(cfg['layers']) + 1)
        if L is None:
            skipped.append(n)
            continue
        if text_x and not cfg['layers']:
            cfg['x']['categorical'] = True
            ss.pop('w|A|x|cat', None)
        L['created'] = next_seq()
        cfg['layers'].append(L)
        fc.assign_uids(cfg)
        L['auto_sig'] = layer_sig(L)
        made.append(n)
    if made:
        clear_widgets('w|L|')
    return made, skipped


def reassign(old, new):
    """Point every layer that used the CSV `old` (and its per-file settings) to the uploaded CSV `new`.
    A layer that was made automatically for `new` and not changed since is removed (the old layer takes its place)."""
    moved = [L for L in cfg['layers'] if L.get('source') == old]
    for L in moved:
        L['source'] = new
    twins = [L for L in cfg['layers'] if L.get('source') == new and untouched_auto(L) and all(L is not M for M in moved)]
    if moved:
        cfg['layers'][:] = [L for L in cfg['layers'] if all(L is not T for T in twins)]
    if old in cfg['sources'] and _default_src(cfg['sources'].get(new)):
        cfg['sources'][new] = cfg['sources'].pop(old)
        if cfg['sources'][new].get('exclude'):
            _excl_check[new] = cfg['sources'][new]['exclude']
    clear_widgets('w|L|')
    clear_widgets(f'w|src|{old}|')
    clear_widgets(f'w|src|{new}|')


_used = [L.get('source') for L in cfg['layers'] if L.get('source')]
missing = list(dict.fromkeys(n for n in _used if n not in raws))
fresh = [n for n in raws if n not in _used]
_pending = list(dict.fromkeys([n for n in ss.get('_pending_auto', []) if n in raws] + [n for n in _new_names if n in raws]))
if raws and len(missing) == 1 and len(fresh) == 1:      # 1 つ外して 1 つ足した → 足した CSV に差し替える
    reassign(missing[0], fresh[0])
    ss._replaced = (missing[0], fresh[0])
    ss._pending_auto = [n for n in _pending if n != fresh[0]]
    st.rerun()
if raws and len(missing) == 1 and not fresh:           # 新しい CSV を先に入れてから古い CSV を外した
    _ms = [L for L in cfg['layers'] if L.get('source') == missing[0]]
    _c0 = max(int(L.get('created') or 0) for L in _ms)
    _cands = [L for L in cfg['layers'] if untouched_auto(L) and int(L.get('created') or 0) > _c0 and L.get('source') in raws
              and sum(M.get('source') == L['source'] for M in cfg['layers']) == 1]
    if len(_cands) == 1:
        _new = _cands[0]['source']
        reassign(missing[0], _new)                       # 自動で作ったレイヤーは消え、古いレイヤーが新しい CSV を使う
        ss._replaced = (missing[0], _new)
        st.rerun()
if raws and not missing:                                # 読み込んだ CSV のレイヤーを自動で作る
    ss._pending_auto = []
    _made, _skipped = add_auto_layers([n for n in _pending if n not in _used])
    if _made or _skipped:
        ss._made = (_made, _skipped)
        st.rerun()
else:
    ss._pending_auto = _pending
if ss.get('_replaced'):
    _o, _n = ss.pop('_replaced')
    st.toast(ui('「{_o}」を使っていたレイヤーを「{_n}」に切り替えました。', 'Layers that used “{_o}” now use “{_n}”.', _o=_o, _n=_n))
elif ss.get('_made'):
    _m, _k = ss.pop('_made')
    if _m:
        st.toast(ui('レイヤーを作りました：', 'Layers made: ') + ui('、', ', ').join(_m) + ui('（x は 1 列目、y は 2 列目の数値の列。「レイヤー」タブで変えられます）', ' (x = the first numeric column, y = the second; change them in the Layers tab)'))
    if _k:
        st.toast(ui('数値の列が足りないので、レイヤーを作りませんでした：', 'No layer made (too few numeric columns): ') + ui('、', ', ').join(_k))
elif cfg['layers'] and not missing:
    for n in _new_names:
        if n in _used and n in _changed:
            st.toast(ui('「{n}」の中身が変わったので、描き直しました。', '“{n}” has changed, so the figure was redrawn.', n=n))

left, right = st.columns([0.53, 0.47], gap='medium')
with left:
    if raws and missing:
        st.warning(ui('レイヤーが使っている CSV がアップロードされていません：', 'A CSV file used by a layer has not been uploaded: ') + ui('、', ', ').join(missing) +
                   ui('。代わりに使う CSV を選んでください。選ぶまで、そのレイヤーは描きません。', '. Choose a CSV file to use instead. Until then, that layer is not drawn.'))
        mc = st.columns(min(len(missing), 3))
        for j, m in enumerate(missing):
            tgt = mc[j % len(mc)].selectbox(ui('「{m}」の代わり', 'Instead of “{m}”', m=m), [ui('（選ぶ）', '(choose)')] + list(raws), key=f'map|{m}')
            if tgt != ui('（選ぶ）', '(choose)'):
                reassign(m, tgt)
                ss.pop(f'map|{m}', None)
                st.rerun()
    tabs = st.tabs([ui('データ', 'Data'), ui('レイヤー', 'Layers'), ui('軸', 'Axes'), ui('系列・色', 'Series & colours'), ui('凡例・注記', 'Legend & notes'), ui('大きさ・文字', 'Size & text'), ui('チェック', 'Checks')])

# ① data
with tabs[0]:
    if not raws:
        st.info(ui('左のサイドバーから CSV をアップロードしてください。試すだけなら「ひな形」→「デモデータも」。', 'Upload CSV files in the sidebar on the left. To just try the app: “Presets” → “With demo data”.'))
    for i, (name, df) in enumerate(raws.items()):
        src = cfg['sources'].setdefault(name, {})
        rs = src.setdefault('reshape', {'enabled': False, 'value_cols': [], 'var_name': 'variable', 'value_name': 'value', 'parse': 'first'})
        with st.expander(ui('**{name}**\u3000{len_df} 行 × {v} 列', '**{name}**  {len_df} rows × {v} columns', name=name, len_df=len(df), v=len(data_cols(df))), expanded=(i == 0 or name in _excl_check)):
            st.dataframe(df.drop(columns=['_row']).head(300), height=180)
            rs['enabled'] = W(st.checkbox, ui('横長の表を縦長にする（列 → 行）', 'Turn a wide table into a long one (columns → rows)'), f'src|{name}|en', rs.get('enabled', False),
                              help=ui('例：l_9.8N, l_19.6N … の列を「荷重」と「値」の 2 列にする。列名の中の数字を取り出せる。', 'e.g. turn the columns l_9.8N, l_19.6N … into two columns, “load” and “value”. Numbers in the '
                                      'column names can be taken out.'))
            if rs['enabled']:
                rs['value_cols'] = MS(ui('値の列（縦にまとめる列）', 'Value columns (to stack into one)'), f'src|{name}|vals', data_cols(df), rs.get('value_cols', []))
                c1, c2, c3 = st.columns(3)
                rs['var_name'] = W(c1.text_input, ui('新しい列の名前（列名から）', 'Name of the new column (from the column names)'), f'src|{name}|var', rs.get('var_name', 'variable'))
                rs['value_name'] = W(c2.text_input, ui('値の列の名前', 'Name of the value column'), f'src|{name}|valn', rs.get('value_name', 'value'))
                rs['parse'] = S(ui('列名の扱い', 'Column names'), f'src|{name}|parse', ['first', 'last', 'none'], rs.get('parse', 'first'),
                                fmt=lambda v: {'first': ui('最初の数字を取り出す', 'Take the first number'), 'last': ui('最後の数字を取り出す', 'Take the last number'), 'none': ui('列名のまま', 'Keep the name')}[v], where=c3)
            # 計算列：選んで作る（式を書くこともできる）
            c1, c2 = st.columns([0.68, 0.32])
            c1.markdown(ui('**計算列**\u3000列と計算を選んで、新しい列を作ります。', '**Calculated columns**  Choose columns and a calculation to make a new column.'))
            raw_edit = c2.checkbox(ui('式を直接書く', 'Write the formula'), key=f'derraw|{name}')
            if raw_edit:
                src['derived'] = W(st.text_area, ui('計算列（1 行に 1 つ：新しい列 = 式）', 'Calculated columns (one per line: new column = formula)'), f'src|{name}|der', src.get('derived', ''), height=90,
                                   placeholder='h = 1 - l_after / l_before\nH_pct = 100 * h',
                                   help=ui('使えるもの：+ − × ÷（* /）、**、( )、数字、列名、log・ln・log10・exp・sqrt・abs。記号を含む列名は `l_9.8N` のように ` で囲む。縦長にした後の列も使える。', 'Allowed: + − × ÷ (* /), **, ( ), numbers, column names, log, ln, log10, exp, sqrt, abs. Put '
                                           'column names with symbols in backticks, like `l_9.8N`. Columns from the wide-to-long step can '
                                           'be used too.'))
            else:
                for j, line in enumerate(_formula_lines(name)):
                    c1, c2 = st.columns([0.86, 0.14])
                    c1.markdown(f'`{pretty_formula(line)}`')
                    c2.button('×', key=f'derdel|{name}|{j}', on_click=_del_formula, args=(name, j), help=ui('この計算列を消す', 'Delete this calculated column'), **WIDE)
            pv = fc.prepared_tables(cfg, {name: df})[name]
            pcols = [c for c in data_cols(pv) if c != '_excluded']
            if pcols:
                c1, c2, c3 = st.columns([0.4, 0.3, 0.3])
                kind = SB(c1, ui('計算', 'Calculation'), list(fc.FORMULAS), f'fk|{name}', format_func=fc.formula_label)
                uses = fc.FORMULAS[kind][2]
                fa = fb = fk = None
                fm = []
                if 'M' in uses:
                    km = f'fm|{name}'
                    ss[km] = [c for c in ss.get(km, []) if c in pcols]
                    fm = st.multiselect(ui('平均する列', 'Columns to average') if kind == 'mean_cols' else ui('足す列', 'Columns to add'), pcols, key=km, placeholder=ui('列を選ぶ…', 'Choose columns…'))
                else:
                    fa = SB(c2, ui('A の列', 'Column A'), pcols, f'fa|{name}')
                    if 'B' in uses:
                        fb = SB(c3, ui('B の列', 'Column B'), pcols, f'fb|{name}', default=next((c for c in pcols if c != fa), pcols[0]))
                    if 'k' in uses:
                        kk = f'fkv|{name}|{kind}'
                        if kk not in ss:
                            ss[kk] = fc.FORMULA_K.get(kind, 1.0)
                        fk = c3.number_input(ui('数', 'Number'), key=kk, format='%g')
                line, newcol = fc.formula_line(kind, fa, fb, fk, fm, ss.get(f'fn|{name}', ''))
                c1, c2, c3 = st.columns([0.4, 0.36, 0.24])
                c1.text_input(ui('新しい列の名前', 'New column name'), key=f'fn|{name}', placeholder=newcol,
                              help=ui('空欄なら薄く出ている名前になります。この名前が軸のラベルや列の一覧に出ます。', 'If left empty, the grey name is used. This name appears in axis labels and column lists.'))
                use_y = c2.checkbox(ui('作った列を y に使う', 'Use the new column as y'), value=True, key=f'fy|{name}',
                                    help=ui('この CSV を使っているレイヤーの y を、作った列に替えます。', 'Layers that use this CSV file get the new column as y.'))
                bad = ''
                if 'M' in uses and len(fm) < 2:
                    bad = ui('列を 2 つ以上選んでください。', 'Choose two or more columns.')
                elif any(ch in newcol for ch in '=`\n'):
                    bad = ui('列の名前に = と ` は使えません。', 'Column names cannot contain = or `.')
                elif newcol in data_cols(df) or (newcol in pcols and newcol not in [x.split('=', 1)[0].strip() for x in _formula_lines(name)]):
                    bad = ui('「{newcol}」は元の列と同じ名前です。別の名前にしてください。', '“{newcol}” is the name of an existing column. Choose another name.', newcol=newcol)
                if not bad:
                    res, nn = fc.add_derived(pv, line)
                    if nn:
                        bad = nn[0]
                    else:
                        v = pd.to_numeric(res[newcol], errors='coerce')
                        good = v[v.notna() & v.abs().lt(float('inf'))]
                        st.caption(ui('`{pretty_formula_line}`\u3000→ 例：', '`{pretty_formula_line}`  → e.g. ', pretty_formula_line=pretty_formula(line)) + ui('、', ', ').join(f'{x:.4g}' for x in good.head(4)) +
                                   (ui('\u3000（最小 {v:.4g}、最大 {v2:.4g}', '  (min {v:.4g}, max {v2:.4g}', v=good.min(), v2=good.max()) if len(good) else ui('\u3000（', '  (')) +
                                   (ui('、計算できない行 {v}', ', rows that cannot be calculated: {v}', v=int(len(v) - len(good))) if len(v) > len(good) else '') + ui('）', ')'))
                if bad:
                    st.caption('⚠ ' + bad)
                c3.button(ui('追加', 'Add'), key=f'fadd|{name}', on_click=_add_formula, args=(name, line, newcol, use_y), disabled=bool(bad),
                          type='primary', **WIDE)
            src['exclude'] = W(st.text_input, ui('除外する行（CSV の行番号。見出しが 1 行目）', 'Rows to exclude (CSV row numbers; the header is row 1)'), f'src|{name}|exc', src.get('exclude', ''),
                               help=ui('例：57, 60:l_98N（60 行目の l_98N だけ）。除外した値は図にも集計にも使わない。', 'e.g. 57, 60:l_98N (only l_98N in row 60). Excluded values are used neither in the figure nor '
                                       'in the statistics.'))
            if name in _excl_check:
                if src['exclude'] != _excl_check[name]:
                    _excl_check.pop(name)
                else:
                    c1, c2 = st.columns([0.78, 0.22])
                    c1.warning(ui('「除外する行」は、前に読み込んだ CSV の行番号で書いたものです。今の CSV でも同じ行を指しているか確認してください。', '“Rows to exclude” was written with the row numbers of the CSV file loaded before. Check that '
                                  'they point to the same rows in the current file.'))
                    if c2.button(ui('確認した', 'Checked'), key=f'exok|{name}'):
                        _excl_check.pop(name)
                        st.rerun()

prep_notes = []
tables = fc.prepared_tables(cfg, raws, prep_notes) if raws else {}
ss._cols = {n: data_cols(t) for n, t in tables.items()}
with tabs[0]:
    for n in prep_notes:
        st.warning(n)

# ② layers
keys0 = fc.series_keys(cfg, tables)
fc.ensure_series(cfg, keys0)
if ss.pop('_apply_palette', False):
    fc.apply_palette(cfg, keys0)
    clear_widgets('w|SR|')
    clear_widgets('w|LS|')
with tabs[1]:
    c1, c2 = st.columns([0.6, 0.4])
    newtype = c1.selectbox(ui('追加する描き方', 'Type to add'), list(TYPE_LABEL), format_func=TYPE_LABEL.get, key='newtype')
    if c2.button(ui('＋ レイヤーを追加', '＋ Add a layer'), **WIDE):
        L = fc.layer_default(len(cfg['layers']) + 1, newtype)
        L['created'] = next_seq()
        if tables:
            used = {M.get('source') for M in cfg['layers']}
            L['source'] = next((n for n in tables if n not in used), list(tables)[0])   # まだ使っていない CSV を先に
        cfg['layers'].append(L)
        fc.assign_uids(cfg)
        clear_widgets('w|L|')
        st.rerun()
    _unused = [n for n in tables if all(M.get('source') != n for M in cfg['layers'])]
    if _unused:
        st.button(ui('CSV ごとにレイヤーを作る', 'Make a layer for each CSV file') if not cfg['layers'] else ui('まだ使っていない CSV のレイヤーを作る（', 'Make layers for the CSV files not used yet (') + ui('、', ', ').join(_unused) + ui('）', ')'),
                  on_click=lambda u=tuple(_unused): ss.__setitem__('_pending_auto', list(u)), key='auto_layers',
                  help=ui('x は 1 列目、y は 2 列目の数値の列。同じ x の値が繰り返されていれば平均 ± SD、そうでなければ線か散布図で描きます。', 'x = the first numeric column, y = the second. Repeated x values give mean ± SD; otherwise a '
                          'line or a scatter plot.'))
    if not cfg['layers']:
        st.info(ui('CSV を読み込むと、CSV ごとにレイヤーが自動でできます。「＋ レイヤーを追加」で描くものを足すこともできます。1 つの図に複数のレイヤー（例：スペクトル＋ティック）を重ねられます。', 'Loading CSV files makes one layer per file. Add more with “＋ Add a layer”. Several layers (e.g. '
                   'spectra + ticks) can be drawn in one figure.'))
    for i, L in enumerate(cfg['layers']):
        p = f'L|{i}|'
        title = f"**{i + 1}. {L['name']}**　{TYPE_LABEL.get(L['type'], L['type'])}" + (f"　{L['source']}" if L.get('source') else '')
        if tables and L.get('source') and L['source'] not in tables:
            title += ui('\u3000⚠ この CSV がありません', '  ⚠ this CSV file is missing')
        with st.expander(title, expanded=True):
            c1, c2 = st.columns(2)
            L['name'] = W(c1.text_input, ui('名前', 'Name'), p + 'name', L['name'])
            L['type'] = S(ui('描き方', 'Type'), p + 'type', list(TYPE_LABEL), L['type'], fmt=TYPE_LABEL.get, where=c2)
            c1, c2, c3, c4, c5 = st.columns([0.115, 0.115, 0.19, 0.19, 0.39])   # 狭い画面でも ↑ ↓・複製・削除が切れない幅
            if len(cfg['layers']) > 1:
                c5.button(ui('ほかのレイヤーにも同じ設定', 'Same settings for the other layers'), key=f'copy{i}', on_click=_copy_layer, args=(i,), **WIDE,
                          help=ui('このレイヤーの描き方（x・y・グループ・まとめる単位・誤差棒・点など）を、ほかのレイヤーにもそのまま当てはめます。各レイヤーの CSV・名前・色はそのまま。同じ名前の列がない CSV '
                                  'のレイヤーは変えません。', 'Applies this layer’s settings (x, y, group, unit, error bars, points, …) to the other layers. '
                                  'Each layer keeps its CSV file, name and colour. Layers whose CSV file has no column of the same '
                                  'name are left unchanged.'))
            if c1.button('↑', key=f'up{i}', disabled=i == 0):
                cfg['layers'][i - 1], cfg['layers'][i] = cfg['layers'][i], cfg['layers'][i - 1]
                clear_widgets('w|L|')
                st.rerun()
            if c2.button('↓', key=f'dn{i}', disabled=i == len(cfg['layers']) - 1):
                cfg['layers'][i + 1], cfg['layers'][i] = cfg['layers'][i], cfg['layers'][i + 1]
                clear_widgets('w|L|')
                st.rerun()
            if c3.button(ui('複製', 'Duplicate'), key=f'dup{i}', help=ui('同じ設定のレイヤーをすぐ下に足します。まだどのレイヤーにも使っていない CSV（同じ列名のもの）があれば、そのデータに切り替えます。色は別になります。', 'Adds a layer with the same settings just below. If a CSV file with the same columns is not used '
                                                          'by any layer yet, the copy uses it. The copy gets its own colour.')):
                M = copy.deepcopy(L)
                M['uid'] = ''
                M['auto_sig'] = ''
                M['created'] = next_seq()
                M['name'] = L['name'] + ui('（複製）', ' (copy)')
                used = {N.get('source') for N in cfg['layers']}
                need = [c for c in (L.get('x'), L.get('y'), L.get('series'), L.get('unit')) if c]
                nxt = next((n for n in tables if n not in used and all(c in tables[n].columns for c in need)), None)
                if nxt:
                    M['source'] = nxt
                    M['name'] = os.path.splitext(nxt)[0]
                cfg['layers'].insert(i + 1, M)
                fc.assign_uids(cfg)
                clear_widgets('w|L|')
                st.rerun()
            if c4.button(ui('削除', 'Delete'), key=f'del{i}'):
                cfg['layers'].pop(i)
                clear_widgets('w|L|')
                st.rerun()
            if not tables:
                st.caption(ui('CSV がまだありません。', 'No CSV file yet.'))
                continue
            if L.get('source') and L['source'] not in tables:
                st.warning(ui('CSV「{source}」がアップロードされていないので、このレイヤーは描いていません。上の欄で代わりの CSV を選ぶか、このレイヤーを削除してください。', 'The CSV file “{source}” has not been uploaded, so this layer is not drawn. Choose a file to '
                              'use instead in the box above, or delete this layer.', source=L['source']))
                continue
            L['source'] = S(ui('データ（CSV）', 'Data (CSV)'), p + 'src', list(tables), L.get('source') or list(tables)[0])
            cols = data_cols(tables[L['source']])
            opt = [''] + cols
            dash = lambda v: v if v else ui('（なし）', '(none)')  # noqa: E731
            c1, c2, c3 = st.columns(3)
            with c1:      # 列名の違う CSV に替えたときは、1 列目を x、それ以外の最初の列を y にする
                L['x'] = S(ui('x の列', 'x column'), p + 'x', cols, L['x'] if L.get('x') in cols else cols[0])
            with c2:
                if L['type'] != 'ticks':
                    L['y'] = S(ui('y の列', 'y column'), p + 'y', cols, L['y'] if L.get('y') in cols else next((c for c in cols if c != L['x']), cols[0]),
                               help=ui('比や治癒率などの計算した列は、「データ」タブの「計算列」で列を選んで作れます。', 'Calculated columns such as ratios or healing ratios can be made under “Calculated columns” in '
                                       'the Data tab.'))
            with c3:
                L['series'] = S(ui('系列（色分け）の列', 'Series (colour) column'), p + 'ser', opt, L.get('series', ''), fmt=dash,
                                help=ui('（なし）にすると、このレイヤー全体が 1 つの系列（1 色）になります。CSV を系列ごとに分けたときは（なし）のままにして、CSV ごとにレイヤーを作ります。', 'With (none), the whole layer is one series (one colour). If your CSV files are split by series, '
                                        'keep (none) and make one layer per file.'))
            # 色・凡例・記号：系列の列がないレイヤーはここで直接変えられる（「系列・色」タブと連動）
            if not L['series']:
                sk = fc.layer_key(L)
                if sk not in cfg['series']:
                    fc.ensure_series(cfg, keys0 + [sk])
                c1, c2, c3 = st.columns([0.12, 0.44, 0.44])     # 2 段に分けて、狭い画面でも名前が切れないように
                SW(c1.color_picker, ui('色', 'Colour'), sk, 'color', 'LS')
                color_menu(c2, sk, 'LS', help=ui('いつも同じ色にしたいときは、名前で選びます。一覧は「系列・色」タブの「色の一覧」で足せます。', 'To keep a colour the same everywhere, choose it by name. Add names under “Colour list” in the '
                                                 'Series & colours tab.'))
                SW(c3.text_input, ui('凡例の名前', 'Legend name'), sk, 'label', 'LS', help=ui('空欄にすると凡例に出しません。', 'Leave empty to keep it out of the legend. ') + SUBSUP_HELP)
                if L['type'] != 'bar':
                    contrast_note(sk)
                c1, c2 = st.columns(2)
                if L['type'] == 'line':
                    SW(c1.selectbox, ui('線', 'Line'), sk, 'ls', 'LS', options=list(LS_LABEL), format_func=LS_LABEL.get)
                else:
                    SW(c1.selectbox, ui('記号', 'Marker'), sk, 'marker', 'LS', options=list(MARKER_LABEL), format_func=MARKER_LABEL.get)
                if L['type'] == 'mean_error':
                    SW(c2.selectbox, ui('つなぐ線', 'Connecting line'), sk, 'ls', 'LS', options=list(LS_LABEL), format_func=LS_LABEL.get,
                       help=ui('平均の点をつなぐ線の種類（「線でつなぐ」にチェックがあるとき）。近似線の種類は下の「近似線の種類」。', 'Style of the line joining the means (when “Connect with a line” is ticked). The fit line has '
                               'its own “Fit line style” below.'))
            else:
                f_, _ = fc.layer_frame(L, tables, cfg)
                ks = fc.natural_order(f_['_series'].unique()) if f_ is not None else []
                if ks:
                    sw = []
                    for k in ks[:12]:
                        sty = cfg['series'].get(k, {})
                        sw.append(f"<span style='color:{html.escape(str(sty.get('color', '#999999')))}'>■</span> "
                                  f"{html.escape(str(sty.get('label') or k))}")
                    st.caption(ui('系列：', 'Series: ') + '　'.join(sw) + ('　…' if len(ks) > 12 else '') + ui('\u3000— 色・記号・凡例の名前は「系列・色」タブで変えます', '  — change colours, markers and legend names in the Series & colours tab'),
                               unsafe_allow_html=True)
            if L['type'] == 'bar':
                c1, c2, c3 = st.columns([0.4, 0.33, 0.27])
                with c1:
                    L['stack'] = S(ui('棒の並べ方', 'Bar arrangement'), p + 'stk', list(STACK_LABEL), L.get('stack', 'none'), fmt=STACK_LABEL.get,
                                   help=ui('積み上げ：同じ x の系列（粒度の区分など）を、下から「系列の順」に積みます。100% 積み上げ：x ごとの合計を 100 % にして積みます（質量や個数のままでも割合で描けます）。凡例は積んだ順の逆（いちばん上の区画が凡例の上）'
                                           'に並びます。', 'Stacked: the series at the same x (e.g. size fractions) are stacked from the bottom in the “Series '
                                           'order”. 100% stacked: each x adds up to 100% (masses or counts are drawn as fractions). The '
                                           'legend lists the parts from the top down.'))
                if L['stack'] != 'none':
                    L['seg_values'] = W(c2.checkbox, ui('区画の中に値を書く', 'Write the values in the parts'), p + 'segv', L.get('seg_values', True),
                                        help=ui('区画に収まらない値（低すぎる・細すぎる区画）は書きません。文字の色は、区画の色に合わせて白か黒。', 'Values that do not fit in their part (too low or too narrow) are left out. The text is white '
                                                'or black to suit the fill.'))
                    L['seg_decimals'] = W(c3.number_input, ui('値の小数の桁', 'Decimals of the values'), p + 'segd', int(L.get('seg_decimals') or 0),
                                          min_value=0, max_value=3, step=1, disabled=not L['seg_values'])
            stacked = fc.is_stacked(L)
            if L['type'] in ('mean_error', 'bar'):
                c1, c2, c3 = st.columns(3)
                with c1:
                    L['unit'] = S(ui('まとめる単位の列', 'Unit column'), p + 'unit', opt, L.get('unit', ''), fmt=dash,
                                  help=ui('例：圧痕 ID。単位ごとに平均してから、単位の間で平均 ± 誤差を出す（n = 単位の数）。', 'e.g. indentation ID. Values are averaged per unit first, then mean ± error is taken over the '
                                          'units (n = number of units).'))
                if stacked:
                    L['bar_width'] = W(c2.number_input, ui('棒の幅（0–1）', 'Bar width (0–1)'), p + 'bw', float(L.get('bar_width') or 0.8), min_value=0.1, max_value=1.0, step=0.05)
                    st.caption(ui('積み上げでは、誤差棒・個々の点・n は描きません。同じ x と系列の行が複数あるときは、その平均を積みます。', 'Stacked bars have no error bars, individual points or n. Several rows with the same x and series '
                                  'are averaged before stacking.')
                               + ('' if cfg['x'].get('categorical') else ui('\u3000温度などを等間隔に並べるなら、「軸」タブの横軸で「項目の軸にする」をオンに。', '  To space temperatures etc. evenly, turn on “Use as an item axis” for the x axis in the Axes '
                                                                            'tab.')))
                else:
                    with c2:
                        L['err'] = S(ui('誤差棒', 'Error bars'), p + 'err', list(ERR_LABEL), L.get('err', 'sd'), fmt=ERR_LABEL.get)
                    with c3:
                        if L['err'] == 'column':
                            L['err_col'] = S(ui('誤差の列（y）', 'Error column (y)'), p + 'errc', cols, L.get('err_col') or cols[0],
                                             help=ui('自分で計算した SD などの列。平均の列を y に選び、まとめずに 1 行 1 点で描きます。', 'A column of SD etc. that you calculated. Choose the mean column as y; each row is drawn as one '
                                                     'point, without grouping.'))
                if L['type'] == 'mean_error':
                    c1, c2, c3 = st.columns(3)
                    with c1:
                        L['group'] = S(ui('グループの列（x が測った値のとき）', 'Group column (when x is measured)'), p + 'grp', opt, L.get('group', ''), fmt=dash,
                                       help=ui('x が設定値ではなく測った値（初期き裂長さなど）のときに使います。この列（例：荷重）の値ごとに、x と y の両方の平均 ± 誤差を出して、平均の位置に縦と横の誤差棒を描きます。（なし）'
                                               'なら、これまでどおり同じ x の値ごとにまとめます。', 'Use this when x is a measured value (e.g. initial crack length) rather than a set value. For '
                                               'each value of this column (e.g. load), the mean ± error of both x and y is drawn at the mean, '
                                               'with vertical and horizontal error bars. With (none), values are grouped by equal x as usual.'))
                    if L['group']:
                        L['xbars'] = W(c2.checkbox, ui('x の誤差棒も描く', 'Draw x error bars too'), p + 'xb', L.get('xbars', True))
                    if L['err'] == 'column':
                        with c3:
                            L['xerr_col'] = S(ui('誤差の列（x、任意）', 'Error column (x, optional)'), p + 'xerrc', opt, L.get('xerr_col', ''), fmt=dash,
                                              help=ui('x にも自分で計算した SD などがあるとき。横の誤差棒になります。', 'When x also has an SD etc. that you calculated. It is drawn as horizontal error bars.'))
                if not stacked:
                    c1, c2, c3 = st.columns(3)
                    L['points'] = W(c1.checkbox, ui('個々の値を点で重ねる', 'Show individual values as points'), p + 'pts', L.get('points', True))
                    if L['type'] == 'mean_error':
                        L['connect'] = W(c2.checkbox, ui('線でつなぐ', 'Connect with a line'), p + 'con', L.get('connect', True))
                        if L.get('group'):
                            c3.caption(ui('x が測った値なので、平均も個々の点も実際の x の位置に描きます（横にずらさない・散らさない）。', 'x is measured, so the means and the individual points are drawn at their real x (no sideways '
                                          'shift or scatter).'))
                        else:
                            L['dodge'] = W(c3.number_input, ui('系列を横にずらす量', 'Sideways shift of series'), p + 'dod', float(L.get('dodge') or 0.0), min_value=0.0, max_value=0.3,
                                           step=0.005, format='%.3f', help=ui('重なりを避けるために、系列ごとに平均の点をわざと横にずらす量（x 軸の幅に対する割合）。0 なら x の値の真上に描く。CSV ごとのレイヤーどうしにも効く。', 'How far the means of each series are shifted sideways to avoid overlap (fraction of the x-axis '
                                                                              'width). 0 draws them right at x. It also works between layers of different CSV files.'))
                    else:
                        L['bar_width'] = W(c3.number_input, ui('棒の幅（0–1）', 'Bar width (0–1)'), p + 'bw', float(L.get('bar_width') or 0.8), min_value=0.1, max_value=1.0, step=0.05)
                    c1, c2, c3 = st.columns(3)
                    with c1:
                        L['n_show'] = S(ui('n を書く', 'Show n'), p + 'nshow', list(N_SHOW_LABEL), L.get('n_show', 'none'), fmt=N_SHOW_LABEL.get,
                                        help=ui('n = その点の平均に使った数（まとめる単位の列があれば単位の数、なければ行の数）。書き方と「Mean ± s.d., n = …」の注記は「凡例・注記」タブ。', 'n = the number averaged for that point (the number of units if a unit column is set, otherwise '
                                                'rows). The format and the “Mean ± s.d., n = …” note are in the Legend & notes tab.'))
                    if L['type'] == 'mean_error' and L.get('unit') and L.get('points', True):
                        with c2:
                            L['pts'] = S(ui('薄い点で描く値', 'Values shown as light points'), p + 'ptl', list(PTS_LABEL), L.get('pts', 'unit'), fmt=PTS_LABEL.get,
                                         help=ui('単位（圧痕など）ごとの平均を点にするか、1 行ずつ（き裂ごと）点にするか。平均 ± 誤差の計算は、どちらでも単位ごとの平均から。', 'Points for the mean of each unit (e.g. indentation) or for each row (e.g. each crack). The mean '
                                                 '± error is calculated from the unit means either way.'))
            if L['type'] in ('mean_error', 'scatter') and not cfg['x'].get('categorical'):
                c1, c2, c3 = st.columns(3)
                with c1:
                    L['fit'] = S(ui('近似線', 'Fit line'), p + 'fit', list(FIT_LABEL), L.get('fit', 'none'), fmt=FIT_LABEL.get,
                                 help=ui('最小二乗で当てはめた線を系列ごとに描きます。べき乗と指数は、対数をとって直線で当てはめます（y が 0 以下の点は使いません）。係数と R² は、プレビューの下の「近似の結果」に出ます。', 'Draws a least-squares fit for each series. Power and exponential fits are made on logarithms '
                                         '(points with y ≤ 0 are not used). The coefficients and R² are shown under the preview in “Fit '
                                         'results”.'))
                if L['fit'] != 'none':
                    with c2:
                        L['fit_ls'] = S(ui('近似線の種類', 'Fit line style'), p + 'fitls', ['--', '-', ':', '-.'], L.get('fit_ls', '--'), fmt=LS_LABEL.get)
                    if L['type'] == 'mean_error':
                        with c3:
                            L['fit_on'] = S(ui('近似に使う値', 'Values to fit'), p + 'fiton', list(FIT_ON_LABEL), L.get('fit_on', 'points'), fmt=FIT_ON_LABEL.get,
                                            help=ui('個々の値：薄い点も含めたすべての行で当てはめる（ふつうはこちら）。平均の点：平均の位置だけで当てはめる。', 'Individual values: fit all rows, including the light points (usual). Mean points: fit only the '
                                                    'means.'))
                    c1, c2, c3 = st.columns(3)
                    L['fit_eq'] = W(c1.checkbox, ui('式と R² を図に書く', 'Show the equation and R² in the figure'), p + 'fiteq', L.get('fit_eq', False),
                                    help=ui('書く位置は「凡例・注記」タブの「近似式の位置」。', 'Its position is “Fit equation position” in the Legend & notes tab.'))
                    L['fit_extend'] = W(c2.checkbox, ui('軸の端まで伸ばす', 'Extend to the axis ends'), p + 'fitext', L.get('fit_extend', False),
                                        help=ui('外すと、当てはめに使った x の範囲だけに線を引きます。', 'When off, the line covers only the x range used for the fit.'))
            if L['type'] == 'line':
                c1, c2, c3 = st.columns(3)
                with c1:
                    L['norm'] = S(ui('規格化', 'Normalise'), p + 'norm', list(NORM_LABEL), L.get('norm', 'none'), fmt=NORM_LABEL.get)
                L['x0'] = numtext(ui('x0（規格化の基準）', 'x0 (reference for normalising)'), p + 'x0', L.get('x0', ''), where=c2, disabled=L['norm'] not in ('sub_x0', 'div_x0'))
                L['offset'] = W(c3.number_input, ui('系列ごとに上へずらす量', 'Upward offset per series'), p + 'off', float(L.get('offset') or 0.0), step=0.1, format='%.3f')
                c1, c2, c3, c4 = st.columns(4)
                L['xmin'] = W(c1.text_input, ui('使う x の下限', 'Lower x limit used'), p + 'xmin', str(L.get('xmin') or ''))
                L['xmax'] = W(c2.text_input, ui('使う x の上限', 'Upper x limit used'), p + 'xmax', str(L.get('xmax') or ''))
                L['trace_labels'] = W(c3.checkbox, ui('線の右端に名前', 'Name at the right end of the line'), p + 'tl', L.get('trace_labels', False))
                L['reverse_offset'] = W(c4.checkbox, ui('ずらす順を逆に', 'Reverse the offset order'), p + 'rev', L.get('reverse_offset', False))
                L['sort'] = W(st.checkbox, ui('x の順に並べ替えてから線を引く', 'Sort by x before drawing the line'), p + 'sort', L.get('sort', False),
                              help=ui('TG の保持区間のように x が戻るデータでは外す。', 'Turn off for data where x goes back, such as the holding segments of TG.'))
            if L['type'] == 'ticks':
                c1, c2, c3, c4 = st.columns(4)
                with c1:
                    L['tick_style'] = S(ui('形', 'Shape'), p + 'ts', ['vline', 'marker'], L.get('tick_style', 'vline'),
                                        fmt=lambda v: {'vline': ui('縦の短い線', 'Short vertical line'), 'marker': ui('記号', 'Marker')}[v])
                L['tick_y'] = W(c2.number_input, ui('高さ（下端）', 'Height (bottom end)'), p + 'ty', float(L.get('tick_y') or 0.04), step=0.01, format='%.3f',
                                help=ui('軸の高さに対する割合（0 = 下端、1 = 上端）', 'Fraction of the axis height (0 = bottom, 1 = top)'))
                L['tick_h'] = W(c3.number_input, ui('線の長さ', 'Line length'), p + 'th', float(L.get('tick_h') or 0.05), step=0.005, format='%.3f')
                L['tick_gap'] = W(c4.number_input, ui('系列ごとの段差', 'Step between series'), p + 'tg', float(L.get('tick_gap') or 0.06), step=0.01, format='%.3f')
            c1, c2, c3 = st.columns(3)
            with c1:
                L['filter_col'] = S(ui('行を絞る列', 'Filter rows by column'), p + 'fc', opt, L.get('filter_col', ''), fmt=dash)
            with c2:
                if L['filter_col']:
                    vals = fc.natural_order(tables[L['source']][L['filter_col']].map(fc.skey).unique())
                    L['filter_vals'] = MS(ui('使う値', 'Values to use'), p + 'fv', vals, [fc.skey(v) for v in L.get('filter_vals', [])])
            with c3:
                L['axis'] = S(ui('縦軸', 'y axis'), p + 'ax', ['left', 'right'], L.get('axis', 'left'), fmt=lambda v: {'left': ui('左', 'Left'), 'right': ui('右（第 2 軸）', 'Right (second axis)')}[v])
            with popover(ui('このレイヤーだけの見た目', 'Look of this layer only')):
                L['in_legend'] = W(st.checkbox, ui('凡例に出す', 'Show in the legend'), p + 'leg', L.get('in_legend', True))
                L['label_suffix'] = W(st.text_input, ui('凡例の名前に付け足す文字', 'Text added to the legend name'), p + 'suf', L.get('label_suffix', ''), help=ui('例：`, H_{V}`（下付き・上付きは凡例の名前と同じ書き方）', 'e.g. `, H_{V}` (sub- and superscripts are written as in legend names)'))
                use_c = W(st.checkbox, ui('色を上書き', 'Override the colour'), p + 'ovc_on', bool(L.get('ov_color')))
                L['ov_color'] = W(st.color_picker, ui('色', 'Colour'), p + 'ovc', L.get('ov_color') or '#1A1A1A') if use_c else ''
                L['ov_ls'] = S(ui('線種を上書き', 'Override the line style'), p + 'ovls', [''] + list(LS_LABEL), L.get('ov_ls', ''), fmt=lambda v: LS_LABEL.get(v, ui('（系列のまま）', '(as the series)')))
                L['ov_fill'] = S(ui('塗りを上書き', 'Override the fill'), p + 'ovf', ['', 'filled', 'open'], L.get('ov_fill', ''),
                                 fmt=lambda v: {'': ui('（系列のまま）', '(as the series)'), 'filled': ui('塗る', 'Filled'), 'open': ui('白抜き', 'Open')}[v])
                L['ov_marker'] = S(ui('記号を上書き', 'Override the marker'), p + 'ovm', [''] + list(MARKER_LABEL), L.get('ov_marker', ''),
                                   fmt=lambda v: MARKER_LABEL.get(v, ui('（系列のまま）', '(as the series)')))
                L['lw'] = W(st.text_input, ui('線の太さ（pt、空欄＝全体の設定）', 'Line width (pt, empty = global setting)'), p + 'lw', str(L.get('lw') or ''))
                L['ms'] = W(st.text_input, ui('記号の大きさ（pt、空欄＝全体の設定）', 'Marker size (pt, empty = global setting)'), p + 'ms', str(L.get('ms') or ''))
                L['ma'] = W(st.text_input, ui('記号の濃さ（0–1、空欄＝全体の設定）', 'Marker opacity (0–1, empty = global setting)'), p + 'ma', str(L.get('ma') or ''),
                            help=ui('平均 ± 誤差の記号、または散布図の記号の濃さ。空欄なら、平均の記号は「大きさ・文字」タブの「記号の濃さ」、散布図は 0.85。', 'Opacity of the mean ± error markers or of the scatter markers. If empty, the means use “Marker '
                                    'opacity” in the Size & text tab and scatter plots 0.85.'))


# ③ axes
with tabs[2]:
    F = cfg['figure']
    with st.expander(ui('**枠と目盛り**', '**Frame and ticks**'), expanded=True):
        c1, c2, c3 = st.columns([0.3, 0.3, 0.4])
        F['frame_top'] = W(c1.checkbox, ui('上の枠線', 'Top frame line'), 'F|ftop', F.get('frame_top', False))
        F['frame_right'] = W(c2.checkbox, ui('右の枠線', 'Right frame line'), 'F|fright', F.get('frame_right', False),
                             help=ui('第 2 縦軸を使うときは、右の枠は第 2 縦軸の線になります。', 'With a second y axis, the right frame is the line of that axis.'))
        with c3:
            F['tick_dir'] = S(ui('目盛りの向き', 'Tick direction'), 'F|tdir', ['out', 'in'], F.get('tick_dir', 'out'), fmt=lambda v: {'out': ui('外向き', 'Outward'), 'in': ui('内向き', 'Inward')}[v])
        F['mirror_ticks'] = W(st.checkbox, ui('枠線を付けた辺にも目盛りを付ける', 'Ticks on the framed sides too'), 'F|mirror', F.get('mirror_ticks', False))
    need_y2 = any(L.get('axis') == 'right' for L in cfg['layers'])
    axes = [('x', ui('横軸（x）', 'x axis (horizontal)')), ('y', ui('縦軸（y、左）', 'y axis (left)'))] + ([('y2', ui('第 2 縦軸（右）', 'Second y axis (right)'))] if need_y2 else [])
    fc.auto_axis_labels(cfg)              # 書き換えていない軸のラベルは、レイヤーの列の名前
    for a, title in axes:
        A = cfg[a]
        with st.expander(f'**{title}**', expanded=True):
            if A.get('auto_label'):
                ss[f'w|A|{a}|lab'] = A.get('label', '')
            c1, c2 = st.columns([0.78, 0.22])
            A['label'] = W(c1.text_input, ui('ラベル', 'Label') + (ui('（列の名前から）', ' (from the column name)') if A.get('auto_label') else ''), f'A|{a}|lab', A.get('label', ''),
                           on_change=_label_typed, args=(a,),
                           help=ui('はじめはレイヤーの列の名前が入ります。書き換えると、列を替えてもそのまま使います。`$l$` のように `$` で囲むと斜体。', 'Starts as the column name of the layer. Once you edit it, it stays when the column changes. '
                                   'Enclose text in `$`, like `$l$`, for italic. ') + SUBSUP_HELP)
            if not A.get('auto_label'):
                c2.button(ui('列の名前に戻す', 'Back to the column name'), key=f'lab_auto|{a}', on_click=_label_auto, args=(a,), **WIDE)
            if a == 'x':
                A['categorical'] = W(st.checkbox, ui('項目の軸にする（棒グラフなど。x の値を数値でなく名前として並べる）', 'Use as an item axis (bar charts etc.: x values are names, not numbers)'), 'A|x|cat', A.get('categorical', False))
            if a == 'x' and A['categorical']:
                A['categories'] = W(st.text_input, ui('項目の並び順（コンマ区切り、空欄＝データの順）', 'Order of the items (comma-separated, empty = data order)'), 'A|x|cats', A.get('categories', ''))
                A['ticklabels'] = W(st.text_input, ui('表示する名前（コンマ区切り、空欄＝値のまま）', 'Names shown (comma-separated, empty = the values)'), 'A|x|catl', A.get('ticklabels', ''))
                continue
            c1, c2, c3 = st.columns(3)
            A['scale'] = c1.radio(ui('目盛り', 'Scale'), ['linear', 'log'], index=['linear', 'log'].index(A.get('scale', 'linear')),
                                  format_func=lambda v: {'linear': ui('線形', 'Linear'), 'log': 'log'}[v], horizontal=True, key=f'w|A|{a}|sc')
            A['min'] = numtext(ui('最小（空欄＝自動）', 'Min (empty = auto)'), f'A|{a}|min', A.get('min', ''), where=c2)
            A['max'] = numtext(ui('最大（空欄＝自動）', 'Max (empty = auto)'), f'A|{a}|max', A.get('max', ''), where=c3)
            c1, c2, c3 = st.columns(3)
            with c1:
                A['ticks'] = S(ui('主目盛り', 'Major ticks'), f'A|{a}|tk', list(TICK_LABEL), A.get('ticks', 'auto'), fmt=TICK_LABEL.get)
            with c2:
                if A['ticks'] == 'step':
                    A['step'] = numtext(ui('間隔', 'Interval'), f'A|{a}|step', A.get('step', ''), help=ui('log 軸では使えません（値を列挙してください）', 'Not for log axes (list the values instead)'))
                elif A['ticks'] == 'list':
                    A['list'] = W(st.text_input, ui('目盛りの値（コンマ区切り）', 'Tick values (comma-separated)'), f'A|{a}|list', A.get('list', ''), placeholder='9.8, 19.6, 49, 98')
            with c3:
                if A['ticks'] == 'list':
                    A['ticklabels'] = W(st.text_input, ui('表示する文字（任意）', 'Text shown (optional)'), f'A|{a}|tl', A.get('ticklabels', ''), placeholder='9.8, 19.6, 49, 98')
            c1, c2, c3 = st.columns(3)
            with c1:
                A['minor'] = S(ui('補助目盛り', 'Minor ticks'), f'A|{a}|mn', list(MINOR_LABEL), A.get('minor', 'auto'), fmt=MINOR_LABEL.get)
            with c2:
                if A['minor'] == 'n':
                    A['minor_n'] = W(st.number_input, ui('主目盛りの間の本数', 'Number between major ticks'), f'A|{a}|mnn', int(A.get('minor_n') or 4), min_value=1, max_value=9, step=1)
            with c3:
                A['fmt'] = S(ui('目盛りの数字', 'Tick labels'), f'A|{a}|fmt', list(FMT_LABEL), A.get('fmt', 'auto'), fmt=FMT_LABEL.get)
            A['hide_ticklabels'] = W(st.checkbox, ui('目盛りの数字を出さない（強度 a.u. など）', 'Hide the tick labels (e.g. intensity in a.u.)'), f'A|{a}|hide', A.get('hide_ticklabels', False))

# ④ series
keys = fc.series_keys(cfg, tables)
fc.ensure_series(cfg, keys)
with tabs[3]:
    if not keys:
        st.info(ui('レイヤーを作ると、系列（色分けの単位）がここに並びます。', 'Once there are layers, their series (the units of colouring) are listed here.'))
    else:
        c1, c2 = st.columns([0.6, 0.4])
        with c1:
            cfg['palette'] = S(ui('色の組み合わせ', 'Palette'), 'pal', list(fc.PALETTE_NAMES), cfg.get('palette', 'paper'), fmt=fc.palette_label,
                               on_change=_palette_changed, help=ui('選ぶと、すべての系列の色がこの組み合わせになります。「…の濃淡」と viridis は、いちばん薄い色でも白地とのコントラストが 3:1 以上（細い線が印刷やプロジェクターで消えない目安）'
                                                                   'の濃さから始まります。', 'Choosing one recolours every series. The shades (“… shades”) and viridis start dark enough that '
                                                                   'even the lightest colour has at least 3:1 contrast with white (the guide for thin lines to stay '
                                                                   'visible in print or on a projector).'))
        with c2:
            st.markdown('<div style="height:1.9rem"></div>', unsafe_allow_html=True)
            if st.button(ui('色を当てはめ直す', 'Reapply the palette'), help=ui('系列ごとに変えた色を、色の組み合わせに戻します。', 'Returns colours changed per series to the palette.'), **WIDE):
                fc.apply_palette(cfg, keys)
                clear_widgets('w|SR|')
                clear_widgets('w|LS|')
                st.rerun()
        cfg['palette_reverse'] = W(st.checkbox, ui('濃淡（色の順）を逆に', 'Reverse the shades (colour order)'), 'palrev', cfg.get('palette_reverse', False),
                                   on_change=_palette_changed,
                                   help=ui('「…の濃淡」では、1 つ目の系列が濃い色になります。ほかの組み合わせでは、色の順が逆になります。', 'With “… shades”, the first series gets the dark colour. Other palettes are used in reverse order.'))
        cfg['series_order'] = W(st.text_input, ui('系列の順（コンマ区切り、空欄＝レイヤーの順）', 'Series order (comma-separated, empty = layer order)'), 'sorder', cfg.get('series_order', ''),
                                help=ui('下の枠の見出しの名前（`5`、`@L1` など）を並べる。凡例の順・横にずらす順・スペクトルを上へずらす順に使う。', 'List the names in the box headings below (`5`, `@L1`, …). Used for the legend order, the sideways '
                                        'shift and the upward offset of spectra.'))
        st.caption(ui('系列の列を（なし）にしたレイヤーは、レイヤーごとに 1 つの系列になります（`@L1` などと CSV の名前で表示）。その色はレイヤーの中でも変えられます。', 'A layer whose series column is (none) is one series (shown as `@L1` etc. with the CSV file name). '
                      'Its colour can also be changed inside the layer.'))
        thin = fc.thin_series(cfg, tables)      # 線や記号で描く系列（棒だけの系列は薄い色でも見える）
        for sk in keys:                      # 系列ごとに枠で囲み、2 段に並べる（狭い画面でも名前が切れないように）
            with st.container(border=True):
                st.markdown(series_title(sk))
                c = st.columns([0.12, 0.44, 0.44])
                SW(c[0].color_picker, ui('色', 'Colour'), sk, 'color', 'SR')
                color_menu(c[1], sk, 'SR', help=ui('いつも同じ色にしたいときは、名前で選びます。一覧は下の「色の一覧」で足せます。', 'To keep a colour the same everywhere, choose it by name. Add names under “Colour list” below.'))
                SW(c[2].text_input, ui('凡例の名前', 'Legend name'), sk, 'label', 'SR', help=ui('空欄にすると凡例に出しません。', 'Leave empty to keep it out of the legend. ') + SUBSUP_HELP)
                if sk in thin:
                    contrast_note(sk)
                c = st.columns(2)
                SW(c[0].selectbox, ui('記号', 'Marker'), sk, 'marker', 'SR', options=list(MARKER_LABEL), format_func=MARKER_LABEL.get)
                SW(c[0].checkbox, ui('記号を塗る', 'Filled markers'), sk, 'filled', 'SR')
                SW(c[1].selectbox, ui('線', 'Line'), sk, 'ls', 'SR', options=list(LS_LABEL), format_func=LS_LABEL.get)
        with st.expander(ui('色の一覧（名前で選べる色）', 'Colour list (colours chosen by name)')):
            if SHARED:
                st.caption(ui('1 行に 1 つ「名前,#RRGGBB」。温度や組成の名前で登録しておくと、名前で同じ色を選べます。'
                              '「この画面で使う」で、この画面（あなたのブラウザ）だけの一覧になります。次回も使うには「colors.csv を保存」で'
                              '手元に保存し、次に開いたときにここへ貼り付けてください。みんなで使う一覧は、管理者が GitHub の colors.csv を直します。',
                              'One “name,#RRGGBB” per line. Register colours under the names of temperatures or compositions to '
                              'choose the same colour by name. “Use on this screen” makes the list yours, on this screen (your '
                              'browser) only. To use it next time, keep it with “Save colors.csv” and paste it here when you come '
                              'back. The shared list is changed by the administrator in colors.csv on GitHub.'))
            else:
                st.caption(ui('1 行に 1 つ「名前,#RRGGBB」。温度や組成の名前で登録しておくと、どの図でも同じ色を名前で選べます。'
                              '「保存」でアプリのフォルダの colors.csv に書き、次に開いたときも同じ一覧になります。',
                              'One “name,#RRGGBB” per line. Register colours under the names of temperatures or compositions to '
                              'choose the same colour by name in every figure. “Save” writes colors.csv in the app folder, so '
                              'the list is there next time.'))
            ctext = '\n'.join(f'{n},{h}' for n, h in named_colors().items())
            new_text = st.text_area(ui('色の一覧', 'Colour list'), value=ctext, height=200, key='colors_text', label_visibility='collapsed')
            c1, c2 = st.columns([0.4, 0.6])
            if c1.button(ui('この画面で使う', 'Use on this screen') if SHARED else ui('保存', 'Save'), key='colors_save'):
                parsed = fc.parse_colors(new_text)
                if not parsed:
                    c2.error(ui('「名前,#RRGGBB」の行がありません。', 'No “name,#RRGGBB” lines.'))
                elif SHARED:                      # 共有のサーバーではアプリのフォルダに書かない（ほかの人の一覧が変わるため）
                    ss['colors_session'] = parsed
                    ss.pop('colors_text', None)
                    st.rerun()
                else:
                    try:
                        with open(COLORS_FILE, 'wb') as fh:
                            fh.write(colors_csv(parsed))
                        ss.pop('colors_text', None)
                        st.rerun()
                    except OSError as e:
                        c2.error(ui('保存できませんでした：{e}', 'Could not save: {e}', e=e))
            if SHARED:
                c2.download_button(ui('colors.csv を保存', 'Save colors.csv'), colors_csv(named_colors()), file_name='colors.csv',
                                   mime='text/csv', key='colors_dl')

# ⑤ legend & notes
with tabs[4]:
    Lg = cfg['legend']
    c1, c2, c3 = st.columns(3)
    Lg['show'] = W(c1.checkbox, ui('凡例を出す', 'Show the legend'), 'LG|show', Lg.get('show', True))
    with c2:
        Lg['loc'] = S(ui('位置', 'Position'), 'LG|loc', list(LOC_LABEL), Lg.get('loc', 'best'), fmt=LOC_LABEL.get,
                      help=ui('「自動」は図の中の空いている場所。100% 積み上げ棒は枠の中に空きがないので、自動なら枠の外（右）に置きます。', '“Auto” uses an empty spot in the plot. 100% stacked bars leave no room inside, so “Auto” puts '
                              'the legend outside (right).'))
    Lg['ncol'] = W(c3.number_input, ui('列の数', 'Columns'), 'LG|ncol', int(Lg.get('ncol') or 1), min_value=1, max_value=6, step=1)
    c1, c2 = st.columns(2)
    Lg['title'] = W(c1.text_input, ui('凡例の見出し', 'Legend title'), 'LG|title', Lg.get('title', ''), help=SUBSUP_HELP)
    Lg['frame'] = W(c2.checkbox, ui('枠で囲む', 'Frame'), 'LG|frame', Lg.get('frame', False))
    st.divider()
    ND = cfg['n_display']
    st.markdown(ui('**n の表示**\u3000各点・凡例に書くかは「レイヤー」タブの「n を書く」で選びます。', '**Showing n**  Whether n is written at each point or in the legend is set with “Show n” in the '
                   'Layers tab.'))
    c1, c2, c3 = st.columns(3)
    with c1:
        ND['style'] = S(ui('各点に書くときの形', 'Format at each point'), 'ND|style', list(N_STYLE_LABEL), ND.get('style', 'eq'), fmt=N_STYLE_LABEL.get)
    ND['note'] = W(c2.checkbox, ui('「Mean ± s.d., n = …」を図に書く', 'Write “Mean ± s.d., n = …” in the figure'), 'ND|note', ND.get('note', False),
                   help=ui('平均 ± 誤差・棒グラフのレイヤーで実際に描いた n の範囲と誤差の種類から作ります。値を除外すると数字も変わります。', 'Made from the range of n actually drawn in mean ± error and bar layers and from the error type. '
                           'Excluding values changes the numbers too.'))
    with c3:
        ND['loc'] = S(ui('注記の位置', 'Note position'), 'ND|loc', list(NOTE_LOC_LABEL), ND.get('loc', 'lower right'), fmt=NOTE_LOC_LABEL.get)
    ND['unit'] = W(st.text_input, ui('n が数えているもの（英語。注記とキャプション用の文に入る）', 'What n counts (in English; used in the note and the caption sentence)'), 'ND|unit', ND.get('unit', ''),
                   placeholder='indentations / cracks / specimens')
    if any(L.get('fit', 'none') != 'none' and L.get('fit_eq') for L in cfg['layers']):
        c1, _ = st.columns([0.4, 0.6])
        with c1:
            cfg['fit_eq_loc'] = S(ui('近似式の位置', 'Fit equation position'), 'FIT|loc', list(NOTE_LOC_LABEL), cfg.get('fit_eq_loc', 'upper right'), fmt=NOTE_LOC_LABEL.get)
    st.divider()
    cfg['annotations'] = W(st.text_area, ui('文字を書き込む（1 行に 1 つ：x | y | 文字 | axes か data | left・center・right）', 'Text in the figure (one per line: x | y | text | axes or data | left, center or right)'), 'NT|ann',
                           cfg.get('annotations', ''), height=90, placeholder='0.98 | 0.05 | Mean ± s.d., $n$ = 9 | axes | right',
                           help=ui('axes：軸の枠に対する位置（0〜1）。data：データの座標。文字では、', 'axes: position within the axes frame (0–1). data: data coordinates. In the text: ') + SUBSUP_HELP)
    cfg['spans'] = W(st.text_area, ui('範囲を塗る（1 行に 1 つ：x の始め | 終わり | 色 | 濃さ 0〜1 | 文字）', 'Shaded ranges (one per line: x start | end | colour | opacity 0–1 | text)'), 'NT|span', cfg.get('spans', ''),
                     height=70, placeholder='0 | 0.3 | #F4CCCC | 0.6 | DZ')
    c1, c2 = st.columns(2)
    cfg['hlines'] = W(c1.text_input, ui('横線を引く y（コンマ区切り）', 'Horizontal lines at y (comma-separated)'), 'NT|hl', cfg.get('hlines', ''), placeholder='0')
    cfg['vlines'] = W(c2.text_input, ui('縦線を引く x（コンマ区切り）', 'Vertical lines at x (comma-separated)'), 'NT|vl', cfg.get('vlines', ''))

# ⑥ size & type
with tabs[5]:
    F = cfg['figure']
    st.markdown(ui('**図の大きさ（mm）**\u3000Nature 系：1 段 88 mm、1.5 段 120 mm、2 段 180 mm', '**Figure size (mm)**  Nature journals: single column 88 mm, 1.5 columns 120 mm, double column '
                   '180 mm'))
    c1, c2 = st.columns(2)
    F['width_mm'] = W(c1.number_input, ui('幅', 'Width'), 'F|w', float(F.get('width_mm', 88)), min_value=20.0, max_value=250.0, step=1.0)
    F['height_mm'] = W(c2.number_input, ui('高さ', 'Height'), 'F|h', float(F.get('height_mm', 60)), min_value=20.0, max_value=250.0, step=1.0)
    b = st.columns([0.24, 0.17, 0.17, 0.17, 0.17, 0.08])
    b[0].caption(ui('幅を合わせる（mm）', 'Set the width (mm)'))

    def _set_width(v):
        ss['w|F|w'] = float(v)
    for col, wv in zip(b[1:], (60, 88, 120, 180)):          # 古い Streamlit では狭いと文字が縦に折れるので、数字だけ
        col.button(str(wv), key=f'wp{wv}', on_click=_set_width, args=(wv,), **WIDE)
    st.markdown(ui('**文字**', '**Text**'))
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        F['font'] = S(ui('書体', 'Font'), 'F|font', ['Arial', 'Helvetica', 'Liberation Sans', 'DejaVu Sans'], F.get('font', 'Arial'),
                      help=ui('Arial がない環境では、字幅が同じ Liberation Sans で描きます。SVG は Arial 指定で保存。', 'Where Arial is missing, Liberation Sans (same character widths) is used. SVG files name Arial '
                              'as the font.'))
    F['label_pt'] = W(c2.number_input, ui('軸ラベル（pt）', 'Axis labels (pt)'), 'F|lab', float(F.get('label_pt', 7)), min_value=4.0, max_value=14.0, step=0.5)
    F['tick_pt'] = W(c3.number_input, ui('目盛り（pt）', 'Tick labels (pt)'), 'F|tick', float(F.get('tick_pt', 6.5)), min_value=4.0, max_value=14.0, step=0.5)
    F['legend_pt'] = W(c4.number_input, ui('凡例（pt）', 'Legend (pt)'), 'F|leg', float(F.get('legend_pt', 6.5)), min_value=4.0, max_value=14.0, step=0.5)
    c1, c2, c3, c4 = st.columns(4)
    F['note_pt'] = W(c1.number_input, ui('書き込み・n（pt）', 'Notes and n (pt)'), 'F|note', float(F.get('note_pt', 6)), min_value=4.0, max_value=14.0, step=0.5)
    F['letter'] = W(c2.text_input, ui('パネル記号', 'Panel letter'), 'F|let', F.get('letter', ''), placeholder='a')
    F['letter_pt'] = W(c3.number_input, ui('記号（pt）', 'Letter (pt)'), 'F|letpt', float(F.get('letter_pt', 8)), min_value=4.0, max_value=16.0, step=0.5)
    F['letter_x'] = W(c4.number_input, ui('記号の横位置', 'Letter position (x)'), 'F|letx', float(F.get('letter_x', -0.15)), min_value=-0.6, max_value=0.2, step=0.01)
    st.markdown(ui('**線と記号**', '**Lines and markers**'))
    c1, c2, c3, c4 = st.columns(4)
    F['axes_lw'] = W(c1.number_input, ui('軸の線（pt）', 'Axis lines (pt)'), 'F|alw', float(F.get('axes_lw', 0.5)), min_value=0.1, max_value=3.0, step=0.1)
    F['line_lw'] = W(c2.number_input, ui('データの線（pt）', 'Data lines (pt)'), 'F|llw', float(F.get('line_lw', 0.9)), min_value=0.1, max_value=5.0, step=0.1)
    F['marker_size'] = W(c3.number_input, ui('記号（pt）', 'Marker (pt)'), 'F|ms', float(F.get('marker_size', 3.8)), min_value=0.5, max_value=15.0, step=0.1)
    F['marker_alpha'] = W(c4.number_input, ui('記号の濃さ', 'Marker opacity'), 'F|malpha', float(F.get('marker_alpha', 1.0)), min_value=0.1, max_value=1.0, step=0.05,
                          help=ui('平均 ± 誤差の記号（平均の点）の濃さ。1 で不透明。2 つの系列の平均が重なるときは 0.6 前後にすると、下の記号が透けて見える。誤差棒と線は不透明のまま。レイヤーごとには「このレイヤーだけの見た目」で変える。', 'Opacity of the mean ± error markers (the means). 1 = opaque. When the means of two series overlap, '
                                  'about 0.6 lets the lower marker show through. Error bars and lines stay opaque. Per layer: “Look '
                                  'of this layer only”.'))
    c1, c2, c3, c4 = st.columns(4)
    F['capsize'] = W(c1.number_input, ui('誤差棒の端（pt）', 'Error bar caps (pt)'), 'F|cap', float(F.get('capsize', 1.5)), min_value=0.0, max_value=6.0, step=0.1)
    F['point_size'] = W(c2.number_input, ui('個々の点（pt）', 'Individual points (pt)'), 'F|ps', float(F.get('point_size', 2.2)), min_value=0.5, max_value=10.0, step=0.1)
    F['point_alpha'] = W(c3.number_input, ui('個々の点の濃さ', 'Individual point opacity'), 'F|pa', float(F.get('point_alpha', 0.45)), min_value=0.05, max_value=1.0, step=0.05)
    F['jitter'] = W(c4.number_input, ui('個々の点の横の散らし', 'Sideways scatter of individual points'), 'F|jit', float(F.get('jitter', 0.0)), min_value=0.0, max_value=0.1, step=0.002,
                    format='%.3f', help=ui('個々の値が重ならないように、x の左右へわざと散らす幅（軸の幅に対する割合）。0 にすると x の真上に一列に並ぶ。平均の点の位置は変わらない。x が設定値（荷重・温度など）のときだけ効きます。'
                                           'グループの列を使う（x が測った値の）レイヤーと散布図は散らしません。', 'How far individual values are scattered left and right of x so that they do not overlap (fraction '
                                           'of the axis width). 0 lines them up right at x. The means do not move. Only for set x values '
                                           '(load, temperature, …); layers with a group column (measured x) and scatter plots are never '
                                           'scattered.'))
    c1, c2 = st.columns(2)
    F['grid'] = W(c2.checkbox, ui('格子線', 'Grid lines'), 'F|grid', F.get('grid', False))
    F['dpi'] = W(c1.number_input, ui('PNG・TIFF の解像度（dpi）', 'PNG/TIFF resolution (dpi)'), 'F|dpi', int(F.get('dpi', 600)), min_value=150, max_value=1200, step=50)

# ---------------------------------------------------------------------------------------------- render
# Streamlit 1.52 以降は、保存ボタンを押したときに書き出す（操作のたびに PDF・SVG・600 dpi の PNG・TIFF を作らない。
# 共有のサーバーで描画は 1 つずつなので、何人かが同時に使っても待ち時間が短くなる）。古い版では、これまでどおり先に作る。
LAZY_EXPORT = tuple(int(x) for x in re.findall(r'\d+', st.__version__)[:2]) >= (1, 52)


def export_later(cfg_snap, tables_snap, fmt, dpi):
    """The file of the figure as it is now, made when the save button is pressed (runs outside the page script)."""
    def build():
        import matplotlib.pyplot as plt
        with LOCK:
            try:
                f_, _, _ = fc.render(cfg_snap, tables_snap)
                if fmt == 'tif':
                    return fc.png_to_tiff(fc.export_bytes(f_, 'png', dpi), dpi)
                return fc.export_bytes(f_, fmt, dpi)
            finally:
                plt.close('all')
    return build


fig = plotted = notes = None
render_error = None
outs = {}
if tables and cfg['layers']:
    with LOCK:
        try:
            fig, plotted, notes = fc.render(cfg, tables)
            disp0 = int(ss.get('disp', 640))
            png_prev = fc.export_bytes(fig, 'png', max(72, int(disp0 / (cfg['figure']['width_mm'] / 25.4))))
            dpi = int(cfg['figure'].get('dpi', 600))
            if LAZY_EXPORT:
                _snap = copy.deepcopy(cfg)
                outs = {fmt: export_later(_snap, tables, fmt, dpi) for fmt in ('pdf', 'svg', 'png', 'tif')}
            else:
                outs = {fmt: fc.export_bytes(fig, fmt, dpi) for fmt in ('pdf', 'svg', 'png')}
                outs['tif'] = fc.png_to_tiff(outs['png'], dpi)
        except Exception as e:  # noqa: BLE001
            fig = None
            render_error = e
        finally:
            import matplotlib.pyplot as plt
            plt.close('all')

# ⑦ checks
with tabs[6]:
    if not (tables and cfg['layers']):
        st.info(ui('レイヤーを作るとチェックが走ります。', 'The checks run once there are layers.'))
    else:
        c1, c2 = st.columns(2)
        z = c1.number_input(ui('飛び離れた値とみなす修正 z', 'Modified z for outliers'), value=3.5, min_value=2.0, max_value=10.0, step=0.5, key='chk_z',
                            help=ui('同じ系列・同じ x の値の中央値と MAD から計算（値が 5 個以上のときだけ）', 'From the median and MAD of the values with the same series and x (only with 5 or more values)'))
        r = c2.number_input(ui('単位内のばらつきとみなす 最長／最短', 'Longest/shortest ratio flagged as spread within a unit'), value=1.5, min_value=1.1, max_value=5.0, step=0.1, key='chk_r')
        chk = fc.run_checks(cfg, tables, z, r)
        if ss.get('_excl_msg'):
            st.success(ss.pop('_excl_msg'))
        if chk.empty:
            st.success(ui('気になる値はありません。', 'Nothing stands out.'))
        else:
            st.write(ui('{len_chk} 件見つかりました。ここでは知らせるだけで、図や平均の計算からは何も外していません（数値でない・空のセルだけは描けないので、自動で飛ばしています）。外すと決めた値は、'
                        '下で選んで「選んだ行を除外する」を押します。行番号は CSV の見出しを 1 行目として数えています（Excel の行番号と同じ）。', '{len_chk} found. This only reports them: nothing has been removed from the figure or the means '
                        '(only non-numeric or empty cells are skipped, as they cannot be drawn). To leave values out, '
                        'choose them below and press “Exclude the chosen rows”. Row numbers count the CSV header as row '
                        '1 (as in Excel).', len_chk=len(chk)))
            st.dataframe(chk.rename(columns={'layer': ui('レイヤー', 'Layer'), 'check': ui('内容', 'Check'), 'csv': 'CSV', 'csv_row': ui('行', 'Row'), 'column': ui('列', 'Column'), 'series': ui('系列', 'Series'),
                                             'unit': ui('単位', 'Unit'), 'value': ui('値', 'Value'), 'detail': ui('詳しく', 'Details')}), height=320, hide_index=True)
            st.download_button(ui('チェック結果（CSV）', 'Check results (CSV)'), chk.to_csv(index=False).encode('utf-8-sig'), file_name='checks.csv', mime='text/csv')
            cand = {}
            for _, rr in chk.iterrows():           # 1 行で決まるもの（飛び離れた値など）だけ、選んで外せる
                if rr['csv'] and rr['csv_row'] and ' ' not in rr['csv_row'] and rr['check'] != ui('数値でない／空', 'Not numeric / empty'):
                    tok = f"{rr['csv_row']}:{rr['column']}" if rr['column'] else rr['csv_row']
                    cand[(rr['csv'], tok)] = (ui('{csv} の {csv_row} 行目', '{csv}, row {csv_row}', csv=rr['csv'], csv_row=rr['csv_row']) + (ui('（{column}）', ' ({column})', column=rr['column']) if rr['column'] else '') +
                                              ui('：{check}\u3000x = {x}、値 {value}', ': {check}  x = {x}, value {value}', check=rr['check'], x=rr['x'], value=rr['value']))
            if cand:
                ck = list(cand)
                ss['chk_pick'] = [v for v in ss.get('chk_pick', []) if v in ck]
                pick = st.multiselect(ui('除外する値を選ぶ', 'Choose values to exclude'), ck, format_func=lambda v: cand.get(v, str(v)), key='chk_pick', placeholder=ui('選ぶ…', 'Choose…'))
                st.button(ui('選んだ行を除外する', 'Exclude the chosen rows'), on_click=_exclude_rows, args=(list(pick),), disabled=not pick, key='chk_ex')
        cur = [(n, t) for n in tables for t in fc.exclusion_tokens((cfg['sources'].get(n) or {}).get('exclude', ''))]
        if cur:
            st.markdown(ui('**除外している値**', '**Excluded values**'))
            ss['chk_back'] = [v for v in ss.get('chk_back', []) if v in cur]
            def excl_name(v):                  # ('a.csv', '60:l_98N') → a.csv の 60 行目（l_98N）
                row, _, col = v[1].partition(':')
                if col:
                    return ui('{csv} の {row} 行目（{col}）', '{csv}, row {row} ({col})', csv=v[0], row=row, col=col)
                return ui('{csv} の {row} 行目', '{csv}, row {row}', csv=v[0], row=row)
            back = st.multiselect(ui('戻す値を選ぶ', 'Choose values to restore'), cur, format_func=excl_name,
                                  key='chk_back', placeholder=ui('選ぶ…', 'Choose…'))
            st.button(ui('選んだ値を戻す', 'Restore the chosen values'), on_click=_include_rows, args=(list(back),), disabled=not back, key='chk_in')
        if plotted:
            st.markdown(ui('**描いた値**', '**Values drawn**'))
            for name, d in plotted:
                st.caption(name)
                st.dataframe(d, height=200, hide_index=True)

with right:
    st.subheader(ui('プレビュー', 'Preview'))
    if not tables:
        st.info(ui('CSV をアップロードすると、ここに図が出ます。', 'The figure appears here once you upload CSV files.'))
    elif not cfg['layers']:
        st.info(ui('「レイヤー」タブで描くものを足してください。', 'Add something to draw in the Layers tab.'))
    elif fig is None:
        st.error(ui('描けませんでした：{render_error}', 'Could not draw: {render_error}', render_error=render_error))
    else:
        disp = st.slider(ui('表示の大きさ', 'Display size'), 300, 1200, 640, 20, key='disp')
        if OLD_ST:
            st.image(png_prev, use_column_width='auto')
        else:
            st.image(png_prev, width=disp)
        st.caption(ui('{width_mm:.0f} × {height_mm:.0f} mm（この大きさで保存されます）', '{width_mm:.0f} × {height_mm:.0f} mm (saved at this size)', width_mm=cfg['figure']['width_mm'], height_mm=cfg['figure']['height_mm']))
        for n in notes or []:
            st.warning(n)
        for n, ex in [(n, ex) for n, ex in _excl_check.items() if n in raws]:
            st.warning(ui('「{n}」は中身が変わりましたが、「除外する行」（{ex}）は前のままです。「データ」タブで確認してください。', '“{n}” has new contents, but “Rows to exclude” ({ex}) has not changed. Check it in the Data tab.', n=n, ex=ex))
        base = st.text_input(ui('保存するファイル名', 'File name'), value='figure', key='basename')
        dpi = int(cfg['figure'].get('dpi', 600))
        c1, c2, c3, c4 = st.columns(4)
        c1.download_button('PDF', outs['pdf'], file_name=base + '.pdf', mime='application/pdf', **WIDE)
        c2.download_button('SVG', outs['svg'], file_name=base + '.svg', mime='image/svg+xml', **WIDE)
        c3.download_button('PNG', outs['png'], file_name=base + '.png', mime='image/png', **WIDE)
        c4.download_button('TIFF', outs['tif'], file_name=base + '.tif', mime='image/tiff', **WIDE)
        st.caption(ui('PNG・TIFF は {dpi} dpi（「大きさ・文字」タブで変更）。TIFF は RGB・LZW 圧縮・透明なし。', 'PNG and TIFF at {dpi} dpi (change it in the Size & text tab). TIFF: RGB, LZW compression, no '
                      'transparency.', dpi=dpi))
        c1, c2 = st.columns(2)
        if plotted:
            allp = pd.concat([d.assign(table=n) for n, d in plotted], ignore_index=True)
            for c in ('n', 'n_members'):               # 表をつなぐと空欄のせいで 3.0 になるので、整数に戻す
                if c in allp:
                    allp[c] = pd.to_numeric(allp[c], errors='coerce').round().astype('Int64')
            c1.download_button(ui('描いた値（CSV）', 'Values drawn (CSV)'), allp.to_csv(index=False).encode('utf-8-sig'), file_name=base + '_plotted.csv',
                               mime='text/csv', **WIDE)
        c2.download_button(ui('この図の設定（.json）', 'Settings of this figure (.json)'), json.dumps(cfg, ensure_ascii=False, indent=1).encode('utf-8'),
                           file_name=base + '.json', mime='application/json', **WIDE)
        fitd = [d for n_, d in (plotted or []) if n_.endswith(ui('（近似）', ' (fit)'))]
        if fitd:
            with st.expander(ui('近似の結果（係数と R²）', 'Fit results (coefficients and R²)'), expanded=True):
                ft = pd.concat(fitd, ignore_index=True)
                ft = ft.assign(model=ft['model'].map(lambda m: FIT_LABEL.get(m, m)))
                st.dataframe(ft[['layer', 'label', 'model', 'a', 'b', 'c', 'r2', 'n', 'x_min', 'x_max', 'equation']]
                             .rename(columns={'layer': ui('レイヤー', 'Layer'), 'label': ui('凡例の名前', 'Legend name'), 'model': ui('式', 'Model'), 'r2': 'R²', 'n': ui('点の数', 'Points'),
                                              'x_min': ui('x の最小', 'x min'), 'x_max': ui('x の最大', 'x max'), 'equation': ui('近似式', 'Equation')}),
                             hide_index=True)
                st.caption(ui('べき乗・指数は対数をとって直線で当てはめた値です。R² は、当てはめに使った点で計算しています。', 'Power and exponential fits are made on logarithms. R² is calculated from the points used for '
                              'the fit.'))
        nt = fc.n_table(plotted)
        if not nt.empty:
            with st.expander(ui('n の一覧とキャプション用の文', 'n per point and caption sentence'), expanded=False):
                pv = nt.pivot_table(index=['layer', 'label'], columns='x', values='n', aggfunc='first', sort=False)
                pv = pv[sorted(pv.columns, key=fc.natural_key)]
                pv.columns = [f'{c:g}' if isinstance(c, float) else str(c) for c in pv.columns]
                pv = pv.map(lambda v: '' if pd.isna(v) else str(int(v))).reset_index()
                pv = pv.rename(columns={'layer': ui('レイヤー', 'Layer'), 'label': ui('凡例の名前', 'Legend name')})
                st.caption(ui('点ごとの n（列 = x の値）', 'n per point (columns = x values)'))
                st.dataframe(pv, hide_index=True)
                st.text_area(ui('キャプションに使える文（n が数えているものは「凡例・注記」タブで指定）', 'Sentence for the figure caption (set what n counts in the Legend & notes tab)'), value=fc.n_caption(cfg, plotted), height=90)
        st.caption(ui('作業ファイル（.zip）があれば `python3 figcore.py 作業ファイル.zip`、設定（.json）と CSV なら `python3 figcore.py 設定.json '
                      'データ.csv` で、同じ図を手元で作り直せます（Mac なら本物の Arial で書き出し）。', 'With a work file (.zip), `python3 figcore.py work.zip`; with settings (.json) and CSV files, '
                      '`python3 figcore.py settings.json data.csv` redraws the same figure on your computer (with the '
                      'real Arial on a Mac).'))


# ---------------------------------------------------------------------------------------------- sidebar: loaded CSVs
if files:
    used_by = {}
    for i, L in enumerate(cfg['layers']):
        if L.get('source'):
            used_by.setdefault(L['source'], []).append(str(i + 1))
    rows_ = []
    for n in files:
        rows = ui('{v} 行', '{v} rows', v=len(raws[n])) if n in raws else ui('読めません', 'unreadable')
        use = (ui('レイヤー ', 'layer ') + ui('・', ', ').join(used_by[n])) if n in used_by else ui('未使用', 'not used')
        kept = n not in uploaded         # アップロード欄にない CSV（デモ、作業ファイル）は × で外す
        demo = ui('（デモ）', ' (demo)') if kept and n in ss.demo_files else ''
        rows_.append((n, kept, f"`{n}`{demo}　{rows}　→ {use}"))
    if not any(k for _, k, _ in rows_):
        files_box.markdown(ui('**読み込んでいる CSV**\n', '**Loaded CSV files**\n') + '\n'.join('- ' + t for _, _, t in rows_))
    else:
        files_box.markdown(ui('**読み込んでいる CSV**', '**Loaded CSV files**'))
        for n, kept, t in rows_:
            c1, c2 = files_box.columns([0.8, 0.2])
            c1.markdown(t)
            if kept:
                c2.button('×', key=f'dropk|{n}', on_click=_drop_kept, args=(n,), help=ui('この CSV を外す', 'Remove this CSV file'), **WIDE)
        files_box.caption(ui('× の付いた CSV は、作業ファイル（またはデモの合成データ）から開いたもので、アップロード欄には出ません。', 'CSV files with × come from a work file (or the synthetic demo data) and are not in the upload '
                             'box.'))
    if OLD_ST and len(up or []) > 3:
        files_box.caption(ui('アップロード欄には 3 つずつしか出ません。古いものは欄の下の「Showing page 1 of …」の先にあります。', 'The upload box shows three files at a time. Older ones are under “Showing page 1 of …” below '
                             'the box.'))


# ---------------------------------------------------------------------------------------------- sidebar: save
# 最後に書くので、この回に変えた設定まで入る
_base = re.sub(r'[\\/:*?"<>|]+', '_', str(ss.get('basename') or 'figure')).strip() or 'figure'
save_box.download_button(ui('作業ファイルを保存（設定＋CSV、.zip）', 'Save work file (settings + CSV files, .zip)'), fc.make_work_zip(cfg, files), file_name=f'{_base}_work.zip',
                         mime='application/zip', help=ui('設定と、読み込んでいる CSV をまとめて保存します。下の「作業ファイル（.zip）または設定（.json）を開く」で続きから再開できます。', 'Saves the settings together with the loaded CSV files. Carry on later with “Open a work file '
                                                         '(.zip) or settings (.json)” below.'), **WIDE)
save_box.download_button(ui('今の設定を保存（.json）', 'Save the current settings (.json)'), json.dumps(cfg, ensure_ascii=False, indent=1).encode('utf-8'),
                         file_name=f'{_base}_settings.json', mime='application/json',
                         help=ui('見た目の設定だけ（CSV は入りません）。別のデータに同じ見た目を使い回すとき。', 'The look only (no CSV files), to reuse the same look on other data.'), **WIDE)


# ---------------------------------------------------------------------------------------------- keyboard shortcuts
# 文字を選んで Ctrl+I（斜体）・Ctrl+B（太字）・Ctrl+,（下付き）・Ctrl+.（上付き）。Word の Ctrl+= / Ctrl+Shift+= も。
# 入力欄に <i>…</i>、<b>…</b>、_{…}、^{…} を書き足すだけなので、どの版の Streamlit でも同じ設定ファイルになる。
# 新しい Streamlit で components.html がなくなっても、ショートカットが効かないだけで動くように
if hasattr(components, 'html'):
  components.html("""<script>
(function () {
  const P = window.parent;
  if (!P || !P.document) return;
  const handler = function (e) {
    const el = e.target;
    if (!el || !(el.tagName === 'TEXTAREA' || (el.tagName === 'INPUT' && (el.type === 'text' || !el.type)))) return;
    if (el.getAttribute('role') === 'combobox' || (el.closest && el.closest('[data-baseweb="select"]'))) return;
    if (!(e.ctrlKey || e.metaKey) || e.altKey) return;
    const k = e.key;
    let open = null, close = null;
    if (k === 'i' || k === 'I') { open = '<i>'; close = '</i>'; }
    else if (k === 'b' || k === 'B') { open = '<b>'; close = '</b>'; }
    else if (k === ',' || k === '=') { open = '_{'; close = '}'; }
    else if (k === '.' || k === '+') { open = '^{'; close = '}'; }
    if (!open) return;
    e.preventDefault();
    e.stopPropagation();
    const v = el.value || '';
    const a = (el.selectionStart == null) ? v.length : el.selectionStart;
    const b = (el.selectionEnd == null) ? a : el.selectionEnd;
    const nv = v.slice(0, a) + open + v.slice(a, b) + close + v.slice(b);
    const proto = el.tagName === 'TEXTAREA' ? P.HTMLTextAreaElement.prototype : P.HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, nv);
    el.dispatchEvent(new P.Event('input', { bubbles: true }));
    el.setSelectionRange(a + open.length, b + open.length);
  };
  if (P.__figappKeys) P.document.removeEventListener('keydown', P.__figappKeys, true);
  P.__figappKeys = handler;
  P.document.addEventListener('keydown', handler, true);
})();
</script>""", height=0)
