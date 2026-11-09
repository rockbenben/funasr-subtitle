param(
    [Parameter(Mandatory=$true)][string]$Zip,
    [ValidateRange(1, [long]::MaxValue)][long]$PartSize = 1900MB
)
$ErrorActionPreference = "Stop"
$Zip = (Resolve-Path -LiteralPath $Zip).Path
$OutDir = Split-Path -Parent $Zip

$zipSize = (Get-Item $Zip).Length
$mb = [math]::Round($zipSize / 1MB, 1)
Write-Host "    zip: $mb MB" -ForegroundColor Gray

# GitHub rejects release assets larger than 2GB, so the full/CUDA package must ship as
# volumes plus a merge script. This used to be a manual step; keeping it in the build
# makes releases reproducible and stops the documented `copy /b` step from referring to
# a merge script that nobody has.
if ($zipSize -gt $PartSize) {
    Write-Host "    splitting into $PartSize-byte volumes" -ForegroundColor Yellow
    $in = [System.IO.File]::OpenRead($Zip)
    try {
        $idx = 1
        $remaining = $in.Length
        $buf = New-Object byte[] (4MB)
        while ($remaining -gt 0) {
            $take = [long][math]::Min($PartSize, $remaining)
            $partPath = "{0}.{1:D3}" -f $Zip, $idx
            $out = [System.IO.File]::Create($partPath)
            try {
                $left = $take
                while ($left -gt 0) {
                    $want = [int][math]::Min([long]$buf.Length, $left)
                    $n = $in.Read($buf, 0, $want)
                    if ($n -le 0) { break }
                    $out.Write($buf, 0, $n)
                    $left -= $n
                }
            } finally { $out.Dispose() }
            Write-Host ("      part {0}: {1:N0} MB" -f $idx, ((Get-Item $partPath).Length / 1MB))
            $remaining -= $take
            $idx++
        }
        $parts = $idx - 1
    } finally { $in.Dispose() }
    Remove-Item -Force $Zip

    # Merge script ships as a release asset NEXT TO the volumes (users need it before
    # they can extract anything). ASCII-only on purpose, same rule as this .ps1.
    # Name it after the zip (funasr-subtitle-cuda-win-x64-merge.bat): release assets are
    # listed alphabetically, so keeping the same prefix puts the script next to the
    # .001/.002 volumes it has to sit next to, instead of hiding under "m".
    $zipName = Split-Path $Zip -Leaf
    $batName = [System.IO.Path]::GetFileNameWithoutExtension($zipName) + "-merge.bat"
    $bat = Join-Path $OutDir $batName
    $partNames = 1..$parts | ForEach-Object { '"{0}.{1:D3}"' -f $zipName, $_ }
    $mergeSources = $partNames -join '+'
    $deleteSources = $partNames -join ' '
    $batBody = @"
@echo off
REM Merge all $parts volumes back into the complete zip.
REM Runs in this folder; double-click is enough.
setlocal
cd /d "%~dp0"
copy /b $mergeSources "$zipName"
if errorlevel 1 (
    echo Merge failed.
    pause
    exit /b 1
)
del $deleteSources
echo Done: $zipName
pause
"@
    Set-Content -Path $bat -Encoding ASCII -Value $batBody
    Write-Host "    volumes: $parts + $batName" -ForegroundColor Green
} else {
    Write-Host "Done: $Zip ($mb MB)" -ForegroundColor Green
}
