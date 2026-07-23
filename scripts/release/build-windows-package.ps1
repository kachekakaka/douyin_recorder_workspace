param(
    [string]$SourceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [Parameter(Mandatory = $true)][string]$OutputDir,
    [string]$SourceCommit = ""
)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$SourceRoot = (Resolve-Path $SourceRoot).Path
$OutputDir = [IO.Path]::GetFullPath($OutputDir)
$lockPath = Join-Path $SourceRoot "packaging\release-lock.json"
$lock = Get-Content -Raw -Encoding UTF8 $lockPath | ConvertFrom-Json
$version = [string]$lock.release_version
if (!$SourceCommit) {
    $SourceCommit = (git -C $SourceRoot rev-parse HEAD).Trim()
}
if ($SourceCommit -notmatch '^[0-9a-f]{40}$') { throw "invalid source commit" }
& python (Join-Path $SourceRoot "tools\release_package.py") validate-lock --root $SourceRoot --lock $lockPath
if ($LASTEXITCODE -ne 0) { throw "release lock validation failed" }

function Get-VerifiedFile {
    param([string]$Url, [string]$Destination, [string]$Sha256)
    for ($attempt = 1; $attempt -le 4; $attempt++) {
        try {
            Invoke-WebRequest -Uri $Url -OutFile $Destination -UseBasicParsing
            break
        } catch {
            if ($attempt -eq 4) { throw }
            Start-Sleep -Seconds (2 * $attempt)
        }
    }
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Destination).Hash.ToLowerInvariant()
    if ($actual -ne $Sha256.ToLowerInvariant()) {
        throw "SHA-256 mismatch for $Url`: $actual"
    }
}

function Copy-Tree {
    param([string]$Relative)
    $source = Join-Path $SourceRoot $Relative
    $target = Join-Path $stage $Relative
    if (!(Test-Path -LiteralPath $source)) { throw "missing source path: $Relative" }
    New-Item -ItemType Directory -Force -Path (Split-Path $target -Parent) | Out-Null
    Copy-Item -LiteralPath $source -Destination $target -Recurse -Force
}

