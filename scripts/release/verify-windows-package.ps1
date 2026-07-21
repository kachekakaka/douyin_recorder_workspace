param(
    [string]$SourceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [Parameter(Mandatory = $true)][string]$AssetDir,
    [string]$ExtractRoot = ""
)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$SourceRoot = (Resolve-Path $SourceRoot).Path
$AssetDir = (Resolve-Path $AssetDir).Path
$lock = Get-Content -Raw -Encoding UTF8 (Join-Path $SourceRoot "packaging\release-lock.json") | ConvertFrom-Json
$zip = Join-Path $AssetDir ([string]$lock.assets.windows_zip)
if (!(Test-Path -LiteralPath $zip -PathType Leaf)) { throw "Windows ZIP missing: $zip" }
$checksum = Join-Path $AssetDir "windows-asset-SHA256SUMS.txt"
$line = (Get-Content -LiteralPath $checksum | Select-Object -First 1)
$parts = $line -split '\s+', 2
if ($parts.Count -ne 2 -or $parts[1] -ne [IO.Path]::GetFileName($zip)) { throw "asset checksum row invalid" }
$actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $zip).Hash.ToLowerInvariant()
if ($actual -ne $parts[0].ToLowerInvariant()) { throw "Windows ZIP SHA-256 mismatch" }
if (!$ExtractRoot) { $ExtractRoot = Join-Path $env:RUNNER_TEMP "便携 包 验证 $([guid]::NewGuid().ToString('N'))" }
if (Test-Path -LiteralPath $ExtractRoot) { Remove-Item -LiteralPath $ExtractRoot -Recurse -Force }
New-Item -ItemType Directory -Force -Path $ExtractRoot | Out-Null
Expand-Archive -LiteralPath $zip -DestinationPath $ExtractRoot -Force
$verify = Join-Path $ExtractRoot "verify.bat"
if (!(Test-Path -LiteralPath $verify -PathType Leaf)) { throw "verify.bat missing after extraction" }
Push-Location $ExtractRoot
try {
    & cmd.exe /d /c "verify.bat"
    if ($LASTEXITCODE -ne 0) { throw "portable verify.bat failed with $LASTEXITCODE" }
} finally {
    Pop-Location
}
Write-Host "[通过] Clean extraction verified: $ExtractRoot"
