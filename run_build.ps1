# Study Tracker Desktop — Windows release build script (Part M).
#
# Usage (from an elevated PowerShell prompt on Windows):
#   powershell -ExecutionPolicy Bypass -File .\run_build.ps1
#
# Output: dist\StudyTracker\StudyTracker.exe  (one-folder distribution —
# copy the whole dist\StudyTracker folder to distribute).

$ErrorActionPreference = 'Stop'

Write-Host '== Study Tracker build ==' -ForegroundColor Cyan

# 1. Virtual environment + pinned dependencies
if (-not (Test-Path .venv)) {
    Write-Host 'Creating virtual environment...'
    python -m venv .venv
}
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 2. Test gate — never ship a red build
Write-Host 'Running test suite...'
& .\.venv\Scripts\python.exe -m pytest tests -q
if ($LASTEXITCODE -ne 0) { throw 'Tests failed — aborting build.' }

# 3. Performance validation (Part L budgets)
Write-Host 'Running performance validation...'
& .\.venv\Scripts\python.exe scripts\perf_check.py
if ($LASTEXITCODE -ne 0) { throw 'Performance budget exceeded — aborting build.' }

# 4. PyInstaller one-folder windowed build
Write-Host 'Building executable (PyInstaller)...'
& .\.venv\Scripts\pyinstaller.exe --noconfirm --clean study_tracker.spec
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }

# 5. Sanity check output layout
$exe = 'dist\StudyTracker\StudyTracker.exe'
$assets = 'dist\StudyTracker\_internal\assets'
if (-not (Test-Path $exe)) { throw "Missing $exe" }
if (-not ((Test-Path $assets) -or (Test-Path 'dist\StudyTracker\assets'))) {
    throw 'Bundled assets folder not found in build output.'
}

Write-Host "OK -> $exe" -ForegroundColor Green
Write-Host 'Distribute the entire dist\StudyTracker folder.'
