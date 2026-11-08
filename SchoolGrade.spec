# -*- mode: python ; coding: utf-8 -*-
# Сборка SchoolGrade в один exe-файл: pyinstaller SchoolGrade.spec
# Для поддержки Windows 7 собирайте на Python 3.8.10 (32-бит) и PyInstaller 5.13.2.

block_cipher = None

a = Analysis(
    ['run_server.py'],
    pathex=['.'],
    binaries=[],
    datas=[('schoolgrade/static', 'static')],
    hiddenimports=['schoolgrade.demo'],
    hookspath=[],
    runtime_hooks=[],
    excludes=['numpy', 'pandas', 'matplotlib', 'PIL', 'pytest'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='SchoolGrade',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,            # UPX часто вызывает ложные срабатывания антивирусов
    runtime_tmpdir=None,
    console=False,        # окно управления сервером (tkinter), без консоли
    icon='schoolgrade/static/icon.ico',
    version='version_info.txt',
)
