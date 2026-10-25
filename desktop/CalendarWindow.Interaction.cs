using System;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Interop;
using System.Windows.Media;
using ShapePath = System.Windows.Shapes.Path;
using System.Windows.Threading;
using Forms = System.Windows.Forms;

namespace Playtime;

public sealed partial class CalendarWindow
{
    readonly Grid desktopRoot = new(), windowRoot = new();
    readonly StackPanel compactActions = new() { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right };
    readonly Border edgeHandle = new();
    readonly CheckBox residentToggle = new(), edgeToggle = new();
    readonly DispatcherTimer edgeTimer = new() { Interval = TimeSpan.FromMilliseconds(200) };
    Button compactPin = null!, quickSidebar = null!;
    bool keepWidgetOnClose = true, edgeAutoHide, edgeHidden, edgeTransition;
    Rect expandedBounds;
    DateTime? edgeLeaveSince;
    string edgeSide = "right";

    Button PinButton(bool iconOnly)
    {
        var button = Button("", () => { Topmost = !Topmost; PinLabel(); SavePreferences(); });
        button.Tag = iconOnly; button.MinWidth = iconOnly ? 34 : 72;
        AutomationProperties.SetName(button, "置顶窗口");
        return button;
    }

    void UpdatePinButtons()
    {
        foreach (var button in new[] { pin, compactPin }.Where(b => b != null))
        {
            var icon = new ShapePath { Data = Geometry.Parse("M5,1 L13,1 M7,1 L7,7 L4,11 L14,11 L11,7 L11,1 M9,11 L9,17"), Stroke = Ink, StrokeThickness = 1.8, Width = 18, Height = 18, Stretch = Stretch.Uniform, Fill = Topmost ? Green : Brushes.Transparent };
            var content = new StackPanel { Orientation = Orientation.Horizontal };
            content.Children.Add(icon);
            if (button.Tag is not true) content.Children.Add(new TextBlock { Text = Topmost ? " 已置顶" : " 置顶", VerticalAlignment = VerticalAlignment.Center });
            button.Content = content; button.ToolTip = Topmost ? "取消窗口置顶" : "置顶窗口";
            AutomationProperties.SetHelpText(button, Topmost ? "当前已置顶" : "当前未置顶");
        }
    }

    void BuildInteractionShell()
    {
        desktopRoot.RowDefinitions.Add(new RowDefinition()); desktopRoot.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        desktopRoot.Children.Add(desktopLayout);
        compactPin = PinButton(true); compactActions.Children.Add(compactPin);
        quickSidebar = Button("☰", () => OpenSidebarPage("calendar")); quickSidebar.MinWidth = 34; quickSidebar.ToolTip = "打开侧边栏：日历、外观、提醒、采集";
        AutomationProperties.SetName(quickSidebar, "打开侧边栏"); compactActions.Children.Add(quickSidebar);
        compactActions.Margin = new Thickness(0, 3, 2, 0); Grid.SetRow(compactActions, 1); desktopRoot.Children.Add(compactActions);
        windowRoot.Children.Add(desktopRoot);
        edgeHandle.Background = Green; edgeHandle.CornerRadius = new CornerRadius(5); edgeHandle.Visibility = Visibility.Collapsed;
        edgeHandle.Child = new TextBlock { Text = "游\n时\n‹", TextAlignment = TextAlignment.Center, VerticalAlignment = VerticalAlignment.Center, FontSize = 11, Foreground = Ink };
        edgeHandle.ToolTip = "移入展开游时"; edgeHandle.MouseEnter += (_, _) => RevealEdge(true);
        AutomationProperties.SetName(edgeHandle, "游时边缘唤出区");
        windowRoot.Children.Add(edgeHandle); Content = windowRoot;
        windowRoot.ClipToBounds = true;
        desktopRoot.RenderTransform = edgeSlide;
        PreviewMouseLeftButtonDown += (_, _) => { if (edgeTransition) RevealEdge(false); };
        edgeTimer.Tick += (_, _) =>
        {
            if (exiting || !IsVisible) return;
            var mouse = Forms.Cursor.Position;
            var origin = PointToScreen(new Point()); var scale = PresentationSource.FromVisual(this)?.CompositionTarget?.TransformFromDevice ?? Matrix.Identity;
            var delta = scale.Transform(new Vector(mouse.X - origin.X, mouse.Y - origin.Y));
            UpdateEdge(new Point(Left + delta.X, Top + delta.Y), DateTime.UtcNow);
        };
        edgeTimer.Start(); UpdatePinButtons();
    }

    void BuildResidentSettings()
    {
        Section(desktopSettings, "常驻与窗口行为");
        residentToggle.Content = "关闭主窗口后保留桌面挂件"; residentToggle.Margin = new Thickness(0, 0, 0, 10);
        residentToggle.Click += (_, _) => { keepWidgetOnClose = residentToggle.IsChecked == true; SavePreferences(); };
        desktopSettings.Children.Add(residentToggle);
        edgeToggle.Content = "挂件靠边时自动隐藏"; edgeToggle.Margin = new Thickness(0, 0, 0, 8);
        edgeToggle.Click += (_, _) => { edgeAutoHide = edgeToggle.IsChecked == true; if (!edgeAutoHide) RevealEdge(); edgeLeaveSince = null; SavePreferences(); };
        desktopSettings.Children.Add(edgeToggle);
        var note = Text("关闭后挂件不占任务栏。靠近屏幕左右边缘，移开约 1 秒收起，移入边缘标签展开；托盘“退出游时”结束程序。", 10);
        note.Foreground = Muted; desktopSettings.Children.Add(note);
    }

