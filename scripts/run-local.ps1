$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root
$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) {
    Write-Host 'Préparation de l’environnement Python…' -ForegroundColor Cyan
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3.11 -m venv .venv
        if ($LASTEXITCODE -ne 0) { & py -m venv .venv }
    } else {
        & python -m venv .venv
    }
}
if (-not (Test-Path -LiteralPath '.env')) {
    $Bytes = New-Object byte[] 64
    $Generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    $Generator.GetBytes($Bytes)
    $Secret = [Convert]::ToBase64String($Bytes)
    @("DJANGO_SECRET_KEY=$Secret", 'DJANGO_DEBUG=1', 'DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost', 'DATABASE_URL=', 'PRIVATE_MEDIA_ROOT=private_media') | Set-Content -Encoding UTF8 '.env'
}
Write-Host 'Installation / mise à jour des dépendances Python…' -ForegroundColor Cyan
& $Python -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Dépendances indisponibles. Vérifie la connexion Internet pour la première installation.' }
& $Python manage.py migrate --noinput
if ($LASTEXITCODE -ne 0) { throw 'Migration de la base impossible.' }
Write-Host 'ComptaPièces fonctionne en local, sans dépendance à Internet une fois démarré.' -ForegroundColor Green
Write-Host 'Garde cette fenêtre ouverte. Ctrl+C arrête le logiciel.'
$BrowserJob = Start-Job -ScriptBlock {
    for ($Attempt = 0; $Attempt -lt 40; $Attempt++) {
        try {
            $Response = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/' -MaximumRedirection 5 -UseBasicParsing -TimeoutSec 2
            if ($Response.StatusCode -eq 200) { Start-Process 'http://127.0.0.1:8000'; break }
        } catch { Start-Sleep -Milliseconds 250 }
    }
}
try {
    & $Python manage.py runserver 127.0.0.1:8000 --noreload
} finally {
    Stop-Job $BrowserJob -ErrorAction SilentlyContinue
    Remove-Job $BrowserJob -Force -ErrorAction SilentlyContinue
}
