# StudyProof — Windows release build script (Part M).
#
# Usage (from an elevated PowerShell prompt on Windows):
#   powershell -ExecutionPolicy Bypass -File .\run_build.ps1
#
# Output: dist\StudyProof\StudyProof.exe  (one-folder distribution —
# copy the whole dist\StudyProof folder to distribute).

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
# Keep test/build scratch files inside the checkout; do not touch real app data.
New-Item -ItemType Directory -Force -Path '_tmp' | Out-Null
$env:TEMP = Join-Path $PSScriptRoot '_tmp'
$env:TMP = $env:TEMP
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYINSTALLER_CONFIG_DIR = Join-Path $PSScriptRoot '_tmp\pyinstaller-cache'

Write-Host '== StudyProof build ==' -ForegroundColor Cyan

# 1. Virtual environment + pinned dependencies
if (-not (Test-Path .venv)) {
    Write-Host 'Creating virtual environment...'
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment.' }
}
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'Could not upgrade pip.' }
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Could not install dependencies.' }

# 2. Test gate — never ship a red build
Write-Host 'Running test suite...'
& .\.venv\Scripts\python.exe -m pytest tests -q --basetemp=_tmp/build-tests
if ($LASTEXITCODE -ne 0) { throw 'Tests failed — aborting build.' }

# 3. Performance validation (Part L budgets)
Write-Host 'Running performance validation...'
& .\.venv\Scripts\python.exe scripts\perf_check.py
if ($LASTEXITCODE -ne 0) { throw 'Performance budget exceeded — aborting build.' }

Write-Host 'Running complete GUI smoke test...'
& .\.venv\Scripts\python.exe -u tests\smoke_gui.py
if ($LASTEXITCODE -ne 0) { throw 'GUI smoke test failed — aborting build.' }

# 4. PyInstaller one-folder windowed build
Write-Host 'Building executable (PyInstaller)...'
& .\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean StudyProof.spec
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }

# 5. Sanity check output layout
$exe = 'dist\StudyProof\StudyProof.exe'
$assets = 'dist\StudyProof\_internal\assets'
if (-not (Test-Path $exe)) { throw "Missing $exe" }
if (-not ((Test-Path $assets) -or (Test-Path 'dist\StudyProof\assets'))) {
    throw 'Bundled assets folder not found in build output.'
}

# Run the actual frozen application against disposable data and verify its report.
Write-Host 'Verifying the frozen executable...'
$reportPath = Join-Path $PSScriptRoot '_tmp\frozen-verification.json'
if (Test-Path -LiteralPath $reportPath) { Remove-Item -LiteralPath $reportPath }
$releaseProcess = Start-Process -FilePath (Join-Path $PSScriptRoot $exe) `
    -ArgumentList @('--verify-release', ('"' + $reportPath + '"')) `
    -WindowStyle Hidden -PassThru
if (-not $releaseProcess.WaitForExit(60000)) {
    $releaseProcess.Kill()
    throw 'Frozen executable verification timed out.'
}
if (-not (Test-Path -LiteralPath $reportPath)) {
    throw 'Frozen executable did not write its verification report.'
}
$releaseReport = Get-Content -LiteralPath $reportPath -Raw | ConvertFrom-Json
if ($releaseProcess.ExitCode -ne 0 -or -not $releaseReport.ok -or -not $releaseReport.frozen) {
    throw ('Frozen executable verification failed: ' + $releaseReport.error)
}
Write-Host ('Frozen checks passed: ' + $releaseReport.checks.Count) -ForegroundColor Green

Write-Host "OK -> $exe" -ForegroundColor Green
Write-Host 'Distribute the entire dist\StudyProof folder.'
