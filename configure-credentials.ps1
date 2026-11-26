# Run manually. Saves credentials as plain JSON, as requested.
$ErrorActionPreference = 'Stop'
$username = Read-Host 'Poseidon username'
if ([string]::IsNullOrWhiteSpace($username)) { throw 'Username cannot be empty.' }
$password = Read-Host 'Poseidon password (saved as plain text locally)' -AsSecureString
if ($password.Length -eq 0) { $password.Dispose(); throw 'Password cannot be empty.' }
try {
    $credential = [PSCredential]::new($username, $password)
    $credentialPath = Join-Path $PSScriptRoot 'credentials.local.json'
    if (Test-Path -LiteralPath $credentialPath) {
        $answer = Read-Host 'Replace existing saved credentials? Type YES to replace'
        if ($answer -cne 'YES') { throw 'No credentials changed.' }
    }
    [PSCustomObject]@{
        username = $username
        password = $credential.GetNetworkCredential().Password
    } | ConvertTo-Json | Set-Content -LiteralPath $credentialPath -Encoding UTF8
    Write-Host 'Saved as plain text in credentials.local.json.'
    Write-Host 'Next: run start.ps1 -CheckLogin, then use codex-config.example.toml to connect.'
}
finally {
    $password.Dispose()
    $credential = $null
}
