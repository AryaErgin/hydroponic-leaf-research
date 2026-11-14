[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$profilePath = Join-Path $PSScriptRoot '.runtime/runtime-profile.json'
if (-not (Test-Path -LiteralPath $profilePath)) { throw 'Run .\setup.ps1 first.' }
$profile = Get-Content -LiteralPath $profilePath -Raw | ConvertFrom-Json
Push-Location -LiteralPath $PSScriptRoot
try {
    if ($profile.backend -eq 'windows') {
        & $profile.python -m src.local_setup.diagnostics --profile $profilePath
    } elseif ($profile.backend -eq 'wsl') {
        $wslRoot = (& wsl.exe -d $profile.wsl_distribution -- wslpath -a -u $PSScriptRoot).Trim()
        & wsl.exe -d $profile.wsl_distribution --cd $wslRoot -- $profile.python -m src.local_setup.diagnostics --profile "$wslRoot/.runtime/runtime-profile.json"
    } else { throw 'Invalid runtime profile; run setup.ps1.' }
    if ($LASTEXITCODE -ne 0) { throw 'One or more checks failed. Follow the instructions above.' }
} finally { Pop-Location }
