# Набор «Claude с нуля» для Windows: ставит Git, GitHub CLI, Obsidian, Claude Code (и Claude Desktop),
# ищет старые наработки (хранилища Obsidian, git-репозитории, настройки прошлого Claude) и готовит промпт
# для нового Claude Code. Секретов не читает и не выводит — только имена и пути.
# Запуск (обычный PowerShell, не от администратора):
#   iex ([Text.Encoding]::UTF8.GetString((New-Object Net.WebClient).DownloadData('https://sawqws.github.io/123/kit/start.ps1')))
# KIT_SKIP_INSTALL=1 — без установки (только поиск и промпт), KIT_NO_LAUNCH=1 — не открывать Claude в конце.

$ErrorActionPreference = 'Continue'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
$Base = if ($env:KIT_BASE) { $env:KIT_BASE } else { 'https://sawqws.github.io/123/kit' }
$Kit = Join-Path $HOME 'claude-kit'
New-Item -ItemType Directory -Force -Path $Kit | Out-Null

function Say($t) { Write-Host "▸ $t" -ForegroundColor Cyan }
function Ok($t) { Write-Host "  ✅ $t" -ForegroundColor Green }
function Warn($t) { Write-Host "  ⚠️ $t" -ForegroundColor Yellow }
function Has($c) { [bool](Get-Command $c -ErrorAction SilentlyContinue) }
function RefreshPath {
  $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User') + ";$HOME\.local\bin"
}

