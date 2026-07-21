param(
    [Parameter(Mandatory = $true)][string]$PackageRoot,
    [Parameter(Mandatory = $true)][string]$WorkRoot
)
$ErrorActionPreference = "Stop"
$PackageRoot = (Resolve-Path $PackageRoot).Path
New-Item -ItemType Directory -Force -Path $WorkRoot | Out-Null
$python = Join-Path $PackageRoot "runtime\python\python.exe"
$ffmpeg = Join-Path $PackageRoot "runtime\ffmpeg\bin\ffmpeg.exe"
$ffprobe = Join-Path $PackageRoot "runtime\ffmpeg\bin\ffprobe.exe"
if (!(Test-Path -LiteralPath $python -PathType Leaf)) { throw "portable python missing" }
$port = 34000 + (Get-Random -Minimum 0 -Maximum 1000)
$env:DOUYIN_RECORDER_CONFIG_DIR = Join-Path $WorkRoot "config"
$env:DOUYIN_RECORDER_USERDATA_DIR = Join-Path $WorkRoot "userdata"
$env:DOUYIN_RECORDER_RECORDS_DIR = Join-Path $WorkRoot "records"
$env:DOUYIN_RECORDER_DATABASE_PATH = Join-Path $WorkRoot "userdata\health.db"
$env:DOUYIN_RECORDER_HOST = "127.0.0.1"
$env:DOUYIN_RECORDER_PORT = "$port"
$env:DOUYIN_RECORDER_FFMPEG = $ffmpeg
$env:DOUYIN_RECORDER_FFPROBE = $ffprobe
$stdout = Join-Path $WorkRoot "server.stdout.log"
$stderr = Join-Path $WorkRoot "server.stderr.log"
$process = Start-Process -FilePath $python -ArgumentList "-m", "app" -WorkingDirectory $PackageRoot -PassThru -NoNewWindow -RedirectStandardOutput $stdout -RedirectStandardError $stderr
try {
    $healthy = $false
    for ($index = 0; $index -lt 60; $index++) {
        if ($process.HasExited) { break }
        try {
            $response = Invoke-RestMethod -Uri "http://127.0.0.1:$port/healthz" -TimeoutSec 2
            if ($response.ok -eq $true -and $response.version -eq "0.1.0") {
                $healthy = $true
                break
            }
        } catch {
            Start-Sleep -Milliseconds 250
        }
    }
    if (!$healthy) {
        if (Test-Path $stdout) { Get-Content $stdout }
        if (Test-Path $stderr) { Get-Content $stderr }
        throw "portable loopback health smoke failed"
    }
    Write-Host "[通过] loopback health smoke"
} finally {
    if (!$process.HasExited) {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        $process.WaitForExit(5000) | Out-Null
    }
}
