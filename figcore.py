# -*- coding: utf-8 -*-
"""figcore — drawing engine of the figure app (no Streamlit inside).

settings (dict, saved as JSON) + CSV tables  →  matplotlib figure + plotted values + data checks.

Layer types
    line        x–y traces per series (spectra, TG, TMA); optional normalisation, vertical offset, x-range, trace labels
    scatter     points per series
    mean_error  mean ± error per (series, x); with a "unit" column the values are first averaged within each unit
                (e.g. the 4 corner cracks of one indentation), then mean ± SD/SE/95 % CI over units; unit values as dots
    bar         same statistics as mean_error, drawn as (grouped) bars on a categorical or numeric x
    ticks       reference positions (e.g. ICDD lines) drawn as short vertical sticks or symbols in a row per series

Series
    A layer with a series column gives one series per value of that column (values shared between layers).
    A layer without one is a series of its own (key '@' + layer uid, default legend name = its CSV file name),
    so two CSV files drawn as two layers get two colours. Series indices (side-by-side shift, bar slots, vertical
    offset of spectra, rows of ticks) count all series drawn by layers of the same type.

Command line (same output as the app; useful on a Mac with Arial):
    python3 figcore.py settings.json data1.csv [data2.csv ...] [--out out] [--name Fig]
    python3 figcore.py work.zip [--out out]        # the app's 作業ファイル (settings + CSVs)
"""
import copy, csv, io, json, math, os, re, sys, threading, unicodedata, zipfile
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import rcParams
from matplotlib.colors import to_rgba
from matplotlib.lines import Line2D
from matplotlib.ticker import (AutoMinorLocator, FixedLocator, FuncFormatter, LogLocator, MultipleLocator,
                               NullFormatter, NullLocator)
import matplotlib.font_manager as fm
try:                                   # 画面の言語（アプリから使うとき）。単独で使うときは日本語
    from i18n import ui
except ImportError:
    def ui(ja, en, **kw):
        return ja.format(**kw) if kw else ja

# matplotlib の rcParams と pyplot は 1 つのプロセスで共有なので、描画は 1 つずつ（同時に使う人がいるサーバーで）
RENDER_LOCK = threading.RLock()

MM = 1 / 25.4
VERSION = 1
APP_VERSION = '1.14'

# ------------------------------------------------------------------------------------------------ defaults
PALETTES = {
    'paper': ['#0072B2', '#D55E00', '#009E73', '#CC79A7', '#E69F00', '#56B4E9', '#000000', '#F0E442'],
    'okabe_ito': ['#E69F00', '#56B4E9', '#009E73', '#F0E442', '#0072B2', '#D55E00', '#CC79A7', '#000000'],
    'blues': 'Blues', 'oranges': 'Oranges', 'greens': 'Greens', 'greys': 'Greys', 'viridis': 'viridis',
    'tab10': ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf'],
}
PALETTE_NAMES = {'paper': '論文用（青・朱・緑…）', 'okabe_ito': 'Okabe–Ito', 'blues': '青の濃淡', 'oranges': '朱の濃淡',
                 'greens': '緑の濃淡', 'greys': '灰の濃淡', 'viridis': 'viridis', 'tab10': 'matplotlib 標準'}


def palette_label(name):
    """The name of a palette in the screen language (PALETTE_NAMES keeps the Japanese one)."""
    return {
        'paper': ui('論文用（青・朱・緑…）', 'Paper (blue, vermilion, green…)'),
        'blues': ui('青の濃淡', 'Blue shades'),
        'oranges': ui('朱の濃淡', 'Vermilion shades'),
        'greens': ui('緑の濃淡', 'Green shades'),
        'greys': ui('灰の濃淡', 'Grey shades'),
        'tab10': ui('matplotlib 標準', 'matplotlib default'),
    }.get(name, PALETTE_NAMES.get(name, str(name)))


# 白地に対する最低のコントラスト比。細い線や小さな記号が印刷・プロジェクターで消えない目安（WCAG 2.1 の非テキストの基準 3:1）。
# 濃淡の組み合わせは、いちばん薄い色でもこれ以上の濃さから始める
MIN_CONTRAST = 3.0
# 名前で選べる色（アプリのフォルダの colors.csv があれば、そちらを使う）。Okabe–Ito の色と灰色
NAMED_COLORS = {'青': '#0072B2', '朱': '#D55E00', '緑': '#009E73', '赤紫': '#CC79A7', '橙': '#E69F00', '空色': '#56B4E9',
                '黄': '#F0E442', '黒': '#000000', '濃い灰': '#4D4D4D', '灰': '#999999', '赤': '#D62728', '紫': '#9467BD', '茶': '#8C564B'}
_EMOJI_RGB = {'🟥': (214, 40, 40), '🟧': (240, 140, 20), '🟨': (245, 215, 50), '🟩': (40, 160, 80), '🟦': (30, 110, 210),
              '🟪': (150, 80, 190), '🟫': (130, 80, 50), '⬛': (25, 25, 25), '⬜': (235, 235, 235)}


def parse_colors(text):
    """'名前,#RRGGBB' (or 名前 = #RRGGBB) per line → {name: '#RRGGBB'}; lines that do not parse are skipped."""
    out = {}
    for line in str(text or '').splitlines():
        m = re.match(r'\s*(.+?)\s*[,=\t]\s*(#?[0-9A-Fa-f]{6})\s*$', line)
        if m and m.group(1).lower() not in ('name', '名前'):
            out[m.group(1)] = '#' + m.group(2).lstrip('#').upper()
    return out


def load_colors(path):
    """The named colours: colors.csv in the app folder if it has any, otherwise NAMED_COLORS."""
    try:
        named = parse_colors(open(path, encoding='utf-8-sig').read())
    except OSError:
        named = {}
    return named or dict(NAMED_COLORS)


def color_emoji(hexcol):
    """The coloured square closest to a colour (a hint in the colour list; the real colour is the code)."""
    try:
        r, g, b = (int(hexcol.lstrip('#')[i:i + 2], 16) for i in (0, 2, 4))
    except (ValueError, AttributeError):
        return '▫️'
    if max(r, g, b) - min(r, g, b) < 40:                      # 灰色は白か黒の四角で
        return '⬛' if (r + g + b) / 3 < 110 else '⬜'
    return min(_EMOJI_RGB, key=lambda e: sum((a - c) ** 2 for a, c in zip(_EMOJI_RGB[e], (r, g, b))))


MARKERS = ['o', 's', '^', 'D', 'v', 'p', 'h', '<', '>', '*', 'x', '+', 'none']
LINESTYLES = {'実線': '-', '破線': '--', '点線': ':', '一点鎖線': '-.', 'なし': 'none'}
SERIES_FIELDS = {'label': '', 'color': '#0072B2', 'marker': 'o', 'filled': True, 'ls': '-'}
N_STYLE = {'eq': '$n$ = {n}', 'paren': '({n})', 'num': '{n}'}
ERR_TEXT = {'sd': 's.d.', 'se': 's.e.m.', 'ci95': '95% CI', 'minmax': 'range', 'column': 'error', 'none': ''}
NOTE_POS = {'lower right': (0.98, 0.04, 'right', 'bottom'), 'lower left': (0.02, 0.04, 'left', 'bottom'),
            'upper right': (0.98, 0.96, 'right', 'top'), 'upper left': (0.02, 0.96, 'left', 'top')}
T975 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228,
        11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086,
        21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060, 26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045, 30: 2.042,
        40: 2.021, 60: 2.000, 120: 1.980}


def t975(df):
    if df < 1:
        return np.nan
    if df in T975:
        return T975[df]
    ks = sorted(T975)
    for lo, hi in zip(ks, ks[1:]):
        if lo < df < hi:
            return T975[lo] + (T975[hi] - T975[lo]) * (df - lo) / (hi - lo)
    return 1.96


def axis_default(label=''):
    # auto_label: the label follows the column name of the first layer on this axis until the user types one
    return dict(label=label, auto_label=not label, scale='linear', min='', max='', ticks='auto', step='', list='', ticklabels='', minor='auto',
                minor_n=4, fmt='auto', hide_ticklabels=False, categorical=False, categories='')


def column_label(name):
    """Axis label made from a column name: '(um)' → '(µm)', '(degC)' → '(°C)'."""
    s = re.sub(r'\((?:u|µ|μ)m\)', '(µm)', str(name))
    return re.sub(r'\(\s*deg\.?\s*C\s*\)', '(°C)', s, flags=re.I)


def auto_axis_labels(cfg):
    """Labels of the axes still marked auto_label: the x (or y) column of the first layer drawn on that axis."""
    for a in ('x', 'y', 'y2'):
        A = cfg.get(a) or {}
        if not A.get('auto_label'):
            continue
        col = None
        for L in cfg.get('layers', []):
            if a == 'x':
                col = L.get('x')
            elif L.get('type') != 'ticks' and (L.get('axis') == 'right') == (a == 'y2'):
                col = L.get('y')
            if col:
                break
        if col:
            A['label'] = column_label(col)
    return cfg


def layer_default(i=1, kind='mean_error'):
    return dict(name=ui('レイヤー {i}', 'Layer {i}', i=i), type=kind, uid='', source='', filter_col='', filter_vals=[], x='', y='', series='', unit='',
                err='sd', err_col='', points=True, connect=True, dodge=0.0, axis='left', in_legend=True, n_show='none',
                norm='none', x0='', offset=0.0, reverse_offset=False, xmin='', xmax='', sort=False, trace_labels=False,
                bar_width=0.8, stack='none', seg_values=True, seg_decimals=0,
                tick_style='vline', tick_y=0.04, tick_h=0.05, tick_gap=0.06, lw='', ms='', ma='',
                group='', xbars=True, pts='unit', xerr_col='',
                fit='none', fit_on='points', fit_ls='--', fit_eq=False, fit_extend=False,
                ov_color='', ov_ls='', ov_fill='', ov_marker='', label_suffix='')


def default_settings():
    return dict(
        version=VERSION,
        figure=dict(width_mm=88.0, height_mm=60.0, font='Arial', label_pt=7.0, tick_pt=6.5, legend_pt=6.5, note_pt=6.0,
                    axes_lw=0.5, line_lw=0.9, marker_size=3.8, marker_alpha=1.0, point_size=2.2, point_alpha=0.45, jitter=0.0, capsize=1.5,
                    frame_top=False, frame_right=False, mirror_ticks=False, tick_dir='out', grid=False,
                    letter='', letter_pt=8.0, letter_x=-0.15, dpi=600),
        sources={}, layers=[],
        x=axis_default(), y=axis_default(), y2=axis_default(),
        series={}, series_order='', palette='paper', palette_reverse=False,
        legend=dict(show=True, loc='best', ncol=1, title='', frame=False),
        n_display=dict(style='eq', note=False, unit='', loc='lower right'),
        fit_eq_loc='upper right',
        annotations='', spans='', hlines='', vlines='',
    )


def getp(d, path, default=None):
    cur = d
    for k in path.split('.'):
        if isinstance(cur, list):
            k = int(k)
            if k >= len(cur):
                return default
            cur = cur[k]
        elif isinstance(cur, dict) and k in cur:
            cur = cur[k]
        else:
            return default
    return cur


def setp(d, path, value):
    ks = path.split('.')
    cur = d
    for k in ks[:-1]:
        if isinstance(cur, list):
            cur = cur[int(k)]
        else:
            cur = cur.setdefault(k, {})
    if isinstance(cur, list):
        cur[int(ks[-1])] = value
    else:
        cur[ks[-1]] = value


def assign_uids(cfg):
    """Give every layer a unique id. It stays with the layer when it is renamed or moved, and is the series key
    ('@' + id) of a layer without a series column."""
    ids = [str(L.get('uid') or '') for L in cfg['layers']]
    seen = set()
    styled = set(cfg.get('series', {}))      # ids of deleted layers keep their style entry: never hand them out again
    for i, L in enumerate(cfg['layers']):
        u = ids[i]
        if not u or u in seen:
            k = 1
            while f'L{k}' in seen or f'L{k}' in ids or f'@L{k}' in styled:
                k += 1
            u = f'L{k}'
            L['uid'] = ids[i] = u
        seen.add(u)
    return cfg


def complete(cfg):
    """Fill in keys missing from an older or hand-written settings file."""
    for a in ('x', 'y', 'y2'):           # settings saved before v1.5: a label already typed in stays as it is
        if isinstance(cfg.get(a), dict) and 'auto_label' not in cfg[a]:
            cfg[a]['auto_label'] = not cfg[a].get('label')
    base = default_settings()
    Fo = cfg.get('figure') or {}
    if 'box' in Fo and 'frame_top' not in Fo:          # settings saved before v1.2: one switch for both sides
        Fo['frame_top'] = Fo['frame_right'] = bool(Fo.pop('box'))
    for k, v in base.items():
        if k not in cfg:
            cfg[k] = copy.deepcopy(v)
        elif isinstance(v, dict) and k not in ('sources', 'series'):
            for kk, vv in v.items():
                cfg[k].setdefault(kk, copy.deepcopy(vv))
    for i, L in enumerate(cfg['layers']):
        for kk, vv in layer_default(i + 1, L.get('type', 'mean_error')).items():
            L.setdefault(kk, copy.deepcopy(vv))
    return assign_uids(cfg)


