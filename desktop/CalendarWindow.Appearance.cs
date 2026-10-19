using System;
using System.IO;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Data;
using System.Windows.Input;
using System.Windows.Media;
using Forms = System.Windows.Forms;

namespace Playtime;

public sealed partial class CalendarWindow
{
    readonly Grid desktopLayout = new();
    readonly ColumnDefinition sidebarColumn = new();
    readonly Border desktopShell = new(), sidebar = new();
    readonly StackPanel sidebarRail = new(), navigation = new(), desktopSettings = new();
    readonly Grid sidebarPanel = new();
    readonly CheckBox bubbleToggle = new(), nativeToggle = new(), reminderToggle = new();
    readonly Slider opacitySlider = new(), widthSlider = new(), heightSlider = new();
    readonly TextBlock opacityLabel = new(), widthLabel = new(), heightLabel = new();
    readonly CheckBox[] offsetToggles = [new(), new(), new()];
    readonly int[] reminderOffsets = [4320, 1440, 60];
    ScrollViewer cardScroll = null!;
    FrameworkElement heading = null!, summaryPanel = null!, footer = null!;
    Button navigationTab = null!, settingsTab = null!, collapseSidebar = null!;
    Forms.ToolStripMenuItem? bubbleMenu;
    bool bubbleMode, sidebarOpen, settingsOpen, applyingAppearance, appearanceReady, updatingSettings;
    double cardOpacity = .85, widgetWidth = 390, widgetHeight = 620, calendarWidth = 820, calendarHeight = 720;
    const double RailWidth = 48, PanelWidth = 224;

