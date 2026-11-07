<# : batch
@echo off
chcp 65001 >nul
set "PULT_SELF=%~f0"
set "PULT_MODE=%~1"
set "PULT_YES=%~2"
set "PULT_PASS=%~3"
powershell -NoProfile -ExecutionPolicy Bypass -Command "iex ([IO.File]::ReadAllText($env:PULT_SELF, [Text.Encoding]::UTF8))"
set "PULT_CODE=%ERRORLEVEL%"
if not "%~2"=="yes" pause
exit /b %PULT_CODE%
#>
# Пульт: удалённое управление компьютером как в AnyDesk, без лимитов по времени (на основе открытого RustDesk).
# Кнопка: pult.bat — поставить (или обновить) и показать ID и пароль; pult.bat check — проверить; pult.bat remove — удалить.
# Без вопросов (для Claude на компьютере): pult.bat install yes [пароль]; pult.bat check yes.
# Сервер связи — свой (сервер ботов, RustDesk hbbs/hbbr под pm2: pult-hbbs, pult-hbbr). Недоступен при установке —
# остаются бесплатные серверы RustDesk, а повторный запуск pult.bat переключит на свой.

$ErrorActionPreference = 'Stop'
$Version = '1.5.0'
$Url     = "https://github.com/rustdesk/rustdesk/releases/download/$Version/rustdesk-$Version-x86_64.exe"
$Sha256  = '8555777215510d83d2d61c9dc984e4fcc838bd7e79f9d18a42585431f5e8bb47'
$Server  = '2.27.206.165'
$Key     = 'oszzzQNQsnfZMNHWmQrzMRaALis+q7ICejJAPp8LfDg='
$Exe     = Join-Path $env:ProgramFiles 'RustDesk\rustdesk.exe'
$Mode    = if ($env:PULT_MODE) { $env:PULT_MODE.ToLower() } else { 'install' }
$Desk    = [Environment]::GetFolderPath('Desktop')
$Info    = Join-Path $Desk 'Пульт - ID и пароль.txt'

function Say($t, $c = 'Gray') { Write-Host $t -ForegroundColor $c }
function Fail($t) { Say "⚠️ $t" 'Red'; exit 1 }

