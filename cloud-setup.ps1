[CmdletBinding()]
param(
    [string]$ProjectId,
    [string]$ProjectNumber,
    [string]$Institution,
    [string]$ModelBundle,
    [switch]$PlanOnly
)
$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    $python = Get-Command py.exe -ErrorAction SilentlyContinue
    $prefix = @('-3.11')
    if (-not $python) { $python = Get-Command python.exe -ErrorAction SilentlyContinue; $prefix = @() }
    if (-not $python) { throw 'Install 64-bit Python 3.11 and Google Cloud CLI, then reopen PowerShell.' }
    $runArgs = $prefix + @('-m','src.cloud_setup.wizard')
    if ($ProjectId) { $runArgs += @('--project-id',$ProjectId) }
    if ($ProjectNumber) { $runArgs += @('--project-number',$ProjectNumber) }
    if ($Institution) { $runArgs += @('--institution',$Institution) }
    if ($ModelBundle) { $runArgs += @('--model-bundle',$ModelBundle) }
    if ($PlanOnly) { $runArgs += '--plan-only' }
    & $python.Source @runArgs
    if ($LASTEXITCODE -ne 0) { throw 'Cloud setup did not complete. Follow the ERROR above; retain .cloud state and rerun with the same project ID.' }
} finally { Pop-Location }
