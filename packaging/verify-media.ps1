param(
    [Parameter(Mandatory=$true)][string]$ToolDir,
    [Parameter(Mandatory=$true)][string]$Sample
)
$ErrorActionPreference = "Stop"
$ffmpeg = Join-Path $ToolDir "ffmpeg.exe"
$ffprobe = Join-Path $ToolDir "ffprobe.exe"
foreach ($tool in @($ffmpeg, $ffprobe)) {
    if (-not (Test-Path -LiteralPath $tool -PathType Leaf)) { throw "Missing bundled tool: $tool" }
}

# Use absolute bundled paths, so a developer's PATH cannot hide a broken portable package.
$probe = & $ffprobe -v error -select_streams a:0 -show_entries stream=codec_type -of json $Sample
if ($LASTEXITCODE -ne 0) { throw "Bundled ffprobe failed (exit $LASTEXITCODE)" }
$info = ($probe -join "`n") | ConvertFrom-Json
if (-not ($info.streams | Where-Object { $_.codec_type -eq "audio" })) {
    throw "Bundled ffprobe found no audio in the smoke-test fixture"
}
$pcm = Join-Path ([IO.Path]::GetTempPath()) ("funasr-build-" + [guid]::NewGuid() + ".pcm")
try {
    & $ffmpeg -nostdin -v error -i $Sample -vn -ac 1 -ar 16000 -f f32le $pcm
    if ($LASTEXITCODE -ne 0) { throw "Bundled ffmpeg failed (exit $LASTEXITCODE)" }
    if (-not (Test-Path -LiteralPath $pcm) -or (Get-Item -LiteralPath $pcm).Length -eq 0) {
        throw "Bundled ffmpeg produced no audio"
    }
} finally {
    if (Test-Path -LiteralPath $pcm) { Remove-Item -LiteralPath $pcm -Force }
}
