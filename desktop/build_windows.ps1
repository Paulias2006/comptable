$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root

$Python = (Get-Command python -ErrorAction Stop).Source
$ISCC = Get-Command ISCC.exe -ErrorAction SilentlyContinue
if (-not $ISCC) {
    $Candidates = @(
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles(x86)\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    )
    $Compiler = $Candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
} else { $Compiler = $ISCC.Source }
if (-not $Compiler) { throw 'Inno Setup est requis. Installe JRSoftware.InnoSetup avec winget, puis relance ce script.' }

& $Python -m pip install -r desktop\requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw 'Installation des dépendances du client Windows impossible.' }
& $Python -m PyInstaller --noconfirm --clean --onefile --windowed --icon desktop\app.ico --name ComptaPieces desktop\launch_comptapieces.py
if ($LASTEXITCODE -ne 0) { throw 'Création de ComptaPieces.exe échouée.' }
& $Compiler /Qp desktop\ComptaPieces.iss
if ($LASTEXITCODE -ne 0) { throw "Creation de l'installeur Inno Setup echouee." }
Write-Host "Installeur prêt : $Root\dist\ComptaPieces-Setup-1.0.0.exe" -ForegroundColor Green
