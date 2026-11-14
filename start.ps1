[CmdletBinding()]
param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$profilePath = Join-Path $projectRoot '.runtime/runtime-profile.json'
if (-not (Test-Path -LiteralPath $profilePath)) { throw 'Run .\setup.ps1 first in this clone.' }
$profile = Get-Content -LiteralPath $profilePath -Raw | ConvertFrom-Json
if ($profile.version -ne 1) { throw 'Unsupported runtime profile; run setup.ps1 again.' }
$installationPath = Join-Path $projectRoot '.runtime/installation.json'
if (-not (Test-Path -LiteralPath $installationPath)) { throw 'Setup has not completed its diagnostics. Run setup.ps1 successfully before starting.' }
$installation = Get-Content -LiteralPath $installationPath -Raw | ConvertFrom-Json
$profileHash = (Get-FileHash -LiteralPath $profilePath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($installation.version -ne 1 -or $installation.profile_sha256 -ne $profileHash -or $installation.mode -ne $profile.mode -or $installation.device -ne $profile.device) {
    throw 'Runtime profile differs from the completed setup. Run setup.ps1 again before starting.'
}

# Check Windows listeners even for a WSL backend: forwarded localhost must not
# hide or replace an unrelated Windows application.
foreach ($localPort in @(9099,8896)) {
    $listener = Get-NetTCPConnection -State Listen -LocalPort $localPort -ErrorAction SilentlyContinue
    if ($listener) { throw "Port $localPort is already in use. Close its original application or preview terminal yourself, then retry. No process was stopped." }
}
Push-Location -LiteralPath $projectRoot
try {
    if ($profile.backend -eq 'windows') {
        if (-not (Test-Path -LiteralPath $profile.python)) { throw 'The local Python environment is missing. Run setup.ps1 again.' }
        $runArgs = @('-m', 'src.local_setup.runtime', 'start', '--profile', $profilePath)
        if ($NoBrowser) { $runArgs += '--no-browser' }
        & $profile.python @runArgs
    } elseif ($profile.backend -eq 'wsl') {
        $distribution = $profile.wsl_distribution
        if (-not $distribution) { throw 'WSL distribution is missing from runtime profile.' }
        $wslRoot = (& wsl.exe -d $distribution -- wslpath -a -u $projectRoot).Trim()
        if ($LASTEXITCODE -ne 0 -or -not $wslRoot.StartsWith('/')) { throw 'Could not locate this clone in WSL.' }
        $runArgs = @('-d', $distribution, '--cd', $wslRoot, '--', $profile.python, '-m', 'src.local_setup.runtime', 'start', '--profile', "$wslRoot/.runtime/runtime-profile.json")
        if ($NoBrowser) { $runArgs += '--no-browser' }
        & wsl.exe @runArgs
    } else { throw 'Unsupported runtime backend; run setup.ps1 again.' }
    if ($LASTEXITCODE -ne 0) { throw 'Local startup stopped with an error. Read the ERROR message above; run check.ps1.' }
} finally { Pop-Location }
