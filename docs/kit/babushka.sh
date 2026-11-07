#!/usr/bin/env bash
# «Заметки» для бабушки, Mac: Git, GitHub CLI, Obsidian → хранилище ~/Заметки = приватный репозиторий GitHub
# (плагин Obsidian Git сам сохраняет туда), правила для Claude, значок «Claude — Заметки» на рабочем столе.
# Запускает Claude Code по промту docs/kit/babushka.md:  curl -fsSL https://sawqws.github.io/123/kit/babushka.sh | bash
# Запускать можно сколько угодно раз: что уже сделано — пропускает, чужое не перезаписывает.
# Код выхода: 0 — всё готово; 10 — нужен вход в GitHub (gh auth login --web), потом снова;
#   11 — нужно нажать «Установить» в окне Apple (инструменты разработчика, ~10 мин), потом снова; 1 — ошибка.
# Переменные: BABUSHKA_VAULT, BABUSHKA_REPO (zametki), BABUSHKA_SKIP_INSTALL=1, BABUSHKA_NO_LAUNCH=1,
#   BABUSHKA_NO_BREW=1 (ставить без Homebrew), BABUSHKA_REMOTE=<адрес git> (тест: вместо GitHub).
set -uo pipefail
VAULT=${BABUSHKA_VAULT:-$HOME/Заметки}
REPO=${BABUSHKA_REPO:-zametki}
NAME=$(basename "$VAULT")
PLUGIN=https://github.com/Vinzent03/obsidian-git/releases/latest/download
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
say() { printf '\033[36m▸ %s\033[0m\n' "$1"; }
ok() { printf '  \033[32m✅ %s\033[0m\n' "$1"; }
warn() { printf '  \033[33m⚠️ %s\033[0m\n' "$1"; }
fail() { printf '  \033[31m❌ %s\033[0m\n' "$1"; exit 1; }
has() { command -v "$1" >/dev/null 2>&1; }
git_ok() { git --version >/dev/null 2>&1; }   # на чистом Mac /usr/bin/git — заглушка без инструментов Apple
mac() { [ "$(uname)" = Darwin ]; }
G() { git -C "$VAULT" "$@" || fail "git $* — ошибка"; }
new() { [ -e "$1" ] || { mkdir -p "$(dirname "$1")"; cat > "$1"; }; }   # записать, только если файла нет
app() { for d in /Applications "$HOME/Applications"; do [ -d "$d/Obsidian.app" ] && { echo "$d/Obsidian.app"; return; }; done; }
brew_ok() { [ "${BABUSHKA_NO_BREW:-}" != 1 ] && has brew; }
latest() { curl -fsSL "https://api.github.com/repos/$1/releases/latest" | grep -o '"browser_download_url": *"[^"]*"' | grep -o 'https://[^"]*' | grep -E "$2" | head -1; }

# ---------- 1. Программы ----------
if [ "${BABUSHKA_SKIP_INSTALL:-}" != 1 ] && mac; then
  say 'Программы: Git, GitHub CLI, Obsidian'
  if git_ok; then ok 'Git есть'
  elif brew_ok; then brew install git && ok 'Git поставлен'
  else
    xcode-select --install >/dev/null 2>&1
    printf '\n\033[35mНУЖНО НАЖАТЬ «Установить» в окне Apple (инструменты разработчика), подождать ~10 мин и запустить снова.\033[0m\n'
    exit 11
  fi
  if has gh; then ok 'GitHub CLI есть'
  elif brew_ok; then brew install gh && ok 'GitHub CLI поставлен'
  else
    a=$([ "$(uname -m)" = arm64 ] && echo arm64 || echo amd64)
    u=$(latest cli/cli "_macOS_${a}\.zip$"); t=$(mktemp -d)
    { [ -n "$u" ] && curl -fsSL "$u" -o "$t/gh.zip" && unzip -q "$t/gh.zip" -d "$t" && mkdir -p ~/.local/bin && cp "$t"/gh_*/bin/gh ~/.local/bin/gh; } \
      && ok 'GitHub CLI поставлен (~/.local/bin/gh)' || warn 'GitHub CLI не встал'
    rm -rf "$t"
  fi
  if [ -n "$(app)" ]; then ok 'Obsidian есть'
  elif brew_ok; then brew install --cask obsidian && ok 'Obsidian поставлен'
  else
    u=$(latest obsidianmd/obsidian-releases '\.dmg$'); t=$(mktemp -d)
    dst=/Applications; [ -w "$dst" ] || { dst="$HOME/Applications"; mkdir -p "$dst"; }
    { [ -n "$u" ] && curl -fsSL "$u" -o "$t/o.dmg" && hdiutil attach -quiet -nobrowse -mountpoint "$t/m" "$t/o.dmg" \
      && cp -R "$t/m/Obsidian.app" "$dst/"; } && ok "Obsidian поставлен ($dst)" || warn 'Obsidian не встал'
    hdiutil detach -quiet "$t/m" 2>/dev/null; rm -rf "$t"
  fi
fi
git_ok || fail 'нет Git — без него не работает'

