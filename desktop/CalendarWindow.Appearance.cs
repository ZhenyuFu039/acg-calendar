using System;
using System.IO;
using System.Text.Json;
using System.Windows;
using System.Windows.Automation;
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
    readonly StackPanel sidebarRail = new(), navigation = new(), desktopSettings = new(), reminderSettings = new(), networkSettings = new();
    readonly Grid sidebarPanel = new();
    readonly CheckBox bubbleToggle = new(), nativeToggle = new(), reminderToggle = new();
    readonly Slider opacitySlider = new(), widthSlider = new(), heightSlider = new();
    readonly TextBlock opacityLabel = new(), widthLabel = new(), heightLabel = new();
    readonly CheckBox[] offsetToggles = [new(), new(), new()];
    readonly int[] reminderOffsets = [4320, 1440, 60];
    ScrollViewer cardScroll = null!, sidebarScroll = null!;
    // Each sidebar page keeps its own scroll position, so a page never opens at another page's depth.
    readonly System.Collections.Generic.Dictionary<string, double> sidebarOffsets = new();
    FrameworkElement heading = null!, summaryPanel = null!, footer = null!;
    Button navigationTab = null!, settingsTab = null!, remindersTab = null!, networkTab = null!, collapseSidebar = null!;
    readonly TextBlock reminderScopeLine = Text("提醒的游戏（网页与桌面共用）", 11);
    string sidebarPage = "calendar";
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
        var titleArea = new StackPanel { Margin = new Thickness(0, 0, 0, 12) }; heading = titleArea;
        var title = new DockPanel();
        ReadSystemTextScale(); BuildShortLayoutControls();
        var close = closeButton = Button("×", CloseToDesktop); close.ToolTip = "关闭主窗口，按设置保留桌面挂件或收起托盘";
        AutomationProperties.SetName(close, "关闭主窗口");
        DockPanel.SetDock(close, Dock.Right); title.Children.Add(close);
        title.Children.Add(brandIcon = new Image { Source = Icon, Width = 33, Height = 33, Margin = new Thickness(0, 1, 9, 0), VerticalAlignment = VerticalAlignment.Top, IsHitTestVisible = false });
        var brand = new StackPanel(); brand.Children.Add(brandTitle = Text("// PLAYTIME", 22, true));
        clock.FontSize = 10; clock.Foreground = Muted; clock.Margin = new Thickness(0, 4, 0, 0); brand.Children.Add(clock);
        brand.MouseLeftButtonDown += (_, e) => { if (e.ClickCount == 2) ToggleMode(); else DragMove(); };
        title.Children.Add(brand); titleArea.Children.Add(title);
        var officialTimeNotice = officialNotice = Text("具体活动情况请参考游戏官方时间", 10); officialTimeNotice.Foreground = Muted; officialTimeNotice.TextAlignment = TextAlignment.Right; officialTimeNotice.Margin = new Thickness(0, 7, 0, 0);
        titleArea.Children.Add(officialTimeNotice); layout.Children.Add(titleArea);
        var summary = new StackPanel { Margin = new Thickness(0, 0, 0, 12) }; summaryPanel = summary;
        subtitle.FontSize = 16; subtitle.FontWeight = FontWeights.SemiBold; subtitle.Text = "正在启动本地日历…"; summary.Children.Add(subtitle);
        summary.Children.Add(nativeFocusLabel); nativeDeadlineBand.Margin = new Thickness(0,5,0,0); summary.Children.Add(nativeDeadlineBand);
        rewardHeader.Orientation = Orientation.Horizontal; rewardHeader.Margin = new Thickness(0, 5, 0, 0);
        rewardHeader.ToolTip = "终末地活动已核实小计，不含地图、信用商店、维护补偿及通行证。完整明细见时间轴。";
        if (RewardIcon() is ImageSource currency) rewardHeader.Children.Add(new Image { Source = currency, Width = 25, Height = 25 });
        rewardTotal.FontSize = 11; rewardTotal.Foreground = Brush("#805B42"); rewardTotal.VerticalAlignment = VerticalAlignment.Center;
        rewardHeader.Children.Add(rewardTotal); summary.Children.Add(rewardHeader); Grid.SetRow(summary, 1); layout.Children.Add(summary);
        content.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto }); content.ColumnDefinitions.Add(new ColumnDefinition());
        month.Margin = new Thickness(0, 0, 16, 0); month.VerticalAlignment = VerticalAlignment.Top;
        month.SelectedDatesChanged += async (_, _) => { if (updatingFocus) return; selected = month.SelectedDate; Render(); await (dateUpdate = SaveDateFocus(selected)); };
        content.Children.Add(month);
        cardScroll = new ScrollViewer { Content = cards, VerticalScrollBarVisibility = ScrollBarVisibility.Auto, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled, Background = null, PanningMode = PanningMode.VerticalOnly };
        Grid.SetColumn(cardScroll, 1); content.Children.Add(cardScroll); Grid.SetRow(content, 2); layout.Children.Add(content);
        status.FontSize = 10; status.Foreground = Muted; status.TextWrapping = TextWrapping.Wrap; status.Margin = new Thickness(0, 10, 0, 0); footer = status;
        Grid.SetRow(status, 3); layout.Children.Add(status); desktopShell.Child = layout;
        Grid.SetColumn(desktopShell, 1); desktopLayout.Children.Add(desktopShell); BuildInteractionShell();
        var menu = new ContextMenu();
        AddContext(menu, "打开侧边栏", () => OpenSidebar(false));
        AddContext(menu, "桌面设置 / 气泡透明度", () => OpenSidebar(true));
        AddContext(menu, "切换普通 / 透明气泡模式", () => SetBubbleMode(!bubbleMode));
        AddContext(menu, "收起到托盘", HideToTray);
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
        title.SetValue(MutedRoleProperty, true);
    }
    void BuildSidebar()
    {
        sidebar.Background = Brush("#EBF0E3"); sidebar.BorderBrush = Brush("#DDE4D3"); sidebar.BorderThickness = new Thickness(0, 0, 1, 0);
        var layers = new Grid(); sidebar.Child = layers;
        sidebarRail.Margin = new Thickness(5, 10, 5, 8);
        sidebarRail.Children.Add(new Image { Source = Icon, Width = 30, Height = 30, Margin = new Thickness(0, 0, 0, 12) });
        foreach (var item in new (string Label, string Tip, Action Action)[] {
            ("更多", "打开侧边栏：游戏、筛选、提醒、采集", () => OpenSidebar(false)), ("月历", "切换挂件 / 月历", ToggleMode),
            ("同步", "同步当前游戏组合", async () => await SyncNow()),
            ("外观", "窗口模式、尺寸与主题", () => OpenSidebar(true)) })
        {
            var button = Button(item.Label, item.Action); button.ToolTip = item.Tip; button.FontSize = 11; button.Margin = new Thickness(0, 0, 0, 8); button.Padding = new Thickness(2, 8, 2, 8);
            AutomationProperties.SetName(button, item.Tip); sidebarRail.Children.Add(button);
        }
        layers.Children.Add(sidebarRail);
        sidebarPanel.Margin = new Thickness(12, 12, 12, 10);
        sidebarPanel.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        sidebarPanel.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        sidebarPanel.RowDefinitions.Add(new RowDefinition()); sidebarPanel.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        var title = new DockPanel();
        var close = collapseSidebar = Button("‹", () => { sidebarOpen = false; ApplyPresentation(); SavePreferences(); }); close.ToolTip = "收起侧边栏"; AutomationProperties.SetName(close, "收起侧边栏");
        DockPanel.SetDock(close, Dock.Right); title.Children.Add(close);
        title.Children.Add(new Image { Source = Icon, Width = 26, Height = 26, Margin = new Thickness(0, 0, 7, 0) }); title.Children.Add(Text("游时", 16, true)); sidebarPanel.Children.Add(title);
        var tabs = new UniformGrid { Columns = 2, Margin = new Thickness(0, 10, 0, 7) };
        navigationTab = Button("日历", () => OpenSidebar(false)); settingsTab = Button("设置", () => OpenSidebar(true)); tabs.Children.Add(navigationTab); tabs.Children.Add(settingsTab);
        settingsTab.Content = "外观";
        remindersTab = Button("提醒", () => OpenSidebarPage("reminders")); networkTab = Button("采集", () => OpenSidebarPage("network")); tabs.Children.Add(remindersTab); tabs.Children.Add(networkTab);
        Grid.SetRow(tabs, 1); sidebarPanel.Children.Add(tabs);
        var body = new Grid(); body.Children.Add(navigation); body.Children.Add(desktopSettings); body.Children.Add(reminderSettings); body.Children.Add(networkSettings);
        var scroll = sidebarScroll = new ScrollViewer { Content = body, VerticalScrollBarVisibility = ScrollBarVisibility.Auto, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled };
        Grid.SetRow(scroll, 2); sidebarPanel.Children.Add(scroll);
        Section(navigation, "我的游戏");
        gameSelect.DisplayMemberPath = "Label"; gameSelect.SelectedValuePath = "Id"; AutomationProperties.SetName(gameSelect, "我的游戏");
        gameSelect.Padding = new Thickness(3, 5, 3, 5); gameSelect.Margin = new Thickness(0, 0, 0, 4); navigation.Children.Add(gameSelect);
        gameSelect.SelectionChanged += async (_, _) => { if (updatingProviders || gameSelect.SelectedValue is not string id) return; selectedGame = id; Render(); SavePreferences(); if(csrf!="") await (scopeUpdate = SaveGameScope(id)); };
        navigation.Children.Add(new Expander { Header = "自选游戏组合", Content = gameCombination, FontSize = 11, Margin = new Thickness(0, 5, 0, 7) });
        var loadCalendar = SidebarButton("＋ 加载新的日历", OpenPluginDirectory);
        loadCalendar.Name = "LoadCalendarButton"; loadCalendar.ToolTip = "打开插件目录：" + PluginDirectory;
        navigation.Children.Add(loadCalendar);
        var pluginHelp = Text("放入完整插件文件夹以安装，移出以卸载；在下方插件列表点“重启游时”应用。", 10);
        pluginHelp.Foreground = Muted; pluginHelp.TextWrapping = TextWrapping.Wrap; pluginHelp.SetValue(MutedRoleProperty, true);
        navigation.Children.Add(pluginHelp);
        BuildPluginControls();
        Section(navigation, "显示与筛选");
        mode = SidebarButton("切换到月历", ToggleMode); navigation.Children.Add(mode);
        navigation.Children.Add(SidebarButton("近期活动", async () => { showAll = false; await SaveDateFocus(null); Render(); SavePreferences(); }));
        allButton = SidebarButton("全部活动", async () => { showAll = !showAll; await SaveDateFocus(null); Render(); SavePreferences(); }); navigation.Children.Add(allButton);
        densityButton = SidebarButton("图文模式", () => { dense = !dense; Render(); SavePreferences(); }); navigation.Children.Add(densityButton);
        BuildPlannerControls();
        Section(navigation, "资料与导出");
        navigation.Children.Add(SidebarButton("⟳ 同步当前组合", async () => await SyncNow()));
        navigation.Children.Add(SidebarButton("完整日历 ↗（浏览器）", () => Open(FullCalendarUrl())));
        navigation.Children.Add(SidebarButton("导出日历 .ics", async () => await Export()));
        navigation.Children.Add(SidebarButton("系统日历说明", ExplainIntegration));
        BuildDesktopSettings();
        var bottom = new StackPanel { Margin = new Thickness(0, 9, 0, 0) };
        var feedback = Text("", 10); feedback.Foreground = Muted; feedback.MaxHeight = 62; feedback.Margin = new Thickness(0, 0, 0, 7);
        feedback.SetBinding(TextBlock.TextProperty, new Binding(nameof(TextBlock.Text)) { Source = status }); bottom.Children.Add(feedback);
        bottom.Children.Add(SidebarButton("收起到托盘", HideToTray)); Grid.SetRow(bottom, 3); sidebarPanel.Children.Add(bottom);
        layers.Children.Add(sidebarPanel);
    }

    void BuildDesktopSettings()
    {
        BuildWorkspaceSettings();
        var saving = Text("本页改动即时生效并自动保存；颜色细节与模块布局在网页编辑，需在网页点“应用”。", 10); saving.Foreground = Muted; saving.Margin = new Thickness(0, 4, 0, 0); desktopSettings.Children.Add(saving);
        Section(desktopSettings, "窗口模式");
        bubbleToggle.Content = "仅显示活动气泡（透明）"; bubbleToggle.Margin = new Thickness(0, 2, 0, 8); bubbleToggle.IsChecked = bubbleMode;
        bubbleToggle.Click += (_, _) => SetBubbleMode(bubbleToggle.IsChecked == true, true); desktopSettings.Children.Add(bubbleToggle);
        var help = Text("下方快捷栏可直接切换气泡、同步、打开完整日历和置顶；拖动卡片空白处移动挂件。", 10); help.Foreground = Muted; help.Margin = new Thickness(0, 0, 0, 12); desktopSettings.Children.Add(help);
        opacityLabel.FontSize = 11; desktopSettings.Children.Add(opacityLabel);
        ConfigureSlider(opacitySlider, 35, 100, cardOpacity * 100, 1); desktopSettings.Children.Add(opacitySlider); AutomationProperties.SetLabeledBy(opacitySlider, opacityLabel);
        opacitySlider.ValueChanged += (_, _) => { if (updatingSettings) return; cardOpacity = opacitySlider.Value / 100; UpdateAppearanceControls(); Render(); SavePreferences(); Saved($"气泡不透明度 {cardOpacity:P0}"); };
        Section(desktopSettings, "挂件尺寸");
        widthLabel.FontSize = 11; desktopSettings.Children.Add(widthLabel); ConfigureSlider(widthSlider, 320, 640, widgetWidth, 10); desktopSettings.Children.Add(widthSlider); AutomationProperties.SetLabeledBy(widthSlider, widthLabel);
        widthSlider.ValueChanged += (_, _) => { if (updatingSettings) return; widgetWidth = widthSlider.Value; ApplyPresentation(); SavePreferences(); Saved($"挂件宽度 {widgetWidth:0}"); };
        heightLabel.FontSize = 11; desktopSettings.Children.Add(heightLabel); ConfigureSlider(heightSlider, 320, 1000, widgetHeight, 10); desktopSettings.Children.Add(heightSlider); AutomationProperties.SetLabeledBy(heightSlider, heightLabel);
        heightSlider.ValueChanged += (_, _) => { if (updatingSettings) return; widgetHeight = heightSlider.Value; ApplyPresentation(); SavePreferences(); Saved($"挂件高度 {widgetHeight:0}"); };
        BuildResidentSettings();
        BuildThemeSettings();
        Section(reminderSettings, "截止提醒");
        reminderToggle.Content = "开启活动截止提醒"; reminderToggle.IsEnabled = false; reminderToggle.Margin = new Thickness(0, 0, 0, 8);
        reminderToggle.Click += async (_, _) => { if (!updatingSettings) await Mutate("/api/settings", new { reminders_enabled = reminderToggle.IsChecked == true }); }; reminderSettings.Children.Add(reminderToggle);
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
            }; reminderSettings.Children.Add(toggle);
        }
        nativeToggle.Content = "启用 Windows 通知"; nativeToggle.Margin = new Thickness(0, 5, 0, 8); nativeToggle.IsEnabled = false;
        nativeToggle.Click += async (_, _) => { if (!updatingSettings) await Mutate("/api/settings", new { native_notifications = nativeToggle.IsChecked == true }); }; reminderSettings.Children.Add(nativeToggle);
        reminderSettings.Children.Add(SidebarButton("发送测试提醒", async () => await Mutate("/api/notifications/test", new { })));
        // The reminder game scope is shared with the web page and edited there.
        reminderScopeLine.Margin = new Thickness(0, 4, 0, 6); reminderSettings.Children.Add(reminderScopeLine);
        BuildReminderScopeControls();
        reminderSettings.Children.Add(SidebarButton("提醒与收件箱 ↗", () => Open(address + "?appearance=reminders")));
        var note = Text("提醒需要程序保持运行：桌面挂件或托盘中的游时都会继续提醒，只有“退出游时”才停止。", 10); note.Foreground = Muted; reminderSettings.Children.Add(note);
        Section(desktopSettings, "程序"); desktopSettings.Children.Add(SidebarButton("收起到托盘", HideToTray)); desktopSettings.Children.Add(SidebarButton("退出游时（停止提醒）", Exit));
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
            foreach (var pair in new[] { (navigationTab, "calendar"), (settingsTab, "appearance"), (remindersTab, "reminders"), (networkTab, "network") }) pair.Item1.Background = sidebarPage == pair.Item2 ? Green : Surface;
        }
        finally { updatingSettings = false; }
    }
    void PinLabel() => UpdatePinButtons();
    double VisibleSidebarWidth => (bubbleMode || compact) && !sidebarOpen ? 0 : sidebarOpen ? workspacePanelWidth : RailWidth;
    void OpenSidebar(bool settings)
    {
        OpenSidebarPage(settings ? "appearance" : "calendar");
    }
    void SetBubbleMode(bool enabled, bool keepSidebar = false)
    {
        bool changed = bubbleMode != enabled;
        bubbleMode = enabled;
        if (enabled) compact = true;
        if (!keepSidebar) sidebarOpen = false;
        ApplyPresentation(); Render(); SavePreferences();
        if (changed) ShowHint(enabled ? "已切换为透明气泡。点“✓ 气泡”回到普通列表。" : "已回到普通列表。");
    }
    void HideToTray() { Hide(); TrayHint(); }
    void ApplyPresentation()
    {
        if (!appearanceReady || applyingAppearance || edgeHidden || edgeTransition) return;
        applyingAppearance = true;
        try
        {
            var area = DesktopWorkArea(); double right = Left + Width;
            sidebarColumn.Width = new GridLength(VisibleSidebarWidth);
            sidebar.Visibility = VisibleSidebarWidth == 0 ? Visibility.Collapsed : Visibility.Visible;
            sidebarRail.Visibility = sidebarOpen ? Visibility.Collapsed : Visibility.Visible; sidebarPanel.Visibility = sidebarOpen ? Visibility.Visible : Visibility.Collapsed;
            navigation.Visibility = sidebarPage == "calendar" ? Visibility.Visible : Visibility.Collapsed; desktopSettings.Visibility = sidebarPage == "appearance" ? Visibility.Visible : Visibility.Collapsed;
            reminderSettings.Visibility = sidebarPage == "reminders" ? Visibility.Visible : Visibility.Collapsed; networkSettings.Visibility = sidebarPage == "network" ? Visibility.Visible : Visibility.Collapsed;
            heading.Visibility = summaryPanel.Visibility = footer.Visibility = bubbleMode ? Visibility.Collapsed : Visibility.Visible;
            month.Visibility = !bubbleMode && !compact ? Visibility.Visible : Visibility.Collapsed;
            desktopShell.Background = bubbleMode ? Brushes.Transparent : DesktopPageBrush(); desktopShell.BorderBrush = ThemeBorder; desktopShell.BorderThickness = new Thickness(bubbleMode ? 0 : themeBorderWidth);
            desktopShell.Padding = new Thickness(bubbleMode ? 2 : 16);
            ResizeMode = bubbleMode ? ResizeMode.NoResize : ResizeMode.CanResizeWithGrip;
            cardScroll.VerticalScrollBarVisibility = bubbleMode ? ScrollBarVisibility.Hidden : ScrollBarVisibility.Auto;
            MinWidth = (compact ? 320 : 640) + VisibleSidebarWidth; MinHeight = 320;
            Width = Math.Min(area.Width, (compact ? widgetWidth : calendarWidth) + VisibleSidebarWidth);
            Height = Math.Min(area.Height, compact ? widgetHeight : calendarHeight);
            Left = Math.Clamp(right - Width, area.Left, Math.Max(area.Left, area.Right - Width));
            Top = Math.Clamp(Top, area.Top, Math.Max(area.Top, area.Bottom - Height));
            mode.Content = compact ? "切换到月历" : "切换到桌面挂件";
            if (bubbleMenu != null) bubbleMenu.Checked = bubbleMode;
            UpdateAppearanceControls();
            ApplyWorkspaceLayout();
            ApplyShortLayout();
            UpdateInteractionChrome();
            desktopLayout.UpdateLayout();
            ApplyWorkspaceVisuals(desktopLayout);
        }
        finally { applyingAppearance = false; }
    }
    void TrackWindowSize()
    {
        if (!appearanceReady || applyingAppearance || edgeTransition || edgeHidden) return;
        if (compact) { widgetWidth = Math.Clamp(Width - VisibleSidebarWidth, 320, 640); widgetHeight = Math.Clamp(Height, 320, 1000); }
        else { calendarWidth = Width - VisibleSidebarWidth; calendarHeight = Height; }
        UpdateAppearanceControls(); SavePreferences();
    }
    void ToggleMode() => ShowMode(!compact);
    void ShowMode(bool mini)
    {
        RevealEdge();
        compact = mini; if (!mini) bubbleMode = false;
        ApplyPresentation(); Show(); Activate(); Render(); SavePreferences();
    }
    Brush CardBackground() => new SolidColorBrush(Color.FromArgb((byte)Math.Round((bubbleMode ? cardOpacity : surfaceOpacity) * 255), Surface.Color.R, Surface.Color.G, Surface.Color.B));
    Border MessageBubble(string message)
    {
        var bubble = new Border { Background = CardBackground(), CornerRadius = new CornerRadius(8), Padding = new Thickness(16), Margin = new Thickness(0, 0, 0, 8), Child = Text(message, 12) };
        AttachBubbleDrag(bubble); return bubble;
    }
    void AttachBubbleDrag(Border card)
    {
        card.ToolTip = "拖动空白处移动挂件 · 下方“更多”或右键打开侧栏与设置";
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
            keepWidgetOnClose = Flag(p, "keep_widget_on_close", true); edgeAutoHide = Flag(p, "edge_auto_hide", false);
            Width = (compact ? widgetWidth : calendarWidth) + VisibleSidebarWidth; Height = compact ? widgetHeight : calendarHeight;
            Left = Number(p, "left", Left, area.Left, Math.Max(area.Left, area.Right - Width)); Top = Number(p, "top", Top, area.Top, Math.Max(area.Top, area.Bottom - Height));
            if (p.TryGetProperty("game", out var game) && game.ValueKind == JsonValueKind.String && game.GetString() is string id && id.Length <= 64) selectedGame = id;
        }
        catch { /* Older or damaged preferences use the defaults. */ }
    }
    void SavePreferences()
    {
        if (!appearanceReady || edgeTransition) return;
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(preferences)!);
            string temporary = preferences + ".tmp";
            File.WriteAllText(temporary, JsonSerializer.Serialize(new { left = edgeHidden ? expandedBounds.Left : Left, top = edgeHidden ? expandedBounds.Top : Top, topmost = Topmost, game = selectedGame, bubble_mode = bubbleMode, card_opacity = cardOpacity, compact, dense, show_all = showAll, sidebar_open = !bubbleMode && sidebarOpen, widget_width = widgetWidth, widget_height = widgetHeight, calendar_width = calendarWidth, calendar_height = calendarHeight, keep_widget_on_close = keepWidgetOnClose, edge_auto_hide = edgeAutoHide }));
            File.Move(temporary, preferences, true);
        }
        catch { /* Appearance persistence must not stop the calendar. */ }
    }
}
