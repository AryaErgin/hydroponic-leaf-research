[CmdletBinding()]
param(
    [string]$ModelBundle,
    [switch]$Demo,
    [ValidateSet('auto','cpu','cuda')][string]$Device = 'auto'
)
$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    $python = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($python) {
        $pythonArgs = @('-3.11')
    } else {
        $python = Get-Command python.exe -ErrorAction SilentlyContinue
        $pythonArgs = @()
    }
    if (-not $python) { throw 'Install 64-bit Python 3.11 from python.org with the Python launcher, then retry.' }
    $runArgs = $pythonArgs + @('-m','src.local_setup.install','--device',$Device)
    if ($ModelBundle) { $runArgs += @('--model-bundle', $ModelBundle) }
    if ($Demo) { $runArgs += '--demo' }
    & $python.Source @runArgs
    if ($LASTEXITCODE -ne 0) { throw 'Setup did not complete. Read the ERROR above, correct it, and retry; existing data was preserved.' }
} finally { Pop-Location }
