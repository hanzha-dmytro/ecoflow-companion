# Builds dist\EcoFlowCompanion\ (EcoFlowCompanion.exe + ecoflow-diag.exe).
# Usage: powershell -ExecutionPolicy Bypass -File packaging\build.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

$uv = if (Get-Command uv -ErrorAction SilentlyContinue) { @("uv") } else { @("python", "-m", "uv") }
& $uv[0] $uv[1..($uv.Length - 1)] sync --group build
if ($LASTEXITCODE -ne 0) { throw "uv sync failed" }

.venv\Scripts\python.exe packaging\make_icon.py build\EcoFlowCompanion.ico
if ($LASTEXITCODE -ne 0) { throw "icon generation failed" }

.venv\Scripts\pyinstaller.exe packaging\EcoFlowCompanion.spec --noconfirm --clean `
    --distpath dist --workpath build\pyinstaller
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

# Also next to the executables, where people look for it (a copy stays in _internal)
Copy-Item LICENSES.md dist\EcoFlowCompanion\LICENSES.md

Write-Host "Built dist\EcoFlowCompanion\EcoFlowCompanion.exe"