    void OpenSidebarPage(string page)
    {
        RevealEdge(); sidebarPage = page; settingsOpen = page != "calendar"; sidebarOpen = true;
        ApplyPresentation(); Show(); Activate(); SavePreferences();
    }

    void UpdateInteractionChrome()
    {
        compactActions.Visibility = compact ? Visibility.Visible : Visibility.Collapsed;
        pin.Visibility = compact ? Visibility.Collapsed : Visibility.Visible;
        ShowInTaskbar = !compact;
        residentToggle.IsChecked = keepWidgetOnClose; edgeToggle.IsChecked = edgeAutoHide;
        UpdatePinButtons(); ApplyWorkspaceVisuals(compactActions);
        if (compact && !sidebarOpen) sidebar.Visibility = Visibility.Collapsed;
    }

    void CloseToDesktop()
    {
        if (exiting) return;
        RevealEdge();
        if (!keepWidgetOnClose) { SavePreferences(); Hide(); return; }
        sidebarOpen = false; ShowMode(true); ShowInTaskbar = false;
        if (edgeAutoHide)
        {
            var area = DesktopWorkArea(); Left = Left + Width / 2 < area.Left + area.Width / 2 ? area.Left : area.Right - Width;
            CollapseEdge();
        }
        SavePreferences();
    }

    Rect DesktopWorkArea()
    {
        if (PresentationSource.FromVisual(this)?.CompositionTarget is not { } target) return SystemParameters.WorkArea;
        var origin = PointToScreen(new Point());
        var bounds = Forms.Screen.FromHandle(new WindowInteropHelper(this).Handle).WorkingArea;
        var start = target.TransformFromDevice.Transform(new Vector(bounds.Left - origin.X, bounds.Top - origin.Y));
        var size = target.TransformFromDevice.Transform(new Vector(bounds.Width, bounds.Height));
        return new Rect(Left + start.X, Top + start.Y, size.X, size.Y);
    }

    void UpdateEdge(Point cursor, DateTime now)
    {
        if (!edgeAutoHide || !compact || sidebarOpen || edgeTransition || Mouse.LeftButton == MouseButtonState.Pressed || IsMouseCaptureWithin) { edgeLeaveSince = null; return; }
        var window = new Rect(Left, Top, ActualWidth, ActualHeight);
        if (edgeHidden) { if (window.Contains(cursor)) RevealEdge(true); return; }
        var area = DesktopWorkArea();
        if (window.Contains(cursor) || (Left > area.Left + 16 && Left + Width < area.Right - 16)) { edgeLeaveSince = null; return; }
        edgeLeaveSince ??= now;
        if ((now - edgeLeaveSince.Value).TotalMilliseconds >= 1000) CollapseEdge();
    }

    void CollapseEdge()
    {
        StartEdgeCollapse();
    }

    void RevealEdge(bool animate = false)
    {
        StartEdgeReveal(animate);
    }

