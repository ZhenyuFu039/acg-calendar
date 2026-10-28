using System;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Media;
using System.Windows.Threading;

namespace Playtime;

// The everyday actions (bubble, sync, full calendar, pin, more) stay in one bar under the
// cards in every window mode, so none of them needs the sidebar, right-click or the tray.
public sealed partial class CalendarWindow
{
    Button bubbleQuick = null!, syncQuick = null!, fullQuick = null!;
    readonly TextBlock quickHint = new() { FontSize = 11, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(4, 4, 4, 2), Visibility = Visibility.Collapsed };
    readonly DispatcherTimer hintTimer = new() { Interval = TimeSpan.FromSeconds(6) };
    bool syncRunning;

    void BuildQuickActions()
    {
        bubbleQuick = QuickButton("气泡", () => SetBubbleMode(!bubbleMode));
        syncQuick = QuickButton("⟳ 同步", async () => await SyncNow());
        fullQuick = QuickButton("完整日历 ↗", () => { Open(FullCalendarUrl()); ShowHint("已在浏览器打开完整日历（时间轴）。"); });
        fullQuick.ToolTip = "在浏览器打开完整日历：时间轴视图，保留当前游戏组合与所选日期";
        compactActions.Children.Add(bubbleQuick); compactActions.Children.Add(syncQuick); compactActions.Children.Add(fullQuick);
        compactPin = PinButton(false); compactPin.MinWidth = 0; StyleQuick(compactPin); compactActions.Children.Add(compactPin);
        quickSidebar = QuickButton("更多", () => OpenSidebarPage("calendar")); quickSidebar.ToolTip = "更多：游戏组合与筛选、外观与尺寸、提醒、采集、收起与退出";
        compactActions.Children.Add(quickSidebar);
        hintTimer.Tick += (_, _) => { hintTimer.Stop(); quickHint.Visibility = Visibility.Collapsed; };
        AutomationProperties.SetLiveSetting(quickHint, AutomationLiveSetting.Polite);
    }

    Button QuickButton(string label, Action action)
    {
        var button = Button(label, action); StyleQuick(button);
        AutomationProperties.SetName(button, label.Trim('⟳', ' ', '↗')); return button;
    }
    static void StyleQuick(Button button) { button.Padding = new Thickness(7, 5, 7, 5); button.Margin = new Thickness(4, 0, 0, 0); button.FontSize = 12; }

    void UpdateQuickActions()
    {
        bubbleQuick.Content = bubbleMode ? "✓ 气泡" : "气泡";
        bubbleQuick.ToolTip = bubbleMode ? "当前：透明气泡。点击切换为普通列表" : "当前：普通列表。点击只显示活动气泡";
        AutomationProperties.SetHelpText(bubbleQuick, bubbleMode ? "当前为透明气泡模式" : "当前为普通列表模式");
        syncQuick.Content = syncRunning ? "⟳ 同步中" : "⟳ 同步"; syncQuick.IsEnabled = !syncRunning;
    }

    // Matches the web page's link contract: ?game=<scope>&view=timeline, plus the chosen date.
    string FullCalendarUrl() => address + "/?game=" + Uri.EscapeDataString(selectedGame) + "&view=timeline" + (selected is DateTime day ? "&date=" + day.ToString("yyyy-MM-dd") : "");

    string ScopeLabel() => selectedGame switch { "all" => "全部游戏", "selected" => "自选组合", _ => games.FirstOrDefault(g => g.Id == selectedGame)?.Short_Name ?? selectedGame };

    async Task SyncNow()
    {
        string scope = ScopeLabel();
        ShowHint("开始同步 " + scope + "：活动日期先更新，图片随后补齐；站点限流不变。");
        await Mutate("/api/sync", new { game = selectedGame });
    }

    void UpdateQuickSync(JsonElement all)
    {
        var scoped = games.Where(g => InScope(g.Id) && all.TryGetProperty(g.Id, out _)).Select(g => all.GetProperty(g.Id)).ToArray();
        syncRunning = scoped.Any(s => s.TryGetProperty("running", out var r) && r.ValueKind == JsonValueKind.True);
        syncQuick.ToolTip = "同步当前范围：" + ScopeLabel() + "\n" + syncProgress.Text;
        AutomationProperties.SetName(syncQuick, "同步" + ScopeLabel()); AutomationProperties.SetHelpText(syncQuick, syncProgress.Text);
        UpdateQuickActions();
    }

    void Saved(string what) => ShowHint("已自动保存：" + what + "。");