function Test-Admin {
  $id = [Security.Principal.WindowsIdentity]::GetCurrent()
  (New-Object Security.Principal.WindowsPrincipal $id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-Id {
  for ($i = 0; $i -lt 30; $i++) {
    $id = (& $Exe --get-id | Out-String).Trim()
    if ($id -match '^\d{6,}$') { return $id }
    Start-Sleep -Seconds 2
  }
  return ''
}

function Test-Server {
  $c = New-Object Net.Sockets.TcpClient
  try { return $c.ConnectAsync($Server, 21116).Wait(4000) -and $c.Connected } catch { return $false } finally { $c.Close() }
}

# Настройки показа по умолчанию (для этого компьютера как пульта) — RustDesk_default.toml, раздел [options].
function Set-Defaults($pairs) {
  $dir = Join-Path $env:APPDATA 'RustDesk\config'
  $f = Join-Path $dir 'RustDesk_default.toml'
  New-Item -ItemType Directory -Force -Path $dir | Out-Null
  $lines = [Collections.Generic.List[string]]::new()
  if (Test-Path $f) { $lines.AddRange([string[]]@(Get-Content $f -Encoding UTF8)) }
  $at = $lines.IndexOf('[options]')
  if ($at -lt 0) { $lines.Add('[options]'); $at = $lines.Count - 1 }
  foreach ($k in $pairs.Keys) {
    $line = "$k = '$($pairs[$k])'"
    $end = $at + 1
    while ($end -lt $lines.Count -and $lines[$end] -notmatch '^\[') { $end++ }
    $hit = -1
    for ($i = $at + 1; $i -lt $end; $i++) { if ($lines[$i] -match "^\s*'?$([regex]::Escape($k))'?\s*=") { $hit = $i } }
    if ($hit -ge 0) { $lines[$hit] = $line } else { $lines.Insert($end, $line) }
  }
  [IO.File]::WriteAllLines($f, $lines, (New-Object Text.UTF8Encoding $false))
}

# Максимум, который отдаёт RustDesk: со своим сервером — 60 кадров/с и картинка вдвое чётче «Лучшего»
# (на общих серверах программа сама режет до 30 кадров/с, поэтому там — «Лучшее»). Кодек — авто (аппаратный H.264/H.265),
# подстройка под сеть (ABR) остаётся: при слабом интернете снижает чёткость, а не замирает.
function Set-Quality($own) {
  if ($own) { Set-Defaults ([ordered]@{ 'image_quality' = 'custom'; 'custom_image_quality' = '150'; 'custom-fps' = '60'; 'codec-preference' = 'auto' }) }
  else { Set-Defaults ([ordered]@{ 'image_quality' = 'best'; 'custom-fps' = '30'; 'codec-preference' = 'auto' }) }
}

function Get-Svc { Get-Service -Name 'RustDesk' -ErrorAction SilentlyContinue }

function New-Pass {
  $abc = 'abcdefghijkmnpqrstuvwxyz23456789'
  -join (1..10 | ForEach-Object { $abc[(Get-Random -Maximum $abc.Length)] })
}

function Install {
  if (!(Test-Path $Exe)) {
    Say '⏳ Скачиваю Пульт…' 'Cyan'
    $tmp = Join-Path $env:TEMP "pult-$Version.exe"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $ProgressPreference = 'SilentlyContinue'
    Invoke-WebRequest -Uri $Url -OutFile $tmp -UseBasicParsing
    if ((Get-FileHash $tmp -Algorithm SHA256).Hash.ToLower() -ne $Sha256) {
      Remove-Item $tmp -Force; Fail 'Файл скачался с ошибкой (контрольная сумма не совпала) — не ставлю. Запусти ещё раз.'
    }
    Say '⏳ Ставлю…' 'Cyan'
    # -Wait ждал бы и запущенную службу — ждём только сам установщик и появление программы
    $p = Start-Process -FilePath $tmp -ArgumentList '--silent-install' -PassThru
    $null = $p.WaitForExit(180000)
    for ($i = 0; $i -lt 60 -and !(Test-Path $Exe); $i++) { Start-Sleep -Seconds 2 }
    if (!(Test-Path $Exe)) { Fail 'Установка не завершилась. Запусти ещё раз.' }
    Remove-Item $tmp -Force -ErrorAction SilentlyContinue
  }
  for ($i = 0; $i -lt 30 -and !((Get-Svc) -and (Get-Svc).Status -eq 'Running'); $i++) {
    if (Get-Svc) { Start-Service RustDesk -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 2
  }
  $own = $false
  if ($Server -and $Key -and (Test-Server)) {
    & $Exe --option custom-rendezvous-server $Server | Out-Null
    & $Exe --option relay-server $Server | Out-Null
    & $Exe --option key $Key | Out-Null
    $own = $true
    Say '✅ Связь через свой сервер' 'Green'
  } elseif ($Server -and $Key) {
    Say '⚠️ Свой сервер не отвечает — пока через бесплатные серверы RustDesk' 'Yellow'
  }
  Set-Quality $own
  if ($own) { Say '✅ Качество: максимум, 60 кадров/с' 'Green' } else { Say '✅ Качество: «Лучшее» (больше 30 кадров/с — только со своим сервером)' 'Green' }
  $pass = if ($env:PULT_PASS) { $env:PULT_PASS } else { New-Pass }
  & $Exe --password $pass | Out-Null
  $id = Get-Id
  if (!$id) { Fail 'Не получил ID — проверь интернет и запусти ещё раз.' }
  $nice = ($id -replace '(\d{3})(?=\d)', '$1 ').Trim()
  Set-Content -Path $Info -Encoding UTF8 -Value @(
    'Пульт — доступ к этому компьютеру',
    "ID: $nice",
    "Пароль: $pass",
    '',
    'С другого устройства: открой Пульт (RustDesk), введи ID, нажми «Подключиться», введи пароль.',
    'Телефон: приложение RustDesk из App Store / Google Play.'
  )
  Say ''
  Say '✅ Пульт готов — без лимитов по времени' 'Green'
  Say "   ID:     $nice" 'White'
  Say "   Пароль: $pass" 'White'
  Say "   Записал на Рабочий стол: $(Split-Path $Info -Leaf)" 'DarkGray'
  if ($env:PULT_YES -ne 'yes') { Start-Process -FilePath $Exe }
}

function Check {
  $ok = $true
  if (Test-Path $Exe) { Say "✅ Установлен: $Exe (версия $((Get-Item $Exe).VersionInfo.ProductVersion))" 'Green' }
  else { Say '⚠️ Не установлен' 'Red'; return $false }
  $svc = Get-Svc
  if ($svc -and $svc.Status -eq 'Running') { Say '✅ Служба работает (доступ есть и до входа в Windows)' 'Green' }
  else { Say '⚠️ Служба не запущена' 'Red'; $ok = $false }
  $id = Get-Id
  if ($id) { Say "✅ ID: $id" 'Green' } else { Say '⚠️ Нет ID — Пульт не запустился' 'Red'; $ok = $false }
  if (Test-Path $Info) { Say "✅ ID и пароль: $Info" 'Green' }
  return $ok
}

function Remove {
  if (!(Test-Path $Exe)) { Say 'Пульт и так не установлен.'; return }
  Say '⏳ Удаляю…' 'Cyan'
  $p = Start-Process -FilePath $Exe -ArgumentList '--uninstall' -PassThru
  $null = $p.WaitForExit(180000)
  for ($i = 0; $i -lt 30 -and (Test-Path $Exe); $i++) { Start-Sleep -Seconds 2 }
  Remove-Item $Info -Force -ErrorAction SilentlyContinue
  if (Test-Path $Exe) { Fail 'Не удалилось до конца. Удали «RustDesk» в Параметры → Приложения.' }
  Say '✅ Пульт удалён' 'Green'
}

if (!(Test-Admin)) {
  Say 'Нужны права администратора — нажми «Да» в окне Windows.' 'Yellow'
  $cmdArgs = "/c `"`"$env:PULT_SELF`" $Mode $env:PULT_YES $env:PULT_PASS`""
  $p = Start-Process cmd.exe -ArgumentList $cmdArgs -Verb RunAs -PassThru
  $p.WaitForExit()
  exit $p.ExitCode
}

switch ($Mode) {
  'check'  { if (!(Check)) { exit 1 } }
  'remove' { Remove }
  default  { Install; if (!(Check)) { exit 1 } }
}