    async Task<bool> InteractionSmokeTest()
    {
        OpenSidebarPage("calendar");
        var loadCalendar = navigation.Children.OfType<Button>().SingleOrDefault(b => b.Name == "LoadCalendarButton");
        if (loadCalendar == null || !loadCalendar.IsVisible || loadCalendar.ToolTip as string != "打开插件目录：" + PluginDirectory) throw new Exception("Calendar plugin entry is missing or points to the wrong data directory");
        SaveRender("plugin-directory-entry.png");
        ShowMode(false); OpenSidebarPage("reminders");
        if (reminderSettings.Visibility != Visibility.Visible || desktopSettings.Visibility != Visibility.Collapsed || networkSettings.Visibility != Visibility.Collapsed) throw new Exception("Reminder page was mixed with other settings");
        OpenSidebarPage("network");
        if (networkSettings.Visibility != Visibility.Visible || reminderSettings.Visibility != Visibility.Collapsed) throw new Exception("Network page routing failed");
        OpenSidebarPage("appearance");
        await Mutate("/api/workspace", new { theme = new { preset = "endfield" } }, true);
        if (workspaceDark || themePreset.SelectedValue as string != "endfield" || ((SolidColorBrush)Green).Color != (Color)ColorConverter.ConvertFromString("#FFFA00")) throw new Exception("Industrial white/yellow preset did not reach desktop");
        if (themePreset.Template.FindName("SelectionContent", themePreset) is not ContentPresenter selection || selection.Content as string != "终末地 · 工业白黄") throw new Exception("Theme selector must display its label, not the backing record");
        ShowMode(false); OpenSidebarPage("appearance"); SaveRender("design-endfield.png");
        await Mutate("/api/workspace", new { theme = workspaceDefaults.GetProperty("themes").GetProperty("zzz") }, true);
        if (themeRadius != 8 || themeControlRadius != 6 || NavInk.Color == ((SolidColorBrush)Ink).Color) throw new Exception("Independent desktop geometry/navigation tokens did not apply");
        SaveRender("design-zzz.png");
        await Mutate("/api/workspace", new { theme = workspaceDefaults.GetProperty("themes").GetProperty("arknights") }, true);
        if (!workspaceDark || themeRadius != 0) throw new Exception("Tactical skin did not apply");
        reminderToggle.ApplyTemplate();
        if (reminderToggle.Template.FindName("CheckMark", reminderToggle) is not ShapePath mark || mark.Stroke != reminderToggle.Foreground) throw new Exception("Check mark does not use the readable theme foreground");
        OpenSidebarPage("reminders"); SaveRender("ux-dark-reminders.png");
        ShowMode(true); sidebarOpen = false; ApplyPresentation();
        bool originalPin = Topmost;
        compactPin.RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
        if (Topmost == originalPin) throw new Exception("Compact pin button failed");
        compactPin.RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
        quickSidebar.RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
        if (!sidebarOpen || navigation.Visibility != Visibility.Visible) throw new Exception("Compact sidebar button failed");
        keepWidgetOnClose = true; edgeAutoHide = false;
        Close(); await Dispatcher.InvokeAsync(() => { }, DispatcherPriority.Background);
        if (!IsVisible || !compact || ShowInTaskbar || compactActions.Visibility != Visibility.Visible) throw new Exception("Closing did not preserve a taskbar-free widget");
        SaveRender("ux-widget-actions.png");
        edgeAutoHide = true; var area = DesktopWorkArea(); Left = area.Right - Width;
        var outside = new Point(area.Left + 30, area.Top + 30); var now = DateTime.UtcNow;
        UpdateEdge(outside, now); UpdateEdge(outside, now.AddSeconds(2));
        if (edgeTransition)
        {
            await Task.Delay(80);
            if (!edgeTransition || Width < 320 || Math.Abs(edgeSlide.X) < 1) throw new Exception("Edge collapse skipped its visible sliding frames");
            SaveRender("ux-edge-collapsing.png");
        }
        await edgeAnimationTask;
        if (!edgeHidden || Width > 20 || !IsVisible) throw new Exception("Edge auto-hide failed");
        SaveRender("ux-edge-tab.png"); SavePreferences();
        using (var doc = JsonDocument.Parse(File.ReadAllText(preferences)))
            if (doc.RootElement.GetProperty("widget_width").GetDouble() < 320 || !doc.RootElement.GetProperty("edge_auto_hide").GetBoolean()) throw new Exception("Edge tab damaged saved widget size");
        UpdateEdge(new Point(Left + Width / 2, Top + Height / 2), now.AddSeconds(3));
        if (edgeTransition)
        {
            await Task.Delay(80);
            if (!edgeTransition || Math.Abs(edgeSlide.X) < 1) throw new Exception("Edge reveal skipped its visible sliding frames");
            SaveRender("ux-edge-revealing.png");
        }
        await edgeAnimationTask;
        if (edgeHidden || Width < 320) throw new Exception("Hover did not reveal the widget");
        // Moving back over the tag during collapse reverses from the current
        // frame, and opening a sidebar can finish that transition immediately.
        CollapseEdge();
        if (edgeTransition) await Task.Delay(60);
        RevealEdge(true); OpenSidebarPage("calendar");
        if (edgeHidden || edgeTransition || Math.Abs(edgeSlide.X) > .1 || !sidebarOpen) throw new Exception("Interrupted edge animation did not restore the interactive widget");
        sidebarOpen = false; ApplyPresentation();
        area = DesktopWorkArea(); Left = area.Left;
        var outsideLeft = new Point(area.Right - 30, area.Bottom - 30);
        UpdateEdge(outsideLeft, now.AddSeconds(5)); UpdateEdge(outsideLeft, now.AddSeconds(7));
        if (edgeTransition)
        {
            await Task.Delay(80);
            if (edgeSlide.X >= -1) throw new Exception("Left-edge collapse moved in the wrong direction");
        }
        await edgeAnimationTask;
        if (!edgeHidden || Width > 20 || Math.Abs(Left - area.Left) > 1) throw new Exception("Left-edge tab did not stay inside the display");
        RevealEdge(true); await edgeAnimationTask;
        if (edgeHidden || edgeTransition || Width < 320 || Math.Abs(Left - area.Left) > 1) throw new Exception("Left-edge reveal did not restore the window bounds");
        OpenSidebarPage("appearance"); UpdateEdge(outside, now.AddSeconds(8));
        if (edgeHidden) throw new Exception("Sidebar interaction was auto-hidden");
        edgeAutoHide = false; keepWidgetOnClose = false; CloseToDesktop();
        if (IsVisible) throw new Exception("Optional tray close did not hide the window");
        keepWidgetOnClose = true; ShowMode(false);
        await Mutate("/api/workspace", new { theme = workspaceDefaults.GetProperty("theme") }, true);
        return true;
    }
}
