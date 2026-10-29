"""Брендинг «Пульта» поверх исходников RustDesk (AGPL-3.0, https://github.com/rustdesk/rustdesk).

python brand.py apply <rustdesk>          — иконки, логотипы, цвета, свой ключ проверки custom.txt
python brand.py sign <rustdesk> <out.txt> — подписать config.json этим ключом (custom.txt рядом с exe)

Имя, сервер и настройки по умолчанию задаёт config.json: RustDesk читает их из подписанного custom.txt
(src/common.rs: read_custom_client). Подпись проверяется вшитым ключом KEY — меняем его на свой,
пара ключей создаётся на каждую сборку и нигде не хранится.
"""
import base64
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ASSETS = HERE / 'assets'
UPSTREAM_KEY = '5Qbwsde3unUcJBtrx9ZkvUmwFNoExHzpryHuPUdqlWM='
SEED_FILE = '.pult-seed'  # приватный ключ сборки: только на время сборки, в репозиторий не попадает

ICONS = {
    'icon.ico': ['res/icon.ico', 'flutter/windows/runner/resources/app_icon.ico'],
    'tray-icon.ico': ['res/tray-icon.ico'],
    'icon-1024.png': ['res/icon.png', 'res/mac-icon.png'],
    'icon-32.png': ['res/32x32.png'],
    'icon-64.png': ['res/64x64.png'],
    'icon-128.png': ['res/128x128.png'],
    'icon-256.png': ['res/128x128@2x.png', 'flutter/assets/icon.png'],
    'icon.svg': ['flutter/assets/icon.svg'],
    'logo_dark.png': ['flutter/assets/logo_dark.png', 'flutter/assets/logo.png'],
    'logo_light.png': ['flutter/assets/logo_light.png'],
}

# Акцент RustDesk (синий) → фиолетовый «Пульта», как градиент иконки
COLORS = {
    'Color(0xFF0071FF)': 'Color(0xFF6C5CFF)',
    'Color(0x770071FF)': 'Color(0x776C5CFF)',
    'Color(0xAA0071FF)': 'Color(0xAA6C5CFF)',
    'Color(0xFF2C8CFF)': 'Color(0xFF7C6CFF)',
}


def replace_once(path, old, new):
    text = path.read_text(encoding='utf-8')
    if text.count(old) != 1:
        sys.exit(f'{path}: ожидалось одно вхождение {old!r}, найдено {text.count(old)}')
    path.write_text(text.replace(old, new), encoding='utf-8')


def apply(root):
    from nacl.signing import SigningKey

    for src, dsts in ICONS.items():
        for dst in dsts:
            shutil.copyfile(ASSETS / src, root / dst)
    common = root / 'flutter/lib/common.dart'
    text = common.read_text(encoding='utf-8')
    for old, new in COLORS.items():
        if old not in text:
            sys.exit(f'{common}: нет цвета {old}')
        text = text.replace(old, new)
    common.write_text(text, encoding='utf-8')
    key = SigningKey.generate()
    (root / SEED_FILE).write_bytes(bytes(key))
    pub = base64.b64encode(bytes(key.verify_key)).decode()
    replace_once(root / 'src/common.rs', f'const KEY: &str = "{UPSTREAM_KEY}";', f'const KEY: &str = "{pub}";')
    print(f'Пульт: брендинг применён, ключ custom.txt {pub}')


def sign(root, out):
    from nacl.signing import SigningKey

    key = SigningKey((root / SEED_FILE).read_bytes())
    cfg = json.loads((HERE / 'config.json').read_text(encoding='utf-8'))
    cfg.pop('_', None)
    signed = key.sign(json.dumps(cfg, ensure_ascii=False).encode())
    Path(out).write_text(base64.b64encode(bytes(signed)).decode(), encoding='ascii')
    print(f'Пульт: custom.txt подписан → {out}')


if __name__ == '__main__':
    cmd, root = sys.argv[1], Path(sys.argv[2]).resolve()
    if cmd == 'apply':
        apply(root)
    elif cmd == 'sign':
        sign(root, sys.argv[3])
    else:
        sys.exit(__doc__)
