# «Заметки» для бабушки, Windows: Git, GitHub CLI, Obsidian → хранилище ~/Заметки = приватный репозиторий GitHub
# (плагин Obsidian Git сам сохраняет туда), правила для Claude, ярлык «Claude — Заметки» на рабочем столе.
# Запускает Claude Code по промту docs/kit/babushka.md (из bash или PowerShell, не от администратора):
#   powershell -NoProfile -ExecutionPolicy Bypass -Command "iex ([Text.Encoding]::UTF8.GetString((New-Object Net.WebClient).DownloadData('https://sawqws.github.io/123/kit/babushka.ps1')))"
# Запускать можно сколько угодно раз: что уже сделано — пропускает, чужое не перезаписывает.
# Код выхода: 0 — всё готово; 10 — нужен вход в GitHub (gh auth login --web), потом запустить снова; 1 — ошибка (текст выше).
# Переменные: BABUSHKA_VAULT (папка, по умолчанию ~/Заметки), BABUSHKA_REPO (zametki), BABUSHKA_SKIP_INSTALL=1,
#   BABUSHKA_NO_LAUNCH=1 (не открывать Obsidian), BABUSHKA_REMOTE=<адрес git> (тест: вместо GitHub).

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch {}

$Vault = if ($env:BABUSHKA_VAULT) { $env:BABUSHKA_VAULT } else { Join-Path $HOME 'Заметки' }
$Repo = if ($env:BABUSHKA_REPO) { $env:BABUSHKA_REPO } else { 'zametki' }
$VaultName = Split-Path $Vault -Leaf
$Utf8 = New-Object Text.UTF8Encoding $false
$PluginUrl = 'https://github.com/Vinzent03/obsidian-git/releases/latest/download'

function Say($t) { Write-Host "▸ $t" -ForegroundColor Cyan }
function Ok($t) { Write-Host "  ✅ $t" -ForegroundColor Green }
function Warn($t) { Write-Host "  ⚠️ $t" -ForegroundColor Yellow }
function Fail($t) { Write-Host "  ❌ $t" -ForegroundColor Red; exit 1 }
function Has($c) { [bool](Get-Command $c -ErrorAction SilentlyContinue) }
function RefreshPath {
  $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User') + ";$HOME\.local\bin"
}
# Файлы — в UTF-8 без BOM: с BOM Obsidian не читает JSON, а git — первую строку .gitignore.
function WriteText($path, $text) {
  New-Item -ItemType Directory -Force -Path (Split-Path $path) | Out-Null
  [IO.File]::WriteAllText($path, ($text -replace "`r`n", "`n"), $Utf8)
}
function WriteNew($path, $text) { if (-not (Test-Path -LiteralPath $path)) { WriteText $path $text } }
function ObsidianExe {
  foreach ($p in "$env:LOCALAPPDATA\Programs\Obsidian\Obsidian.exe", "$env:ProgramFiles\Obsidian\Obsidian.exe") { if (Test-Path $p) { return $p } }
  return $null
}
function G { & git -C $Vault @args; if ($LASTEXITCODE) { Fail "git $($args -join ' ') — код $LASTEXITCODE" } }

# ---------- 1. Программы ----------
RefreshPath
if ($env:BABUSHKA_SKIP_INSTALL -ne '1') {
  Say 'Программы: Git, GitHub CLI, Obsidian'
  $apps = @(
    @{ id = 'Git.Git'; name = 'Git'; have = { Has 'git' } },
    @{ id = 'GitHub.cli'; name = 'GitHub CLI'; have = { Has 'gh' } },
    @{ id = 'Obsidian.Obsidian'; name = 'Obsidian'; have = { [bool](ObsidianExe) } }
  )
  foreach ($a in $apps) {
    if (& $a.have) { Ok "$($a.name) уже есть"; continue }
    if (-not (Has 'winget')) { Warn "$($a.name): нет winget — поставь из Microsoft Store «Установщик приложений» и запусти снова"; continue }
    winget install --id $a.id -e --silent --accept-package-agreements --accept-source-agreements | Out-Null
    RefreshPath
    if (& $a.have) { Ok "$($a.name) поставлен" } else { Warn "$($a.name) не встал (winget, код $LASTEXITCODE)" }
  }
}
if (-not (Has 'git')) { Fail 'нет Git — без него не работает' }

