param([switch]$Offline, [switch]$Rebuild)
$ErrorActionPreference = 'Stop'
try {
Set-Location -LiteralPath $PSScriptRoot
$env:DOTNET_CLI_HOME = Join-Path $PSScriptRoot '.dotnet'
$env:DOTNET_CLI_TELEMETRY_OPTOUT = '1'
$env:DOTNET_GENERATE_ASPNET_CERTIFICATE = 'false'
$executable = Join-Path $PSScriptRoot 'dist\desktop\Playtime.exe'
$buildDirectory = Join-Path $PSScriptRoot 'dist\desktop'
$latestBuild = Get-ChildItem -LiteralPath $buildDirectory -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '^build-\d+$' -and (Test-Path -LiteralPath (Join-Path $_.FullName 'Playtime.exe')) } | Sort-Object Name -Descending | Select-Object -First 1
if ($latestBuild) { $executable = Join-Path $latestBuild.FullName 'Playtime.exe' }
if ($Rebuild -or -not (Test-Path -LiteralPath $executable)) {
    if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) {
        throw '.NET 10 SDK is required to build the desktop app from source.'
    }
    $newBuild = Join-Path $buildDirectory ('build-' + (Get-Date -Format 'yyyyMMddHHmmssfff'))
    & dotnet publish (Join-Path $PSScriptRoot 'desktop\Playtime.Desktop.csproj') -c Release --no-self-contained -o $newBuild
    if ($LASTEXITCODE -ne 0) { throw 'Desktop build failed.' }
    $executable = Join-Path $newBuild 'Playtime.exe'
}
$desktopArguments = @('--project', ('"' + $PSScriptRoot + '"'))
if ($Offline) { $desktopArguments += '--offline' }
Start-Process -FilePath $executable -ArgumentList $desktopArguments -WorkingDirectory $PSScriptRoot
} catch {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, 'Playtime desktop startup') | Out-Null
    exit 1
}
