$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path $PSScriptRoot -Parent
$AccountPath = Join-Path $ProjectRoot '.runtime\e2e-account.json'
& (Join-Path $ProjectRoot 'backend\.venv\Scripts\python.exe') (Join-Path $PSScriptRoot 'e2e_account.py') create
if ($LASTEXITCODE -ne 0) { throw 'Test provisioning failed' }
try {
    $TestIdentity = Get-Content -LiteralPath $AccountPath -Raw | ConvertFrom-Json
    $env:E2E_EMAIL = $TestIdentity.email
    $env:E2E_PASSWORD = $TestIdentity.password
    Push-Location (Join-Path $ProjectRoot 'frontend')
    try { npm.cmd test; $TestExit = $LASTEXITCODE } finally { Pop-Location }
} finally {
    Remove-Item Env:E2E_EMAIL,Env:E2E_PASSWORD -ErrorAction SilentlyContinue
    & (Join-Path $ProjectRoot 'backend\.venv\Scripts\python.exe') (Join-Path $PSScriptRoot 'e2e_account.py') remove
}
exit $TestExit
