param(
    [Parameter(Mandatory=$true)]
    [ValidateSet('T02','T03','T04','T05','T03-NEW')]
    [string]$Case,
    [switch]$NextStage,
    [switch]$Execute
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$env:PYTHONPATH = "$repoRoot\src;$repoRoot"
$env:PYTHONDONTWRITEBYTECODE = '1'
$pythonResolution = @(& python -c 'import sys; print(sys.executable)' 2>$null)
if ($LASTEXITCODE -ne 0 -or $pythonResolution.Count -ne 1) { exit 126 }
$pythonPath = [System.IO.Path]::GetFullPath($pythonResolution[0].Trim())
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) { exit 126 }

$outputRoot = Join-Path $repoRoot $(if ($NextStage) {
    'work\temporal_product_real_20261014_next_stage'
} else {
    'work\temporal_product_real_20261014_release_gate'
})
$arguments = @('-m', 'tests.smoke.run_temporal_product_http', $Case)
if ($NextStage) { $arguments += '--next-stage' }
if (-not $Execute) {
    & $pythonPath @arguments
    exit $LASTEXITCODE
}
$stdout = Join-Path $outputRoot "$Case-launcher.stdout.txt"
$stderr = Join-Path $outputRoot "$Case-launcher.stderr.txt"
if ((Test-Path -LiteralPath $stdout) -or (Test-Path -LiteralPath $stderr)) { exit 126 }
$arguments += '--execute'
$process = Start-Process -FilePath $pythonPath -ArgumentList $arguments `
    -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $stdout -RedirectStandardError $stderr
if (-not $process.WaitForExit(1900000)) {
    $taskkill = Join-Path $env:SystemRoot 'System32\taskkill.exe'
    & $taskkill /PID $process.Id /T /F | Out-Null
    if ($LASTEXITCODE -ne 0) { exit 125 }
    try { [void]$process.WaitForExit(10000) } catch {}
    exit 124
}
exit $process.ExitCode