# ---------- 2. Хранилище Obsidian ----------
say "Хранилище: $VAULT"
mkdir -p "$VAULT/.obsidian/plugins/obsidian-git" "$VAULT/.claude"
VAULT=$(cd "$VAULT" && pwd -P)
new "$VAULT/.gitignore" <<'EOF'
.obsidian/workspace.json
.obsidian/workspace-mobile.json
.obsidian/cache
.trash/
Thumbs.db
desktop.ini
.DS_Store
EOF
new "$VAULT/Начни здесь.md" <<'EOF'
# Начни здесь 👋

Это ваши заметки. Пишите что угодно — **сохранять не нужно**: всё сохраняется само, а через пару минут копия уходит в интернет (GitHub). Даже если компьютер сломается, заметки не пропадут.

- **Новая заметка** — Cmd+N (на Windows — Ctrl+N).
- **Найти заметку** — Cmd+O и начните печатать название.
- **Помощник Claude** — значок «Claude — Заметки» на рабочем столе. Пишите ему обычными словами:
  - «Запиши рецепт пирога: …»
  - «Найди, где я писала про давление»
  - «Сделай список покупок на неделю»
EOF
new "$VAULT/.claude/NOW.md" <<'EOF'
# Сейчас

> Живая передача между чатами Claude: у каждой задачи свой раздел «## …» (что делаем, что сделано, что дальше).
> Обновлять после каждого заметного шага, задача закончена — раздел удалить.
EOF
new "$VAULT/.claude/CLAUDE.md" <<EOF
# Заметки бабушки — правила для Claude