# ---------- 2. Хранилище Obsidian ----------
Say "Хранилище: $Vault"
New-Item -ItemType Directory -Force -Path $Vault | Out-Null
WriteNew (Join-Path $Vault '.gitignore') @'
.obsidian/workspace.json
.obsidian/workspace-mobile.json
.obsidian/cache
.trash/
Thumbs.db
desktop.ini
.DS_Store
'@
WriteNew (Join-Path $Vault 'Начни здесь.md') @'
# Начни здесь 👋

Это ваши заметки. Пишите что угодно — **сохранять не нужно**: всё сохраняется само, а через пару минут копия уходит в интернет (GitHub). Даже если компьютер сломается, заметки не пропадут.

- **Новая заметка** — Ctrl+N (на Mac — Cmd+N).
- **Найти заметку** — Ctrl+O и начните печатать название.
- **Помощник Claude** — ярлык «Claude — Заметки» на рабочем столе. Пишите ему обычными словами:
  - «Запиши рецепт пирога: …»
  - «Найди, где я писала про давление»
  - «Сделай список покупок на неделю»
'@
WriteNew (Join-Path $Vault '.claude\NOW.md') @'
# Сейчас

> Живая передача между чатами Claude: у каждой задачи свой раздел «## …» (что делаем, что сделано, что дальше).
> Обновлять после каждого заметного шага, задача закончена — раздел удалить.
'@
WriteNew (Join-Path $Vault '.claude\CLAUDE.md') @"
# Заметки бабушки — правила для Claude

Эта папка — хранилище Obsidian и приватный репозиторий GitHub ``$Repo`` (ветка main).

## Как говорить
- Просто, по-русски, коротко, без компьютерных слов. Одна просьба за раз.
- Если ей нужно что-то нажать — одной фразой: что и где.

## Заметки
- Заметка — файл ``.md`` в этой папке, название по-русски и понятное («Рецепт борща», «Лекарства»).
- Ничего не удалять и не переносить без её «да».
- Пароли, коды из СМС, номера карт и документов в заметки не писать — мягко предложить бумажную тетрадь.

## Сохранение
- Obsidian сохраняет в GitHub сам (плагин Git, через пару минут после правки).
- В начале работы — ``git pull``. Поменял файлы сам — сразу ``git add -A``, ``git commit``, ``git push``.
- Конфликт в git — разрешить самому, ничего не теряя, и сказать ей одной фразой.

## Переход в новый чат
- ``.claude/NOW.md`` — что сейчас делаем. Читать в начале чата, обновлять после заметного шага (commit + push), готово — раздел удалить.
"@
WriteNew (Join-Path $Vault '.claude\settings.json') @'
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
'@
$ob = Join-Path $Vault '.obsidian'
WriteNew (Join-Path $ob 'appearance.json') "{`n  `"baseFontSize`": 20`n}`n"
$cpFile = Join-Path $ob 'community-plugins.json'
$cp = @()
if (Test-Path -LiteralPath $cpFile) { try { $cp = @((Get-Content -LiteralPath $cpFile -Raw -Encoding UTF8 | ConvertFrom-Json) | ForEach-Object { "$_" }) } catch { $cp = @() } }
if ($cp -notcontains 'obsidian-git') { WriteText $cpFile ((ConvertTo-Json -InputObject @($cp + 'obsidian-git') -Compress) + "`n") }
$pd = Join-Path $ob 'plugins\obsidian-git'
New-Item -ItemType Directory -Force -Path $pd | Out-Null
foreach ($f in 'main.js', 'manifest.json', 'styles.css') {
  $dst = Join-Path $pd $f
  if ((Test-Path -LiteralPath $dst) -and (Get-Item -LiteralPath $dst).Length -gt 0) { continue }
  try { (New-Object Net.WebClient).DownloadFile("$PluginUrl/$f", $dst) } catch { Fail "плагин Obsidian Git: не скачался $f ($($_.Exception.Message))" }
}
# Ключи сверены с DEFAULT_SETTINGS плагина 2.43: коммит и отправка через 2 мин после последней правки, приём раз в 10 мин и при запуске.
WriteNew (Join-Path $pd 'data.json') @'
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
'@
Ok 'файлы хранилища и плагин Git на месте'

