# -*- mode: python ; coding: utf-8 -*-
# PyInstaller の設定：figapp の PC 版（Mac は figapp.app、Windows は figapp\figapp.exe）を作る。
# リポジトリのいちばん上で:  pyinstaller desktop/figapp.spec --noconfirm --clean
# app.py と figcore.py などはそのままの .py でアプリの中に入れ、Streamlit が Web 版と同じように実行する。
import os
import re
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

ROOT = os.path.abspath(os.path.join(SPECPATH, '..'))
DESK = os.path.join(ROOT, 'desktop')
VERSION = re.search(r"APP_VERSION = '([^']+)'", open(os.path.join(ROOT, 'figcore.py'), encoding='utf-8').read()).group(1)
APP_MODULES = ['figcore', 'demo_data', 'i18n', 'i18n_text']

datas = [(os.path.join(ROOT, 'app.py'), '.'),
         (os.path.join(ROOT, 'colors.csv'), '.'),
         (os.path.join(ROOT, 'presets'), 'presets'),
         (os.path.join(ROOT, '.streamlit', 'config.toml'), '.streamlit')]
datas += collect_data_files('streamlit', excludes=['**/tests/**'])          # 画面（static）と部品のひな形
for dist in ('streamlit', 'altair', 'narwhals', 'pyarrow', 'pandas', 'numpy', 'matplotlib', 'pillow', 'starlette', 'uvicorn',
             'websockets', 'protobuf', 'pydeck', 'click', 'packaging', 'requests', 'jsonschema', 'typing_extensions',
             'anyio', 'python-multipart', 'itsdangerous'):
    datas += copy_metadata(dist)

hiddenimports = APP_MODULES + ['selftest']
hiddenimports += collect_submodules('streamlit', filter=lambda n: not n.startswith(('streamlit.testing', 'streamlit.hello',
                                                                                     'streamlit.external')))
hiddenimports += collect_submodules('uvicorn') + collect_submodules('websockets') + collect_submodules('starlette')
hiddenimports += ['matplotlib.backends.backend_agg', 'matplotlib.backends.backend_pdf', 'matplotlib.backends.backend_svg',
                  'matplotlib.backends.backend_ps', 'PIL.TiffImagePlugin', 'PIL.PngImagePlugin']
if sys.platform == 'darwin':
    hiddenimports += ['webview.platforms.cocoa', 'Foundation', 'AppKit', 'WebKit']
elif sys.platform == 'win32':
    hiddenimports += ['webview.platforms.winforms', 'webview.platforms.edgechromium', 'clr']

# pyarrow は Streamlit と pandas が lib・compute・ipc だけを使う。どの部品からも使われない Flight・S3 などは入れない（約 50 MB 減る）。
# Parquet・Dataset・Acero・Substrait は libarrow_python が読み込むので残す
PYARROW_UNUSED = ['pyarrow.' + m for m in (
    'flight', '_flight', '_s3fs', '_gcsfs', '_azurefs', '_hdfs', 'orc', '_orc', '_dataset_orc', 'cuda', '_cuda',
    '_pyarrow_cpp_tests', 'tests', 'benchmark', '_parquet_encryption', '_dataset_parquet_encryption', 'parquet.encryption')]
_DROP = ('flight', 'arrow_s3', '_s3fs', 'gcsfs', 'azurefs', 'hdfs', 'orc', 'cuda', 'cpp_tests', 'encryption')


def _keep(dest):
    d = dest.replace('\\', '/')
    name = d.rsplit('/', 1)[-1].lower()
    # pyarrow の中のものと、PyInstaller がいちばん上に作る pyarrow のライブラリへのリンク（libarrow_flight.so.2600 など）
    if not (d.startswith('pyarrow/') or name.startswith(('libarrow', 'arrow', 'libparquet', 'parquet'))):
        return True
    if d.startswith('pyarrow/include/') or d.startswith('pyarrow/tests/') or name.endswith(('.pyx', '.pxd', '.pxi', '.h', '.cc')):
        return False
    return not any(k in name for k in _DROP)


a = Analysis(
    [os.path.join(DESK, 'launcher.py')],
    pathex=[ROOT, DESK],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=['tkinter', 'IPython', 'pytest', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'gi', 'scipy', 'webview.platforms.qt',
              'webview.platforms.gtk', 'webview.platforms.cef', 'webview.platforms.android'] + PYARROW_UNUSED,
    module_collection_mode={m: 'py' for m in APP_MODULES},                  # .py のまま app.py の隣に置く
    noarchive=False,
)
a.binaries = [b for b in a.binaries if _keep(b[0])]
a.datas = [d for d in a.datas if _keep(d[0])]
pyz = PYZ(a.pure)

icon = os.path.join(DESK, 'icons', 'figapp.icns' if sys.platform == 'darwin' else 'figapp.ico')
version_file = None
if sys.platform == 'win32':
    from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct, StringTable,
                                                     VarFileInfo, VarStruct, VSVersionInfo)
    nums = tuple(int(x) for x in (VERSION.split('.') + ['0', '0', '0'])[:4])
    version_file = VSVersionInfo(
        ffi=FixedFileInfo(filevers=nums, prodvers=nums),
        kids=[StringFileInfo([StringTable('041104B0', [
            StringStruct('ProductName', 'figapp'), StringStruct('FileDescription', 'figapp（論文用グラフ作成）'),
            StringStruct('FileVersion', VERSION), StringStruct('ProductVersion', VERSION),
            StringStruct('InternalName', 'figapp'), StringStruct('OriginalFilename', 'figapp.exe'),
            StringStruct('CompanyName', 'figapp'), StringStruct('LegalCopyright', 'figapp')])]),
              VarFileInfo([VarStruct('Translation', [0x0411, 1200])])])

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='figapp',
    console=False,
    icon=icon,
    version=version_file,
    disable_windowed_traceback=False,
    argv_emulation=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='figapp')

if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name='figapp.app',
        icon=icon,
        bundle_identifier='io.github.a-okawa.figapp',
        version=VERSION,
        info_plist={
            'CFBundleName': 'figapp',
            'CFBundleDisplayName': 'figapp',
            'CFBundleShortVersionString': VERSION,
            'CFBundleVersion': VERSION,
            'LSMinimumSystemVersion': '12.0',
            'LSApplicationCategoryType': 'public.app-category.education',
            'NSHighResolutionCapable': True,
            'NSRequiresAquaSystemAppearance': False,
            'NSAppTransportSecurity': {'NSAllowsLocalNetworking': True},   # http://127.0.0.1 の画面を窓に出す
            'NSHumanReadableCopyright': 'figapp',
        },
    )
