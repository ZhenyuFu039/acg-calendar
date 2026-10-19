using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;

namespace Playtime;

public sealed partial class CalendarWindow
{
    [StructLayout(LayoutKind.Sequential)]
    struct ScreenPoint { public int X, Y; }
    [DllImport("user32.dll")]
    static extern IntPtr WindowFromPoint(ScreenPoint point);

    RenderTargetBitmap CaptureRender()
    {
        UpdateLayout(); var visual = (FrameworkElement)Content;
        var bitmap = new RenderTargetBitmap((int)Math.Ceiling(visual.ActualWidth), (int)Math.Ceiling(visual.ActualHeight), 96, 96, PixelFormats.Pbgra32);
        bitmap.Render(visual); return bitmap;
    }
    static byte Alpha(RenderTargetBitmap image, Point point)
    {
        var pixel = new byte[4]; image.CopyPixels(new Int32Rect((int)point.X, (int)point.Y, 1, 1), pixel, 4, 0); return pixel[3];
    }
    async Task<bool> AppearanceSmokeTest()
    {
        // Exercise the real controls and inspect rendered alpha, rather than window opacity.
        ShowMode(true); OpenSidebar(false); SaveRender("sidebar.png");
        OpenSidebar(true); bubbleToggle.IsChecked = true; bubbleToggle.RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
        opacitySlider.Value = 65;
        if (!bubbleMode || Math.Abs(cardOpacity - .65) > .001) throw new Exception("Appearance controls did not apply");
        SaveRender("bubble-settings.png");
        collapseSidebar.RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
        showAll = false; selected = null; Render();
        // Prior game switches can retain a scroll offset; sample a visible card.
        cardScroll.ScrollToTop();
        await Dispatcher.InvokeAsync(() => { }, DispatcherPriority.Render);
        var capture = CaptureRender();
        if (Alpha(capture, new Point(0, 0)) != 0) throw new Exception("Bubble mode has an opaque window background");
        var firstCard = (Border)cards.Children[0];
        Point blank = firstCard.TranslatePoint(new Point(70, 4), (UIElement)Content);
        if (Math.Abs(Alpha(capture, blank) - 166) > 2) throw new Exception("Bubble background alpha does not match 65 percent");
        Point gap = firstCard.TranslatePoint(new Point(70, firstCard.ActualHeight + 2), (UIElement)Content);
        if (Alpha(capture, gap) != 0) throw new Exception("Space between bubbles is not fully transparent");
        if (sidebar.Visibility != Visibility.Collapsed || heading.Visibility != Visibility.Collapsed || footer.Visibility != Visibility.Collapsed)
            throw new Exception("Bubble mode still shows window controls");
        // Read-only native hit testing: transparent pixels must allow the desktop behind through.
        bool wasTopmost = Topmost; Topmost = true; Activate();
        await Task.Delay(180);
        IntPtr ownWindow = new WindowInteropHelper(this).Handle;
        Point cardScreen = PointToScreen(blank), gapScreen = PointToScreen(gap);
        if (WindowFromPoint(new ScreenPoint { X = (int)cardScreen.X, Y = (int)cardScreen.Y }) != ownWindow)
            throw new Exception("Activity bubbles are not interactive");
        if (WindowFromPoint(new ScreenPoint { X = (int)gapScreen.X, Y = (int)gapScreen.Y }) == ownWindow)
            throw new Exception("Transparent gaps intercept desktop input");
        Topmost = wasTopmost; SavePreferences();
        using (var saved = JsonDocument.Parse(File.ReadAllText(preferences)))
        {
            if (!saved.RootElement.GetProperty("bubble_mode").GetBoolean() || Math.Abs(saved.RootElement.GetProperty("card_opacity").GetDouble() - .65) > .001)
                throw new Exception("Bubble settings were not saved");
        }
        appearanceReady = false;
        try { bubbleMode = false; cardOpacity = 1; LoadPreferences(); }
        finally { appearanceReady = true; }
        if (!bubbleMode || sidebarOpen || Math.Abs(cardOpacity - .65) > .001) throw new Exception("Bubble settings did not restore on startup");
        ApplyPresentation(); Render(); SaveRender("bubbles.png");
        OpenSidebar(true); SaveRender("bubble-settings.png");
        if (sidebar.Visibility != Visibility.Visible) throw new Exception("Settings cannot be recovered from bubble-only mode");
        SetBubbleMode(false); ShowMode(false); showAll = true; Render();
        return true;
    }
}
