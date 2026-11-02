#!/usr/bin/env bash
# Набор «Claude с нуля» для Mac/Linux: Git, GitHub CLI, Obsidian, Claude Code + поиск старых наработок + промпт.
# Запуск:  curl -fsSL https://sawqws.github.io/123/kit/start.sh | bash
# KIT_SKIP_INSTALL=1 — без установки, KIT_NO_LAUNCH=1 — не открывать Claude в конце.
set -uo pipefail
BASE=${KIT_BASE:-https://sawqws.github.io/123/kit}
KIT="$HOME/claude-kit"; mkdir -p "$KIT"
say() { printf '\033[36m▸ %s\033[0m\n' "$1"; }
ok() { printf '  \033[32m✅ %s\033[0m\n' "$1"; }
warn() { printf '  \033[33m⚠️ %s\033[0m\n' "$1"; }
has() { command -v "$1" >/dev/null 2>&1; }
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

if [ "${KIT_SKIP_INSTALL:-}" != 1 ]; then
  say 'Установка программ'
  if [ "$(uname)" = Darwin ]; then
    has brew || NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    eval "$(/opt/homebrew/bin/brew shellenv 2>/dev/null || /usr/local/bin/brew shellenv 2>/dev/null)"
    has git || brew install git; has gh || brew install gh
    [ -d /Applications/Obsidian.app ] || brew install --cask obsidian
    [ -d /Applications/Claude.app ] || brew install --cask claude || warn 'Claude Desktop не встал'
  elif has apt-get; then
    has git || sudo apt-get install -y git; has gh || sudo apt-get install -y gh || warn 'gh не встал'
  fi
  for c in git gh; do has $c && ok "$c есть" || warn "$c нет"; done
  has claude || curl -fsSL https://claude.ai/install.sh | bash
  has claude && ok 'Claude Code есть' || warn 'Claude Code не найден — открой новый терминал'
fi

say 'Ищу старые наработки (только пути и имена)'
python3 - "$KIT/scan.json" <<'PY'
import json, os, subprocess, sys, glob, datetime
H = os.path.expanduser('~'); SKIP = {'node_modules', '.cache', '.npm', 'Library', '.Trash', '.venv', 'venv', 'site-packages'}
def walk(name, depth, file=False):
    out = []
    for root, dirs, files in os.walk(H):
        d = root[len(H):].count(os.sep)
        dirs[:] = [x for x in dirs if x not in SKIP] if d < depth else []
        if (name in files) if file else (name in dirs): out.append(os.path.join(root, name))
    return out
def run(*a):
    try: return subprocess.run(a, capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception: return ''
s = {'created': datetime.datetime.now().isoformat(timespec='seconds'), 'os': sys.platform, 'home': H}
vaults = set(os.path.dirname(p) for p in walk('.obsidian', 5))
for cfg in ('~/Library/Application Support/obsidian/obsidian.json', '~/.config/obsidian/obsidian.json'):
    try: vaults |= {v['path'] for v in json.load(open(os.path.expanduser(cfg)))['vaults'].values()}
    except Exception: pass
s['obsidian_vaults'] = [{'path': v, 'notes': len(glob.glob(v + '/**/*.md', recursive=True)), 'git': os.path.isdir(v + '/.git')}
                        for v in sorted(vaults) if os.path.isdir(v)]
s['git_repos'] = []
for g in sorted(walk('.git', 6)):
    p = os.path.dirname(g)
    s['git_repos'].append({'path': p, 'remote': run('git', '-C', p, 'remote', 'get-url', 'origin'),
        'branch': run('git', '-C', p, 'branch', '--show-current'),
        'uncommitted': len([l for l in run('git', '-C', p, 'status', '--porcelain').splitlines() if l]),
        'unpushed': run('git', '-C', p, 'rev-list', '--count', '@{u}..HEAD'), 'last_commit': run('git', '-C', p, 'log', '-1', '--format=%cs %s'),
        'has_claude_md': os.path.exists(p + '/CLAUDE.md') or os.path.isdir(p + '/.claude')})
s['git_user'], s['git_email'] = run('git', 'config', '--global', 'user.name'), run('git', 'config', '--global', 'user.email')
s['gh_status'] = run('gh', 'auth', 'status')
c = H + '/.claude'; cc = {'exists': os.path.isdir(c)}
for k in ('skills', 'agents', 'commands', 'hooks', 'plugins'):
    cc[k] = sorted(os.listdir(c + '/' + k)) if os.path.isdir(c + '/' + k) else []
cc['global_claude_md'] = os.path.exists(c + '/CLAUDE.md'); cc['settings'] = os.path.exists(c + '/settings.json')
cc['chat_projects'] = sorted(os.listdir(c + '/projects')) if os.path.isdir(c + '/projects') else []
try:
    j = json.load(open(H + '/.claude.json')); cc['mcp_servers'] = sorted(j.get('mcpServers', {})); cc['account'] = j.get('oauthAccount', {}).get('emailAddress', '')
except Exception: pass
s['claude_code'] = cc
dc = os.path.expanduser('~/Library/Application Support/Claude/claude_desktop_config.json')
s['claude_desktop'] = {'config': os.path.exists(dc)}
try: s['claude_desktop']['mcp_servers'] = sorted(json.load(open(dc)).get('mcpServers', {}))
except Exception: pass
s['claude_md_files'] = walk('CLAUDE.md', 6, file=True)
json.dump(s, open(sys.argv[1], 'w'), ensure_ascii=False, indent=2)
print(f"  ✅ Хранилищ Obsidian: {len(s['obsidian_vaults'])} · git-репозиториев: {len(s['git_repos'])} · CLAUDE.md: {len(s['claude_md_files'])} · прошлый Claude Code: {cc['exists']}")
for v in s['obsidian_vaults']: print(f"    📓 {v['path']} — заметок {v['notes']}")
for r in s['git_repos']: print(f"    📦 {r['path']} {'→ ' + r['remote'] if r['remote'] else '(без GitHub)'}")
PY

say 'Промпт для нового Claude'
curl -fsSL "$BASE/prompt.md" -o "$KIT/prompt.md" && ok "Сохранён: $KIT/prompt.md" || warn 'prompt.md не скачался'
FIRST='Прочитай файл ~/claude-kit/prompt.md и выполни его целиком. Отчёт о старых наработках — ~/claude-kit/scan.json.'
printf '%s' "$FIRST" > "$KIT/first-message.txt"
if has pbcopy; then printf '%s' "$FIRST" | pbcopy && ok 'Первое сообщение скопировано — вставь (Cmd+V)'; fi
printf '\n\033[35mДальше: войди в аккаунт Claude (браузер), потом вставь первое сообщение и Enter.\033[0m\n'
if [ "${KIT_NO_LAUNCH:-}" != 1 ] && has claude; then cd "$HOME" && exec claude < /dev/tty; fi