if (Test-Path -LiteralPath $OutputDir) { Remove-Item -LiteralPath $OutputDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$work = Join-Path $OutputDir "work"
$stage = Join-Path $work "douyin-recorder-v$version-windows-x64"
$downloads = Join-Path $work "downloads"
New-Item -ItemType Directory -Force -Path $stage, $downloads | Out-Null

foreach ($path in @("app", "web")) { Copy-Tree $path }
New-Item -ItemType Directory -Force -Path (Join-Path $stage "config"), (Join-Path $stage "requirements") | Out-Null
Copy-Item -LiteralPath (Join-Path $SourceRoot "config\config.json.default") -Destination (Join-Path $stage "config\config.json.default")
Copy-Item -LiteralPath (Join-Path $SourceRoot "config\runtime.env.default") -Destination (Join-Path $stage "config\runtime.env.default")
Copy-Item -LiteralPath (Join-Path $SourceRoot "requirements\runtime.lock") -Destination (Join-Path $stage "requirements\runtime.lock")
foreach ($file in @("README.md", "THIRD_PARTY_NOTICES.md")) {
    Copy-Item -LiteralPath (Join-Path $SourceRoot $file) -Destination (Join-Path $stage $file)
}
New-Item -ItemType Directory -Force -Path (Join-Path $stage "tools"), (Join-Path $stage "scripts\release"), (Join-Path $stage "packaging") | Out-Null
foreach ($tool in @(
    "backup_runtime.py",
    "database_integrity_check.py",
    "database_maintenance.py",
    "diagnostics_report.py",
    "ffmpeg_supervisor_smoke.py",
    "postprocess_smoke.py",
    "recording_session_smoke.py",
    "release_package.py",
    "start_info.py"
)) {
    Copy-Item -LiteralPath (Join-Path $SourceRoot "tools\$tool") -Destination (Join-Path $stage "tools\$tool")
}
Copy-Item -LiteralPath (Join-Path $SourceRoot "scripts\release\health-smoke.ps1") -Destination (Join-Path $stage "scripts\release\health-smoke.ps1")
Copy-Item -LiteralPath (Join-Path $SourceRoot "packaging\release-lock.json") -Destination (Join-Path $stage "packaging\release-lock.json")
foreach ($entrypoint in @("start.bat", "verify.bat", "backup.bat", "diagnostics.bat", "maintenance.bat", "operations.bat")) {
    Copy-Item -LiteralPath (Join-Path $SourceRoot "packaging\windows\$entrypoint") -Destination (Join-Path $stage $entrypoint)
}

$pythonZip = Join-Path $downloads "python-embed.zip"
Get-VerifiedFile -Url ([string]$lock.python.url) -Destination $pythonZip -Sha256 ([string]$lock.python.sha256)
$pythonRoot = Join-Path $stage "runtime\python"
New-Item -ItemType Directory -Force -Path $pythonRoot | Out-Null
Expand-Archive -LiteralPath $pythonZip -DestinationPath $pythonRoot -Force
$pth = Get-ChildItem -LiteralPath $pythonRoot -Filter "python*._pth" | Select-Object -First 1
if (!$pth) { throw "Python embeddable _pth file missing" }
@("python313.zip", ".", "Lib\site-packages", "..\..", "import site") | Set-Content -Encoding ASCII -LiteralPath $pth.FullName
$sitePackages = Join-Path $pythonRoot "Lib\site-packages"
New-Item -ItemType Directory -Force -Path $sitePackages | Out-Null
python -m pip install --disable-pip-version-check --no-compile --only-binary=:all: --target $sitePackages -r (Join-Path $SourceRoot "requirements\runtime.lock")
if ($LASTEXITCODE -ne 0) { throw "runtime dependency installation failed" }

$assetName = [string]$lock.ffmpeg.asset
$assetSha = [string]$lock.ffmpeg.asset_sha256
if ($assetSha -notmatch '^[0-9a-f]{64}$') { throw "FFmpeg asset checksum is invalid" }
$ffmpegZip = Join-Path $downloads $assetName
Get-VerifiedFile -Url ([string]$lock.ffmpeg.asset_url) -Destination $ffmpegZip -Sha256 $assetSha
$ffmpegExtract = Join-Path $work "ffmpeg-extract"
Expand-Archive -LiteralPath $ffmpegZip -DestinationPath $ffmpegExtract -Force
$ffmpegExe = Get-ChildItem -LiteralPath $ffmpegExtract -Recurse -Filter ffmpeg.exe | Select-Object -First 1
$ffprobeExe = Get-ChildItem -LiteralPath $ffmpegExtract -Recurse -Filter ffprobe.exe | Select-Object -First 1
if (!$ffmpegExe -or !$ffprobeExe -or $ffmpegExe.DirectoryName -ne $ffprobeExe.DirectoryName) {
    throw "FFmpeg archive layout is invalid"
}
$ffmpegBin = Join-Path $stage "runtime\ffmpeg\bin"
New-Item -ItemType Directory -Force -Path $ffmpegBin | Out-Null
Copy-Item -Path (Join-Path $ffmpegExe.DirectoryName "*") -Destination $ffmpegBin -Recurse -Force

$licenseRoot = Join-Path $stage "licenses"
New-Item -ItemType Directory -Force -Path (Join-Path $licenseRoot "ffmpeg") | Out-Null
Copy-Item -LiteralPath (Join-Path $SourceRoot "packaging\licenses\Gyan-FFmpeg-Build-NOTICE.md") -Destination $licenseRoot
Copy-Item -LiteralPath (Join-Path $SourceRoot "packaging\licenses\FFmpeg-NOTICE.md") -Destination $licenseRoot
$licenseCandidates = Get-ChildItem -LiteralPath $ffmpegExtract -Recurse -File | Where-Object { $_.Name -match '^(LICENSE|COPYING|NOTICE|README)' }
if (!$licenseCandidates) { throw "FFmpeg archive contains no license/notice files" }
foreach ($item in $licenseCandidates) {
    $targetName = ($item.FullName.Substring($ffmpegExtract.Length).TrimStart('\') -replace '[\\/:*?"<>|]', '_')
    Copy-Item -LiteralPath $item.FullName -Destination (Join-Path $licenseRoot "ffmpeg\$targetName")
}

python (Join-Path $SourceRoot "tools\release_package.py") dependencies --site-packages $sitePackages --output (Join-Path $stage "python-dependencies.json") --license-root (Join-Path $licenseRoot "python")
if ($LASTEXITCODE -ne 0) { throw "dependency license manifest failed" }
Get-ChildItem -LiteralPath $stage -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force
Get-ChildItem -LiteralPath $stage -Recurse -File -Include *.pyc,*.pyo | Remove-Item -Force
python (Join-Path $SourceRoot "tools\release_package.py") manifest --package-root $stage --version $version --source-commit $SourceCommit --lock $lockPath
if ($LASTEXITCODE -ne 0) { throw "package manifest generation failed" }
python (Join-Path $SourceRoot "tools\release_package.py") verify --package-root $stage
if ($LASTEXITCODE -ne 0) { throw "package verification failed" }

$zipPath = Join-Path $OutputDir ([string]$lock.assets.windows_zip)
python (Join-Path $SourceRoot "tools\release_package.py") zip --package-root $stage --output $zipPath
if ($LASTEXITCODE -ne 0) { throw "package ZIP creation failed" }
Copy-Item -LiteralPath (Join-Path $stage "windows-manifest.json") -Destination (Join-Path $OutputDir "windows-manifest.json")
Copy-Item -LiteralPath (Join-Path $stage "windows-SHA256SUMS.txt") -Destination (Join-Path $OutputDir "windows-SHA256SUMS.txt")
Copy-Item -LiteralPath (Join-Path $stage "python-dependencies.json") -Destination (Join-Path $OutputDir "python-dependencies.json")
$zipSha = (Get-FileHash -Algorithm SHA256 -LiteralPath $zipPath).Hash.ToLowerInvariant()
"$zipSha  $([IO.Path]::GetFileName($zipPath))" | Set-Content -Encoding ASCII (Join-Path $OutputDir "windows-asset-SHA256SUMS.txt")
Remove-Item -LiteralPath $work -Recurse -Force
Write-Host "[通过] Windows package built: $zipPath"
