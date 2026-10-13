param([ValidateSet('start','stop')][string]$Action = 'start', [string]$PgBin = 'C:\Program Files\PostgreSQL\18\bin')
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path $PSScriptRoot -Parent
$DataDir = Join-Path $ProjectRoot '.runtime\postgres'
$PasswordFile = Join-Path $ProjectRoot '.runtime\pg-password'
if ($Action -eq 'stop') {
    & "$PgBin\pg_ctl.exe" -D $DataDir stop -m fast
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL shutdown failed' }
    exit
}
if (-not (Test-Path -LiteralPath $PasswordFile)) { throw 'Run scripts/init_env.py first.' }
if (-not (Test-Path -LiteralPath (Join-Path $DataDir 'PG_VERSION'))) {
    & "$PgBin\initdb.exe" -D $DataDir -U recon_dev --encoding=UTF8 --auth=scram-sha-256 "--pwfile=$PasswordFile"
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL initialization failed' }
}
& "$PgBin\pg_ctl.exe" -D $DataDir status *> $null
if ($LASTEXITCODE -ne 0) {
    & "$PgBin\pg_ctl.exe" -D $DataDir -l (Join-Path $ProjectRoot '.runtime\postgres.log') -o '-p 55432 -h 127.0.0.1' start -w
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL startup failed' }
}
$env:PGPASSWORD = [System.IO.File]::ReadAllText($PasswordFile).Trim()
try {
    $Exists = & "$PgBin\psql.exe" -h 127.0.0.1 -p 55432 -U recon_dev -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = 'reconcile'"
    if ($LASTEXITCODE -ne 0) { throw 'Database check failed' }
    if ($Exists -ne '1') {
        & "$PgBin\createdb.exe" -h 127.0.0.1 -p 55432 -U recon_dev reconcile
        if ($LASTEXITCODE -ne 0) { throw 'Database creation failed' }
    }
} finally { Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue }
Write-Output 'Project PostgreSQL ready at 127.0.0.1:55432.'
