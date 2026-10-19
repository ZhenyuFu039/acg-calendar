$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $PSScriptRoot
$desktopDirectory = [Environment]::GetFolderPath('DesktopDirectory')
$shortcutPath = Join-Path $desktopDirectory 'Playtime.lnk'
$shell = New-Object -ComObject WScript.Shell
if (Test-Path -LiteralPath $shortcutPath) {
    $existing = $shell.CreateShortcut($shortcutPath)
    if ($existing.TargetPath -ne (Join-Path $projectDirectory 'Playtime.cmd')) {
        throw 'A different Playtime shortcut already exists. Rename it before running this installer.'
    }
}
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = Join-Path $projectDirectory 'Playtime.cmd'
$shortcut.WorkingDirectory = $projectDirectory
$shortcut.WindowStyle = 7
$shortcut.Description = 'Playtime local game calendar and desktop widget'
$iconPath = Join-Path $projectDirectory 'dist\desktop\Playtime.exe'
if (Test-Path -LiteralPath $iconPath) { $shortcut.IconLocation = $iconPath + ',0' }
$shortcut.Save()
Write-Host "Created: $shortcutPath"
