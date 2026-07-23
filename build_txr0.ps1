param([string]$OutputDirectory = "dist")

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$source = Join-Path $projectRoot "txr0_toolkit"
$output = Join-Path $projectRoot $OutputDirectory
$zip = Join-Path $output "txr0_toolkit-0.7.2.zip"
$staging = Join-Path $output ".txr0_build"
$stagedAddon = Join-Path $staging "txr0_toolkit"
if (-not (Test-Path -LiteralPath $source)) { throw "Missing add-on source: $source" }
New-Item -ItemType Directory -Path $output -Force | Out-Null
if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip }
if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse }
New-Item -ItemType Directory -Path $stagedAddon -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $source "__init__.py") -Destination $stagedAddon
Copy-Item -LiteralPath (Join-Path $source "README.txt") -Destination $stagedAddon
Compress-Archive -Path $stagedAddon -DestinationPath $zip -CompressionLevel Optimal
Remove-Item -LiteralPath $staging -Recurse
Write-Host "Built $zip"
