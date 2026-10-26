<# : batch
@echo off
set "RAZBOR_SELF=%~f0"
set "RAZBOR_MODE=%~1"
set "RAZBOR_YES=%~2"
powershell -NoProfile -ExecutionPolicy Bypass -Command "iex ([IO.File]::ReadAllText($env:RAZBOR_SELF, [Text.Encoding]::UTF8))"
if not "%~2"=="yes" pause
exit /b
#>
# Разбор файлов: Рабочий стол и Загрузки — по папкам, шлак — в карантин. Всё пишется в журнал,
# «Вернуть как было» (кнопка на Рабочем столе) откатывает последний разбор. Удаляет насовсем только пункт 3.
# Кнопка: razbor.bat — меню; razbor.bat undo — сразу вернуть; razbor.bat purge — удалить шлак.
# Без вопросов (для Claude на компьютере): razbor.bat plan — только план; razbor.bat sort yes — разобрать.

$ErrorActionPreference = 'Stop'
$State  = Join-Path $env:USERPROFILE 'Razbor'
$Junk   = Join-Path $State 'Шлак'
$Self   = $env:RAZBOR_SELF
$Desk   = [Environment]::GetFolderPath('Desktop')
$Button = Join-Path $Desk 'Вернуть как было.bat'

$Cats = [ordered]@{
  'Документы'   = 'pdf doc docx odt rtf txt md xls xlsx xlsm ods csv ppt pptx odp epub djvu fb2 pages numbers key'
  'Картинки'    = 'jpg jpeg png gif bmp webp heic heif svg tif tiff raw cr2 nef arw dng ico psd'
  'Видео'       = 'mp4 mov avi mkv wmv webm m4v 3gp flv mpg mpeg'
  'Музыка'      = 'mp3 wav flac m4a aac ogg opus wma'
  'Архивы'      = 'zip rar 7z tar gz tgz bz2 xz iso img'
  'Установщики' = 'exe msi msix msixbundle appx appxbundle dmg pkg apk'
  'Код'         = 'py js ts html htm css json xml yml yaml ps1 bat cmd sh java c cpp h cs ipynb sql'
  'Торренты'    = 'torrent'
}
$ExtCat = @{}
foreach ($c in $Cats.Keys) { foreach ($e in $Cats[$c].Split(' ')) { $ExtCat['.' + $e] = $c } }
$TempExt = '.tmp', '.crdownload', '.part', '.partial', '.download', '.opdownload'
$Installers = $Cats['Установщики'].Split(' ') | ForEach-Object { '.' + $_ }

function Say($t, $c = 'Gray') { Write-Host $t -ForegroundColor $c }
function Size($b) {
  if ($b -ge 1GB) { return ('{0:N1} ГБ' -f ($b / 1GB)) }
  if ($b -ge 1MB) { return ('{0:N0} МБ' -f ($b / 1MB)) }
  return ('{0:N0} КБ' -f ($b / 1KB))
}
function Yes($q) { if ($env:RAZBOR_YES -eq 'yes') { return $true }; $a = (Read-Host $q).Trim().ToLower(); return ($a -in 'да', 'lf', 'y', 'yes', 'д', 'l') }

function Get-Downloads {
  try {
    $k = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders'
    $p = $k.'{374DE290-123F-4565-9164-39C4925E467B}'
    if ($p) { $p = [Environment]::ExpandEnvironmentVariables($p); if (Test-Path -LiteralPath $p) { return $p } }
  } catch {}
  return (Join-Path $env:USERPROFILE 'Downloads')
}

# Свободное имя: «файл.pdf» занят → «файл (2).pdf»
function Free-Name($dir, $name) {
  $p = Join-Path $dir $name
  if (-not (Test-Path -LiteralPath $p)) { return $p }
  $b = [IO.Path]::GetFileNameWithoutExtension($name); $e = [IO.Path]::GetExtension($name)
  for ($i = 2; ; $i++) { $p = Join-Path $dir "$b ($i)$e"; if (-not (Test-Path -LiteralPath $p)) { return $p } }
}

