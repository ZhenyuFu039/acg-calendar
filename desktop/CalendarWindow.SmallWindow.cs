using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Automation.Peers;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Threading;

namespace Playtime;

// A short list widget gives its height to the cards: the title shrinks to one line, repeated
// captions move into tooltips, and the deadline strip keeps two days plus a link to the month.
// Nothing is removed: every hidden detail is still in a tooltip, the month window or "更多".
public sealed partial class CalendarWindow
{
    TextBlock brandTitle = null!, officialNotice = null!;
    Image brandIcon = null!;
    Button closeButton = null!, moreDeadlines = null!;
    bool shortWidget;
    const double ShortWidgetHeight = 480;
    const int ShortDeadlineDays = 2;
    // Windows "Make text bigger" (Settings > Accessibility > Text size), read once, capped so the
    // 320px widget still lays out.
    double systemTextScale = 1;

    void ReadSystemTextScale()
    {
        try { if (Microsoft.Win32.Registry.GetValue(@"HKEY_CURRENT_USER\Software\Microsoft\Accessibility", "TextScaleFactor", 100) is int percent) systemTextScale = Math.Clamp(percent / 100.0, 1, 1.5); }
        catch { /* No accessibility key: keep the default size. */ }
    }

    void ApplyShortLayout()
    {
        if (brandTitle == null) return;
        shortWidget = compact && !bubbleMode && Height < ShortWidgetHeight;
        brandTitle.SetValue(BaseFontProperty, shortWidget ? 15.0 : 22.0);
        subtitle.SetValue(BaseFontProperty, shortWidget ? 13.0 : 16.0);
        brandIcon.Width = brandIcon.Height = shortWidget ? 22 : 33;
        clock.Visibility = officialNotice.Visibility = nativeFocusLabel.Visibility = shortWidget ? Visibility.Collapsed : Visibility.Visible;
        brandTitle.ToolTip = shortWidget ? clock.Text + "\n" + officialNotice.Text : null;
        subtitle.ToolTip = shortWidget ? nativeFocusLabel.Text : null;
        closeButton.Padding = shortWidget ? new Thickness(7, 2, 7, 2) : new Thickness(9, 6, 9, 6);
        heading.Margin = summaryPanel.Margin = new Thickness(0, 0, 0, shortWidget ? 4 : 12);
        // The source/status footer keeps its first line; the rest is in its tooltip and the sidebar.
        status.TextWrapping = shortWidget ? TextWrapping.NoWrap : TextWrapping.Wrap; status.TextTrimming = TextTrimming.CharacterEllipsis;
        status.MaxHeight = shortWidget ? 10 * fontScale * systemTextScale * 1.45 : double.PositiveInfinity;
        status.Margin = new Thickness(0, shortWidget ? 4 : 10, 0, 0); status.ToolTip = shortWidget ? status.Text : null;
        if (shortWidget) rewardHeader.Visibility = Visibility.Collapsed; // The month window shows the reward total.
        if (!bubbleMode) desktopShell.Padding = new Thickness(shortWidget ? 8 : 16);
        if (!nativeDeadlineBand.Children.Contains(moreDeadlines)) nativeDeadlineBand.Children.Add(moreDeadlines);
        var days = nativeDeadlineBand.Children.OfType<Button>().Where(b => b != moreDeadlines).ToArray();
        for (int i = 0; i < days.Length; i++) days[i].Visibility = !shortWidget || i < ShortDeadlineDays ? Visibility.Visible : Visibility.Collapsed;
        int hidden = days.Length - ShortDeadlineDays;
        moreDeadlines.Visibility = shortWidget && hidden > 0 ? Visibility.Visible : Visibility.Collapsed;
        moreDeadlines.Content = $"+{hidden} 天 · 月历 ›";
        AutomationProperties.SetName(moreDeadlines, $"另有 {hidden} 个截止日期，打开月历查看");
    }