# Obsidian сам откроет это хранилище (список хранилищ — %APPDATA%\obsidian\obsidian.json; Obsidian должен быть закрыт)
$running = Get-Process -Name Obsidian -ErrorAction SilentlyContinue
if ($running) { $running | Stop-Process -Force -ErrorAction SilentlyContinue; Start-Sleep 2 }
$oj = Join-Path $env:APPDATA 'obsidian\obsidian.json'
$cfg = $null
if (Test-Path -LiteralPath $oj) { try { $cfg = Get-Content -LiteralPath $oj -Raw -Encoding UTF8 | ConvertFrom-Json } catch { $cfg = $null } }
if (-not $cfg) { $cfg = New-Object psobject }
if (-not $cfg.PSObject.Properties['vaults']) { $cfg | Add-Member -NotePropertyName vaults -NotePropertyValue (New-Object psobject) }
$full = (Resolve-Path -LiteralPath $Vault).ProviderPath.TrimEnd('\')
$mine = $null
foreach ($p in $cfg.vaults.PSObject.Properties) {
  if ($p.Value.path -and $p.Value.path.TrimEnd('\') -ieq $full) { $mine = $p.Value } elseif ($p.Value.PSObject.Properties['open']) { $p.Value.open = $false }
}
if (-not $mine) {
  $id = -join ((1..16) | ForEach-Object { '{0:x}' -f (Get-Random -Maximum 16) })
  $mine = [pscustomobject]@{ path = $full; ts = [long]([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()); open = $true }
  $cfg.vaults | Add-Member -NotePropertyName $id -NotePropertyValue $mine
} else { $mine | Add-Member -NotePropertyName open -NotePropertyValue $true -Force }
WriteText $oj (($cfg | ConvertTo-Json -Depth 10) + "`n")
Ok "Obsidian откроет «${VaultName}» сам"

# ---------- 3. Git ----------
Say 'Git'
if (-not (Test-Path -LiteralPath (Join-Path $Vault '.git'))) { G init -q; G symbolic-ref HEAD refs/heads/main }
G config core.quotepath false
G config core.autocrlf false
$authed = $false
if (-not $env:BABUSHKA_REMOTE) {
  if (-not (Has 'gh')) { Fail 'нет GitHub CLI (gh)' }
  gh auth status --hostname github.com *> $null
  $authed = ($LASTEXITCODE -eq 0)
  if ($authed) {
    gh auth setup-git --hostname github.com | Out-Null
    if (-not (git config --global user.name)) {
      $login = gh api user --jq .login; $uid = gh api user --jq .id; $name = gh api user --jq .name
      if (-not $name) { $name = $login }
      git config --global user.name $name
      git config --global user.email "$uid+$login@users.noreply.github.com"
    }
  }
}
if (-not $env:BABUSHKA_REMOTE -and -not $authed) {
  Write-Host ''
  Write-Host "НУЖЕН ВХОД В GITHUB. Команда: gh auth login --hostname github.com --web --git-protocol https (gh = $((Get-Command gh).Source)). Потом запусти этот скрипт снова." -ForegroundColor Magenta
  exit 10
}
if (-not (git config user.name)) { Fail 'у git нет имени автора (git config --global user.name)' }
G add -A
git -C $Vault diff --cached --quiet
if ($LASTEXITCODE) { G commit -q -m 'Заметки: настройка' ; Ok 'сохранено в git' } else { Ok 'нечего сохранять — всё уже в git' }

# ---------- 4. GitHub ----------
Say "GitHub: приватный репозиторий $Repo"
$hasOrigin = [bool](git -C $Vault remote 2>$null | Where-Object { $_ -eq 'origin' })
if (-not $hasOrigin) {
  if ($env:BABUSHKA_REMOTE) { G remote add origin $env:BABUSHKA_REMOTE }
  else {
    gh repo view $Repo --json name *> $null
    if ($LASTEXITCODE -eq 0) { G remote add origin ((gh repo view $Repo --json url --jq .url) + '.git') }
    else {
      gh repo create $Repo --private --description 'Заметки (Obsidian)' --source $Vault --remote origin
      if ($LASTEXITCODE) { Fail "не создался репозиторий $Repo на GitHub" }
    }
  }
}
git -C $Vault fetch -q origin 2>$null
git -C $Vault rev-parse -q --verify refs/remotes/origin/main *> $null
if ($LASTEXITCODE -eq 0) {
  git -C $Vault pull -q --no-rebase --allow-unrelated-histories --no-edit origin main
  if ($LASTEXITCODE) { Fail 'конфликт при слиянии с GitHub — разреши его в папке и запусти снова' }
}
G push -q -u origin main
Ok 'всё отправлено в GitHub'

# ---------- 5. Claude: правила для всех чатов и ярлык ----------
Say 'Claude'
$gm = Join-Path $HOME '.claude\CLAUDE.md'
$old = if (Test-Path -LiteralPath $gm) { [IO.File]::ReadAllText($gm) } else { '' }
if ($old -notmatch 'babushka-notes') {
  $block = @"
<!-- babushka-notes -->
# Ты помогаешь бабушке
- Она с компьютером на «вы»: говори просто, по-русски, коротко, без компьютерных слов. Одна просьба за раз.
- Её заметки — в папке ``$full`` (Obsidian, сохраняются в GitHub сами). Записать, найти, поправить заметку — там; правила — в .claude/CLAUDE.md этой папки.
- Ничего не удалять и не оплачивать без её «да». Пароли и коды из СМС не записывать.
"@
  WriteText $gm (($old.TrimEnd() + "`n`n" + $block).TrimStart() + "`n")
  Ok 'правила Claude для всех чатов — ~/.claude/CLAUDE.md'
}
$claude = (Get-Command claude -ErrorAction SilentlyContinue).Source
if (-not $claude -and (Test-Path "$HOME\.local\bin\claude.exe")) { $claude = "$HOME\.local\bin\claude.exe" }
if ($claude) {
  $lnk = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Claude — Заметки.lnk'
  try {
    if (-not ('BabushkaLnk' -as [type])) {
      Add-Type -TypeDefinition @'
using System; using System.Text; using System.Runtime.InteropServices; using System.Runtime.InteropServices.ComTypes;
[ComImport, Guid("00021401-0000-0000-C000-000000000046")] class BabushkaShellLink {}
[ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown), Guid("000214F9-0000-0000-C000-000000000046")]
interface IBabushkaShellLinkW {
  void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder f, int c, IntPtr fd, uint fl);
  void GetIDList(out IntPtr p); void SetIDList(IntPtr p);
  void GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder s, int c);
  void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string s);
  void GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder s, int c);
  void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string s);
  void GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder s, int c);
  void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string s);
  void GetHotkey(out short h); void SetHotkey(short h);
  void GetShowCmd(out int i); void SetShowCmd(int i);
  void GetIconLocation([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder s, int c, out int i);
  void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string s, int i);
  void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string s, uint r);
  void Resolve(IntPtr h, uint f);
  void SetPath([MarshalAs(UnmanagedType.LPWStr)] string s);
}
public static class BabushkaLnk {
  public static void Save(string lnk, string target, string args, string dir, string icon) {
    var l = (IBabushkaShellLinkW)new BabushkaShellLink();
    l.SetPath(target); l.SetArguments(args); l.SetWorkingDirectory(dir);
    if (!string.IsNullOrEmpty(icon)) l.SetIconLocation(icon, 0);
    ((IPersistFile)l).Save(lnk, true);
  }
}
'@
    }
    $icon = if ($claude -like '*.exe') { $claude } else { '' }
    [BabushkaLnk]::Save($lnk, "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe", "-NoLogo -NoExit -Command `"& '$($claude -replace "'", "''")'`"", $full, $icon)
    Ok 'ярлык «Claude — Заметки» на рабочем столе'
  } catch { Warn "ярлык не сделан: $($_.Exception.Message)" }
} else { Warn 'Claude Code (команда claude) не найден — ярлык не сделан' }

# ---------- 6. Открыть Obsidian ----------
if ($env:BABUSHKA_NO_LAUNCH -ne '1') {
  $exe = ObsidianExe
  if ($exe) {
    Start-Process -FilePath $exe
    Start-Sleep 8
    try { Start-Process ('obsidian://open?vault=' + [Uri]::EscapeDataString($VaultName) + '&file=' + [Uri]::EscapeDataString('Начни здесь')) } catch {}
    Ok 'Obsidian открыт'
  } else { Warn 'Obsidian не найден' }
}
Write-Host ''
Write-Host "ГОТОВО: хранилище $full · репозиторий $Repo · плагин Git включится после «Доверять автору» в Obsidian" -ForegroundColor Magenta
exit 0