    // A short message above the bar: mode changes, closing, saving. It also reaches screen readers.
    void ShowHint(string message)
    {
        // A solid surface keeps the text readable over the wallpaper behind transparent bubbles.
        quickHint.Text = message; quickHint.Visibility = Visibility.Visible; quickHint.Background = Surface; quickHint.Padding = new Thickness(6, 3, 6, 3); ApplyWorkspaceVisuals(quickHint);
        hintTimer.Stop(); hintTimer.Start();
    }

    async Task<bool> QuickActionsSmokeTest()
    {
        bool originalBubble = bubbleMode, originalCompact = compact; double originalWidth = widgetWidth, originalHeight = widgetHeight;
        var buttons = new[] { bubbleQuick, syncQuick, fullQuick, compactPin, quickSidebar };
        foreach (var (width, height, bubble) in new[] { (320.0, 320.0, false), (320.0, 620.0, false), (320.0, 320.0, true), (320.0, 620.0, true) })
        {
            widgetWidth = width; widgetHeight = height; sidebarOpen = false; compact = true; bubbleMode = bubble;
            ApplyPresentation(); Render(); UpdateLayout();
            await Dispatcher.InvokeAsync(() => { }, DispatcherPriority.Background);
            foreach (var button in buttons)
            {
                if (!button.IsVisible) throw new Exception($"Quick action {AutomationProperties.GetName(button)} is hidden at {width}x{height} (bubble {bubble})");
                var box = button.TransformToAncestor(this).TransformBounds(new Rect(0, 0, button.ActualWidth, button.ActualHeight));
                if (box.Left < -.5 || box.Top < -.5 || box.Right > ActualWidth + .5 || box.Bottom > ActualHeight + .5) throw new Exception($"Quick action {AutomationProperties.GetName(button)} falls outside the {width}x{height} window: {box}");
            }
            SaveRender($"quick-{(bubble ? "bubble" : "list")}-{width:0}x{height:0}.png");
        }
        bubbleMode = false; ApplyPresentation();
        bubbleQuick.RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
        if (!bubbleMode || (string)bubbleQuick.Content != "✓ 气泡") throw new Exception("Bubble quick action did not switch to bubbles or show its state");
        bubbleQuick.RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
        if (bubbleMode) throw new Exception("Bubble quick action did not return to the list");
        // The month window shows the same bar.
        ShowMode(false); UpdateLayout();
        if (!compactActions.IsVisible || !syncQuick.IsVisible) throw new Exception("Month window lost the quick bar");
        var previous = selected; selected = new DateTime(2026, 11, 9);
        string url = FullCalendarUrl(); selected = previous;
        if (!url.Contains("&view=timeline") || !url.Contains("&date=2026-11-09") || !url.Contains("?game=" + Uri.EscapeDataString(selectedGame))) throw new Exception("Full calendar link lost its view, date or scope: " + url);
        CloseToDesktop();
        if (quickHint.Visibility != Visibility.Visible || !quickHint.Text.Contains("桌面挂件")) throw new Exception("Closing to the widget gave no feedback");
        // Appearance page: window controls before theme; every page keeps its own scroll position.
        int bubbleAt = desktopSettings.Children.IndexOf(bubbleToggle), themeAt = desktopSettings.Children.IndexOf(themePreset);
        if (bubbleAt < 0 || themeAt < 0 || bubbleAt > themeAt || desktopSettings.Children.IndexOf(widthSlider) > themeAt) throw new Exception("Window mode and size are not ahead of the theme settings");
        widgetHeight = 620; ApplyPresentation(); OpenSidebarPage("appearance"); sidebarScroll.UpdateLayout(); sidebarScroll.ScrollToEnd(); sidebarScroll.UpdateLayout();
        double deep = sidebarScroll.VerticalOffset;
        if (deep < 1) throw new Exception("Appearance page is too short to check scroll memory");
        OpenSidebarPage("reminders"); sidebarScroll.UpdateLayout();
        if (sidebarScroll.VerticalOffset > .5) throw new Exception("Reminder page opened at the appearance page's scroll depth");
        OpenSidebarPage("appearance"); sidebarScroll.UpdateLayout();
        if (Math.Abs(sidebarScroll.VerticalOffset - deep) > 1) throw new Exception("Appearance page did not keep its own scroll position");
        sidebarScroll.ScrollToTop(); sidebarScroll.UpdateLayout(); SaveRender("appearance-order.png"); sidebarOpen = false;
        widgetWidth = originalWidth; widgetHeight = originalHeight; bubbleMode = originalBubble; compact = originalCompact; ApplyPresentation();
        return true;
    }
}
