param([switch]$Offline)
$ErrorActionPreference = 'Stop'
try {
Set-Location -LiteralPath $PSScriptRoot
$env:DOTNET_CLI_HOME = Join-Path $PSScriptRoot '.dotnet'
$env:DOTNET_CLI_TELEMETRY_OPTOUT = '1'
$env:DOTNET_GENERATE_ASPNET_CERTIFICATE = 'false'
$executable = Join-Path $PSScriptRoot 'dist\desktop\Playtime.exe'
if (-not (Test-Path -LiteralPath $executable)) {
    if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) {
        throw '.NET 10 SDK is required to build the desktop app from source.'
    }
    & dotnet publish (Join-Path $PSScriptRoot 'desktop\Playtime.Desktop.csproj') -c Release --no-self-contained -o (Join-Path $PSScriptRoot 'dist\desktop')
    if ($LASTEXITCODE -ne 0) { throw 'Desktop build failed.' }
}
$desktopArguments = @('--project', ('"' + $PSScriptRoot + '"'))
if ($Offline) { $desktopArguments += '--offline' }
Start-Process -FilePath $executable -ArgumentList $desktopArguments -WorkingDirectory $PSScriptRoot
} catch {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, 'Playtime desktop startup') | Out-Null
    exit 1
}