Эта папка — хранилище Obsidian и приватный репозиторий GitHub \`$REPO\` (ветка main).

## Как говорить
- Просто, по-русски, коротко, без компьютерных слов. Одна просьба за раз.
- Если ей нужно что-то нажать — одной фразой: что и где.

## Заметки
- Заметка — файл \`.md\` в этой папке, название по-русски и понятное («Рецепт борща», «Лекарства»).
- Ничего не удалять и не переносить без её «да».
- Пароли, коды из СМС, номера карт и документов в заметки не писать — мягко предложить бумажную тетрадь.

## Сохранение
- Obsidian сохраняет в GitHub сам (плагин Git, через пару минут после правки).
- В начале работы — \`git pull\`. Поменял файлы сам — сразу \`git add -A\`, \`git commit\`, \`git push\`.
- Конфликт в git — разрешить самому, ничего не теряя, и сказать ей одной фразой.

## Переход в новый чат
- \`.claude/NOW.md\` — что сейчас делаем. Читать в начале чата, обновлять после заметного шага (commit + push), готово — раздел удалить.
EOF
new "$VAULT/.claude/settings.json" <<'EOF'
{
  "hooks": {
    "SessionStart": [
      { "hooks": [
        { "type": "command", "command": "git pull --ff-only -q" },
        { "type": "command", "command": "cat .claude/NOW.md" }
      ] }
    ]
  }
}
EOF
O="$VAULT/.obsidian"
printf '{\n  "baseFontSize": 20\n}\n' | new "$O/appearance.json"
python3 - "$O/community-plugins.json" <<'PY' || fail 'не записался community-plugins.json'
import json, sys
p = sys.argv[1]
try: cp = json.load(open(p, encoding='utf-8'))
except Exception: cp = []
if 'obsidian-git' not in cp: json.dump(cp + ['obsidian-git'], open(p, 'w', encoding='utf-8'))
PY
for f in main.js manifest.json styles.css; do
  [ -s "$O/plugins/obsidian-git/$f" ] || curl -fsSL "$PLUGIN/$f" -o "$O/plugins/obsidian-git/$f" || fail "плагин Obsidian Git: не скачался $f"
done
# Ключи сверены с DEFAULT_SETTINGS плагина 2.43: коммит и отправка через 2 мин после последней правки, приём раз в 10 мин и при запуске.
new "$O/plugins/obsidian-git/data.json" <<'EOF'
{
  "autoSaveInterval": 2,
  "autoBackupAfterFileChange": true,
  "autoPullInterval": 10,
  "autoPullOnBoot": true,
  "pullBeforePush": true,
  "syncMethod": "merge",
  "commitMessage": "Заметки {{date}}",
  "autoCommitMessage": "Заметки {{date}}",
  "disablePopups": true,
  "disablePopupsForNoChanges": true,
  "showErrorNotices": true,
  "showStatusBar": true
}
EOF
ok 'файлы хранилища и плагин Git на месте'

# Obsidian сам откроет это хранилище (список хранилищ — obsidian.json; Obsidian должен быть закрыт)
if mac; then OJ="$HOME/Library/Application Support/obsidian/obsidian.json"; else OJ="$HOME/.config/obsidian/obsidian.json"; fi
if pgrep -x Obsidian >/dev/null 2>&1; then osascript -e 'quit app "Obsidian"' >/dev/null 2>&1; sleep 3; fi
python3 - "$OJ" "$VAULT" <<'PY' || fail 'не записался obsidian.json'
import json, os, secrets, sys, time
p, vault = sys.argv[1], sys.argv[2]
os.makedirs(os.path.dirname(p), exist_ok=True)
try: cfg = json.load(open(p, encoding='utf-8'))
except Exception: cfg = {}
vaults = cfg.setdefault('vaults', {})
mine = next((v for v in vaults.values() if os.path.normpath(v.get('path', '')) == vault), None)
for v in vaults.values(): v['open'] = False
if mine is None: mine = vaults[secrets.token_hex(8)] = {'path': vault, 'ts': int(time.time() * 1000)}
mine['open'] = True
json.dump(cfg, open(p, 'w', encoding='utf-8'), ensure_ascii=False)
PY
ok "Obsidian откроет «$NAME» сам"

# ---------- 3. Git ----------
say 'Git'
[ -d "$VAULT/.git" ] || { G init -q; G symbolic-ref HEAD refs/heads/main; }
G config core.quotepath false
authed=0
if [ -z "${BABUSHKA_REMOTE:-}" ]; then
  has gh || fail 'нет GitHub CLI (gh)'
  if gh auth status --hostname github.com >/dev/null 2>&1; then
    authed=1
    gh auth setup-git --hostname github.com
    if [ -z "$(git config --global user.name)" ]; then
      login=$(gh api user --jq .login); uid=$(gh api user --jq .id); name=$(gh api user --jq .name)
      git config --global user.name "${name:-$login}"
      git config --global user.email "$uid+$login@users.noreply.github.com"
    fi
  fi
  if [ $authed = 0 ]; then
    printf '\n\033[35mНУЖЕН ВХОД В GITHUB: %s auth login --hostname github.com --web --git-protocol https — потом запусти этот скрипт снова.\033[0m\n' "$(command -v gh)"
    exit 10
  fi
fi
[ -n "$(git config user.name)" ] || fail 'у git нет имени автора (git config --global user.name)'
G add -A
if git -C "$VAULT" diff --cached --quiet; then ok 'нечего сохранять — всё уже в git'; else G commit -q -m 'Заметки: настройка'; ok 'сохранено в git'; fi

# ---------- 4. GitHub ----------
say "GitHub: приватный репозиторий $REPO"
if ! git -C "$VAULT" remote | grep -qx origin; then
  if [ -n "${BABUSHKA_REMOTE:-}" ]; then G remote add origin "$BABUSHKA_REMOTE"
  elif gh repo view "$REPO" --json name >/dev/null 2>&1; then G remote add origin "$(gh repo view "$REPO" --json url --jq .url).git"
  else gh repo create "$REPO" --private --description 'Заметки (Obsidian)' --source "$VAULT" --remote origin || fail "не создался репозиторий $REPO на GitHub"
  fi
fi
git -C "$VAULT" fetch -q origin 2>/dev/null
if git -C "$VAULT" rev-parse -q --verify refs/remotes/origin/main >/dev/null; then
  git -C "$VAULT" pull -q --no-rebase --allow-unrelated-histories --no-edit origin main || fail 'конфликт при слиянии с GitHub — разреши его в папке и запусти снова'
fi
G push -q -u origin main
ok 'всё отправлено в GitHub'

# ---------- 5. Claude: правила для всех чатов и значок ----------
say 'Claude'
GM="$HOME/.claude/CLAUDE.md"; mkdir -p "$HOME/.claude"
if ! grep -q 'babushka-notes' "$GM" 2>/dev/null; then
  { [ -s "$GM" ] && printf '\n'; cat <<EOF
<!-- babushka-notes -->
# Ты помогаешь бабушке
- Она с компьютером на «вы»: говори просто, по-русски, коротко, без компьютерных слов. Одна просьба за раз.
- Её заметки — в папке \`$VAULT\` (Obsidian, сохраняются в GitHub сами). Записать, найти, поправить заметку — там; правила — в .claude/CLAUDE.md этой папки.
- Ничего не удалять и не оплачивать без её «да». Пароли и коды из СМС не записывать.
EOF
  } >> "$GM"
  ok 'правила Claude для всех чатов — ~/.claude/CLAUDE.md'
fi
CL=$(command -v claude || true)
if [ -n "$CL" ] && [ -d "$HOME/Desktop" ]; then
  L="$HOME/Desktop/Claude — Заметки.command"
  printf '#!/bin/bash\ncd %q && exec %q\n' "$VAULT" "$CL" > "$L" && chmod +x "$L" && ok 'значок «Claude — Заметки» на рабочем столе'
else warn 'Claude Code (команда claude) не найден — значок не сделан'; fi

# ---------- 6. Открыть Obsidian ----------
if [ "${BABUSHKA_NO_LAUNCH:-}" != 1 ] && mac && [ -n "$(app)" ]; then
  open "$(app)"; sleep 8
  open "obsidian://open?vault=$(python3 -c 'import sys,urllib.parse as u; print(u.quote(sys.argv[1]))' "$NAME")&file=$(python3 -c 'import urllib.parse as u; print(u.quote("Начни здесь"))')" 2>/dev/null
  ok 'Obsidian открыт'
fi
printf '\n\033[35mГОТОВО: хранилище %s · репозиторий %s · плагин Git включится после «Доверять автору» в Obsidian\033[0m\n' "$VAULT" "$REPO"