# ---------- 1. Установка ----------
if ($env:KIT_SKIP_INSTALL -ne '1') {
  Say 'Установка программ'
  $wg = Has 'winget'
  if (-not $wg) { Warn 'winget нет — Git/gh/Obsidian поставь вручную (Microsoft Store → «Установщик приложений»)' }
  $apps = @(
    @{ id = 'Git.Git'; cmd = 'git'; name = 'Git' },
    @{ id = 'GitHub.cli'; cmd = 'gh'; name = 'GitHub CLI' },
    @{ id = 'Obsidian.Obsidian'; cmd = ''; name = 'Obsidian'; path = "$env:LOCALAPPDATA\Programs\Obsidian\Obsidian.exe" },
    @{ id = 'Anthropic.Claude'; cmd = ''; name = 'Claude Desktop'; path = "$env:LOCALAPPDATA\AnthropicClaude\claude.exe" }
  )
  foreach ($a in $apps) {
    $have = if ($a.cmd) { Has $a.cmd } else { Test-Path $a.path }
    if ($have) { Ok "$($a.name) уже есть"; continue }
    if (-not $wg) { continue }
    winget install --id $a.id -e --silent --accept-package-agreements --accept-source-agreements | Out-Null
    RefreshPath
    $have = if ($a.cmd) { Has $a.cmd } else { Test-Path $a.path }
    if ($have -or $LASTEXITCODE -eq 0) { Ok "$($a.name) поставлен" } else { Warn "$($a.name): не встал (код $LASTEXITCODE)" }
  }
  RefreshPath
  if (Has 'claude') { Ok 'Claude Code уже есть' }
  else {
    Say 'Claude Code (официальный установщик claude.ai/install.ps1)'
    try { Invoke-RestMethod https://claude.ai/install.ps1 | Invoke-Expression } catch { Warn "Claude Code: $($_.Exception.Message)" }
    RefreshPath
    if (Has 'claude') { Ok 'Claude Code поставлен' } else { Warn 'Claude Code не найден в PATH — открой новый PowerShell' }
  }
}
RefreshPath

# ---------- 2. Поиск старых наработок ----------
Say 'Ищу старые наработки (только пути и имена, без содержимого секретов)'
$skip = '\\(node_modules|\.cache|\.npm|\.nuget|\.cargo|\.rustup|site-packages|\.venv|venv)\\'
$noRoot = '^(AppData|\.cache|\.npm|\.nuget|\.cargo|\.rustup|\.vscode|scoop|Windows|Program Files.*|ProgramData|\$Recycle\.Bin|System Volume Information|PerfLogs|Recovery)$'
$Roots = @(Get-ChildItem -Path $HOME -Directory -Force -ErrorAction SilentlyContinue | Where-Object { $_.Name -notmatch $noRoot } | ForEach-Object { $_.FullName })
$sys = $HOME.Substring(0, 3)
foreach ($d in Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue) {
  if ($d.Root -eq $sys -or -not $d.Used) { continue }
  $Roots += @(Get-ChildItem -Path $d.Root -Directory -Force -ErrorAction SilentlyContinue | Where-Object { $_.Name -notmatch $noRoot } | ForEach-Object { $_.FullName })
}
function Find-Items($name, $depth, [switch]$File) {
  foreach ($r in $Roots) {
    if ($File) { $items = Get-ChildItem -Path $r -File -Force -Recurse -Depth $depth -Filter $name -ErrorAction SilentlyContinue }
    else { $items = Get-ChildItem -Path $r -Directory -Force -Recurse -Depth $depth -Filter $name -ErrorAction SilentlyContinue }
    $items | Where-Object { $_.FullName -notmatch $skip }
  }
}
function Run($exe, $argv) { try { (& $exe @argv 2>$null | Out-String).Trim() } catch { '' } }

$scan = [ordered]@{ created = (Get-Date).ToString('s'); os = 'windows'; computer = $env:COMPUTERNAME; home = $HOME }

# Obsidian
$vaults = @()
$oj = Join-Path $env:APPDATA 'obsidian\obsidian.json'
if (Test-Path $oj) {
  try { (Get-Content $oj -Raw -Encoding UTF8 | ConvertFrom-Json).vaults.PSObject.Properties | ForEach-Object { $vaults += $_.Value.path } } catch {}
}
Find-Items '.obsidian' 4 | ForEach-Object { $vaults += $_.Parent.FullName }
$scan.obsidian_vaults = @($vaults | Where-Object { $_ -and (Test-Path $_) } | Sort-Object -Unique | ForEach-Object {
  $md = @(Get-ChildItem -Path $_ -Recurse -File -Filter *.md -ErrorAction SilentlyContinue | Where-Object { $_.FullName -notmatch '\\\.(obsidian|trash)\\' })
  $plugins = @()
  $cp = Join-Path $_ '.obsidian\community-plugins.json'
  if (Test-Path $cp) { try { $plugins = @(Get-Content $cp -Raw | ConvertFrom-Json) } catch {} }
  [ordered]@{ path = $_; notes = $md.Count; git = (Test-Path (Join-Path $_ '.git')); plugins = $plugins
    last_edit = if ($md) { ($md | Sort-Object LastWriteTime -Descending | Select-Object -First 1).LastWriteTime.ToString('s') } else { '' } }
})

# Git-репозитории
$scan.git_repos = @()
if (Has 'git') {
  $scan.git_repos = @(Find-Items '.git' 5 | ForEach-Object { $_.Parent.FullName } | Sort-Object -Unique | ForEach-Object {
    $p = $_
    [ordered]@{ path = $p
      remote = Run 'git' @('-C', $p, 'remote', 'get-url', 'origin')
      branch = Run 'git' @('-C', $p, 'branch', '--show-current')
      uncommitted = @((Run 'git' @('-C', $p, 'status', '--porcelain')) -split "`n" | Where-Object { $_ }).Count
      unpushed = Run 'git' @('-C', $p, 'rev-list', '--count', '@{u}..HEAD')
      last_commit = Run 'git' @('-C', $p, 'log', '-1', '--format=%cs %s')
      has_claude_md = (Test-Path (Join-Path $p 'CLAUDE.md')) -or (Test-Path (Join-Path $p '.claude')) }
  })
  $scan.git_user = Run 'git' @('config', '--global', 'user.name')
  $scan.git_email = Run 'git' @('config', '--global', 'user.email')
}
if (Has 'gh') { $scan.gh_status = Run 'gh' @('auth', 'status') }

# Прошлый Claude: Claude Code (~/.claude), Claude Desktop (MCP), CLAUDE.md вне репозиториев
$cl = Join-Path $HOME '.claude'
$scan.claude_code = [ordered]@{ exists = (Test-Path $cl) }
if (Test-Path $cl) {
  foreach ($k in 'skills', 'agents', 'commands', 'hooks', 'plugins') {
    $d = Join-Path $cl $k
    $scan.claude_code[$k] = if (Test-Path $d) { @(Get-ChildItem $d -ErrorAction SilentlyContinue | ForEach-Object { $_.Name }) } else { @() }
  }
  $scan.claude_code.global_claude_md = Test-Path (Join-Path $cl 'CLAUDE.md')
  $scan.claude_code.settings = Test-Path (Join-Path $cl 'settings.json')
  $pd = Join-Path $cl 'projects'
  $scan.claude_code.chat_projects = if (Test-Path $pd) { @(Get-ChildItem $pd -Directory | ForEach-Object { $_.Name }) } else { @() }
}
$cj = Join-Path $HOME '.claude.json'
if (Test-Path $cj) {
  try { $j = Get-Content $cj -Raw -Encoding UTF8 | ConvertFrom-Json
    $scan.claude_code.mcp_servers = @($j.mcpServers.PSObject.Properties.Name)
    $scan.claude_code.account = [string]$j.oauthAccount.emailAddress } catch {}
}
$dc = Join-Path $env:APPDATA 'Claude\claude_desktop_config.json'
$scan.claude_desktop = [ordered]@{ installed = (Test-Path "$env:LOCALAPPDATA\AnthropicClaude"); config = (Test-Path $dc) }
if (Test-Path $dc) { try { $scan.claude_desktop.mcp_servers = @((Get-Content $dc -Raw -Encoding UTF8 | ConvertFrom-Json).mcpServers.PSObject.Properties.Name) } catch {} }
$scan.claude_md_files = @(Find-Items 'CLAUDE.md' 5 -File | ForEach-Object { $_.FullName })

# Прочее: проекты без git, правила других ИИ (Cursor, Codex, Gemini), редакторы
$scan.code_projects = @(foreach ($m in 'package.json', 'requirements.txt', 'pyproject.toml', 'main.py', 'bot.py', 'index.html') {
  Find-Items $m 4 -File | ForEach-Object { $_.DirectoryName } }) | Sort-Object -Unique | Where-Object {
  $d = $_; -not ($scan.git_repos | Where-Object { $d -like "$($_.path)*" }) } | Select-Object -First 200
$scan.ai_rules = @(foreach ($m in 'AGENTS.md', 'GEMINI.md', '.cursorrules', '.windsurfrules') { Find-Items $m 5 -File | ForEach-Object { $_.FullName } })
$scan.ai_dirs = @(foreach ($m in '.cursor', '.codex', '.gemini', '.continue', '.aider') { $d = Join-Path $HOME $m; if (Test-Path $d) { $d } })
$scan.editors = @(foreach ($e in 'Code', 'Cursor', 'Windsurf') { if (Test-Path (Join-Path $env:APPDATA "$e\User")) { $e } })

$scan | ConvertTo-Json -Depth 6 | Set-Content -Path (Join-Path $Kit 'scan.json') -Encoding UTF8
Ok "Хранилищ Obsidian: $($scan.obsidian_vaults.Count) · git-репозиториев: $($scan.git_repos.Count) · проектов без git: $(@($scan.code_projects).Count) · CLAUDE.md: $($scan.claude_md_files.Count) · прошлый Claude Code: $($scan.claude_code.exists)"
foreach ($v in $scan.obsidian_vaults) { Write-Host "    📓 $($v.path) — заметок $($v.notes)" }
foreach ($r in $scan.git_repos) { Write-Host "    📦 $($r.path) $(if ($r.remote) { '→ ' + $r.remote } else { '(без GitHub)' })" }

# ---------- 3. Промпт для нового Claude ----------
Say 'Промпт для нового Claude'
try {
  $p = [Text.Encoding]::UTF8.GetString((New-Object Net.WebClient).DownloadData("$Base/prompt.md"))
  [IO.File]::WriteAllText((Join-Path $Kit 'prompt.md'), $p, (New-Object Text.UTF8Encoding $false))
  Ok "Сохранён: $Kit\prompt.md"
} catch { Warn "Не скачался prompt.md: $($_.Exception.Message)" }
$first = 'Прочитай файл ~/claude-kit/prompt.md и выполни его целиком. Отчёт о старых наработках — ~/claude-kit/scan.json.'
try { Set-Clipboard -Value $first; Ok 'Первое сообщение для Claude скопировано — просто вставь (Ctrl+V)' } catch {}
[IO.File]::WriteAllText((Join-Path $Kit 'first-message.txt'), $first, (New-Object Text.UTF8Encoding $false))

Write-Host ''
Write-Host 'Дальше: в открывшемся окне Claude войди в аккаунт (браузер), потом Ctrl+V и Enter.' -ForegroundColor Magenta
if ($env:KIT_NO_LAUNCH -ne '1' -and (Has 'claude')) {
  Start-Process powershell -WorkingDirectory $HOME -ArgumentList '-NoExit', '-Command', 'claude'
}
$global:LASTEXITCODE = 0
