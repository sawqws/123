# Мост «принтер -> сервер» для Ender-3 V3 KE. Запуск: команда из чата с Claude ($h и $p уже заданы).
$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
if (-not $h -or -not $p) { Write-Host 'Скопируй команду из чата целиком.' -ForegroundColor Red; return }
if (-not (Get-Command ssh -ErrorAction SilentlyContinue)) {
  Write-Host 'В Windows нет ssh. Параметры -> Приложения -> Дополнительные компоненты -> «Клиент OpenSSH» -> Установить, потом снова команду.' -ForegroundColor Red; return
}
$ip = (Read-Host 'IP принтера (Enter = 192.168.1.141)').Trim()
if (-not $ip) { $ip = '192.168.1.141' }
if ($ip -notmatch '^\d{1,3}(\.\d{1,3}){3}$') { Write-Host "Это не IP: $ip" -ForegroundColor Red; return }
$src = (Invoke-WebRequest -UseBasicParsing 'https://raw.githubusercontent.com/sawqws/123/claude/ender-k3-filament-rewinder-nff89j/print/bridge/printer.sh').Content
if ($src -is [byte[]]) { $src = [Text.Encoding]::UTF8.GetString($src) }
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($src -replace "`r", '')))
Write-Host ''
Write-Host 'Сейчас спросит пароль принтера: он на экране в «Root account information». Буквы при вводе не видны — это нормально.' -ForegroundColor Yellow
Write-Host 'Если спросит «Are you sure you want to continue connecting» — напиши yes.' -ForegroundColor Yellow
$cmd = "(echo $b64 | base64 -d 2>/dev/null || echo $b64 | python3 -c 'import sys,base64;sys.stdout.buffer.write(base64.b64decode(sys.stdin.read()))') > /tmp/bridge.sh; H='$h' P='$p' sh /tmp/bridge.sh; rm -f /tmp/bridge.sh"
ssh -o StrictHostKeyChecking=accept-new "root@$ip" $cmd | Tee-Object -Variable out
$line = @($out) | Where-Object { $_ -like 'BRIDGE-KEY:*' } | Select-Object -Last 1
if ($LASTEXITCODE -eq 0 -and $line) {
  $key = ($line -replace '^BRIDGE-KEY:\s*', '').Trim()
  try { Set-Clipboard -Value "мост: $key"; $copied = $true } catch { $copied = $false }
  Write-Host ''
  if ($copied) { Write-Host 'Готово! Ключ уже скопирован — вставь его в чат Claude (Ctrl+V) и отправь.' -ForegroundColor Green }
  else { Write-Host "Готово! Отправь Claude в чат эту строку:`nмост: $key" -ForegroundColor Green }
}
else { Write-Host "Не вышло (код $LASTEXITCODE). Сфотографируй это окно и пришли Claude." -ForegroundColor Red }
