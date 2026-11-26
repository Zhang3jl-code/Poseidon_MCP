param([switch]$CheckLogin, [switch]$NonInteractive)
$ErrorActionPreference = 'Stop'
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$serverPath = Join-Path $PSScriptRoot 'server.py'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Missing .venv. See README.md for installation instructions.'
}
$previousUsername = $env:POSEIDON_USERNAME
$previousPassword = $env:POSEIDON_PASSWORD
try {
    $savedCredentialPath = Join-Path $PSScriptRoot 'credentials.local.json'
    if ((-not $env:POSEIDON_USERNAME -or -not $env:POSEIDON_PASSWORD) -and
        (Test-Path -LiteralPath $savedCredentialPath)) {
        $savedCredential = Get-Content -LiteralPath $savedCredentialPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($savedCredential.username -isnot [string] -or [string]::IsNullOrWhiteSpace($savedCredential.username) -or
            $savedCredential.password -isnot [string] -or $savedCredential.password.Length -eq 0) {
            throw 'credentials.local.json must contain non-empty username and password strings.'
        }
        $env:POSEIDON_USERNAME = $savedCredential.username
        $env:POSEIDON_PASSWORD = $savedCredential.password
    }
    if ($NonInteractive -and (-not $env:POSEIDON_USERNAME -or -not $env:POSEIDON_PASSWORD)) {
        throw 'Missing credentials. Run configure-credentials.ps1 locally first, or provide POSEIDON_USERNAME and POSEIDON_PASSWORD.'
    }
    if (-not $env:POSEIDON_USERNAME) {
        $env:POSEIDON_USERNAME = Read-Host 'Poseidon username'
    }
    if (-not $env:POSEIDON_PASSWORD) {
        $securePassword = Read-Host 'Poseidon password' -AsSecureString
        $credential = [PSCredential]::new($env:POSEIDON_USERNAME, $securePassword)
        $env:POSEIDON_PASSWORD = $credential.GetNetworkCredential().Password
    }
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    if ($CheckLogin) { & $pythonPath $serverPath --check-login }
    else { & $pythonPath $serverPath }
    $processExitCode = $LASTEXITCODE
}
finally {
    $env:POSEIDON_USERNAME = $previousUsername
    $env:POSEIDON_PASSWORD = $previousPassword
    $savedCredential = $null
    if ($credential) { $credential.Password.Dispose() }
    $credential = $null
    if ($securePassword) { $securePassword.Dispose() }
}
exit $processExitCode
