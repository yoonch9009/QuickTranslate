$ErrorActionPreference = "Stop"

$projectRoot = $PSScriptRoot
$python = Join-Path $projectRoot ".venv314\Scripts\python.exe"
$deploy = Join-Path $projectRoot ".venv314\Lib\site-packages\PySide6\scripts\deploy.py"
$generatedExe = Join-Path $projectRoot "deployment\QuickTranslate.exe"
$releaseDirectory = Join-Path $projectRoot "release"
$releaseExe = Join-Path $releaseDirectory "QuickTranslate.exe"
$specPath = Join-Path $projectRoot "pysidedeploy.spec"
$specContent = [IO.File]::ReadAllText($specPath)

if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $deploy)) {
    throw "Python 3.14 build environment is missing: .venv314"
}

Push-Location $projectRoot
try {
    & $python -m ruff check .
    if ($LASTEXITCODE -ne 0) { throw "Ruff failed" }
    & $python -m pytest
    if ($LASTEXITCODE -ne 0) { throw "Tests failed" }
    $expectedVersion = & $python -c 'from quicktranslate import __version__; print(__version__)'

    # pyside6-deploy 6.11.2 does not create a nested exec_directory before
    # its final copy, so create it explicitly.
    New-Item -ItemType Directory -Path $releaseDirectory -Force | Out-Null
    & $python $deploy -c (Join-Path $projectRoot "pysidedeploy.spec") --force
    if ($LASTEXITCODE -ne 0) { throw "EXE build failed" }

    if (-not (Test-Path -LiteralPath $releaseExe)) {
        if (-not (Test-Path -LiteralPath $generatedExe)) {
            throw "Deployment completed without producing QuickTranslate.exe"
        }
        Copy-Item -LiteralPath $generatedExe -Destination $releaseExe -Force
    }

    if ((Get-Item -LiteralPath $releaseExe).VersionInfo.FileVersion -ne "$expectedVersion.0") {
        throw "Built EXE version does not match source version $expectedVersion"
    }

    Write-Host "Built: $releaseExe"
}
finally {
    # Deployment writes a machine-specific Python path; keep the public config portable.
    [IO.File]::WriteAllText($specPath, $specContent)
    Pop-Location
}