function Folder-Size($path) {
  $s = (Get-ChildItem -LiteralPath $path -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum
  if ($s) { return [long]$s } else { return [long]0 }
}

# .NET вместо Move-Item: имена с [ ] Move-Item понимает как шаблон
function Move-Path($from, $to) {
  if (Test-Path -LiteralPath $from -PathType Container) { [IO.Directory]::Move($from, $to) } else { [IO.File]::Move($from, $to) }
}

# Облачные файлы OneDrive (ещё не скачаны): хеш их скачал бы — не трогаем при поиске копий
function Is-Cloud($f) { return (([int]$f.Attributes -band 0x441000) -ne 0) }

function Make-Plan {
  $plan = New-Object System.Collections.ArrayList
  $sources = @($Desk, (Get-Downloads)) | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -Unique
  $old30 = (Get-Date).AddDays(-30)
  $keep = New-Object System.Collections.ArrayList
  foreach ($src in $sources) {
    $isDown = ($src -ne $Desk)
    $files = Get-ChildItem -LiteralPath $src -File -Force -ErrorAction SilentlyContinue | Where-Object {
      (([int]$_.Attributes -band 6) -eq 0) -and ($_.Extension -notin '.lnk', '.url') -and
      ($_.FullName -ne $Self) -and ($_.FullName -ne $Button)
    }
    foreach ($f in $files) {
      $ext = $f.Extension.ToLower()
      $why = $null
      if (Is-Cloud $f) { }
      elseif ($ext -in $TempExt) { $why = 'недокачанное и временное' }
      elseif ($f.Length -eq 0) { $why = 'пустые файлы (0 байт)' }
      elseif ($isDown -and ($ext -in $Installers) -and $f.LastWriteTime -lt $old30) { $why = 'старые установщики (больше 30 дней)' }
      if ($why) { [void]$plan.Add([pscustomobject]@{ Kind = 'junk'; File = $f; Src = $src; Cat = $why; Size = $f.Length; Tag = (Split-Path $src -Leaf) }) }
      else { [void]$keep.Add([pscustomobject]@{ File = $f; Src = $src }) }
    }
  }
  # Точные копии (одинаковый размер и SHA-256): оставляем файл без «(1)» в имени, потом самый старый
  $dups = @{}
  $groups = $keep | Where-Object { $_.File.Length -gt 0 -and $_.File.Length -le 1GB -and -not (Is-Cloud $_.File) } |
    Group-Object { $_.File.Length } | Where-Object { $_.Count -gt 1 }
  foreach ($g in $groups) {
    $byHash = $g.Group | Group-Object { try { (Get-FileHash -LiteralPath $_.File.FullName -Algorithm SHA256).Hash } catch { [guid]::NewGuid() } } | Where-Object { $_.Count -gt 1 }
    foreach ($h in $byHash) {
      $sorted = $h.Group | Sort-Object { if ($_.File.BaseName -match '\(\d+\)$|копия|copy') { 1 } else { 0 } }, { $_.File.LastWriteTime }, { $_.File.Name.Length }
      $sorted | Select-Object -Skip 1 | ForEach-Object { $dups[$_.File.FullName] = $true }
    }
  }
  foreach ($k in $keep) {
    $f = $k.File
    if ($dups.ContainsKey($f.FullName)) {
      [void]$plan.Add([pscustomobject]@{ Kind = 'junk'; File = $f; Src = $k.Src; Cat = 'одинаковые копии'; Size = $f.Length; Tag = (Split-Path $k.Src -Leaf) })
      continue
    }
    $cat = $ExtCat[$f.Extension.ToLower()]; if (-not $cat) { $cat = 'Прочее' }
    [void]$plan.Add([pscustomobject]@{ Kind = 'move'; File = $f; Src = $k.Src; Cat = $cat; Size = $f.Length; Tag = '' })
  }
  # Временные файлы Windows старше 7 дней (только на том же диске — перенос мгновенный)
  $tmp = [IO.Path]::GetTempPath()
  if ([IO.Path]::GetPathRoot($tmp) -eq [IO.Path]::GetPathRoot($State)) {
    $old7 = (Get-Date).AddDays(-7)
    Get-ChildItem -LiteralPath $tmp -Force -ErrorAction SilentlyContinue | Where-Object { $_.LastWriteTime -lt $old7 } | ForEach-Object {
      $sz = if ($_.PSIsContainer) { Folder-Size $_.FullName } else { $_.Length }
      [void]$plan.Add([pscustomobject]@{ Kind = 'junk'; File = $_; Src = $tmp; Cat = 'временные файлы Windows (старше 7 дней)'; Size = $sz; Tag = 'Temp' })
    }
  }
  return $plan
}

function Show-Plan($plan) {
  $moves = @($plan | Where-Object Kind -eq 'move')
  $junk = @($plan | Where-Object Kind -eq 'junk')
  Say ''
  Say "Разложу по папкам: $($moves.Count) файлов" 'Cyan'
  $moves | Group-Object Cat | Sort-Object Count -Descending | ForEach-Object { Say ("  {0,-12} {1,5}" -f $_.Name, $_.Count) }
  Say ''
  Say ("В карантин (шлак): {0} шт., {1}" -f $junk.Count, (Size (($junk | Measure-Object Size -Sum).Sum))) 'Yellow'
  $junk | Group-Object Cat | ForEach-Object {
    Say ("  {0}: {1} шт., {2}" -f $_.Name, $_.Count, (Size (($_.Group | Measure-Object Size -Sum).Sum)))
    $_.Group | Select-Object -First 4 | ForEach-Object { Say ("      {0}" -f $_.File.Name) 'DarkGray' }
  }
  Say ''
}

function Open-Journal($stamp) {
  if (-not (Test-Path -LiteralPath $State)) { [void](New-Item -ItemType Directory -Path $State) }
  $w = New-Object IO.StreamWriter((Join-Path $State "journal-$stamp.tsv"), $true, (New-Object Text.UTF8Encoding $false))
  $w.AutoFlush = $true
  return $w
}

# Папку создаём по уровням и каждый записываем — откат уберёт их, если они останутся пустыми
function Ensure-Dir($dir, $w) {
  if (Test-Path -LiteralPath $dir) { return }
  Ensure-Dir (Split-Path $dir -Parent) $w
  [void](New-Item -ItemType Directory -Path $dir)
  $w.WriteLine("mkdir`t`t$dir")
}

function Run-Sort {
  Say 'Смотрю Рабочий стол, Загрузки и временные файлы…' 'Cyan'
  $plan = Make-Plan
  if ($plan.Count -eq 0) { Say 'Разбирать нечего — всё чисто.' 'Green'; return }
  Show-Plan $plan
  if (-not (Yes 'Разобрать? (да / нет)')) { Say 'Отменено, ничего не тронуто.'; return }
  $stamp = Get-Date -Format 'yyyy-MM-dd_HH-mm-ss'
  $w = Open-Journal $stamp
  $ok = 0; $skip = 0; $freed = [long]0
  try {
    foreach ($p in $plan) {
      $from = $p.File.FullName
      if ($p.Kind -eq 'move') { $dir = Join-Path $p.Src $p.Cat }
      else { $dir = Join-Path (Join-Path $Junk $stamp) $p.Tag }
      try {
        Ensure-Dir $dir $w
        $to = Free-Name $dir $p.File.Name
        Move-Path $from $to
        $w.WriteLine("$($p.Kind)`t$from`t$to")
        $ok++
        if ($p.Kind -eq 'junk') { $freed += $p.Size }
      } catch { $skip++ }
    }
  } finally { $w.Close() }
  if ($Self -and (Test-Path -LiteralPath $Self) -and ($Self -ne (Join-Path $State 'razbor.bat'))) {
    Copy-Item -LiteralPath $Self -Destination (Join-Path $State 'razbor.bat') -Force
  }
  [IO.File]::WriteAllText($Button, "@echo off`r`ncall `"%USERPROFILE%\Razbor\razbor.bat`" undo`r`n", [Text.Encoding]::ASCII)
  Say ''
  Say "Готово: перенесено $ok" 'Green'
  if ($skip) { Say "Пропущено $skip (файл открыт в программе или нет доступа) — они остались на месте." 'Yellow' }
  Say "Шлак ($(Size $freed)) лежит в $Junk — место освободится после пункта 3 «Удалить шлак насовсем»."
  Say 'Вернуть всё как было — двойной щелчок по «Вернуть как было» на Рабочем столе.' 'Cyan'
}

function Active-Journals {
  if (-not (Test-Path -LiteralPath $State)) { return @() }
  return @(Get-ChildItem -LiteralPath $State -Filter 'journal-*.tsv' | Where-Object { $_.Name -notlike '*.undone.tsv' } | Sort-Object Name)
}

function Run-Undo {
  $js = Active-Journals
  if ($js.Count -eq 0) { Say 'Возвращать нечего.' 'Green'; if (Test-Path -LiteralPath $Button) { Remove-Item -LiteralPath $Button }; return }
  $j = $js[-1]
  Say "Возвращаю разбор $($j.BaseName.Substring(8))…" 'Cyan'
  $lines = @(Get-Content -LiteralPath $j.FullName -Encoding UTF8)
  [array]::Reverse($lines)
  $ok = 0; $fail = 0; $gone = 0
  foreach ($l in $lines) {
    $kind, $from, $to = $l.Split("`t")
    if (-not $to) { continue }
    if ($kind -eq 'mkdir') {
      if ((Test-Path -LiteralPath $to) -and -not (Get-ChildItem -LiteralPath $to -Force | Select-Object -First 1)) {
        try { Remove-Item -LiteralPath $to } catch {}
      }
      continue
    }
    if (-not (Test-Path -LiteralPath $to)) { $gone++; continue }
    try {
      $dest = $from
      if (Test-Path -LiteralPath $from) { $dest = Free-Name (Split-Path $from -Parent) (Split-Path $from -Leaf) }
      $parent = Split-Path $dest -Parent
      if (-not (Test-Path -LiteralPath $parent)) { [void](New-Item -ItemType Directory -Path $parent) }
      Move-Path $to $dest
      $ok++
    } catch { $fail++ }
  }
  Say "Вернул на место: $ok" 'Green'
  if ($gone) { Say "Не нашёл $gone (удалены пунктом 3 или перенесены вручную)." 'Yellow' }
  if ($fail) {
    Say "Не смог вернуть $fail (файл открыт?) — закрой программы и нажми «Вернуть как было» ещё раз." 'Yellow'
    return
  }
  Rename-Item -LiteralPath $j.FullName -NewName ($j.BaseName + '.undone.tsv')
  if ((Active-Journals).Count -eq 0) { Remove-Item -LiteralPath $Button -ErrorAction SilentlyContinue; Say 'Всё как было.' 'Green' }
  else { Say 'Остались более ранние разборы — нажми «Вернуть как было» ещё раз, чтобы вернуть и их.' }
}

function Run-Purge {
  if (-not (Test-Path -LiteralPath $Junk)) { Say 'Карантин пуст.' 'Green'; return }
  $sz = Folder-Size $Junk
  Say "В карантине $(Size $sz): $Junk" 'Yellow'
  Say 'После удаления шлак не вернуть (разложенные по папкам файлы вернуть можно).' 'Yellow'
  if (-not (Yes 'Удалить насовсем? (да / нет)')) { Say 'Отменено.'; return }
  Remove-Item -LiteralPath $Junk -Recurse -Force -ErrorAction SilentlyContinue
  if (Test-Path -LiteralPath $Junk) { Say "Часть не удалилась (занято) — освобождено $(Size ($sz - (Folder-Size $Junk)))." 'Yellow' }
  else { Say "Удалено, освобождено $(Size $sz)." 'Green' }
}

function Main {
  $mode = "$env:RAZBOR_MODE".ToLower()
  if (-not $mode) {
    Say 'Разбор файлов' 'Cyan'
    Say '  1 — Разобрать Рабочий стол и Загрузки'
    Say '  2 — Вернуть как было'
    Say '  3 — Удалить шлак насовсем'
    $a = (Read-Host 'Номер').Trim()
    $mode = @{ '1' = 'sort'; '2' = 'undo'; '3' = 'purge' }[$a]
  }
  switch ($mode) {
    'plan'  { $p = Make-Plan; if ($p) { Show-Plan $p } else { Say 'Разбирать нечего.' } }
    'sort'  { Run-Sort }
    'undo'  { Run-Undo }
    'purge' { Run-Purge }
    default { Say 'Нет такого пункта.' }
  }
}

try { Main } catch { Say "⚠️ Ошибка: $($_.Exception.Message)" 'Red' }
