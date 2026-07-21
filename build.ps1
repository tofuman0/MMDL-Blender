[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string]$Version
)

$ErrorActionPreference = 'Stop'

$repositoryRoot = $PSScriptRoot
$addonName = 'sb_online_toolkit'
$addonDirectory = Join-Path $repositoryRoot $addonName
$initPath = Join-Path $addonDirectory '__init__.py'
$readmePath = Join-Path $addonDirectory 'README.txt'
$distDirectory = Join-Path $repositoryRoot 'dist'

if (-not (Test-Path -LiteralPath $initPath -PathType Leaf)) {
    throw "Missing Blender add-on entry point: $initPath"
}

$versionParts = $Version.Split('.') | ForEach-Object { [int]$_ }
$versionTuple = $versionParts -join ', '

$initText = Get-Content -Raw -LiteralPath $initPath
$versionPattern = '(?m)^(\s*"version"\s*:\s*)\(\d+\s*,\s*\d+\s*,\s*\d+\)(\s*,)'
if ([regex]::Matches($initText, $versionPattern).Count -ne 1) {
    throw 'Could not find exactly one bl_info version tuple in __init__.py'
}
$initText = [regex]::Replace(
    $initText,
    $versionPattern,
    { param($match) $match.Groups[1].Value + '(' + $versionTuple + ')' + $match.Groups[2].Value }
)
$utf8WithoutBom = [Text.UTF8Encoding]::new($false)
[IO.File]::WriteAllText($initPath, $initText, $utf8WithoutBom)

if (Test-Path -LiteralPath $readmePath -PathType Leaf) {
    $readmeText = Get-Content -Raw -LiteralPath $readmePath
    $readmePattern = '(?m)^(SB Online Toolkit[^\r\n]*? v)\d+\.\d+\.\d+(\s*)$'
    if ([regex]::Matches($readmeText, $readmePattern).Count -ne 1) {
        throw 'Could not find exactly one toolkit version heading in README.txt'
    }
    $readmeText = [regex]::Replace(
        $readmeText,
        $readmePattern,
        { param($match) $match.Groups[1].Value + $Version + $match.Groups[2].Value }
    )
    [IO.File]::WriteAllText($readmePath, $readmeText, $utf8WithoutBom)
}

New-Item -ItemType Directory -Path $distDirectory -Force | Out-Null
$zipPath = Join-Path $distDirectory "$addonName-$Version.zip"
if (Test-Path -LiteralPath $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}

$buildTempRoot = Join-Path ([IO.Path]::GetTempPath()) ('sbol-build-' + [guid]::NewGuid().ToString('N'))
$stagedAddon = Join-Path $buildTempRoot $addonName

try {
    New-Item -ItemType Directory -Path $buildTempRoot | Out-Null
    Copy-Item -LiteralPath $addonDirectory -Destination $stagedAddon -Recurse

    Get-ChildItem -LiteralPath $stagedAddon -Directory -Filter '__pycache__' -Recurse |
        Remove-Item -Recurse -Force
    Get-ChildItem -LiteralPath $stagedAddon -File -Filter '*.pyc' -Recurse |
        Remove-Item -Force

    Compress-Archive -LiteralPath $stagedAddon -DestinationPath $zipPath -CompressionLevel Optimal
}
finally {
    if (Test-Path -LiteralPath $buildTempRoot) {
        Remove-Item -LiteralPath $buildTempRoot -Recurse -Force
    }
}

Write-Host "Built $zipPath"
