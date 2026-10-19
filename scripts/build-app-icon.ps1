$ErrorActionPreference = 'Stop'
# Render the bundled vector mark with WPF; no external packages or image service.
Add-Type -AssemblyName PresentationCore, WindowsBase
$projectDirectory = Split-Path -Parent $PSScriptRoot
$vectorPath = Join-Path $projectDirectory 'web\favicon.svg'
$assetDirectory = Join-Path $projectDirectory 'web\assets'
$document = New-Object System.Xml.XmlDocument
$document.XmlResolver = $null
$document.Load($vectorPath)
$group = $document.DocumentElement.FirstChild
if ($group.GetAttribute('transform') -ne 'rotate(-5 32 32)') { throw 'Unsupported icon transform.' }
$sizes = @(16, 20, 24, 32, 48, 64, 128, 256)
$frames = @()
foreach ($size in $sizes) {
    $visual = New-Object System.Windows.Media.DrawingVisual
    $context = $visual.RenderOpen()
    $context.PushTransform((New-Object System.Windows.Media.ScaleTransform ($size / 64.0), ($size / 64.0)))
    $context.PushTransform((New-Object System.Windows.Media.RotateTransform -5, 32, 32))
    foreach ($element in $group.ChildNodes) {
        if ($element.LocalName -ne 'rect') { throw 'Unsupported icon geometry.' }
        $numbers = @{}
        foreach ($attribute in @('x', 'y', 'width', 'height', 'rx')) {
            $numbers[$attribute] = [double]::Parse($element.GetAttribute($attribute), [Globalization.CultureInfo]::InvariantCulture)
        }
        $brush = [System.Windows.Media.BrushConverter]::new().ConvertFromInvariantString($element.GetAttribute('fill'))
        $opacity = if ($element.HasAttribute('opacity')) { [double]::Parse($element.GetAttribute('opacity'), [Globalization.CultureInfo]::InvariantCulture) } else { 1.0 }
        $context.PushOpacity($opacity)
        $rectangle = New-Object System.Windows.Rect $numbers.x, $numbers.y, $numbers.width, $numbers.height
        $context.DrawRoundedRectangle($brush, $null, $rectangle, $numbers.rx, $numbers.rx)
        $context.Pop()
    }
    $context.Pop(); $context.Pop(); $context.Close()
    $bitmap = New-Object System.Windows.Media.Imaging.RenderTargetBitmap $size, $size, 96, 96, ([System.Windows.Media.PixelFormats]::Pbgra32)
    $bitmap.Render($visual)
    $encoder = New-Object System.Windows.Media.Imaging.PngBitmapEncoder
    $encoder.Frames.Add([System.Windows.Media.Imaging.BitmapFrame]::Create($bitmap))
    $memory = New-Object System.IO.MemoryStream
    $encoder.Save($memory)
    $frames += ,$memory.ToArray()
    $memory.Dispose()
}
$iconPath = Join-Path $assetDirectory 'playtime.ico'
$stream = [System.IO.File]::Create($iconPath)
$writer = New-Object System.IO.BinaryWriter $stream
try {
    $writer.Write([uint16]0); $writer.Write([uint16]1); $writer.Write([uint16]$sizes.Count)
    $offset = 6 + 16 * $sizes.Count
    for ($index = 0; $index -lt $sizes.Count; $index++) {
        $dimension = if ($sizes[$index] -eq 256) { 0 } else { $sizes[$index] }
        $writer.Write([byte]$dimension); $writer.Write([byte]$dimension)
        $writer.Write([byte]0); $writer.Write([byte]0)
        $writer.Write([uint16]1); $writer.Write([uint16]32)
        $writer.Write([uint32]$frames[$index].Length); $writer.Write([uint32]$offset)
        $offset += $frames[$index].Length
    }
    foreach ($frame in $frames) { $writer.Write([byte[]]$frame) }
} finally { $writer.Dispose(); $stream.Dispose() }
[System.IO.File]::WriteAllBytes((Join-Path $assetDirectory 'playtime.png'), [byte[]]$frames[-1])
Write-Host "Generated: $iconPath ($($sizes -join ', ') px)"