    void BuildShortLayoutControls()
    {
        moreDeadlines = Button("", () => ShowMode(false)); moreDeadlines.Margin = new Thickness(0, 0, 4, 4); moreDeadlines.Visibility = Visibility.Collapsed;
        moreDeadlines.ToolTip = "其余截止日期在月历窗口里";
    }

    // Every control a keyboard can reach must say what it is to assistive technology.
    static IEnumerable<Control> KeyboardControls(DependencyObject node)
    {
        if (node is Control control && control.Focusable && control.IsVisible && control.IsEnabled && control is ButtonBase or ComboBox or Slider) yield return control;
        for (int i = 0; i < VisualTreeHelper.GetChildrenCount(node); i++)
            foreach (var child in KeyboardControls(VisualTreeHelper.GetChild(node, i))) yield return child;
    }
    // WCAG contrast ratio of two opaque colours.
    static double Contrast(Color a, Color b)
    {
        static double Luminance(Color c) { double L(byte v) { double s = v / 255.0; return s <= .03928 ? s / 12.92 : Math.Pow((s + .055) / 1.055, 2.4); } return .2126 * L(c.R) + .7152 * L(c.G) + .0722 * L(c.B); }
        double x = Luminance(a), y = Luminance(b); return (Math.Max(x, y) + .05) / (Math.Min(x, y) + .05);
    }
    static string AccessibleName(Control control) => UIElementAutomationPeer.CreatePeerForElement(control)?.GetName() ?? "";

