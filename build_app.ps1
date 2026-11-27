# Builds the PROJECT B.A.N.T.A.Y. Windows installer:  powershell -ExecutionPolicy Bypass -File build_app.ps1
# 1. frontend  2. Python server -> app\bantay-server  3. Electron + NSIS -> app\installer\PROJECT-BANTAY-Setup-<version>.exe
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Push-Location frontend
npm run build
if ($LASTEXITCODE -ne 0) { throw "frontend build failed" }
Pop-Location

# console exe: Electron hides its window and needs its stdin to signal shutdown
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean `
  --name "bantay-server" --icon bantay.ico `
  --add-data "frontend/dist;frontend/dist" `
  --add-data "dataset;dataset" `
  --exclude-module pytest --exclude-module tests --exclude-module webview `
  --distpath app --workpath build `
  bantay_server.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

Push-Location desktop
if (-not (Test-Path node_modules)) { npm install }
npm run dist
if ($LASTEXITCODE -ne 0) { throw "electron-builder failed" }
Pop-Location
Write-Host "Built: $(Get-ChildItem app\installer\*.exe | Select-Object -ExpandProperty FullName)"