# ------------------------------------------------------------------------------------------------ data
def read_table(data: bytes, name: str = '') -> pd.DataFrame:
    """CSV/TSV (UTF-8, UTF-8 BOM or Shift_JIS) → strings; column _row = line number in the file (header = 1)."""
    text = None
    for enc in ('utf-8-sig', 'cp932', 'latin-1'):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    try:
        delim = csv.Sniffer().sniff(text[:5000], delimiters=',;\t').delimiter
    except csv.Error:
        delim = ','
    df = pd.read_csv(io.StringIO(text), sep=delim, dtype=str, keep_default_na=False, skip_blank_lines=False)
    df.columns = [str(c).strip() for c in df.columns]
    df = df.apply(lambda s: s.str.strip())
    df.insert(0, '_row', np.arange(len(df)) + 2)
    data_cols = [c for c in df.columns if c != '_row']
    df = df[~(df[data_cols] == '').all(axis=1)].reset_index(drop=True)
    return df


def first_number(s, which='first'):
    m = re.findall(r'[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?', str(s))
    if not m:
        return s
    return m[0] if which == 'first' else m[-1]


def reshape(df, rcfg):
    """Wide → long: the chosen value columns become rows; their header (or the number in it) becomes a new column."""
    if not rcfg or not rcfg.get('enabled') or not rcfg.get('value_cols'):
        out = df.copy()
        out['_col'] = ''
        return out
    vals = [c for c in rcfg['value_cols'] if c in df.columns]
    ids = [c for c in df.columns if c not in vals]
    var = rcfg.get('var_name') or 'variable'
    val = rcfg.get('value_name') or 'value'
    long = df.melt(id_vars=ids, value_vars=vals, var_name='_col', value_name=val)
    mode = rcfg.get('parse', 'first')
    long[var] = long['_col'].map(lambda s: first_number(s, mode) if mode in ('first', 'last') else s)
    long = long[long[val] != ''].reset_index(drop=True)
    return long


FUNCS = {'log': np.log, 'ln': np.log, 'log10': np.log10, 'exp': np.exp, 'sqrt': np.sqrt, 'abs': np.abs}


