# Builds the BANTAY LiDAR desktop app:  powershell -ExecutionPolicy Bypass -File build_app.ps1
# Output: app\PROJECT BANTAY\PROJECT BANTAY.exe  (a folder app; keep the whole folder together)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Push-Location frontend
npm run build
Pop-Location

.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --windowed `
  --name "PROJECT BANTAY" --icon bantay.ico `
  --add-data "frontend/dist;frontend/dist" `
  --exclude-module pytest --exclude-module tests `
  --distpath app --workpath build `
  bantay_app.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
Write-Host "Built: $PSScriptRoot\app\PROJECT BANTAY\PROJECT BANTAY.exe"
