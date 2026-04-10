param(
    [int]$BackendPort = 8000,
    [switch]$DevReload
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$frontend = Join-Path $root "frontend"
$venvActivate = Join-Path $root ".venv\\Scripts\\Activate.ps1"
$venvPython = Join-Path $root ".venv\\Scripts\\python.exe"

if (-not (Test-Path $venvActivate)) {
    throw @"
Missing virtual environment: $venvActivate

Please run once:
  cd "$root"
  python -m venv .venv
  .\.venv\Scripts\Activate.ps1
  pip install -r requirements.txt
"@
}

if (-not (Test-Path $venvPython)) {
    throw "Missing virtual environment python executable: $venvPython"
}

$rootEscaped = $root.Replace("'", "''")
$frontendEscaped = $frontend.Replace("'", "''")
$venvActivateEscaped = $venvActivate.Replace("'", "''")
$venvPythonEscaped = $venvPython.Replace("'", "''")

$backendScript = @"
Set-Location -LiteralPath '$rootEscaped'
. '$venvActivateEscaped'
`$uvicornArgs = @(
    'app:app',
    '--port', '$BackendPort'
)
if ($DevReload) {
    `$uvicornArgs += @(
        '--reload',
        '--reload-exclude', 'workplace',
        '--reload-exclude', 'exports',
        '--reload-exclude', '.data',
        '--reload-exclude', '.aui-dashboard'
    )
}
& '$venvPythonEscaped' -m uvicorn @uvicornArgs
"@

$frontendScript = @"
Set-Location -LiteralPath '$frontendEscaped'
npm run dev
"@

$backendEncoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($backendScript))
$frontendEncoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($frontendScript))

Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-ExecutionPolicy", "Bypass",
    "-EncodedCommand", $backendEncoded
)

Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-ExecutionPolicy", "Bypass",
    "-EncodedCommand", $frontendEncoded
)

Write-Host "Backend and frontend are starting in two new PowerShell windows."
Write-Host "Backend: http://127.0.0.1:$BackendPort"
Write-Host "Frontend: http://localhost:3001"
if ($DevReload) {
    Write-Host "Mode: DevReload enabled"
} else {
    Write-Host "Mode: Stable (reload disabled)"
}