    async Task<bool> SmallWindowSmokeTest()
    {
        bool originalBubble = bubbleMode; double originalHeight = widgetHeight, originalWidth = widgetWidth;
        var report = new List<object>();
        async Task Measure(string label, double width, double height, bool bubble, bool hint, double textScale = 1)
        {
            widgetWidth = width; widgetHeight = height; compact = true; bubbleMode = bubble; sidebarOpen = false; systemTextScale = textScale;
            ApplyPresentation(); Render(); if (hint) ShowHint("已自动保存：挂件高度 320。"); else { hintTimer.Stop(); quickHint.Visibility = Visibility.Collapsed; }
            UpdateLayout(); await Dispatcher.InvokeAsync(() => { }, DispatcherPriority.Background); UpdateLayout();
            foreach (var button in new[] { bubbleQuick, syncQuick, fullQuick, compactPin, quickSidebar })
            {
                var box = button.TransformToAncestor(this).TransformBounds(new Rect(0, 0, button.ActualWidth, button.ActualHeight));
                if (!button.IsVisible || box.Left < -.5 || box.Top < -.5 || box.Right > ActualWidth + .5 || box.Bottom > ActualHeight + .5) throw new Exception($"{label}: quick action {AutomationProperties.GetName(button)} is not fully visible ({box})");
            }
            var first = cards.Children.OfType<FrameworkElement>().FirstOrDefault();
            double area = cardScroll.ActualHeight, card = first?.ActualHeight ?? 0;
            report.Add(new { label, width, height, bubble, hint, textScale, cardArea = Math.Round(area), firstCard = Math.Round(card), shortWidget });
            if (textScale == 1 && (first == null || area + .5 < card)) throw new Exception($"{label}: card area {area:0}px does not hold one whole card ({card:0}px)");
            SaveRender("small-" + label + ".png");
        }
        await Measure("list-320x320", 320, 320, false, false);
        if (!shortWidget || clock.Visibility == Visibility.Visible || officialNotice.Visibility == Visibility.Visible) throw new Exception("A 320px-high list widget kept its full heading");
        if (brandTitle.ToolTip is not string tip || !tip.Contains(officialNotice.Text)) throw new Exception("The hidden official-time notice is not reachable from the title tooltip");
        int visibleDays = nativeDeadlineBand.Children.OfType<Button>().Count(b => b != moreDeadlines && b.Visibility == Visibility.Visible);
        if (visibleDays > ShortDeadlineDays) throw new Exception("The short widget kept more than two deadline days");
        await Measure("list-320x320-hint", 320, 320, false, true);
        await Measure("list-320x620", 320, 620, false, false);
        if (shortWidget || clock.Visibility != Visibility.Visible) throw new Exception("A tall widget used the short layout");
        await Measure("bubble-320x320", 320, 320, true, false);
        await Measure("bubble-320x620", 320, 620, true, true);
        await Measure("list-320x320-text150", 320, 320, false, false, 1.5);
        if (compactPin.Content is not StackPanel { Children: [_, TextBlock pinLabel] } || pinLabel.FontSize < 12 * fontScale * 1.5 - .1) throw new Exception("The pin label ignored the system text size");
        // Dark theme at the short height.
        await Mutate("/api/workspace", new { theme = workspaceDefaults.GetProperty("themes").GetProperty("arknights") }, true);
        await Measure("dark-list-320x320", 320, 320, false, true);
        // A text badge (no logo) must sit on the theme surface, not on white under light theme text.
        var textBadge = cards.Children.OfType<Border>().Select(c => (c.Child as Grid)?.Children.OfType<Border>().FirstOrDefault()).FirstOrDefault(b => b?.Child is TextBlock);
        if (textBadge == null || textBadge.Background == Brushes.White || textBadge.Child is not TextBlock { Foreground: SolidColorBrush ink } || textBadge.Background is not SolidColorBrush plate || Contrast(ink.Color, plate.Color) < 4.5) throw new Exception("A game text badge is unreadable in the dark theme");
        await Measure("dark-bubble-320x320", 320, 320, true, false);
        // Keyboard: every reachable control has a name; Tab walks the quick bar forward and back with a visible focus frame.
        await Measure("keyboard-320x620", 320, 620, false, false);
        var unnamed = KeyboardControls(this).Where(c => string.IsNullOrWhiteSpace(AccessibleName(c))).Select(c => c.GetType().Name + "@" + (c.ToolTip as string ?? "")).ToArray();
        if (unnamed.Length > 0) throw new Exception("Controls without an accessible name: " + string.Join(", ", unnamed));
        var bar = new[] { bubbleQuick, syncQuick, fullQuick, compactPin, quickSidebar }.Where(b => b.IsEnabled).ToArray();
        Activate(); Keyboard.Focus(bar[0]);
        for (int i = 1; i < bar.Length; i++)
        {
            ((UIElement)Keyboard.FocusedElement).MoveFocus(new TraversalRequest(FocusNavigationDirection.Next));
            if (Keyboard.FocusedElement != bar[i]) throw new Exception($"Tab from {AutomationProperties.GetName(bar[i - 1])} went to {Keyboard.FocusedElement} instead of {AutomationProperties.GetName(bar[i])}");
            if (bar[i].Template.FindName("ButtonSurface", bar[i]) is not Border frame || frame.BorderThickness.Left < 2 || frame.BorderBrush != Resources["FocusInk"]) throw new Exception("Keyboard focus on a quick action has no visible frame");
        }
        for (int i = bar.Length - 2; i >= 0; i--)
        {
            ((UIElement)Keyboard.FocusedElement).MoveFocus(new TraversalRequest(FocusNavigationDirection.Previous));
            if (Keyboard.FocusedElement != bar[i]) throw new Exception("Shift+Tab did not walk the quick bar back");
        }
        SaveRender("small-keyboard-focus.png");
        await Mutate("/api/workspace", new { theme = workspaceDefaults.GetProperty("theme") }, true);
        systemTextScale = 1; ReadSystemTextScale();
        widgetWidth = originalWidth; widgetHeight = originalHeight; bubbleMode = originalBubble; ApplyPresentation(); Render();
        await System.IO.File.WriteAllTextAsync(System.IO.Path.Combine(dataDirectory, "small-window.json"), System.Text.Json.JsonSerializer.Serialize(report, new System.Text.Json.JsonSerializerOptions { WriteIndented = true }));
        return true;
    }
}
