param(
    [int]$Port = 8765,
    [switch]$Offline,
    [switch]$NoDesktop
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$launchers = @()
foreach ($commandName in @('py', 'python', 'python3')) {
    $candidate = Get-Command $commandName -ErrorAction SilentlyContinue
    if ($candidate -and $candidate.Source -notlike '*\WindowsApps\*') {
        $arguments = if ($commandName -eq 'py') { @('-3') } else { @() }
        $launchers += @{ Command = $candidate.Source; Arguments = $arguments }
    }
}

# Optional fallback for machines that already have Codex's bundled Python.
$bundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
if (Test-Path -LiteralPath $bundledPython) {
    $launchers += @{ Command = $bundledPython; Arguments = @() }
}

$selected = $null
foreach ($launcher in $launchers) {
    $prefixArguments = $launcher.Arguments
    & $launcher.Command @prefixArguments -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>$null
    if ($LASTEXITCODE -eq 0) { $selected = $launcher; break }
}
if (-not $selected) { throw 'Python 3.11+ is required. Install Python, then run this script again.' }

$appArguments = @((Join-Path $PSScriptRoot 'app.py'), '--port', [string]$Port)
if ($Offline) { $appArguments += '--no-sync' }
if ($NoDesktop) { $appArguments += '--no-desktop' }
$prefixArguments = $selected.Arguments
Write-Host "Local calendar: http://127.0.0.1:$Port"
& $selected.Command @prefixArguments @appArguments
exit $LASTEXITCODE