def add_derived(df, text):
    """Computed columns, one per line: 'h = 1 - l_after / l_before'. Columns with other characters in `backticks`.
    Only + - * / ** ( ), numbers, column names and log/ln/log10/exp/sqrt/abs are accepted."""
    notes = []
    df = df.copy()
    for line in str(text or '').splitlines():
        if '=' not in line or not line.strip():
            continue
        name, expr = (t.strip() for t in line.split('=', 1))
        tok = re.findall(r'`[^`]+`|[A-Za-z_][A-Za-z0-9_]*|\d+(?:\.\d*)?(?:[eE][-+]?\d+)?|\.\d+|\*\*|[-+*/() ]', expr)
        if not name or ''.join(tok).replace(' ', '') != expr.replace(' ', ''):
            notes.append(ui('計算列「{v}」: 使えない文字があります', 'Calculated column “{v}”: contains characters that cannot be used', v=line.strip()))
            continue
        cols = []
        for t in tok:
            c = t[1:-1] if t.startswith('`') else (t if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', t) else None)
            if c is None or (c in FUNCS and not t.startswith('`')):
                continue
            if c not in df.columns:
                notes.append(ui('計算列「{name}」: 列「{c}」がありません', 'Calculated column “{name}”: there is no column “{c}”', name=name, c=c))
                cols = None
                break
            cols.append(c)
        if cols is None:
            continue
        env, expr2 = {}, expr
        for i, c in enumerate(sorted(set(cols), key=len, reverse=True)):
            v = f'_c{i}'
            expr2 = expr2.replace(f'`{c}`', v)
            expr2 = re.sub(rf'(?<![A-Za-z0-9_`]){re.escape(c)}(?![A-Za-z0-9_])', v, expr2)
            env[v] = pd.to_numeric(df[c], errors='coerce').to_numpy(float)
        env.update(FUNCS)
        try:
            with np.errstate(all='ignore'):
                val = eval(compile(expr2, '<expr>', 'eval'), {'__builtins__': {}}, env)  # noqa: S307 (tokens checked above)
            df[name] = np.broadcast_to(np.asarray(val, float), (len(df),)).copy()
        except Exception as e:  # noqa: BLE001
            notes.append(ui('計算列「{name}」: 計算できません（{e}）', 'Calculated column “{name}”: cannot be calculated ({e})', name=name, e=e))
    return df, notes


# 選んで作る計算列：(画面の名前, 式, 使うもの A・B・k(数)・M(複数の列), 新しい列の名前の案)
FORMULAS = {
    'ratio': ('A ÷ B（比）', '{A} / {B}', 'AB', '{a}/{b}'),
    'one_minus_ratio': ('1 − A ÷ B（治癒率 h など）', '1 - {A} / {B}', 'AB', '1-{a}/{b}'),
    'diff': ('A − B（差）', '{A} - {B}', 'AB', '{a}-{b}'),
    'rel_change': ('(A − B) ÷ B（変化率）', '({A} - {B}) / {B}', 'AB', '({a}-{b})/{b}'),
    'sum': ('A + B（和）', '{A} + {B}', 'AB', '{a}+{b}'),
    'prod': ('A × B（積）', '{A} * {B}', 'AB', '{a}*{b}'),
    'mean_cols': ('選んだ列の平均', 'MEAN', 'M', 'mean'),
    'sum_cols': ('選んだ列の和', 'SUM', 'M', 'sum'),
    'mul_k': ('A × 数（％、単位の換算）', '{A} * {k}', 'Ak', '{a}*{k}'),
    'div_k': ('A ÷ 数（単位の換算）', '{A} / {k}', 'Ak', '{a}/{k}'),
    'sub_k': ('A − 数（基準を引く）', '{A} - {k}', 'Ak', '{a}-{k}'),
    'add_k': ('A + 数', '{A} + {k}', 'Ak', '{a}+{k}'),
    'pow_k': ('A の 数 乗', '{A} ** {k}', 'Ak', '{a}^{k}'),
    'inv': ('1 ÷ A', '1 / {A}', 'A', '1/{a}'),
    'sqrt': ('√A', 'sqrt({A})', 'A', 'sqrt({a})'),
    'log10': ('log10(A)', 'log10({A})', 'A', 'log10({a})'),
    'ln': ('ln(A)', 'ln({A})', 'A', 'ln({a})'),
    'abs': ('|A|（絶対値）', 'abs({A})', 'A', '|{a}|'),
}
FORMULA_K = {'mul_k': 100.0, 'div_k': 1000.0, 'sub_k': 0.0, 'add_k': 0.0, 'pow_k': 2.0}


def formula_label(kind):
    """The name of a calculation in the screen language (FORMULAS keeps the Japanese one)."""
    return {
        'ratio': ui('A ÷ B（比）', 'A ÷ B (ratio)'),
        'one_minus_ratio': ui('1 − A ÷ B（治癒率 h など）', '1 − A ÷ B (e.g. healing ratio h)'),
        'diff': ui('A − B（差）', 'A − B (difference)'),
        'rel_change': ui('(A − B) ÷ B（変化率）', '(A − B) ÷ B (relative change)'),
        'sum': ui('A + B（和）', 'A + B (sum)'),
        'prod': ui('A × B（積）', 'A × B (product)'),
        'mean_cols': ui('選んだ列の平均', 'Mean of the chosen columns'),
        'sum_cols': ui('選んだ列の和', 'Sum of the chosen columns'),
        'mul_k': ui('A × 数（％、単位の換算）', 'A × number (%, unit conversion)'),
        'div_k': ui('A ÷ 数（単位の換算）', 'A ÷ number (unit conversion)'),
        'sub_k': ui('A − 数（基準を引く）', 'A − number (subtract a baseline)'),
        'add_k': ui('A + 数', 'A + number'),
        'pow_k': ui('A の 数 乗', 'A to the power of a number'),
        'abs': ui('|A|（絶対値）', '|A| (absolute value)'),
    }.get(kind, FORMULAS[kind][0] if kind in FORMULAS else str(kind))


def formula_line(kind, a=None, b=None, k=None, cols=(), name=''):
    """One line for the calculated columns from choices: ('ratio', 'l_after', 'l_before') → 'l_after/l_before = `l_after` / `l_before`'.
    Returns (line, name); name is the suggestion when none is given."""
    label, expr, uses, sug = FORMULAS[kind]
    q = lambda c: '`' + str(c) + '`'  # noqa: E731
    kk = f'{float(k):g}' if k is not None else ''
    if 'M' in uses:
        cols = [c for c in cols if c]
        inner = ' + '.join(q(c) for c in cols)
        expr = (f'({inner}) / {len(cols)}' if expr == 'MEAN' else inner) if cols else ''
        sug = (('mean(' if kind == 'mean_cols' else 'sum(') + ','.join(map(str, cols)) + ')') if cols else sug
    else:
        expr = expr.format(A=q(a), B=q(b), k=kk)
        sug = sug.format(a=a, b=b, k=kk)
    name = (name or '').strip() or sug
    return f'{name} = {expr}', name


ID_NAME = re.compile(r'(?i)^(id|no\.?|#|index|indent(ation)?(_?(id|no))?|corner|crack(_?(id|no))?|sample(_?(id|no))?|specimen(_?(id|no))?|'
                     r'試料(番号)?|番号|圧痕(番号)?|き裂(番号)?|亀裂(番号)?)$')


def auto_layer(name, df, i=1):
    """A first layer for a CSV just loaded.
    Two or more numeric columns: x = the first, y = the next; a text column with a few values (e.g. composition) colours
    the series. Repeated x → mean ± SD; many distinct x in order → line; otherwise → scatter.
    One numeric column and a text column: bars of the numeric column by the text (x as items).
    Returns (layer, x_is_text) or (None, False) when nothing sensible can be drawn."""
    cols = [c for c in df.columns if c not in ('_row', '_col', '_excluded')]
    if len(cols) < 2:
        return None, False

    def filled(c):
        v = df[c].astype(str).str.strip()
        return v[(v != '') & (v.str.lower() != 'nan')]
    ids = [c for c in cols if ID_NAME.match(str(c).strip())]          # 圧痕番号・き裂番号などは x・y にしない
    num = [c for c in cols if c not in ids and len(filled(c)) and pd.to_numeric(filled(c), errors='coerce').notna().mean() >= 0.8]
    text = [c for c in cols if c not in num and c not in ids and len(filled(c))]
    series, stack = '', 'none'
    few = [c for c in text if 1 < filled(c).nunique() <= 12 and filled(c).nunique() < 0.5 * len(filled(c))]
    cand = [c for c in text if 1 < filled(c).nunique() <= 12]           # 割合の内訳の区分（粒度など）の候補
    if len(num) >= 2:
        x, y = num[0], num[1]
        comp = next((c for c in cand if composition_like(df, x, y, c)), '')
        series = comp or (few[0] if few else '')
        xv = pd.to_numeric(df[x], errors='coerce').dropna()
        if comp:                                                   # 割合の内訳（ふるい分けなど）→ 100% 積み上げ棒
            kind, stack, text_x = 'bar', 'percent', True
        elif xv.duplicated().any():
            kind, text_x = 'mean_error', False
        elif len(xv) >= 30 and (xv.is_monotonic_increasing or xv.is_monotonic_decreasing):
            kind, text_x = 'line', False
        else:
            kind, text_x = 'scatter', False
    elif len(num) == 1 and text:
        x, y, kind, text_x = text[0], num[0], 'bar', True
        pair = next(((a, c) for a in text for c in cand if c != a and composition_like(df, a, y, c)), None)
        if pair:                                                   # x と区分の列がどちらの順に並んでいても
            (x, series), stack = pair, 'percent'
    else:
        return None, False
    L = layer_default(i, kind)
    L.update(name=os.path.splitext(str(name))[0], source=name, x=x, y=y, series=series, stack=stack)
    return L, text_x


def composition_like(df, x, y, series):
    """True when the y values of each x add up to about 100 over the series, one row per x and series
    (e.g. sieve fractions in % at each holding temperature): such data are drawn as 100 % stacked bars."""
    try:
        d = pd.DataFrame({'x': df[x].map(skey), 's': df[series].map(skey), 'y': pd.to_numeric(df[y], errors='coerce')})
    except KeyError:
        return False
    d = d[(d['x'] != '') & (d['s'] != '') & (d['x'].str.lower() != 'nan') & (d['s'].str.lower() != 'nan')].dropna()
    if d.empty or d['s'].nunique() < 2 or d.duplicated(['x', 's']).any() or (d['y'] < 0).any():
        return False
    tot = d.groupby('x')['y'].sum()
    return bool(((tot - 100.0).abs() <= 2.0).all())


def exclusion_tokens(text):
    """'57, 60:l_98N' → ['57', '60:l_98N']"""
    return [t for t in re.split(r'[,\s、]+', str(text or '')) if t]


def parse_exclusions(text):
    """'57, 60:l_98N' → rows {57}, cells {(60, 'l_98N')}"""
    rows, cells = set(), set()
    for tok in exclusion_tokens(text):
        if not tok:
            continue
        if ':' in tok:
            r, c = tok.split(':', 1)
            if r.strip().isdigit():
                cells.add((int(r), c.strip()))
        elif tok.isdigit():
            rows.add(int(tok))
    return rows, cells


def prepared_tables(cfg, raw, notes=None):
    """raw: {name: DataFrame from read_table} → {name: reshaped DataFrame + computed columns, exclusions in column _excluded}"""
    out = {}
    for name, df in raw.items():
        scfg = cfg['sources'].get(name, {})
        d = reshape(df, scfg.get('reshape'))
        d, nn = add_derived(d, scfg.get('derived', ''))
        if notes is not None:
            notes += [f'{name}: {n}' for n in nn]
        rows, cells = parse_exclusions(scfg.get('exclude', ''))
        d['_excluded'] = d['_row'].isin(rows) | d.apply(lambda r: (r['_row'], r['_col']) in cells, axis=1) if len(d) else False
        out[name] = d
    return out


def skey(v):
    """Canonical series / category key: 5.0 → '5', ' 10 ' → '10'."""
    s = str(v).strip()
    try:
        f = float(s)
        if math.isfinite(f) and f == int(f) and not re.search(r'[eE]', s):
            return str(int(f))
    except ValueError:
        pass
    return s


_BELOW, _ABOVE = '<≤＜≦', '>≥＞≧'


def natural_key(s):
    """'5vol_750C' < '5vol_950C' < '5vol_1050C'; '5' < '10'; size classes in size order: '<38 µm' < '38–63 µm' < '>63 µm'."""
    t = str(s).lstrip()
    nudge = 0.0
    m = re.match(r'([' + _BELOW + _ABOVE + r'])\s*(?=\d)', t)
    if m:                                    # '<38' は 38 のすぐ下、'>63' は 63 のすぐ上
        nudge = -1e-9 if m.group(1) in _BELOW else 1e-9
        t = t[m.end():]
    parts = re.split(r'(\d+(?:\.\d+)?)', t)
    key = [(0, float(p), '') if re.fullmatch(r'\d+(?:\.\d+)?', p) else (1, 0.0, p.lower()) for p in parts if p != '']
    if nudge and key and key[0][0] == 0:
        key[0] = (0, key[0][1] * (1 + nudge) + nudge, '')
    return key


def natural_order(keys):
    return sorted(keys, key=natural_key)


def layer_key(L):
    """Series key of a layer without a series column: the whole layer is one series."""
    return '@' + str(L.get('uid') or L.get('name') or 'layer')


def layer_of(cfg, k):
    for L in cfg['layers']:
        if layer_key(L) == k:
            return L
    return None


def default_label(cfg, k):
    """Legend name a new series starts with: the value of the series column, or the CSV file name of a layer-own series."""
    k = str(k)
    if k.startswith('@'):
        L = layer_of(cfg, k)
        if L is not None:
            return os.path.splitext(str(L.get('source') or ''))[0] or str(L.get('name') or k)
    return nice_label(k)


def nice_label(s):
    """A value typed in plain ASCII in a CSV, as it is printed in the figure: '38-63 um' → '38–63 µm', '<38um' → '<38 µm'
    (Excel's Shift_JIS CSV cannot keep µ or –, so the CSV can stay ASCII)."""
    t = re.sub(r'(?<=\d)\s*(?:u|μ|µ)m\b', ' µm', str(s))
    return re.sub(r'(\d)\s*-\s*(?=\d[\d.]*\s*(?:µm|mm|nm|°C|%))', '\\1–', t)


def series_name(cfg, k):
    """Readable name of a series for tables: the value of the series column, or the CSV file of a layer-own series."""
    k = str(k)
    if k.startswith('@'):
        L = layer_of(cfg, k)
        if L is not None:
            return str(L.get('source') or L.get('name') or k)
    return k


def layer_frame(L, tables, cfg):
    """Rows of one layer with numeric x/y and string series/unit keys. Returns (frame, bad_rows)."""
    if L.get('source') not in tables or (not L.get('y') and L.get('type') != 'ticks') or not L.get('x'):
        return None, None
    d = tables[L['source']]
    d = d[~d['_excluded']].copy()
    if L.get('filter_col') and L['filter_col'] in d.columns and L.get('filter_vals'):
        d = d[d[L['filter_col']].map(skey).isin([skey(v) for v in L['filter_vals']])]
    cat = bool(cfg['x'].get('categorical')) and L['type'] in ('mean_error', 'bar', 'scatter')
    out = pd.DataFrame({'_row': d['_row'], '_col': d.get('_col', '')})
    if L['type'] == 'ticks':
        out['_x'] = pd.to_numeric(d[L['x']], errors='coerce') if L.get('x') in d.columns else np.nan
        out['_y'] = 0.0
    else:
        if L.get('x') in d.columns:
            out['_x'] = d[L['x']].map(skey) if cat else pd.to_numeric(d[L['x']], errors='coerce')
        else:
            out['_x'] = '' if cat else np.nan
        out['_y'] = pd.to_numeric(d[L['y']], errors='coerce') if L.get('y') in d.columns else np.nan
    out['_series'] = d[L['series']].map(skey) if L.get('series') and L['series'] in d.columns else layer_key(L)
    out['_unit'] = d[L['unit']].astype(str) if L.get('unit') in d.columns else ''
    if L.get('err') == 'column' and L.get('err_col') in d.columns:
        out['_e'] = pd.to_numeric(d[L['err_col']], errors='coerce')
    if L.get('err') == 'column' and L.get('xerr_col') in d.columns:
        out['_ex'] = pd.to_numeric(d[L['xerr_col']], errors='coerce')
    if L['type'] == 'mean_error' and L.get('group') and L['group'] in d.columns:
        out['_g'] = d[L['group']].map(skey)      # x は測った値：この列の値ごとに x と y の平均 ± 誤差
    bad = out[out['_y'].isna() | (out['_x'].isna() if not cat else out['_x'].eq(''))]
    out = out.drop(bad.index)
    return out, bad


def _stats(g, prefix=''):
    s = g.agg(n='size', mean='mean', sd=lambda v: v.std(ddof=1) if len(v) > 1 else np.nan, min='min', max='max').reset_index()
    s['se'] = s['sd'] / np.sqrt(s['n'])
    s['ci95'] = [t975(n - 1) * se if n > 1 else np.nan for n, se in zip(s['n'], s['se'])]
    return s.rename(columns={c: prefix + c for c in ('n', 'mean', 'sd', 'min', 'max', 'se', 'ci95')}) if prefix else s


def aggregate(f, err='sd'):
    """f: layer frame. Unit means first (if a unit column is set), then statistics over units per (series, x).
    With a group column (_g, e.g. the load when x is a measured crack length): per (series, group), and x is averaged
    as well (x_mean, x_sd, … ; the point is drawn at x_mean)."""
    if f is None or f.empty:
        return pd.DataFrame(), pd.DataFrame()
    has_unit = (f['_unit'] != '').any()
    if '_g' in f.columns:
        if has_unit:
            u = (f.groupby(['_series', '_g', '_unit'], sort=False)
                 .agg(_x=('_x', 'mean'), y=('_y', 'mean'), n_members=('_y', 'size'), rows=('_row', lambda r: ' '.join(map(str, r))))
                 .reset_index())
        else:
            u = (f.rename(columns={'_y': 'y'}).assign(n_members=1, rows=f['_row'].astype(str))
                 [['_series', '_g', '_x', '_unit', 'y', 'n_members', 'rows']])
        gu = u.groupby(['_series', '_g'], sort=False)
        s = _stats(gu['y']).merge(_stats(gu['_x'], 'x_').drop(columns=['x_n']), on=['_series', '_g'])
        s['_x'] = s['x_mean']
        for col, key in (('_e', 'err_column'), ('_ex', 'xerr_column')):
            if err == 'column' and col in f.columns:
                s = s.merge(f.groupby(['_series', '_g'], sort=False)[col].first().reset_index(), on=['_series', '_g'], how='left').rename(columns={col: key})
        return s, u
    if has_unit:
        u = (f.groupby(['_series', '_x', '_unit'], sort=False)
             .agg(y=('_y', 'mean'), n_members=('_y', 'size'), rows=('_row', lambda r: ' '.join(map(str, r)))).reset_index())
    else:
        u = f.rename(columns={'_y': 'y'}).assign(n_members=1, rows=f['_row'].astype(str))[['_series', '_x', '_unit', 'y', 'n_members', 'rows']]
    g = u.groupby(['_series', '_x'], sort=False)['y']
    s = g.agg(n='size', mean='mean', sd=lambda v: v.std(ddof=1) if len(v) > 1 else np.nan, min='min', max='max').reset_index()
    s['se'] = s['sd'] / np.sqrt(s['n'])
    s['ci95'] = [t975(n - 1) * se if n > 1 else np.nan for n, se in zip(s['n'], s['se'])]
    for col, key in (('_e', 'err_column'), ('_ex', 'xerr_column')):
        if err == 'column' and col in f.columns:
            s = s.merge(f.groupby(['_series', '_x'], sort=False)[col].first().reset_index(), on=['_series', '_x'], how='left').rename(columns={col: key})
    return s, u


def xerr_values(s, err):
    """Horizontal error bars: the spread of x within a group (group column), or a column of x errors."""
    if err == 'column':
        return (s['xerr_column'].to_numpy(), s['xerr_column'].to_numpy()) if 'xerr_column' in s else (None, None)
    if 'x_sd' not in s:
        return None, None
    if err in ('sd', 'se', 'ci95'):
        v = s['x_' + err].to_numpy()
        return v, v
    if err == 'minmax':
        return (s['x_mean'] - s['x_min']).to_numpy(), (s['x_max'] - s['x_mean']).to_numpy()
    return None, None


def err_values(s, err):
    if err == 'sd':
        return s['sd'].to_numpy(), s['sd'].to_numpy()
    if err == 'se':
        return s['se'].to_numpy(), s['se'].to_numpy()
    if err == 'ci95':
        return s['ci95'].to_numpy(), s['ci95'].to_numpy()
    if err == 'minmax':
        return (s['mean'] - s['min']).to_numpy(), (s['max'] - s['mean']).to_numpy()
    if err == 'column' and 'err_column' in s:
        return s['err_column'].to_numpy(), s['err_column'].to_numpy()
    return None, None


def n_range(ns):
    """'9' or '9–16'."""
    v = [int(x) for x in ns if x == x]
    if not v:
        return ''
    return f'{min(v)}' if min(v) == max(v) else f'{min(v)}–{max(v)}'


# ------------------------------------------------------------------------------------------------ style
def contrast_on_white(color):
    """WCAG 2.x contrast ratio of a colour against white: 1 = invisible … 21 = black."""
    try:
        rgb = np.array(matplotlib.colors.to_rgb(color), float)
    except (ValueError, TypeError):
        return 21.0
    lin = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    return 1.05 / (float(lin @ np.array([0.2126, 0.7152, 0.0722])) + 0.05)


_SHADE_RANGE = {}


def shade_range(cmap_name):
    """(start, end) of a colour map used for the series colours, chosen so that every colour in between has at least
    MIN_CONTRAST against white. Maps that get darker towards their end (Blues, Oranges, Greens, Greys) start at the
    lightest colour that still reaches it and end at the darkest; maps that get lighter (viridis) stop before they get
    too light. Worked out from the colours themselves, so it holds for any matplotlib version."""
    if cmap_name not in _SHADE_RANGE:
        cmap = plt.get_cmap(cmap_name)
        xs = np.linspace(0.0, 1.0, 1001)
        ok = [contrast_on_white(matplotlib.colors.to_hex(cmap(x))) >= MIN_CONTRAST for x in xs]
        if contrast_on_white(cmap(1.0)) >= contrast_on_white(cmap(0.0)):          # darker towards the end
            i = len(xs) - 1
            while i > 0 and ok[i - 1]:
                i -= 1
            rng = (float(xs[i]), 1.0) if ok[-1] else (0.35, 0.95)
        else:                                                                       # lighter towards the end
            i = 0
            while i < len(xs) - 1 and ok[i + 1]:
                i += 1
            rng = (0.0, float(xs[i])) if ok[0] else (0.35, 0.95)
        _SHADE_RANGE[cmap_name] = rng
    return _SHADE_RANGE[cmap_name]


def palette_colors(name, n, reverse=False):
    """n colours of a palette. The shades (blues…) go from light to dark, the lightest one still at least MIN_CONTRAST
    against white; reverse=True starts with the dark one (for the other palettes it reverses the order of the colours)."""
    p = PALETTES.get(name, PALETTES['paper'])
    if isinstance(p, str):
        cmap = plt.get_cmap(p)
        a, b = shade_range(p)
        if n == 1:
            return [matplotlib.colors.to_hex(cmap(a + 0.4 * (b - a)))]
        cols = [matplotlib.colors.to_hex(cmap(a + (b - a) * i / (n - 1))) for i in range(n)]
    else:
        cols = [p[i % len(p)] for i in range(n)]
    return cols[::-1] if reverse else cols


def thin_series(cfg, tables):
    """Series drawn as lines or markers (in a layer other than bars): where a light colour can all but disappear."""
    out = set()
    for L in cfg['layers']:
        if L.get('type') == 'bar':
            continue
        if not L.get('series'):
            out.add(layer_key(L))
            continue
        f, _ = layer_frame(L, tables, cfg)
        if f is not None:
            out.update(f['_series'].unique())
    return out


def series_keys(cfg, tables):
    """All series keys in drawing order: the explicit order first, then layer by layer (natural order within a layer).
    A layer without a series column gives its own key even before its data and columns are chosen."""
    keys = []
    for L in cfg['layers']:
        if not L.get('series'):
            k = layer_key(L)
            if k not in keys:
                keys.append(k)
            continue
        f, _ = layer_frame(L, tables, cfg)
        if f is not None:
            for k in natural_order(f['_series'].unique()):
                if k not in keys:
                    keys.append(k)
    order = [skey(s) for s in re.split(r'\s*,\s*', cfg.get('series_order') or '') if s.strip()]
    return [k for k in order if k in keys] + [k for k in keys if k not in order]


def ensure_series(cfg, keys):
    """Give every series a style entry (a new one gets the palette colour and marker of its position)."""
    new = [k for k in keys if k not in cfg['series']]
    if new:
        cols = palette_colors(cfg.get('palette', 'paper'), len(keys), cfg.get('palette_reverse', False))
        for k in new:
            i = keys.index(k)
            cfg['series'][k] = dict(label=default_label(cfg, k), color=cols[i], marker=MARKERS[i % 9], filled=True, ls='-')
    for k in keys:
        for f, v in SERIES_FIELDS.items():
            cfg['series'][k].setdefault(f, default_label(cfg, k) if f == 'label' else v)
    return cfg


def apply_palette(cfg, keys):
    cols = palette_colors(cfg.get('palette', 'paper'), len(keys), cfg.get('palette_reverse', False))
    for i, k in enumerate(keys):
        cfg['series'].setdefault(k, dict(label=default_label(cfg, k), marker=MARKERS[i % 9], filled=True, ls='-'))
        cfg['series'][k]['color'] = cols[i]
    return cfg


def layer_style(cfg, L, sk):
    """Series style with the layer's overrides (used e.g. to tell H_V and K apart on a twin axis).
    An empty legend name leaves the series out of the legend."""
    S = dict(cfg['series'][sk])
    if L.get('ov_color'):
        S['color'] = L['ov_color']
    if L.get('ov_ls'):
        S['ls'] = L['ov_ls']
    if L.get('ov_fill') in ('filled', 'open'):
        S['filled'] = L['ov_fill'] == 'filled'
    if L.get('ov_marker'):
        S['marker'] = L['ov_marker']
    lab = S.get('label')
    if lab is None:
        lab = default_label(cfg, sk)
    S['label'] = (str(lab) + (L.get('label_suffix') or '')) if str(lab).strip() else ''
    return S


def pick_font(name):
    avail = {f.name for f in fm.fontManager.ttflist}
    for f in (name, 'Arial', 'Helvetica', 'Liberation Sans', 'Arimo', 'DejaVu Sans'):
        if f in avail:
            return f
    return 'DejaVu Sans'


def set_rc(F):
    font = pick_font(F.get('font', 'Arial'))
    rcParams.update({
        'font.family': font, 'font.size': F['label_pt'], 'axes.labelsize': F['label_pt'], 'axes.titlesize': F['label_pt'],
        'xtick.labelsize': F['tick_pt'], 'ytick.labelsize': F['tick_pt'], 'legend.fontsize': F['legend_pt'],
        'legend.title_fontsize': F['legend_pt'], 'axes.linewidth': F['axes_lw'],
        'xtick.major.width': F['axes_lw'], 'ytick.major.width': F['axes_lw'], 'xtick.minor.width': 0.8 * F['axes_lw'],
        'ytick.minor.width': 0.8 * F['axes_lw'], 'xtick.major.size': 2.5, 'ytick.major.size': 2.5, 'xtick.minor.size': 1.5,
        'ytick.minor.size': 1.5, 'xtick.direction': F['tick_dir'], 'ytick.direction': F['tick_dir'],
        'xtick.major.pad': 2.5, 'ytick.major.pad': 2.5, 'axes.labelpad': 2.5,
        'axes.spines.top': bool(F.get('frame_top')), 'axes.spines.right': bool(F.get('frame_right')),
        'lines.linewidth': F['line_lw'], 'lines.markersize': F['marker_size'], 'lines.markeredgewidth': 0.6,
        'legend.frameon': False, 'legend.handlelength': 2.0, 'legend.handletextpad': 0.5, 'legend.borderaxespad': 0.3,
        'legend.labelspacing': 0.35, 'legend.columnspacing': 1.0, 'mathtext.default': 'it', 'mathtext.fontset': 'custom',
        'mathtext.rm': font, 'mathtext.it': font + ':italic', 'mathtext.bf': font + ':bold', 'mathtext.sf': font,
        'mathtext.cal': font, 'mathtext.tt': 'DejaVu Sans Mono',
        'svg.fonttype': 'none', 'pdf.fonttype': 42, 'ps.fonttype': 42, 'axes.unicode_minus': True,
        'savefig.facecolor': 'white', 'figure.facecolor': 'white',
    })
    return font


def num_or_none(s):
    try:
        v = float(str(s).strip())
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


FIT_MODELS = ('linear', 'origin', 'poly2', 'power', 'exp', 'log')


def fit_curve(x, y, model):
    """Least squares: linear y = a + bx, origin y = bx, poly2 y = a + bx + cx², power y = a·x^b (straight line in log–log),
    exp y = a·e^(bx) (straight line of ln y), log y = a + b·ln x. Returns a dict (parameters, R², n, x range, f) or None."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    m = np.isfinite(x) & np.isfinite(y)
    if model in ('power', 'log'):
        m &= x > 0
    if model in ('power', 'exp'):
        m &= y > 0
    x, y = x[m], y[m]
    need = 3 if model == 'poly2' else 2
    if model not in FIT_MODELS or len(x) < need or len(np.unique(x)) < need:
        return None
    p = {}
    if model == 'linear':
        p['b'], p['a'] = np.polyfit(x, y, 1)
        f = lambda t: p['a'] + p['b'] * t  # noqa: E731
    elif model == 'origin':
        p['b'] = float(x @ y / (x @ x))
        f = lambda t: p['b'] * t  # noqa: E731
    elif model == 'poly2':
        p['c'], p['b'], p['a'] = np.polyfit(x, y, 2)
        f = lambda t: p['a'] + p['b'] * t + p['c'] * t ** 2  # noqa: E731
    elif model == 'power':
        p['b'], la = np.polyfit(np.log(x), np.log(y), 1)
        p['a'] = float(np.exp(la))
        f = lambda t: p['a'] * np.power(t, p['b'])  # noqa: E731
    elif model == 'exp':
        p['b'], la = np.polyfit(x, np.log(y), 1)
        p['a'] = float(np.exp(la))
        f = lambda t: p['a'] * np.exp(p['b'] * t)  # noqa: E731
    else:
        p['b'], p['a'] = np.polyfit(np.log(x), y, 1)
        f = lambda t: p['a'] + p['b'] * np.log(t)  # noqa: E731
    res = y - f(x)
    tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1 - float((res ** 2).sum()) / tot if tot > 0 else np.nan
    return dict(model=model, n=int(len(x)), r2=r2, x_min=float(x.min()), x_max=float(x.max()), f=f,
                **{k: float(v) for k, v in p.items()})


def fit_equation(r):
    """'y = 0.123x + 1.45, R^{2} = 0.861' (written with the app's sub/superscript markup)."""
    g = lambda v: f'{v:.3g}'.replace('-', '−')  # noqa: E731
    sg = lambda v: ('+ ' if v >= 0 else '− ') + g(abs(v))  # noqa: E731
    a, b, c = r.get('a'), r.get('b'), r.get('c')
    eq = {'linear': lambda: f'y = {g(b)}x {sg(a)}', 'origin': lambda: f'y = {g(b)}x',
          'poly2': lambda: f'y = {g(c)}x^{{2}} {sg(b)}x {sg(a)}', 'power': lambda: f'y = {g(a)}x^{{{g(b)}}}',
          'exp': lambda: f'y = {g(a)}e^{{{g(b)}x}}', 'log': lambda: f'y = {g(b)} ln x {sg(a)}'}[r['model']]()
    return eq + ('' if not np.isfinite(r['r2']) else f', R^{{2}} = {r["r2"]:.3f}')


def marker_alpha(L, default):
    """Opacity of a layer's markers: the layer's own value (field ma) if given, else `default`."""
    a = num_or_none(L.get('ma'))
    a = float(default if a is None else a)
    return min(1.0, max(0.05, a))


def num_list(s):
    return [v for v in (num_or_none(t) for t in re.split(r'[,\s]+', str(s or '').strip())) if v is not None]


def fmt_func(fmt):
    if fmt == 'g':
        return FuncFormatter(lambda v, p: f'{v:g}')
    if fmt in ('0', '1', '2', '3'):
        n = int(fmt)
        return FuncFormatter(lambda v, p: f'{v:.{n}f}')
    if fmt == 'sci':
        return FuncFormatter(lambda v, p: '' if v <= 0 else '$10^{%d}$' % round(math.log10(v)) if abs(math.log10(v) - round(math.log10(v))) < 1e-9 else f'{v:g}')
    return None


def apply_axis(ax, A, which, F):
    """A: axis settings; which: 'x' or 'y'."""
    axis = ax.xaxis if which == 'x' else ax.yaxis
    setscale = ax.set_xscale if which == 'x' else ax.set_yscale
    setlim = ax.set_xlim if which == 'x' else ax.set_ylim
    setlabel = ax.set_xlabel if which == 'x' else ax.set_ylabel
    cat = which == 'x' and A.get('categorical')
    if not cat:
        setscale('log' if A.get('scale') == 'log' else 'linear')
    lo, hi = num_or_none(A.get('min')), num_or_none(A.get('max'))
    if lo is not None or hi is not None:
        cur = ax.get_xlim() if which == 'x' else ax.get_ylim()
        setlim(lo if lo is not None else cur[0], hi if hi is not None else cur[1])
    if not cat:
        mode = A.get('ticks', 'auto')
        if mode == 'step' and num_or_none(A.get('step')) and A.get('scale') != 'log':
            axis.set_major_locator(MultipleLocator(num_or_none(A['step'])))
        elif mode == 'list' and num_list(A.get('list')):
            vals = num_list(A['list'])
            axis.set_major_locator(FixedLocator(vals))
            labs = [t.strip() for t in str(A.get('ticklabels') or '').split(',')] if A.get('ticklabels') else None
            if labs and len(labs) == len(vals):
                labs = [rich(t) for t in labs]
                axis.set_major_formatter(FuncFormatter(lambda v, p, vals=vals, labs=labs: labs[int(np.argmin(np.abs(np.array(vals) - v)))]))
            else:
                axis.set_major_formatter(FuncFormatter(lambda v, p: f'{v:g}'))
        f = fmt_func(A.get('fmt', 'auto'))
        if f is not None and not (mode == 'list' and A.get('ticklabels')):
            axis.set_major_formatter(f)
        elif A.get('scale') == 'log' and A.get('fmt', 'auto') == 'auto' and mode != 'list':
            axis.set_major_formatter(FuncFormatter(lambda v, p: f'{v:g}'))
        minor = A.get('minor', 'auto')
        if minor == 'off':
            axis.set_minor_locator(NullLocator())
        elif A.get('scale') == 'log':
            axis.set_minor_locator(LogLocator(base=10, subs=np.arange(2, 10) * 0.1, numticks=50))
            axis.set_minor_formatter(NullFormatter())
        elif minor == 'n':
            axis.set_minor_locator(AutoMinorLocator(int(A.get('minor_n') or 4) + 1))
        else:
            axis.set_minor_locator(AutoMinorLocator())
    if A.get('hide_ticklabels'):
        axis.set_major_formatter(NullFormatter())
        (ax.tick_params(axis=which, which='both', length=0) if which == 'y' else None)
    setlabel(rich(A.get('label') or ''))


def parse_lines(text, n_min):
    """One item per line, fields separated by '|' (text may contain commas)."""
    out = []
    for line in str(text or '').splitlines():
        parts = [p.strip() for p in line.split('|')]
        if len(parts) >= n_min and any(parts):
            out.append(parts)
    return out


# ------------------------------------------------------------------------------------------------ render
def render(cfg, tables, preview_dpi=None):
    """Draw the figure. tables = prepared_tables(cfg, raw). Returns (fig, plotted: list[(layer, DataFrame)], notes)."""
    cfg = auto_axis_labels(complete(cfg))
    F = cfg['figure']
    ND = cfg['n_display']
    set_rc(F)
    keys = series_keys(cfg, tables)
    ensure_series(cfg, keys)
    fig = plt.figure(figsize=(F['width_mm'] * MM, F['height_mm'] * MM), layout='constrained')
    fig.get_layout_engine().set(w_pad=1.5 / 72, h_pad=1.5 / 72, wspace=0, hspace=0)
    ax = fig.add_subplot(111)
    ax2 = None
    plotted, notes, handles = [], [], {}
    fits, fit_texts = [], []          # (axis, fit, colour, line style, line width, to the axis ends), (equation, colour)

    def add_fit(tax, L, S, sk, fx, fy):
        r = fit_curve(fx, fy, L.get('fit', 'none'))
        if r is None:
            if L.get('fit') in ('power', 'exp', 'log'):
                notes.append(ui('{layer}: {series} は近似できません（点が足りないか、0 以下の値があります）',
                                '{layer}: {series} cannot be fitted (too few points, or values of 0 or less)',
                                layer=L['name'], series=S['label'] or series_name(cfg, sk)))
            else:
                notes.append(ui('{layer}: {series} は近似できません（点か、異なる x の値が足りません）',
                                '{layer}: {series} cannot be fitted (too few points or too few different x values)',
                                layer=L['name'], series=S['label'] or series_name(cfg, sk)))
            return None
        ls_fit = L.get('fit_ls') or '--'
        fits.append((tax, r, S['color'], '-' if ls_fit == 'none' else ls_fit, lw, bool(L.get('fit_extend'))))
        if L.get('fit_eq'):
            fit_texts.append(((f"{S['label']}: " if S['label'] else '') + fit_equation(r), S['color']))
        return dict(layer=L['name'], series=series_name(cfg, sk), label=S['label'], model=r['model'],
                    a=r.get('a', np.nan), b=r.get('b', np.nan), c=r.get('c', np.nan), r2=r['r2'], n=r['n'],
                    x_min=r['x_min'], x_max=r['x_max'], equation=plain(fit_equation(r)))
    n_all, n_errs = [], []
    frames = [layer_frame(L, tables, cfg) for L in cfg['layers']]
    X = cfg['x']
    cat = bool(X.get('categorical'))
    # categorical order
    cats = [skey(c) for c in str(X.get('categories') or '').split(',') if c.strip()]
    if cat:
        for f, _ in frames:
            if f is not None:
                for v in f['_x'].unique():
                    if v not in cats:
                        cats.append(v)
    cpos = {c: i for i, c in enumerate(cats)}
    # series index within a layer type (side-by-side shift, bar slots, offset of spectra, rows of ticks)
    present = {}
    for (f, _), L in zip(frames, cfg['layers']):
        if f is not None:
            present.setdefault(L['type'], set()).update(f['_series'].unique())
    type_keys = {t: [k for k in keys if k in v] for t, v in present.items()}
    # categorical bars: the series that have a bar at each category share its width
    # (a stacked layer takes one place for all its series: its key is '§' + the layer key)
    stacks = [L for (f, _), L in zip(frames, cfg['layers']) if f is not None and is_stacked(L)]
    bar_slots = {}
    if cat:
        pairs = set()
        for (f, _), L in zip(frames, cfg['layers']):
            if f is not None and L['type'] == 'bar':
                if is_stacked(L):
                    pairs.update(('§' + layer_key(L), c) for c in f['_x'].unique())
                else:
                    pairs.update(zip(f['_series'], f['_x']))
        slot_keys = keys + ['§' + layer_key(L) for L in stacks]
        bar_slots = {c: [k for k in slot_keys if (k, c) in pairs] for c in cats}
    seg_labels = []               # values written inside stacked bars, after the axes are set: (axis, x, bottom, height, width, text, face)
    pct_axes = []                 # axes with 100 % stacked bars (0–100 unless the range is set)
    # x span for dodge / jitter
    xs = []
    for f, _ in frames:
        if f is not None and not f.empty and not cat:
            xs += [f['_x'].min(), f['_x'].max()]
    xlo = num_or_none(X.get('min')) or (min(xs) if xs else 1)
    xhi = num_or_none(X.get('max')) or (max(xs) if xs else 10)
    logx = X.get('scale') == 'log' and not cat
    # the axis scales are set after drawing (apply_axis): errorbar() on an axis already set to log adds a stray
    # data limit near 1, which stretched an automatic log x-axis down to about 1
    span = (math.log10(xhi / xlo) if logx and xlo > 0 and xhi > xlo else (xhi - xlo) or 1.0)

    def shift(x, off):
        x = np.asarray(x, float)
        return x * 10 ** (off * span) if logx else x + off * (1 if cat else span)

    def put_n(tax, xp, mean, lo, hi, ns, color, where):
        """n next to each mean: above the upper end of its error bar, or below the lower end."""
        tmpl = N_STYLE.get(ND.get('style', 'eq'), N_STYLE['eq'])
        logy = (cfg['y2'] if (ax2 is not None and tax is ax2) else cfg['y']).get('scale') == 'log'
        mean = np.asarray(mean, float)
        lo = np.zeros(len(mean)) if lo is None else np.nan_to_num(np.asarray(lo, float))
        hi = np.zeros(len(mean)) if hi is None else np.nan_to_num(np.asarray(hi, float))
        for x, m, a, b, n in zip(np.asarray(xp, float), mean, lo, hi, np.asarray(ns)):
            if not (np.isfinite(x) and np.isfinite(m)):
                continue
            y = m + b if where == 'above' else m - a
            if logy and y <= 0:
                y = m
            tax.annotate(tmpl.format(n=int(n)), (x, y), xytext=(0, 2.0 if where == 'above' else -2.0), textcoords='offset points',
                         ha='center', va='bottom' if where == 'above' else 'top', fontsize=F['note_pt'], color=color, zorder=4)

    rng = np.random.default_rng(0)
    J = max(0.0, float(F.get('jitter', 0.0) or 0.0))      # sideways scatter of the individual values (fraction of the axis width)
    ext = {'x': [], 'y': [], 'y2': []}      # points drawn (means, individual values, scatter), to tell when the axis range hides some

    def keep(xv=None, yv=None, right=False):
        if xv is not None and not cat:
            ext['x'].append(np.asarray(xv, float))
        if yv is not None:
            ext['y2' if right else 'y'].append(np.asarray(yv, float))
    for (f, bad), L in zip(frames, cfg['layers']):
        if f is None or f.empty:
            if L.get('source') and L['source'] not in tables:
                notes.append(ui('{name}: CSV「{source}」がアップロードされていないので描いていません', '{name}: not drawn because the CSV file “{source}” has not been uploaded', name=L['name'], source=L['source']))
            else:
                notes.append(ui('{name}: 描く値がありません（データ・列を確認）', '{name}: nothing to draw (check the data and the columns)', name=L['name']))
            continue
        tax = ax
        if L.get('axis') == 'right':
            if ax2 is None:
                ax2 = ax.twinx()
                ax2.spines['right'].set_visible(True)
            tax = ax2
        sers = [k for k in keys if k in set(f['_series'])]
        right = tax is not ax
        lw = num_or_none(L.get('lw')) or F['line_lw']
        ms = num_or_none(L.get('ms')) or F['marker_size']
        t = L['type']
        G = type_keys.get(t, sers)
        kk = len(G)
        fit_rows = []
        if t in ('mean_error', 'bar'):
            stacked = is_stacked(L)
            s, u = aggregate(f, 'sd' if stacked else L.get('err', 'sd'))
            s['_series'] = pd.Categorical(s['_series'], sers, ordered=True)
            s = s.sort_values(['_series', '_x'], key=lambda c: c.map(cpos) if (cat and c.name == '_x') else c)
            n_show = L.get('n_show', 'none')
            lab_plain = {}
            if stacked:
                # 積み上げ棒：同じ x の系列を「系列の順」に下から積む（同じ x・系列の行が複数あれば、その平均）。誤差棒・個々の点・n は描かない
                pct = L.get('stack') == 'percent'
                s = s.copy()
                xk = s['_x'].astype(str)
                tot = s.groupby(xk, sort=False)['mean'].sum()
                if pct:
                    den = xk.map(tot).astype(float)
                    s['percent'] = np.where(den != 0, 100.0 * s['mean'] / den.where(den != 0, 1.0), np.nan)
                    s['height'] = s['percent']
                    off = [(k, v) for k, v in tot.items() if abs(v - 100.0) > 0.05]
                    if off:
                        notes.append(ui('{name}: x ごとの合計を 100 % に揃えて描いています（合計 ', '{name}: drawn with each x scaled to a total of 100% (totals ', name=L['name'])
                                     + ui('、', ', ').join(f'{k}: {v:.4g}' for k, v in off[:6]) + (ui('、…', ', …') if len(off) > 6 else '') + ui('）', ')'))
                    if all(tax is not a_ for a_ in pct_axes):
                        pct_axes.append(tax)
                else:
                    s['height'] = s['mean']
                if (s['height'] < 0).any():
                    notes.append(ui('{name}: 負の値は積み上げられないので、0 として描いています', '{name}: negative values cannot be stacked and are drawn as 0', name=L['name']))
                    s['height'] = s['height'].clip(lower=0)
                s['stack_bottom'] = s.groupby(xk, sort=False)['height'].cumsum() - s['height']
                s['stack_top'] = s['stack_bottom'] + s['height']
                bwid = float(L.get('bar_width') or 0.8)
                skey_ = '§' + layer_key(L)
                si = next((i for i, M in enumerate(stacks) if M is L), 0)
                sn = max(len(stacks), 1)
                dec = int(num_or_none(L.get('seg_decimals')) or 0)
                hand = []
                for sk in sers:
                    S = layer_style(cfg, L, sk)
                    lab_plain[sk] = S['label']
                    ss = s[s['_series'] == sk]
                    if ss.empty:
                        continue
                    xv = ss['_x'].map(cpos).to_numpy(float) if cat else ss['_x'].to_numpy(float)
                    if cat:
                        slots = [bar_slots.get(c) or [skey_] for c in ss['_x']]
                        m_ = np.array([max(len(sl), 1) for sl in slots], float)
                        j_ = np.array([sl.index(skey_) if skey_ in sl else 0 for sl in slots], float)
                        bw = bwid / m_
                        xb = xv + (j_ - (m_ - 1) / 2) * bw
                    else:
                        xb = shift(xv, (si - (sn - 1) / 2) * float(L.get('dodge') or 0))
                        bw = np.full(len(xv), bwid * 0.05 * span)
                    filled = S.get('filled', True)
                    face = S['color'] if filled else 'white'
                    edge = '#1A1A1A' if filled else S['color']
                    tax.bar(xb, ss['height'], bottom=ss['stack_bottom'], width=bw, color=face, edgecolor=edge,
                            linewidth=F['axes_lw'], zorder=2)
                    keep(xb, ss['stack_top'], right)
                    if L.get('seg_values', True):
                        vals = ss['percent'] if pct else ss['mean']
                        for x_, b_, h_, w_, v_ in zip(xb, ss['stack_bottom'], ss['height'], np.broadcast_to(bw, len(xb)), vals):
                            if np.isfinite(v_) and np.isfinite(h_) and h_ > 0:
                                seg_labels.append((tax, float(x_), float(b_), float(h_), float(w_), f'{v_:.{dec}f}', face))
                    if L.get('in_legend', True):
                        hand.append((S['label'], Line2D([], [], marker='s', ls='none', mfc=face, mec=edge, mew=F['axes_lw'], ms=ms * 1.3)))
                for lab_, h_ in reversed(hand):            # 凡例は積んだ順の逆（いちばん上の区画が凡例の上）
                    handles.setdefault(lab_, h_)
            for sk in ([] if stacked else sers):
                S = layer_style(cfg, L, sk)
                gi = G.index(sk) if sk in G else 0
                ss = s[s['_series'] == sk]
                uu = u[u['_series'] == sk]
                xv = ss['_x'].map(cpos).to_numpy(float) if cat else ss['_x'].to_numpy(float)
                xu = uu['_x'].map(cpos).to_numpy(float) if cat else uu['_x'].to_numpy(float)
                lo, hi = err_values(ss, L.get('err', 'sd'))
                yerr = None if lo is None else np.vstack([np.nan_to_num(lo), np.nan_to_num(hi)])
                xlo, xhi = xerr_values(ss, L.get('err', 'sd')) if L.get('xbars', True) else (None, None)
                xerr = None if xlo is None or cat else np.vstack([np.nan_to_num(xlo), np.nan_to_num(xhi)])
                grouped = '_g' in f.columns
                if L.get('pts') == 'row' and (f['_unit'] != '').any():      # 薄い点は 1 行ずつ（計算は単位ごとのまま）
                    fr = f[f['_series'] == sk]
                    px_raw = fr['_x'].map(cpos).to_numpy(float) if cat else fr['_x'].to_numpy(float)
                    py_raw = fr['_y'].to_numpy(float)
                else:
                    px_raw, py_raw = xu, uu['y'].to_numpy(float)
                lab_plain[sk] = S['label']
                lab = S['label']
                if n_show == 'legend' and lab:
                    lab = f"{lab} ({N_STYLE['eq'].format(n=n_range(ss['n']))})"
                n_all.extend(int(v) for v in ss['n'])
                keep(xv, ss['mean'], right)
                if t == 'bar':
                    bwid = float(L.get('bar_width') or 0.8)
                    if cat:
                        cx = list(ss['_x'])
                        slots = [bar_slots.get(c) or [sk] for c in cx]
                        m_ = np.array([max(len(sl), 1) for sl in slots], float)
                        j_ = np.array([sl.index(sk) if sk in sl else 0 for sl in slots], float)
                        bw = bwid / m_
                        xb = xv + (j_ - (m_ - 1) / 2) * bw
                    else:
                        xb = shift(xv, (gi - (kk - 1) / 2) * float(L.get('dodge') or 0))
                        bw = bwid * 0.05 * span
                    tax.bar(xb, ss['mean'], width=bw, color=S['color'] if S.get('filled', True) else 'white',
                            edgecolor=S['color'], linewidth=F['axes_lw'], zorder=2,
                            yerr=yerr, error_kw=dict(elinewidth=0.6, capsize=F['capsize'], capthick=0.6, ecolor='#1A1A1A'))
                    if L.get('points', True) and (uu['n_members'] > 0).any():
                        if cat:
                            offs = dict(zip(cx, np.asarray(xb, float) - xv))
                            wids = dict(zip(cx, np.broadcast_to(np.asarray(bw, float), (len(cx),))))
                            px = xu + uu['_x'].map(offs).to_numpy(float)
                            pw = uu['_x'].map(wids).to_numpy(float)
                        else:
                            px = shift(xu, (gi - (kk - 1) / 2) * float(L.get('dodge') or 0))
                            pw = np.full(len(uu), float(bw))
                        jit = rng.uniform(-1, 1, len(uu)) * min(0.45, 0.25 * J / 0.012) * pw
                        keep(None, uu['y'], right)
                        tax.plot(px + jit, uu['y'], 'o', ms=F['point_size'], color='#1A1A1A', alpha=F['point_alpha'], mec='none', zorder=3)
                    if n_show in ('above', 'below'):
                        put_n(tax, xb, ss['mean'], lo, hi, ss['n'], '#1A1A1A', n_show)
                    if L.get('in_legend', True):
                        handles.setdefault(lab, Line2D([], [], marker='s', ls='none', mfc=S['color'] if S.get('filled', True) else 'white',
                                                       mec=S['color'], ms=ms * 1.3))
                else:
                    # x が測った値（グループの列）なら、平均も個々の点も実際の x の位置のまま（横にずらさない・散らさない）
                    dd = 0.0 if grouped else (gi - (kk - 1) / 2) * float(L.get('dodge') or 0)
                    if L.get('points', True) and (uu['n_members'] > 0).any() and len(py_raw) > len(ss):
                        jx = shift(px_raw, dd)
                        if not grouped:                                   # x が測った値なら、その x のまま（散らさない）
                            jx = jx * 10 ** (rng.uniform(-J, J, len(jx)) * span) if logx else jx + rng.uniform(-J, J, len(jx)) * (1 if cat else span) * (8 if cat else 1)
                        keep(px_raw if grouped else None, py_raw, right)
                        tax.plot(jx, py_raw, 'o', ms=F['point_size'], color=S['color'], alpha=F['point_alpha'], mec='none', zorder=1)
                    ls = S.get('ls', '-') if L.get('connect', True) else 'none'
                    mk = S.get('marker', 'o')
                    xp = shift(xv, dd)
                    am = marker_alpha(L, F.get('marker_alpha', 1.0))
                    mfc = to_rgba(S['color'] if S.get('filled', True) else 'white', am)
                    mec = to_rgba('white' if S.get('filled', True) else S['color'], am)
                    tax.errorbar(xp, ss['mean'], yerr=yerr, xerr=xerr, fmt='none' if mk == 'none' else mk, ls=ls, color=S['color'],
                                 mfc=mfc, mec=mec, mew=0.6 if S.get('filled', True) else 0.8, ms=ms, lw=lw,
                                 capsize=F['capsize'], elinewidth=0.6, capthick=0.6, zorder=3)
                    if n_show in ('above', 'below'):
                        put_n(tax, xp, ss['mean'], lo, hi, ss['n'], S['color'], n_show)
                    if L.get('in_legend', True):
                        handles.setdefault(lab, Line2D([], [], color=S['color'], marker=None if mk == 'none' else mk, ls=ls,
                                                       mfc=mfc, mec=mec, ms=ms, lw=lw))
                    if L.get('fit', 'none') != 'none' and not cat:
                        if L.get('fit_on') == 'means':
                            fx, fy = ss['_x'].to_numpy(float), ss['mean'].to_numpy(float)
                        else:
                            fr = f[f['_series'] == sk]
                            fx, fy = fr['_x'].to_numpy(float), fr['_y'].to_numpy(float)
                        fr_ = add_fit(tax, L, S, sk, fx, fy)
                        if fr_:
                            fit_rows.append(fr_)
            if not stacked:
                n_errs.append(L.get('err', 'sd'))
            out = s.rename(columns={'_x': 'x', '_g': 'group'}).drop(columns=['x_mean'], errors='ignore').copy()
            sk_ = out['_series'].astype(str).to_numpy()
            out = out.drop(columns=['_series'])
            out.insert(0, 'layer', L['name'])
            out.insert(1, 'series', [series_name(cfg, k) for k in sk_])
            out.insert(2, 'label', [lab_plain.get(k, '') for k in sk_])
            out['error_bar'] = 'none' if stacked else L.get('err', 'sd')
            plotted.append((L['name'], out))
            uo = u.rename(columns={'_x': 'x', '_g': 'group', '_unit': 'unit', 'y': 'unit_mean', 'rows': 'csv_rows'}).copy()
            uk = uo['_series'].astype(str).to_numpy()
            uo = uo.drop(columns=['_series'])
            uo.insert(0, 'layer', L['name'])
            uo.insert(1, 'series', [series_name(cfg, k) for k in uk])
            plotted.append((L['name'] + ui('（単位ごと）', ' (per unit)'), uo))
        elif t in ('line', 'scatter'):
            rows = []
            order = G[::-1] if L.get('reverse_offset') else G
            for sk in [k for k in order if k in sers]:
                i = order.index(sk)
                S = layer_style(cfg, L, sk)
                g = f[f['_series'] == sk]
                if L.get('sort'):
                    g = g.sort_values('_x')
                x = g['_x'].to_numpy(float) if not cat else g['_x'].map(cpos).to_numpy(float)
                y = g['_y'].to_numpy(float)
                a, b = num_or_none(L.get('xmin')), num_or_none(L.get('xmax'))
                m = np.ones(len(x), bool)
                if a is not None:
                    m &= x >= a
                if b is not None:
                    m &= x <= b
                x, y = x[m], y[m]
                if not len(x):
                    continue
                norm = L.get('norm', 'none')
                x0 = num_or_none(L.get('x0'))
                if norm == 'max':
                    y = y / np.nanmax(y)
                elif norm == 'minmax':
                    y = (y - np.nanmin(y)) / (np.nanmax(y) - np.nanmin(y))
                elif norm in ('sub_x0', 'div_x0') and x0 is not None:
                    y0 = y[int(np.argmin(np.abs(x - x0)))]
                    y = y - y0 if norm == 'sub_x0' else y / y0
                y = y + i * float(L.get('offset') or 0)
                if t == 'scatter':        # spectra are often cut to a range on purpose, so only points are checked
                    keep(x, y, right)
                if t == 'line':
                    tax.plot(x, y, color=S['color'], lw=lw, ls=S.get('ls', '-') if S.get('ls', '-') != 'none' else '-', zorder=2)
                    if L.get('trace_labels'):
                        tax.annotate(rich(S['label']), (x[-1] if x[-1] > x[0] else x[0], y[-1] if x[-1] > x[0] else y[0]),
                                     xytext=(2, 0), textcoords='offset points', fontsize=F['note_pt'], va='bottom', ha='right', color='#1A1A1A')
                    if L.get('in_legend', True) and not L.get('trace_labels'):
                        handles.setdefault(S['label'], Line2D([], [], color=S['color'], lw=lw, ls=S.get('ls', '-') if S.get('ls', '-') != 'none' else '-'))
                else:
                    mk = S.get('marker', 'o') if S.get('marker', 'o') != 'none' else 'o'
                    tax.plot(x, y, mk, ls='none', color=S['color'], mfc=S['color'] if S.get('filled', True) else 'none',
                             mec=S['color'], mew=0.6, ms=ms, alpha=marker_alpha(L, 0.85), zorder=2)
                    if L.get('in_legend', True):
                        handles.setdefault(S['label'], Line2D([], [], marker=mk, ls='none', color=S['color'],
                                                              mfc=S['color'] if S.get('filled', True) else 'none', mec=S['color'], ms=ms))
                    if L.get('fit', 'none') != 'none' and not cat:
                        fr_ = add_fit(tax, L, S, sk, x, y)
                        if fr_:
                            fit_rows.append(fr_)
                rows.append(pd.DataFrame({'layer': L['name'], 'series': series_name(cfg, sk), 'label': S['label'], 'x': x, 'y_plotted': y}))
            if rows:
                plotted.append((L['name'], pd.concat(rows, ignore_index=True)))
        elif t == 'ticks':
            ytr = tax.get_xaxis_transform()
            ty = num_or_none(L.get('tick_y'))
            th = num_or_none(L.get('tick_h'))
            tg = num_or_none(L.get('tick_gap'))
            ty, th, tg = (0.04 if ty is None else ty), (0.05 if th is None else th), (0.06 if tg is None else tg)
            for sk in sers:
                i = G.index(sk) if sk in G else 0
                S = layer_style(cfg, L, sk)
                xv = f[f['_series'] == sk]['_x'].dropna().to_numpy(float)
                y0 = ty + i * tg
                if L.get('tick_style', 'vline') == 'vline':
                    tax.vlines(xv, y0, y0 + th, transform=ytr, color=S['color'], lw=lw, zorder=2)
                    hd = Line2D([], [], marker='|', ls='none', color=S['color'], ms=ms * 1.5, mew=lw)
                else:
                    mk = S.get('marker', 'v') if S.get('marker', 'v') != 'none' else 'v'
                    tax.plot(xv, np.full(len(xv), y0), mk, transform=ytr, ls='none', color=S['color'],
                             mfc=S['color'] if S.get('filled', True) else 'none', mec=S['color'], ms=ms, zorder=2, clip_on=False)
                    hd = Line2D([], [], marker=mk, ls='none', color=S['color'], mfc=S['color'] if S.get('filled', True) else 'none', mec=S['color'], ms=ms)
                if L.get('in_legend', True):
                    handles.setdefault(S['label'], hd)
            tk = f[['_series', '_x']].rename(columns={'_series': 'series', '_x': 'x'}).copy()
            tk['series'] = [series_name(cfg, k) for k in tk['series']]
            plotted.append((L['name'], tk.assign(layer=L['name'])))
        if fit_rows:
            plotted.append((L['name'] + ui('（近似）', ' (fit)'), pd.DataFrame(fit_rows)))
        if bad is not None and len(bad):
            notes.append(ui('{name}: 数値でない／空の値 {len_bad} 行を除外（CSV の行 ', '{name}: {len_bad} non-numeric or empty rows left out (CSV rows ', name=L['name'], len_bad=len(bad)) + ', '.join(map(str, bad['_row'].head(12))) + (' …' if len(bad) > 12 else '') + ui('）', ')'))
    # categorical ticks
    if cat:
        ax.set_xticks(range(len(cats)))
        ax.set_xticklabels([rich(t.strip()) for t in str(X.get('ticklabels') or '').split(',')] if X.get('ticklabels') and len(str(X.get('ticklabels')).split(',')) == len(cats)
                           else [rich(nice_label(c)) for c in cats])
        ax.set_xlim(-0.6, len(cats) - 0.4)
        ax.tick_params(axis='x', which='minor', bottom=False)
    # spans, reference lines, annotations
    for p in parse_lines(cfg.get('spans'), 2):
        a, b = num_or_none(p[0]), num_or_none(p[1])
        if a is None or b is None:
            continue
        col = p[2] if len(p) > 2 and p[2] else '#BDBDBD'
        al = num_or_none(p[3]) if len(p) > 3 else None
        ax.axvspan(a, b, color=col, alpha=al if al is not None else 0.2, lw=0, zorder=0)
        if len(p) > 4 and p[4]:
            ax.text((a * b) ** 0.5 if logx and a > 0 else (a + b) / 2, 0.98, rich(p[4]), transform=ax.get_xaxis_transform(), ha='center', va='top',
                    fontsize=F['note_pt'], color='#4D4D4D')
    for v in num_list(cfg.get('hlines')):
        ax.axhline(v, color='#1A1A1A', lw=0.5, zorder=1)
    for v in num_list(cfg.get('vlines')):
        ax.axvline(v, color='#6F6F6F', lw=0.5, ls='--', zorder=1)
    apply_axis(ax, X, 'x', F)
    apply_axis(ax, cfg['y'], 'y', F)
    if ax2 is not None:
        apply_axis(ax2, cfg['y2'], 'y', F)
    for tax in pct_axes:              # 100 % 積み上げ棒の軸は、範囲を指定していなければ 0–100 ちょうど
        A = cfg['y2'] if (ax2 is not None and tax is ax2) else cfg['y']
        if num_or_none(A.get('min')) is None and num_or_none(A.get('max')) is None and A.get('scale') != 'log':
            lo_, hi_ = tax.get_ylim()
            if lo_ >= -1e-9 and hi_ <= 105.0 + 1e-6:         # ほかに 0–100 からはみ出す値がなければ
                tax.set_ylim(0, 100)
    for tax, r, col, ls_fit, lw_fit, to_ends in fits:
        lo_, hi_ = tax.get_xlim() if to_ends else (r['x_min'], r['x_max'])
        if r['model'] in ('power', 'log'):
            lo_ = max(lo_, r['x_min'] * 1e-3 if to_ends else r['x_min'])
        xs_ = np.geomspace(lo_, hi_, 300) if logx and lo_ > 0 else np.linspace(lo_, hi_, 300)
        with np.errstate(all='ignore'):
            tax.plot(xs_, r['f'](xs_), ls=ls_fit, color=col, lw=lw_fit, zorder=2, scalex=False, scaley=False)
    if fit_texts:
        px, py, ha, va = NOTE_POS.get(cfg.get('fit_eq_loc', 'upper right'), NOTE_POS['upper right'])
        k = len(fit_texts)
        for i, (txt, col) in enumerate(fit_texts):
            dy = -i * F['note_pt'] * 1.4 if va == 'top' else (k - 1 - i) * F['note_pt'] * 1.4
            ax.annotate(rich(txt), (px, py), xycoords='axes fraction', xytext=(0, dy), textcoords='offset points',
                        ha=ha, va=va, fontsize=F['note_pt'], color=col, zorder=5)
    if F.get('mirror_ticks'):
        if F.get('frame_top'):
            ax.tick_params(axis='x', which='major', top=True)
            ax.tick_params(axis='x', which='minor', top=not cat)
        if F.get('frame_right') and ax2 is None:
            ax.tick_params(axis='y', which='both', right=True)
    if F.get('grid'):
        ax.grid(True, lw=0.3, color='#D9D9D9', zorder=0)
    for a, b, na, nb, kind in x_mismatches(cfg, tables, frames):
        notes.append(x_mismatch_text(a, b, na, nb, kind))
    for key, A, name in (('x', X, ui('x 軸', 'The x axis')), ('y', cfg['y'], ui('y 軸', 'The y axis')), ('y2', cfg['y2'], ui('右の y 軸', 'The right y axis'))):
        if ext[key] and not (key == 'y2' and ax2 is None):
            msg = out_of_range_text(np.concatenate(ext[key]), A, name)
            if msg:
                notes.append(msg)
    for p in parse_lines(cfg.get('annotations'), 3):
        x, y = num_or_none(p[0]), num_or_none(p[1])
        if x is None or y is None:
            continue
        coords = p[3] if len(p) > 3 and p[3] in ('axes', 'data') else 'axes'
        ha = p[4] if len(p) > 4 and p[4] in ('left', 'center', 'right') else 'left'
        ax.text(x, y, rich(p[2]), transform=ax.transAxes if coords == 'axes' else ax.transData, ha=ha, va='center',
                fontsize=F['note_pt'], color='#4D4D4D')
    # "Mean ± s.d., n = 9–16 indentations" from the n actually plotted
    if ND.get('note') and n_all:
        txt = n_note_text(n_errs, n_all, ND.get('unit'))
        px, py, ha, va = NOTE_POS.get(ND.get('loc', 'lower right'), NOTE_POS['lower right'])
        ax.text(px, py, txt, transform=ax.transAxes, ha=ha, va=va, fontsize=F['note_pt'], color='#4D4D4D', zorder=5)
    Lg = cfg['legend']
    items = [(k, h) for k, h in handles.items() if str(k).strip()]
    if Lg.get('show') and items:
        loc = Lg.get('loc', 'best')
        if loc == 'best' and pct_axes:          # 100 % 積み上げ棒は枠の中に空きがないので、自動なら枠の外（右）
            loc = 'outside right'
        kw = dict(ncol=int(Lg.get('ncol') or 1), title=rich(Lg.get('title')) or None, frameon=bool(Lg.get('frame')))
        hs, labs = [h for _, h in items], [rich(k) for k, _ in items]
        if loc == 'outside right':
            fig.legend(hs, labs, loc='outside right upper', **kw)
        elif loc == 'outside top':
            fig.legend(hs, labs, loc='outside upper center', **kw)
        else:
            ax.legend(hs, labs, loc=loc, **kw)
    if F.get('letter'):
        ttl = ax.set_title(F['letter'], loc='left', fontsize=F['letter_pt'], fontweight='bold', pad=5)
        ttl.set_x(float(F.get('letter_x', -0.15)))
    if seg_labels:
        put_segment_values(fig, seg_labels, F['tick_pt'])
    return fig, plotted, notes


def is_stacked(L):
    return L.get('type') == 'bar' and L.get('stack', 'none') in ('stack', 'percent')


def text_on(face):
    """Black or white text, whichever has the higher contrast on a fill colour."""
    if str(face).strip().lower() in ('white', '#ffffff', '#fff', 'none', ''):
        return '#1A1A1A'
    lum = 1.05 / contrast_on_white(face) - 0.05
    return 'white' if 1.05 / (lum + 0.05) >= (lum + 0.05) / 0.05 else '#1A1A1A'


def put_segment_values(fig, seg_labels, size):
    """Values inside the parts of stacked bars, measured on the finished layout: a value that does not fit in its part
    (too low or too narrow for the text) is left out rather than spilling over the neighbours."""
    try:
        fig.draw_without_rendering()          # constrained layout: the final size of the axes
    except Exception:
        pass
    k = 72.0 / fig.dpi
    for tax, x_, b_, h_, w_, txt, face in seg_labels:
        (x0, y0), (x1, y1) = tax.transData.transform([(x_ - w_ / 2, b_), (x_ + w_ / 2, b_ + h_)])
        hpt, wpt = abs(y1 - y0) * k, abs(x1 - x0) * k
        lo, hi = sorted(tax.get_ylim())
        yc = b_ + h_ / 2
        if hpt < size * 1.15 or wpt < len(txt) * size * 0.6 + 2 or not (lo <= yc <= hi):
            continue
        tax.text(x_, yc, txt, ha='center', va='center', fontsize=size, color=text_on(face), zorder=4, clip_on=True)


def x_mismatches(cfg, tables, frames=None):
    """x values of mean ± error / bar layers that nearly coincide but are not equal: present in one series and not the
    other (relative difference < 5 %, e.g. 49 N in one CSV and 50 N in the other), or both inside one series
    (< 1 %, e.g. 98 and 98.1). Such points are drawn side by side instead of above each other.
    Returns [(a, b, series with a, series with b, 'between' | 'within')]."""
    if cfg['x'].get('categorical'):
        return []
    groups = []
    for i, L in enumerate(cfg['layers']):
        if L['type'] not in ('mean_error', 'bar') or L.get('group') or (L.get('err') == 'column' and L.get('xerr_col')):
            continue                                       # x が測った値（グループでまとめる・x の誤差の列がある）なら調べない
        f = frames[i][0] if frames is not None else layer_frame(L, tables, cfg)[0]
        if f is None or f.empty:
            continue
        for sk, g in f.groupby('_series', sort=False):
            lab = plain(cfg['series'].get(sk, {}).get('label') or '') or series_name(cfg, sk)
            xs = {float(v) for v in pd.to_numeric(g['_x'], errors='coerce') if np.isfinite(v)}
            groups.append((lab, xs))
    allx = sorted(set().union(*[s for _, s in groups])) if groups else []
    out = []
    for a, b in zip(allx, allx[1:]):
        rel = (b - a) / max(abs(a), abs(b))
        if not 0 < rel < 0.05:
            continue
        only_a = list(dict.fromkeys(n for n, s in groups if a in s and b not in s))
        only_b = list(dict.fromkeys(n for n, s in groups if b in s and a not in s))
        both = list(dict.fromkeys(n for n, s in groups if a in s and b in s))
        if only_a and only_b:
            out.append((a, b, only_a, only_b, 'between'))
        elif both and rel < 0.01:
            out.append((a, b, both, both, 'within'))
    return out


def out_of_range_text(v, A, name):
    """Values hidden by a fixed axis range (or by a log axis), e.g. after switching a preset to other data."""
    v = np.asarray(v, float)
    v = v[np.isfinite(v)]
    if not len(v):
        return ''
    lo, hi = num_or_none(A.get('min')), num_or_none(A.get('max'))
    out = np.zeros(len(v), bool)
    if lo is not None:
        out |= v < lo - 1e-9 * max(1.0, abs(lo))
    if hi is not None:
        out |= v > hi + 1e-9 * max(1.0, abs(hi))
    msg = []
    if out.any():
        rng = ui('{v}〜{v2}', '{v}–{v2}', v='' if lo is None else f'{lo:g}', v2='' if hi is None else f'{hi:g}')
        what = ui('にデータが入っていません', 'contains no data') if out.all() else ui('から外れたデータがあり、その分は図に出ていません', 'leaves out some data, which are not shown')
        msg.append(ui('{name}の範囲（{rng}）{what}（データは {v:g}〜{v2:g}）。「軸」タブの最小・最大を確認してください。', '{name} range ({rng}) {what} (data: {v:g} to {v2:g}). Check the min and max in the Axes tab.', name=name, rng=rng, what=what, v=v.min(), v2=v.max()))
    if A.get('scale') == 'log' and (v <= 0).any():
        msg.append(ui('{name}は log なので、0 以下のデータ（{v} 個）は図に出ていません。', '{name} is logarithmic, so {v} values of 0 or less are not shown.', name=name, v=int((v <= 0).sum())))
    return ui('', ' ').join(msg)


def x_mismatch_text(a, b, na, nb, kind):
    sep = ui('、', ', ')
    if kind == 'between':
        return ui('x の値が近いのに一致しません：{na} は {a:g}、{nb} は {b:g}。同じ条件なら CSV の値を揃えてください'
                  '（揃っていないと点が横にずれて描かれます）。',
                  'x values are close but not equal: {na} has {a:g}, {nb} has {b:g}. If the conditions are the same, '
                  'make the CSV values equal (otherwise the points are drawn side by side).',
                  na=sep.join(na), nb=sep.join(nb), a=a, b=b)
    return ui('{na} の中に x = {a:g} と {b:g} があります。同じ条件なら値を揃えてください（別々の点として平均されます）。',
              '{na} contains x = {a:g} and {b:g}. If the conditions are the same, make the values equal (otherwise they '
              'are averaged as separate points).',
              na=sep.join(na), a=a, b=b)


def n_note_text(errs, ns, unit=''):
    """'Mean ± s.d., $n$ = 9–16 indentations' (mathtext) from the error types and n of the plotted means."""
    e = [ERR_TEXT.get(x, '') for x in dict.fromkeys(errs)]
    e = [x for x in e if x]
    head = ('Mean ± ' + ' / '.join(e)) if e else 'Mean'
    unit = str(unit or '').strip()
    return f"{head}, {N_STYLE['eq'].format(n=n_range(ns))}" + (f' {unit}' if unit else '')


def n_table(plotted):
    """n of every plotted mean (mean ± error and bar layers): layer, series, label, x, n, error_bar."""
    rows = [d for _, d in (plotted or []) if {'mean', 'n', 'label'} <= set(d.columns)]
    rows = [d for d in rows if not ('stack_top' in d.columns and (d['n'] <= 1).all())]   # 積み上げ棒で 1 行 1 区画なら n はない
    if not rows:
        return pd.DataFrame(columns=['layer', 'series', 'label', 'x', 'n', 'error_bar'])
    rows = [d.assign(x=d['group']) if 'group' in d.columns else d for d in rows]     # x が測った値なら、グループ（荷重など）で並べる
    return pd.concat(rows, ignore_index=True)[['layer', 'series', 'label', 'x', 'n', 'error_bar']]


# 文字の飾り（図の文字すべて）：
#   _{…} 下付き、^{…} 上付き（^ の後が数字だけなら { } は省ける：m^2、s^-1）、<i>…</i> 斜体、<b>…</b> 太字、
#   <sub>…</sub>・<sup>…</sup> も同じ。$…$ の中（matplotlib の数式）はそのまま。\_ と \^ は文字のまま。
_TAG = re.compile(r'<(/?)(i|b|sub|sup)>', re.I)
_SUPNUM = re.compile(r'\^([+\-−]?\d+(?:\.\d+)?)')


def _runs(s, it=0, bd=0, script=None):
    """Split marked-up text into ('math', text) and ('text', text, italic, bold, script) pieces."""
    out, buf, i = [], [], 0

    def flush():
        if buf:
            out.append(('text', ''.join(buf), it > 0, bd > 0, script))
            buf.clear()
    while i < len(s):
        c = s[i]
        if c == '\\' and i + 1 < len(s) and s[i + 1] in '_^$<':
            buf.append(s[i + 1] if s[i + 1] != '$' else '\\$')
            i += 2
            continue
        if c == '$':
            j = i + 1
            while j < len(s) and not (s[j] == '$' and s[j - 1] != '\\'):
                j += 1
            if j < len(s):
                flush()
                out.append(('math', s[i:j + 1]))
                i = j + 1
                continue
        m = _TAG.match(s, i)
        if m:
            flush()
            tag, closing = m.group(2).lower(), m.group(1) == '/'
            if tag == 'i':
                it = max(0, it - 1) if closing else it + 1
            elif tag == 'b':
                bd = max(0, bd - 1) if closing else bd + 1
            elif script is None or closing:
                k = s.lower().find(f'</{tag}>', m.end()) if not closing else -1
                if not closing:
                    inner_end = k if k >= 0 else len(s)
                    out.extend(_runs(s[m.end():inner_end], it, bd, tag))
                    i = inner_end + (len(tag) + 3 if k >= 0 else 0)
                    continue
            i = m.end()
            continue
        if c in '_^' and i + 1 < len(s) and s[i + 1] == '{':
            j = s.find('}', i + 2)
            if j > i + 2:
                flush()
                out.extend(_runs(s[i + 2:j], it, bd, 'sub' if c == '_' else 'sup'))
                i = j + 1
                continue
        m = _SUPNUM.match(s, i) if c == '^' and script is None else None
        if m:
            flush()
            out.append(('text', m.group(1), it > 0, bd > 0, 'sup'))
            i = m.end()
            continue
        buf.append(c)
        i += 1
    flush()
    return out


def _mathrm(t):
    """Text inside \\mathrm{} etc.: keep spaces, escape % # & { }, and write + and − without the extra space mathtext puts
    around binary operators (Eu^{3+} → 3+, not 3 +)."""
    t = t.replace('−', '-')
    t = re.sub(r'([ %#&{}])', lambda m: '\\ ' if m.group(1) == ' ' else '\\' + m.group(1), t)
    return re.sub(r'([+\-])', r'{\1}', t)


def rich(s):
    """Text for the figure: 'K_{IC} (MPa m^{1/2})', 'Yb_{2}Si_{2}O_{7}', 'cm^-1', 'Δ<i>l</i>' (italic l), '<b>a</b>'."""
    s = '' if s is None else str(s)
    if not any(ch in s for ch in '_^<'):
        return s
    if len(re.findall(r'(?<!\\)\$', s)) % 2:          # an odd number of $: leave it as it is
        return s
    out, group, gscript = [], [], None

    def close():
        if group:
            body = ''.join(group)
            out.append('$' + {'sub': '_{', 'sup': '^{'}.get(gscript, '') + body + ('}' if gscript else '') + '$')
            group.clear()
    for r in _runs(s):
        if r[0] == 'math':
            close()
            out.append(r[1])
            continue
        _, t, it, bd, script = r
        if not it and not bd and script is None:
            close()
            out.append(t)
            continue
        cmd = '\\mathbf' if bd else ('\\mathit' if it else '\\mathrm')
        if script != gscript:
            close()
            gscript = script
        group.append(f'{cmd}{{{_mathrm(t)}}}')
    close()
    return ''.join(out)


SUB_CHARS = str.maketrans('0123456789+-−=()', '₀₁₂₃₄₅₆₇₈₉₊₋₋₌₍₎')
SUP_CHARS = str.maketrans('0123456789+-−=()', '⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁻⁼⁽⁾')


def plain(s):
    """Text for captions and tables: '$H_\\mathrm{V}$' → 'H_V', 'Yb_{2}Si_{2}O_{7}' → 'Yb₂Si₂O₇', 'm^{1/2}' → 'm^(1/2)',
    'Δ<i>l</i>' → 'Δl'."""
    out = []
    for r in _runs(str(s or '')):
        if r[0] == 'math':
            m = re.sub(r'\\math(?:rm|it|bf|sf)\{([^}]*)\}', r'\1', r[1][1:-1])
            m = re.sub(r'([_^])\{([^{}]*)\}', lambda q: q.group(1) + q.group(2), m)
            out.append(m.replace('{', '').replace('}', '').replace('\\', ''))
            continue
        _, t, it, bd, script = r
        if script is None:
            out.append(t)
        elif all(ch in '0123456789+-−=()' for ch in t):
            out.append(t.translate(SUB_CHARS if script == 'sub' else SUP_CHARS))
        elif script == 'sub':
            out.append('_' + (t if ' ' not in t else f'({t})'))
        else:
            out.append('^' + (t if len(t) <= 1 else f'({t})'))
    return ''.join(out)


def n_caption(cfg, plotted):
    """A sentence for the figure legend, e.g. 'Mean ± s.d.; n = 9–16 indentations per data point (5 vol% SiC: 9–12; …).'"""
    nt = n_table(plotted)
    if nt.empty:
        return ''
    unit = str(cfg.get('n_display', {}).get('unit') or '').strip()
    e = [ERR_TEXT.get(x, '') for x in dict.fromkeys(nt['error_bar'])]
    e = [x for x in e if x]
    head = ('Mean ± ' + ' / '.join(e)) if e else 'Mean'
    txt = f"{head}; n = {n_range(nt['n'])}{' ' + unit if unit else ''} per data point"
    groups = list(dict.fromkeys(zip(nt['layer'], nt['label'])))
    if len(groups) > 1:
        parts = []
        for lay, lab in groups:
            g = nt[(nt['layer'] == lay) & (nt['label'] == lab)]
            parts.append(f"{plain(lab) or lay}: {n_range(g['n'])}")
        txt += ' (' + '; '.join(parts) + ')'
    return txt + '.'


def png_to_tiff(png, dpi=600):
    """RGB TIFF (no alpha channel, LZW-compressed) with the resolution written in, from PNG bytes."""
    from PIL import Image
    im = Image.open(io.BytesIO(png))
    im.load()
    if im.mode in ('RGBA', 'LA', 'P'):
        im = im.convert('RGBA')
        bg = Image.new('RGB', im.size, 'white')
        bg.paste(im, mask=im.split()[3])
        im = bg
    elif im.mode != 'RGB':
        im = im.convert('RGB')
    buf = io.BytesIO()
    im.save(buf, format='TIFF', compression='tiff_lzw', dpi=(int(dpi), int(dpi)))
    return buf.getvalue()


def export_bytes(fig, fmt, dpi=600):
    if fmt in ('tif', 'tiff'):
        return png_to_tiff(export_bytes(fig, 'png', dpi), dpi)
    buf = io.BytesIO()
    fig.savefig(buf, format=fmt, dpi=dpi)
    data = buf.getvalue()
    if fmt == 'svg':
        s = data.decode('utf-8')
        for name in ('Liberation Sans', 'Arimo', 'DejaVu Sans'):
            s = s.replace(f"font-family: '{name}'", "font-family: 'Arial'").replace(f'font-family:{name}', 'font-family:Arial')
        data = s.encode('utf-8')
    return data


# ------------------------------------------------------------------------------------------------ checks
def run_checks(cfg, tables, z_thr=3.5, ratio_thr=1.5):
    """List of suspicious values for every mean_error / bar / scatter layer (CSV row numbers refer to the uploaded file)."""
    rows = []

    def nm(k):
        return series_name(cfg, k)
    for L in cfg['layers']:
        if L['type'] not in ('mean_error', 'bar', 'scatter', 'line'):
            continue
        f, bad = layer_frame(L, tables, cfg)
        if f is None:
            continue
        name = L['name']
        n0 = len(rows)
        if bad is not None:
            for _, r in bad.iterrows():
                rows.append(dict(layer=name, check=ui('数値でない／空', 'Not numeric / empty'), csv_row=r['_row'], column=r['_col'], series=nm(r['_series']), x=r['_x'],
                                 unit=r['_unit'], value='', detail=ui('描画から外した', 'left out of the drawing')))
        if L['type'] == 'line':
            continue
        key = '_g' if '_g' in f.columns else '_x'          # x が測った値なら、グループ（荷重など）ごとに調べる
        for (sk, xv), g in f.groupby(['_series', key], sort=False):
            y = g['_y'].to_numpy(float)
            if len(y) >= 5:
                med = np.median(y)
                mad = 1.4826 * np.median(np.abs(y - med))
                if mad > 0:
                    for (_, r), v in zip(g.iterrows(), y):
                        z = (v - med) / mad
                        if abs(z) > z_thr:
                            rows.append(dict(layer=name, check=ui('飛び離れた値', 'Outlier'), csv_row=r['_row'], column=r['_col'], series=nm(sk), x=xv,
                                             unit=r['_unit'], value=v, detail=ui('中央値 {med:.4g}、修正 z = {z:+.1f}', 'median {med:.4g}, modified z = {z:+.1f}', med=med, z=z)))
            if (g['_unit'] != '').any():
                cnt = g.groupby('_unit')['_y'].size()
                mode = int(cnt.mode().iloc[0]) if len(cnt) else 0
                for un, c in cnt.items():
                    if c != mode:
                        rows.append(dict(layer=name, check=ui('単位内の個数が違う', 'Different count within a unit'), csv_row=' '.join(map(str, g[g['_unit'] == un]['_row'])),
                                         column='', series=nm(sk), x=xv, unit=un, value=c, detail=ui('ほかの単位は {mode} 個', 'other units have {mode}', mode=mode)))
                for un, gg in g.groupby('_unit'):
                    v = gg['_y'].to_numpy(float)
                    if len(v) > 1 and (v > 0).all() and v.max() / v.min() > ratio_thr:
                        rows.append(dict(layer=name, check=ui('単位内のばらつき', 'Spread within a unit'), csv_row=' '.join(map(str, gg['_row'])), column='',
                                         series=nm(sk), x=xv, unit=un, value=' / '.join(f'{t:g}' for t in v),
                                         detail=ui('最長／最短 = {v:.2f}', 'longest/shortest = {v:.2f}', v=v.max() / v.min())))
                if g['_unit'].nunique() < 3 and L['type'] in ('mean_error', 'bar'):
                    rows.append(dict(layer=name, check=ui('単位が 3 未満', 'Fewer than 3 units'), csv_row='', column='', series=nm(sk), x=xv, unit='',
                                     value=g['_unit'].nunique(), detail=ui('SD・SE はほぼ意味を持たない', 'SD and SE mean little')))
        if (f['_unit'] != '').any() and L['type'] in ('mean_error', 'bar'):
            # a whole unit repeated elsewhere (same set of values) — typical of a pasted block
            for xv, g in f.groupby(key, sort=False):
                seen = {}
                for (sk, un), gg in g.groupby(['_series', '_unit'], sort=False):
                    v = tuple(sorted(np.round(gg['_y'].to_numpy(float), 9)))
                    if len(v) >= 2:
                        seen.setdefault(v, []).append((sk, un, ' '.join(map(str, gg['_row']))))
                for v, lst in seen.items():
                    if len(lst) > 1:
                        for sk, un, rr in lst:
                            others = ui('、', ', ').join(f'{nm(a)}/{b}' for a, b, _ in lst if (a, b) != (sk, un))
                            rows.append(dict(layer=name, check=ui('単位まるごと同じ値', 'Whole unit repeated'), csv_row=rr, column='', series=nm(sk), x=xv, unit=un,
                                             value=' / '.join(f'{t:g}' for t in v), detail=ui('{others} と同じ値の組', 'same set of values as {others}', others=others)))
        for r in rows[n0:]:
            r['csv'] = L.get('source', '')
    for a, b, na, nb, kind in x_mismatches(cfg, tables):
        rows.append(dict(layer='', check=ui('x の値の食い違い', 'x values that differ slightly'), csv_row='', column='', series=ui('、', ', ').join(dict.fromkeys(na + nb)),
                         x=f'{a:g} / {b:g}', unit='', value='', detail=x_mismatch_text(a, b, na, nb, kind)))
    out = pd.DataFrame(rows, columns=['layer', 'check', 'csv', 'csv_row', 'column', 'series', 'x', 'unit', 'value', 'detail'])
    for c in ('csv', 'csv_row', 'column', 'series', 'x', 'unit', 'value'):
        out[c] = out[c].map(lambda v: '' if v is None or (isinstance(v, float) and np.isnan(v)) else (f'{v:g}' if isinstance(v, float) else str(v)))
    return out


# ------------------------------------------------------------------------------------------------ CLI
# ------------------------------------------------------------------------------------------------ work file
WORK_SETTINGS = 'settings.json'
DATA_EXT = ('.csv', '.tsv', '.txt')


def make_work_zip(cfg, files):
    """The app's 作業ファイル: settings.json and the CSV files as they were uploaded (data/…), in one .zip."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr(WORK_SETTINGS, json.dumps(cfg, ensure_ascii=False, indent=1))
        for n, b in files.items():
            z.writestr('data/' + n, b)
    return buf.getvalue()


def read_work_zip(data, max_bytes=50 * 2 ** 20):
    """→ (settings, {CSV name: bytes}) from a work file. Only settings.json and CSV/TSV/TXT files are read."""
    cfg, files = None, {}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist():
            if info.is_dir() or info.file_size > max_bytes:
                continue
            name = info.filename
            if not info.flag_bits & 0x800:          # written without the UTF-8 flag (Mac: UTF-8, Windows: Shift_JIS)
                raw_name = name.encode('cp437')
                for enc in ('utf-8', 'cp932'):
                    try:
                        name = raw_name.decode(enc)
                        break
                    except UnicodeDecodeError:
                        continue
            name = unicodedata.normalize('NFC', name)  # the Mac stores デ as テ + ゛
            base = name.replace('\\', '/').rsplit('/', 1)[-1]
            if base.startswith('._') or '__MACOSX' in name:
                continue
            if base == WORK_SETTINGS and cfg is None:
                cfg = json.loads(z.read(info).decode('utf-8-sig'))
            elif os.path.splitext(base)[1].lower() in DATA_EXT:
                files[base] = z.read(info)
    if cfg is None:
        raise ValueError(ui('{WORK_SETTINGS} が入っていません（アプリの「作業ファイルを保存」で作った .zip を選んでください）', 'There is no {WORK_SETTINGS} inside (choose a .zip made with “Save work file” in the app)', WORK_SETTINGS=WORK_SETTINGS))
    return complete(cfg), files


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(description='settings.json + CSV（または作業ファイル .zip）→ PDF/SVG/PNG/TIFF（アプリと同じ描画）')
    ap.add_argument('settings', help='settings.json、またはアプリで保存した作業ファイル（.zip）')
    ap.add_argument('csv', nargs='*', help='CSV（作業ファイルなら不要。渡すと同じ名前の CSV を置き換える）')
    ap.add_argument('--out', default='out')
    ap.add_argument('--name', default=None)
    a = ap.parse_args(argv)
    if a.settings.lower().endswith('.zip'):
        cfg, packed = read_work_zip(open(a.settings, 'rb').read())
        raw = {n: read_table(b, n) for n, b in packed.items()}
    else:
        cfg, raw = complete(json.load(open(a.settings, encoding='utf-8'))), {}
    raw.update({os.path.basename(p): read_table(open(p, 'rb').read(), p) for p in a.csv})
    missing = [L['source'] for L in cfg['layers'] if L.get('source') and L['source'] not in raw]
    if missing:
        sys.exit('設定が参照する CSV が見つかりません: ' + ', '.join(sorted(set(missing))) + '（同じファイル名で渡してください）')
    prep = []
    tables = prepared_tables(cfg, raw, prep)
    fig, plotted, notes = render(cfg, tables)
    os.makedirs(a.out, exist_ok=True)
    name = a.name or os.path.splitext(os.path.basename(a.settings))[0]
    dpi = int(cfg['figure'].get('dpi', 600))
    outs = {fmt: export_bytes(fig, fmt, dpi) for fmt in ('pdf', 'svg', 'png')}
    outs['tif'] = png_to_tiff(outs['png'], dpi)
    for fmt, b in outs.items():
        open(os.path.join(a.out, f'{name}.{fmt}'), 'wb').write(b)
    if plotted:
        pd.concat([p for _, p in plotted], ignore_index=True).to_csv(os.path.join(a.out, f'{name}_plotted.csv'), index=False, encoding='utf-8-sig')
    chk = run_checks(cfg, tables)
    chk.to_csv(os.path.join(a.out, f'{name}_checks.csv'), index=False, encoding='utf-8-sig')
    for n in prep + notes:
        print(n)
    cap = n_caption(cfg, plotted)
    if cap:
        print('n:', cap)
    print(f'{name}: {len(chk)} 件のチェック項目 → {a.out}')


if __name__ == '__main__':
    main(sys.argv[1:])
