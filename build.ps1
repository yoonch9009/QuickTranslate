$ErrorActionPreference = "Stop"

$projectRoot = $PSScriptRoot
$python = Join-Path $projectRoot ".venv314\Scripts\python.exe"
$deploy = Join-Path $projectRoot ".venv314\Scripts\pyside6-deploy.exe"
$generatedExe = Join-Path $projectRoot "deployment\QuickTranslate.exe"
$releaseDirectory = Join-Path $projectRoot "release"
$releaseExe = Join-Path $releaseDirectory "QuickTranslate.exe"

if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $deploy)) {
    throw "Python 3.14 build environment is missing: .venv314"
}

Push-Location $projectRoot
try {
    & $python -m ruff check .
    & $python -m pytest

    # pyside6-deploy 6.11.2 does not create a nested exec_directory before
    # its final copy, so create it explicitly.
    New-Item -ItemType Directory -Path $releaseDirectory -Force | Out-Null
    & $deploy -c (Join-Path $projectRoot "pysidedeploy.spec") --force

    if (-not (Test-Path -LiteralPath $releaseExe)) {
        if (-not (Test-Path -LiteralPath $generatedExe)) {
            throw "Deployment completed without producing QuickTranslate.exe"
        }
        Copy-Item -LiteralPath $generatedExe -Destination $releaseExe -Force
    }

    Write-Host "Built: $releaseExe"
}
finally {
    Pop-Location
}
