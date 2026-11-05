# -*- coding: utf-8 -*-
"""Screen languages: 日本語, English, Tiếng Việt, ไทย. Figures keep the text the user types.

app.py and figcore.py write each screen text as ui('日本語', 'English'); Vietnamese and Thai come from
INLINE_TEXT, looked up by the English text (i18n_text.py). A text missing there is shown in English.
Texts with {placeholders} are filled after the language is chosen: ui('{n} 行', '{n} rows', n=5).

The language of the current screen comes from lang_getter: app.py points it at its session state
(use_streamlit_session), so every user of a shared server sees their own language. figcore.py used on
its own (the command line) stays in Japanese.
"""
try:
    from i18n_text import INLINE as _INLINE
except ImportError:              # 表がなければ英語で出す
    _INLINE = []

LANGUAGES = {'日本語': 'ja', 'English': 'en', 'Tiếng Việt': 'vi', 'ไทย': 'th'}
CODES = list(LANGUAGES.values())
NAMES = {code: name for name, code in LANGUAGES.items()}

INLINE_TEXT = {'vi': {en: vi for en, vi, _ in _INLINE}, 'th': {en: th for en, _, th in _INLINE}}

# The colour names of colors.csv as the app writes them (shared with XRD Maker), shown translated in the
# colour box. The file keeps its Japanese names; names not listed here show as written.
# (Japanese name, English, Vietnamese, Thai)
_COLOR_NAMES = [
    ('青', 'Blue', 'Xanh dương', 'น้ำเงิน'),
    ('朱', 'Vermilion', 'Đỏ son', 'แสด'),
    ('緑', 'Green', 'Xanh lá', 'เขียว'),
    ('赤紫', 'Reddish purple', 'Tím đỏ', 'ม่วงแดง'),
    ('橙', 'Orange', 'Cam', 'ส้ม'),
    ('空色', 'Sky blue', 'Xanh da trời', 'ฟ้า'),
    ('黄', 'Yellow', 'Vàng', 'เหลือง'),
    ('黒', 'Black', 'Đen', 'ดำ'),
    ('濃い灰', 'Dark grey', 'Xám đậm', 'เทาเข้ม'),
    ('灰', 'Grey', 'Xám', 'เทา'),
    ('赤', 'Red', 'Đỏ', 'แดง'),
    ('紫', 'Purple', 'Tím', 'ม่วง'),
    ('茶', 'Brown', 'Nâu', 'น้ำตาล'),
]
COLOR_NAME_TEXT = {code: {ja: names[i] for ja, *names in _COLOR_NAMES} for i, code in enumerate(('en', 'vi', 'th'))}


def _japanese():
    return 'ja'


lang_getter = _japanese


def use_streamlit_session(key='lang'):
    """Take the screen language from st.session_state[key] (a code: ja, en, vi or th)."""
    import streamlit as st
    global lang_getter

    def from_session():
        code = st.session_state.get(key)
        return code if code in CODES else 'ja'
    lang_getter = from_session


def lang():
    try:
        code = lang_getter()
    except Exception:  # noqa: BLE001  (outside a Streamlit run)
        return 'ja'
    return code if code in CODES else 'ja'


def ui(ja, en, **kw):
    """The text for the current screen language; {placeholders} are filled with kw."""
    code = lang()
    text = ja if code == 'ja' else en if code == 'en' else INLINE_TEXT.get(code, {}).get(en, en)
    return text.format(**kw) if kw else text


def color_name(name, code=None):
    """A colors.csv name as shown in the current language (the stored name stays as written)."""
    code = code or lang()
    return COLOR_NAME_TEXT.get(code, {}).get(name, name)