    void InitializeDesktopUI()
    {
        desktopLayout.ColumnDefinitions.Add(sidebarColumn);
        desktopLayout.ColumnDefinitions.Add(new ColumnDefinition());
        BuildSidebar(); desktopLayout.Children.Add(sidebar);
        desktopShell.BorderBrush = Brush("#DDE4D3");
        var layout = new Grid();
        foreach (var height in new[] { GridLength.Auto, GridLength.Auto, new GridLength(1, GridUnitType.Star), GridLength.Auto })
            layout.RowDefinitions.Add(new RowDefinition { Height = height });
        var title = new DockPanel { Margin = new Thickness(0, 0, 0, 12) }; heading = title;
        var close = Button("×", () => Hide()); close.ToolTip = "收起到系统托盘，提醒继续运行";
        DockPanel.SetDock(close, Dock.Right); title.Children.Add(close);
        title.Children.Add(new Image { Source = Icon, Width = 33, Height = 33, Margin = new Thickness(0, 1, 9, 0), VerticalAlignment = VerticalAlignment.Top, IsHitTestVisible = false });
        var brand = new StackPanel(); brand.Children.Add(Text("游时  PLAYTIME", 18, true));
        clock.FontSize = 10; clock.Foreground = Muted; clock.Margin = new Thickness(0, 4, 0, 0); brand.Children.Add(clock);
        brand.MouseLeftButtonDown += (_, e) => { if (e.ClickCount == 2) ToggleMode(); else DragMove(); };
        title.Children.Add(brand); layout.Children.Add(title);
        var summary = new StackPanel { Margin = new Thickness(0, 0, 0, 12) }; summaryPanel = summary;
        subtitle.FontSize = 16; subtitle.FontWeight = FontWeights.SemiBold; subtitle.Text = "正在启动本地日历…"; summary.Children.Add(subtitle);
        rewardHeader.Orientation = Orientation.Horizontal; rewardHeader.Margin = new Thickness(0, 5, 0, 0);
        rewardHeader.ToolTip = "终末地活动已核实小计，不含地图、信用商店、维护补偿及通行证。完整明细见时间轴。";
        if (RewardIcon() is ImageSource currency) rewardHeader.Children.Add(new Image { Source = currency, Width = 25, Height = 25 });
        rewardTotal.FontSize = 11; rewardTotal.Foreground = Brush("#805B42"); rewardTotal.VerticalAlignment = VerticalAlignment.Center;
        rewardHeader.Children.Add(rewardTotal); summary.Children.Add(rewardHeader); Grid.SetRow(summary, 1); layout.Children.Add(summary);
        content.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto }); content.ColumnDefinitions.Add(new ColumnDefinition());
        month.Margin = new Thickness(0, 0, 16, 0); month.VerticalAlignment = VerticalAlignment.Top;
        month.SelectedDatesChanged += (_, _) => { selected = month.SelectedDate; Render(); };
        content.Children.Add(month);
        cardScroll = new ScrollViewer { Content = cards, VerticalScrollBarVisibility = ScrollBarVisibility.Auto, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled, Background = null, PanningMode = PanningMode.VerticalOnly };
        Grid.SetColumn(cardScroll, 1); content.Children.Add(cardScroll); Grid.SetRow(content, 2); layout.Children.Add(content);
        status.FontSize = 10; status.Foreground = Muted; status.TextWrapping = TextWrapping.Wrap; status.Margin = new Thickness(0, 10, 0, 0); footer = status;
        Grid.SetRow(status, 3); layout.Children.Add(status); desktopShell.Child = layout;
        Grid.SetColumn(desktopShell, 1); desktopLayout.Children.Add(desktopShell); Content = desktopLayout;
        var menu = new ContextMenu();
        AddContext(menu, "打开侧边栏", () => OpenSidebar(false));
        AddContext(menu, "桌面设置 / 气泡透明度", () => OpenSidebar(true));
        AddContext(menu, "切换普通 / 透明气泡模式", () => SetBubbleMode(!bubbleMode));
        AddContext(menu, "收起到托盘", () => Hide());
        desktopShell.ContextMenu = menu;
        PreviewKeyDown += (_, e) =>
        {
            if (e.Key == Key.Escape && sidebarOpen) { sidebarOpen = false; ApplyPresentation(); SavePreferences(); e.Handled = true; }
            if (e.Key == Key.OemComma && Keyboard.Modifiers.HasFlag(ModifierKeys.Control)) { OpenSidebar(true); e.Handled = true; }
        };
    }

    static void AddContext(ContextMenu menu, string label, Action action)
    {
        var item = new MenuItem { Header = label }; item.Click += (_, _) => action(); menu.Items.Add(item);
    }
    Button SidebarButton(string label, Action action)
    {
        var button = Button(label, action); button.HorizontalContentAlignment = HorizontalAlignment.Left;
        button.HorizontalAlignment = HorizontalAlignment.Stretch; button.Margin = new Thickness(0, 0, 0, 6); return button;
    }
    void Section(StackPanel panel, string label)
    {
        var title = Text(label, 11, true); title.Foreground = Muted; title.Margin = new Thickness(0, 12, 0, 7); panel.Children.Add(title);
    }
    void BuildSidebar()
    {
        sidebar.Background = Brush("#EBF0E3"); sidebar.BorderBrush = Brush("#DDE4D3"); sidebar.BorderThickness = new Thickness(0, 0, 1, 0);
        var layers = new Grid(); sidebar.Child = layers;
        sidebarRail.Margin = new Thickness(5, 10, 5, 8);
        sidebarRail.Children.Add(new Image { Source = Icon, Width = 30, Height = 30, Margin = new Thickness(0, 0, 0, 12) });
        foreach (var item in new (string Label, string Tip, Action Action)[] {
            ("☰", "打开侧边栏", () => OpenSidebar(false)), ("历", "切换挂件 / 月历", ToggleMode),
            ("同", "同步当前游戏", async () => await Mutate("/api/sync", new { game = selectedGame })),
            ("⚙", "桌面设置", () => OpenSidebar(true)) })
        {
            var button = Button(item.Label, item.Action); button.ToolTip = item.Tip; button.Margin = new Thickness(0, 0, 0, 8); button.Padding = new Thickness(3, 8, 3, 8); sidebarRail.Children.Add(button);
        }
        layers.Children.Add(sidebarRail);
        sidebarPanel.Margin = new Thickness(12, 12, 12, 10);
        sidebarPanel.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        sidebarPanel.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        sidebarPanel.RowDefinitions.Add(new RowDefinition()); sidebarPanel.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        var title = new DockPanel();
        var close = collapseSidebar = Button("‹", () => { sidebarOpen = false; ApplyPresentation(); SavePreferences(); }); close.ToolTip = "收起侧边栏";
        DockPanel.SetDock(close, Dock.Right); title.Children.Add(close);
        title.Children.Add(new Image { Source = Icon, Width = 26, Height = 26, Margin = new Thickness(0, 0, 7, 0) }); title.Children.Add(Text("游时", 16, true)); sidebarPanel.Children.Add(title);
        var tabs = new UniformGrid { Columns = 2, Margin = new Thickness(0, 10, 0, 7) };
        navigationTab = Button("日历", () => OpenSidebar(false)); settingsTab = Button("设置", () => OpenSidebar(true)); tabs.Children.Add(navigationTab); tabs.Children.Add(settingsTab);
        Grid.SetRow(tabs, 1); sidebarPanel.Children.Add(tabs);
        var body = new Grid(); body.Children.Add(navigation); body.Children.Add(desktopSettings);
        var scroll = new ScrollViewer { Content = body, VerticalScrollBarVisibility = ScrollBarVisibility.Auto, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled };
        Grid.SetRow(scroll, 2); sidebarPanel.Children.Add(scroll);
        Section(navigation, "我的游戏");
        gameSelect.DisplayMemberPath = "Label"; gameSelect.SelectedValuePath = "Id";
        gameSelect.Padding = new Thickness(3, 5, 3, 5); gameSelect.Margin = new Thickness(0, 0, 0, 4); navigation.Children.Add(gameSelect);
        gameSelect.SelectionChanged += (_, _) => { if (updatingProviders || gameSelect.SelectedValue is not string id) return; selectedGame = id; selected = null; month.SelectedDate = null; Render(); SavePreferences(); };
        navigation.Children.Add(new Expander { Header = "自选游戏组合", Content = gameCombination, FontSize = 11, Margin = new Thickness(0, 5, 0, 7) });
        Section(navigation, "显示与筛选");
        mode = SidebarButton("展开月历", ToggleMode); navigation.Children.Add(mode);
        navigation.Children.Add(SidebarButton("近期活动", () => { showAll = false; selected = null; month.SelectedDate = null; Render(); SavePreferences(); }));
        allButton = SidebarButton("全部活动", () => { showAll = !showAll; selected = null; month.SelectedDate = null; Render(); SavePreferences(); }); navigation.Children.Add(allButton);
        densityButton = SidebarButton("图文模式", () => { dense = !dense; Render(); SavePreferences(); }); navigation.Children.Add(densityButton);
        Section(navigation, "资料与导出");
        navigation.Children.Add(SidebarButton("同步当前游戏", async () => await Mutate("/api/sync", new { game = selectedGame })));
        navigation.Children.Add(SidebarButton("完整时间轴 ↗", () => Open(address + "?game=" + selectedGame)));
        navigation.Children.Add(SidebarButton("导出日历 .ics", async () => await Export()));
        navigation.Children.Add(SidebarButton("系统日历说明", ExplainIntegration));
        BuildDesktopSettings();
        var bottom = new StackPanel { Margin = new Thickness(0, 9, 0, 0) };
        var feedback = Text("", 10); feedback.Foreground = Muted; feedback.MaxHeight = 62; feedback.Margin = new Thickness(0, 0, 0, 7);
        feedback.SetBinding(TextBlock.TextProperty, new Binding(nameof(TextBlock.Text)) { Source = status }); bottom.Children.Add(feedback);
        bottom.Children.Add(SidebarButton("收起到托盘", () => Hide())); Grid.SetRow(bottom, 3); sidebarPanel.Children.Add(bottom);
        layers.Children.Add(sidebarPanel);
    }

    void BuildDesktopSettings()
    {
        Section(desktopSettings, "桌面外观");
        bubbleToggle.Content = "仅显示活动气泡"; bubbleToggle.Margin = new Thickness(0, 2, 0, 8); bubbleToggle.IsChecked = bubbleMode;
        bubbleToggle.Click += (_, _) => SetBubbleMode(bubbleToggle.IsChecked == true, true); desktopSettings.Children.Add(bubbleToggle);
        var help = Text("收起侧栏后只保留活动卡片。右键气泡或托盘可重新打开设置，拖动卡片空白处可移动。", 10); help.Foreground = Muted; help.Margin = new Thickness(0, 0, 0, 12); desktopSettings.Children.Add(help);
        opacityLabel.FontSize = 11; desktopSettings.Children.Add(opacityLabel);
        ConfigureSlider(opacitySlider, 35, 100, cardOpacity * 100, 1); desktopSettings.Children.Add(opacitySlider);
        opacitySlider.ValueChanged += (_, _) => { if (updatingSettings) return; cardOpacity = opacitySlider.Value / 100; UpdateAppearanceControls(); Render(); SavePreferences(); };
        Section(desktopSettings, "挂件尺寸");
        widthLabel.FontSize = 11; desktopSettings.Children.Add(widthLabel); ConfigureSlider(widthSlider, 320, 640, widgetWidth, 10); desktopSettings.Children.Add(widthSlider);
        widthSlider.ValueChanged += (_, _) => { if (updatingSettings) return; widgetWidth = widthSlider.Value; ApplyPresentation(); SavePreferences(); };
        heightLabel.FontSize = 11; desktopSettings.Children.Add(heightLabel); ConfigureSlider(heightSlider, 320, 1000, widgetHeight, 10); desktopSettings.Children.Add(heightSlider);
        heightSlider.ValueChanged += (_, _) => { if (updatingSettings) return; widgetHeight = heightSlider.Value; ApplyPresentation(); SavePreferences(); };
        pin = SidebarButton("置顶", () => { Topmost = !Topmost; PinLabel(); SavePreferences(); }); desktopSettings.Children.Add(pin); PinLabel();
        Section(desktopSettings, "截止提醒");
        reminderToggle.Content = "开启活动截止提醒"; reminderToggle.IsEnabled = false; reminderToggle.Margin = new Thickness(0, 0, 0, 8);
        reminderToggle.Click += async (_, _) => { if (!updatingSettings) await Mutate("/api/settings", new { reminders_enabled = reminderToggle.IsChecked == true }); }; desktopSettings.Children.Add(reminderToggle);
        for (int index = 0; index < offsetToggles.Length; index++)
        {
            var toggle = offsetToggles[index]; toggle.Content = index == 0 ? "提前 3 天" : index == 1 ? "提前 24 小时" : "提前 1 小时";
            toggle.IsEnabled = false; toggle.Margin = new Thickness(12, 0, 0, 7);
            toggle.Click += async (_, _) =>
            {
                if (updatingSettings) return;
                var offsets = new System.Collections.Generic.List<int>();
                for (int i = 0; i < offsetToggles.Length; i++) if (offsetToggles[i].IsChecked == true) offsets.Add(reminderOffsets[i]);
                await Mutate("/api/settings", new { reminder_offsets = offsets });
            }; desktopSettings.Children.Add(toggle);
        }
        nativeToggle.Content = "启用 Windows 通知"; nativeToggle.Margin = new Thickness(0, 5, 0, 8); nativeToggle.IsEnabled = false;
        nativeToggle.Click += async (_, _) => { if (!updatingSettings) await Mutate("/api/settings", new { native_notifications = nativeToggle.IsChecked == true }); }; desktopSettings.Children.Add(nativeToggle);
        desktopSettings.Children.Add(SidebarButton("发送测试提醒", async () => await Mutate("/api/notifications/test", new { })));
        var note = Text("提醒需要程序保持运行；关闭窗口会收起到托盘。", 10); note.Foreground = Muted; desktopSettings.Children.Add(note);
        Section(desktopSettings, "程序"); desktopSettings.Children.Add(SidebarButton("退出游时", Exit));
    }

    static void ConfigureSlider(Slider slider, double min, double max, double value, double step)
    {
        slider.Minimum = min; slider.Maximum = max; slider.Value = value; slider.TickFrequency = step; slider.IsSnapToTickEnabled = true;
        slider.Margin = new Thickness(0, 7, 0, 13); slider.SmallChange = step; slider.LargeChange = step * 5;
    }
    void UpdateReminderControls(JsonElement settings)
    {
        updatingSettings = true;
        try
        {
            reminderToggle.IsEnabled = nativeToggle.IsEnabled = true;
            reminderToggle.IsChecked = settings.GetProperty("reminders_enabled").GetBoolean();
            nativeToggle.IsChecked = settings.GetProperty("native_notifications").GetBoolean();
            var offsets = new System.Collections.Generic.HashSet<int>(); foreach (var offset in settings.GetProperty("reminder_offsets").EnumerateArray()) offsets.Add(offset.GetInt32());
            for (int i = 0; i < offsetToggles.Length; i++) { offsetToggles[i].IsEnabled = true; offsetToggles[i].IsChecked = offsets.Contains(reminderOffsets[i]); }
        }
        finally { updatingSettings = false; }
    }
    void UpdateAppearanceControls()
    {
        updatingSettings = true;
        try
        {
            bubbleToggle.IsChecked = bubbleMode; opacitySlider.Value = cardOpacity * 100; opacitySlider.IsEnabled = bubbleMode;
            opacityLabel.Text = $"气泡底色不透明度 · {cardOpacity:P0}";
            widthLabel.Text = $"气泡列宽 · {widgetWidth:0}"; heightLabel.Text = $"挂件高度 · {widgetHeight:0}";
            widthSlider.Value = widgetWidth; heightSlider.Value = widgetHeight;
            navigationTab.Background = settingsOpen ? Brushes.Transparent : Green; settingsTab.Background = settingsOpen ? Green : Brushes.Transparent;
        }
        finally { updatingSettings = false; }
    }
    void PinLabel() => pin.Content = Topmost ? "已置顶 · 点击取消" : "置顶显示";
    double VisibleSidebarWidth => bubbleMode && !sidebarOpen ? 0 : sidebarOpen ? PanelWidth : RailWidth;
    void OpenSidebar(bool settings)
    {
        sidebarOpen = true; settingsOpen = settings; ApplyPresentation(); Show(); Activate(); SavePreferences();
    }
    void SetBubbleMode(bool enabled, bool keepSidebar = false)
    {
        bubbleMode = enabled;
        if (enabled) { compact = true; selected = null; month.SelectedDate = null; }
        if (!keepSidebar) sidebarOpen = false;
        ApplyPresentation(); Render(); SavePreferences();
    }
    void ApplyPresentation()
    {
        if (!appearanceReady || applyingAppearance) return;
        applyingAppearance = true;
        try
        {
            var area = SystemParameters.WorkArea; double right = Left + Width;
            sidebarColumn.Width = new GridLength(VisibleSidebarWidth);
            sidebar.Visibility = VisibleSidebarWidth == 0 ? Visibility.Collapsed : Visibility.Visible;
            sidebarRail.Visibility = sidebarOpen ? Visibility.Collapsed : Visibility.Visible; sidebarPanel.Visibility = sidebarOpen ? Visibility.Visible : Visibility.Collapsed;
            navigation.Visibility = settingsOpen ? Visibility.Collapsed : Visibility.Visible; desktopSettings.Visibility = settingsOpen ? Visibility.Visible : Visibility.Collapsed;
            heading.Visibility = summaryPanel.Visibility = footer.Visibility = bubbleMode ? Visibility.Collapsed : Visibility.Visible;
            month.Visibility = !bubbleMode && !compact ? Visibility.Visible : Visibility.Collapsed;
            desktopShell.Background = bubbleMode ? Brushes.Transparent : Brush("#F6F8F1"); desktopShell.BorderThickness = new Thickness(bubbleMode ? 0 : 1);
            desktopShell.Padding = new Thickness(bubbleMode ? 2 : 16);
            ResizeMode = bubbleMode ? ResizeMode.NoResize : ResizeMode.CanResizeWithGrip;
            cardScroll.VerticalScrollBarVisibility = bubbleMode ? ScrollBarVisibility.Hidden : ScrollBarVisibility.Auto;
            MinWidth = (compact ? 320 : 640) + VisibleSidebarWidth; MinHeight = 320;
            Width = Math.Min(area.Width, (compact ? widgetWidth : calendarWidth) + VisibleSidebarWidth);
            Height = Math.Min(area.Height, compact ? widgetHeight : calendarHeight);
            Left = Math.Clamp(right - Width, area.Left, Math.Max(area.Left, area.Right - Width));
            Top = Math.Clamp(Top, area.Top, Math.Max(area.Top, area.Bottom - Height));
            mode.Content = compact ? "展开月历" : "桌面挂件";
            if (bubbleMenu != null) bubbleMenu.Checked = bubbleMode;
            UpdateAppearanceControls();
        }
        finally { applyingAppearance = false; }
    }
    void TrackWindowSize()
    {
        if (!appearanceReady || applyingAppearance) return;
        if (compact) { widgetWidth = Math.Clamp(Width - VisibleSidebarWidth, 320, 640); widgetHeight = Math.Clamp(Height, 320, 1000); }
        else { calendarWidth = Width - VisibleSidebarWidth; calendarHeight = Height; }
        UpdateAppearanceControls(); SavePreferences();
    }
    void ToggleMode() => ShowMode(!compact);
    void ShowMode(bool mini)
    {
        compact = mini; if (!mini) bubbleMode = false;
        selected = null; month.SelectedDate = null;
        ApplyPresentation(); Show(); Activate(); Render(); SavePreferences();
    }
    Brush CardBackground() => bubbleMode ? new SolidColorBrush(Color.FromArgb((byte)Math.Round(cardOpacity * 255), 255, 255, 255)) : Brushes.White;
    Border MessageBubble(string message)
    {
        var bubble = new Border { Background = CardBackground(), CornerRadius = new CornerRadius(8), Padding = new Thickness(16), Margin = new Thickness(0, 0, 0, 8), Child = Text(message, 12) };
        AttachBubbleDrag(bubble); return bubble;
    }
    void AttachBubbleDrag(Border card)
    {
        card.ToolTip = "右键打开侧边栏与设置 · 拖动空白处移动挂件";
        card.PreviewMouseLeftButtonDown += (_, e) =>
        {
            if (e.LeftButton != MouseButtonState.Pressed) return;
            DependencyObject? node = e.OriginalSource as DependencyObject;
            while (node != null && node != card)
            {
                if (node is ButtonBase or Slider or TextBoxBase or ComboBox or ScrollBar) return;
                node = node is Visual or System.Windows.Media.Media3D.Visual3D ? VisualTreeHelper.GetParent(node) : LogicalTreeHelper.GetParent(node);
            }
            DragMove(); e.Handled = true;
        };
    }

    static double Number(JsonElement p, string key, double fallback, double min, double max)
        => p.TryGetProperty(key, out var value) && value.TryGetDouble(out var number) && double.IsFinite(number) ? Math.Clamp(number, min, max) : fallback;
    static bool Flag(JsonElement p, string key, bool fallback) => p.TryGetProperty(key, out var value) && value.ValueKind is JsonValueKind.True or JsonValueKind.False ? value.GetBoolean() : fallback;
    void LoadPreferences()
    {
        try
        {
            using var doc = JsonDocument.Parse(File.ReadAllText(preferences)); var p = doc.RootElement; var area = SystemParameters.WorkArea;
            widgetWidth = Number(p, "widget_width", widgetWidth, 320, 640); widgetHeight = Number(p, "widget_height", widgetHeight, 320, 1000);
            calendarWidth = Number(p, "calendar_width", calendarWidth, 640, area.Width); calendarHeight = Number(p, "calendar_height", calendarHeight, 320, area.Height);
            cardOpacity = Number(p, "card_opacity", .85, .35, 1); bubbleMode = Flag(p, "bubble_mode", false);
            compact = bubbleMode || Flag(p, "compact", true); dense = Flag(p, "dense", true); showAll = Flag(p, "show_all", false);
            sidebarOpen = !bubbleMode && Flag(p, "sidebar_open", false); Topmost = Flag(p, "topmost", false);
            Width = (compact ? widgetWidth : calendarWidth) + VisibleSidebarWidth; Height = compact ? widgetHeight : calendarHeight;
            Left = Number(p, "left", Left, area.Left, Math.Max(area.Left, area.Right - Width)); Top = Number(p, "top", Top, area.Top, Math.Max(area.Top, area.Bottom - Height));
            if (p.TryGetProperty("game", out var game) && game.ValueKind == JsonValueKind.String && game.GetString() is string id && id.Length <= 64) selectedGame = id;
        }
        catch { /* Older or damaged preferences use the defaults. */ }
    }
    void SavePreferences()
    {
        if (!appearanceReady) return;
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(preferences)!);
            string temporary = preferences + ".tmp";
            File.WriteAllText(temporary, JsonSerializer.Serialize(new { left = Left, top = Top, topmost = Topmost, game = selectedGame, bubble_mode = bubbleMode, card_opacity = cardOpacity, compact, dense, show_all = showAll, sidebar_open = !bubbleMode && sidebarOpen, widget_width = widgetWidth, widget_height = widgetHeight, calendar_width = calendarWidth, calendar_height = calendarHeight }));
            File.Move(temporary, preferences, true);
        }
        catch { /* Appearance persistence must not stop the calendar. */ }
    }
}
