using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Markup;

namespace Playtime;

public sealed partial class CalendarWindow
{
    readonly TextBlock networkStatus = new();
    readonly StackPanel personalPlansPanel = new();
    readonly ComboBox themePreset = new();
    readonly Dictionary<string, (bool Visible, bool Collapsed)> workspaceModules = new();
    readonly Dictionary<string, Brush> categoryBrushes = new();
    static SolidColorBrush Surface = new(Colors.White), PageSurface = new(Color.FromRgb(246,248,241)), ThemeBorder = new(Color.FromRgb(221,228,211));
    static SolidColorBrush AccentInk = new(Colors.Black), NavSurface = new(Colors.White), NavInk = new(Colors.Black), NavMuted = new(Colors.Gray), PanelAlt = new(Colors.WhiteSmoke), LinkInk = new(Colors.DarkOliveGreen);
    static SolidColorBrush TrackSurface = new(Colors.WhiteSmoke);
    static readonly DependencyProperty BaseFontProperty = DependencyProperty.RegisterAttached("BaseFont", typeof(double), typeof(CalendarWindow), new PropertyMetadata(double.NaN));
    static readonly DependencyProperty MutedRoleProperty = DependencyProperty.RegisterAttached("MutedRole", typeof(bool), typeof(CalendarWindow), new PropertyMetadata(false));
    JsonElement workspaceDefaults;
    string workspaceSignature = "", sidebarPosition = "left", calendarPosition = "left";
    string[] moduleOrder = ["sources", "rewards", "deadlines", "calendar", "personal-plans"];
    bool networkPaused, workspaceDark, changingWorkspace;
    double workspacePanelWidth = PanelWidth, themeRadius = 8, themeSpacing = 14, fontScale = 1, surfaceOpacity = 1;
    double themeControlRadius = 4, themeBorderWidth = 1, themeHeadingWeight = 800;
    string themeTexture = "none";
    double themeTextureOpacity = .03;

    void BuildWorkspaceSettings()
    {
        Resources["ControlCorner"] = new CornerRadius(4); Resources["FocusInk"] = Ink;
        Resources[typeof(Button)] = (Style)XamlReader.Parse("""
            <Style xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml" TargetType="Button">
              <Setter Property="Template"><Setter.Value><ControlTemplate TargetType="Button"><Border x:Name="ButtonSurface" Background="{TemplateBinding Background}" BorderBrush="{TemplateBinding BorderBrush}" BorderThickness="{TemplateBinding BorderThickness}" CornerRadius="{DynamicResource ControlCorner}" Padding="{TemplateBinding Padding}"><ContentPresenter HorizontalAlignment="{TemplateBinding HorizontalContentAlignment}" VerticalAlignment="Center" RecognizesAccessKey="True"/></Border><ControlTemplate.Triggers><Trigger Property="IsMouseOver" Value="True"><Setter TargetName="ButtonSurface" Property="Opacity" Value="0.8"/></Trigger><Trigger Property="IsKeyboardFocused" Value="True"><Setter TargetName="ButtonSurface" Property="BorderThickness" Value="2"/><Setter TargetName="ButtonSurface" Property="BorderBrush" Value="{DynamicResource FocusInk}"/></Trigger><Trigger Property="IsEnabled" Value="False"><Setter Property="Opacity" Value="0.5"/></Trigger></ControlTemplate.Triggers></ControlTemplate></Setter.Value></Setter>
            </Style>
            """);
        // The Windows default ComboBox template keeps a light background even
        // when its foreground changes. Use the validated window palette for both.
        Resources[typeof(ComboBox)] = (Style)XamlReader.Parse("""
            <Style xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml" TargetType="ComboBox">
              <Setter Property="Template"><Setter.Value><ControlTemplate TargetType="ComboBox">
                <Grid>
                  <Border x:Name="Frame" Background="{TemplateBinding Background}" BorderBrush="{TemplateBinding BorderBrush}" BorderThickness="1" CornerRadius="{DynamicResource ControlCorner}" Padding="8,5">
                    <DockPanel><TextBlock DockPanel.Dock="Right" Text="▾" Foreground="{TemplateBinding Foreground}" Margin="8,0,0,0"/><ContentPresenter x:Name="SelectionContent" Content="{Binding SelectedItem.Label, RelativeSource={RelativeSource TemplatedParent}}" VerticalAlignment="Center"/></DockPanel>
                  </Border>
                  <ToggleButton Focusable="False" IsChecked="{Binding IsDropDownOpen, RelativeSource={RelativeSource TemplatedParent}, Mode=TwoWay}">
                    <ToggleButton.Template><ControlTemplate TargetType="ToggleButton"><Border Background="Transparent"/></ControlTemplate></ToggleButton.Template>
                  </ToggleButton>
                  <Popup x:Name="PART_Popup" IsOpen="{TemplateBinding IsDropDownOpen}" Placement="Bottom" AllowsTransparency="True" Focusable="False">
                    <Border Background="{TemplateBinding Background}" TextElement.Foreground="{TemplateBinding Foreground}" BorderBrush="{TemplateBinding BorderBrush}" BorderThickness="1" MinWidth="{Binding ActualWidth, RelativeSource={RelativeSource TemplatedParent}}">
                      <ScrollViewer MaxHeight="300"><ItemsPresenter KeyboardNavigation.DirectionalNavigation="Contained"/></ScrollViewer>
                    </Border>
                  </Popup>
                </Grid>
              <ControlTemplate.Triggers><Trigger Property="IsKeyboardFocusWithin" Value="True"><Setter TargetName="Frame" Property="BorderThickness" Value="2"/><Setter TargetName="Frame" Property="BorderBrush" Value="{DynamicResource FocusInk}"/></Trigger></ControlTemplate.Triggers>
              </ControlTemplate></Setter.Value></Setter>
            </Style>
            """);
        Resources[typeof(Slider)] = (Style)XamlReader.Parse("""
            <Style xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml" TargetType="Slider">
              <Setter Property="Template"><Setter.Value><ControlTemplate TargetType="Slider"><Grid MinHeight="24" Background="Transparent">
                <Track x:Name="PART_Track" Minimum="{TemplateBinding Minimum}" Maximum="{TemplateBinding Maximum}" Value="{Binding Value, RelativeSource={RelativeSource TemplatedParent}, Mode=TwoWay}">
                  <Track.DecreaseRepeatButton><RepeatButton Command="Slider.DecreaseLarge"><RepeatButton.Template><ControlTemplate TargetType="RepeatButton"><Border Height="3" Background="{TemplateBinding Foreground}" VerticalAlignment="Center"/></ControlTemplate></RepeatButton.Template></RepeatButton></Track.DecreaseRepeatButton>
                  <Track.IncreaseRepeatButton><RepeatButton Command="Slider.IncreaseLarge"><RepeatButton.Template><ControlTemplate TargetType="RepeatButton"><Border Height="3" Background="{TemplateBinding Foreground}" Opacity="0.25" VerticalAlignment="Center"/></ControlTemplate></RepeatButton.Template></RepeatButton></Track.IncreaseRepeatButton>
                  <Track.Thumb><Thumb x:Name="SliderThumb" Width="10" Height="16"><Thumb.Template><ControlTemplate TargetType="Thumb"><Border Background="{TemplateBinding Foreground}"/></ControlTemplate></Thumb.Template></Thumb></Track.Thumb>
                </Track>
              </Grid><ControlTemplate.Triggers><Trigger Property="IsKeyboardFocused" Value="True"><Setter TargetName="SliderThumb" Property="Width" Value="15"/><Setter TargetName="SliderThumb" Property="Height" Value="20"/></Trigger></ControlTemplate.Triggers></ControlTemplate></Setter.Value></Setter>
            </Style>
            """);
        Resources[typeof(CheckBox)] = (Style)XamlReader.Parse("""
            <Style xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml" TargetType="CheckBox">
              <Setter Property="Template"><Setter.Value><ControlTemplate TargetType="CheckBox">
                <Grid Background="Transparent"><Grid.ColumnDefinitions><ColumnDefinition Width="Auto"/><ColumnDefinition Width="*"/></Grid.ColumnDefinitions>
                  <Border x:Name="Box" Width="18" Height="18" Margin="0,0,7,0" VerticalAlignment="Center" BorderBrush="{TemplateBinding Foreground}" BorderThickness="1" CornerRadius="3" Background="{TemplateBinding Background}">
                    <Path x:Name="CheckMark" Data="M2,7 L6,11 L13,2" Stroke="{TemplateBinding Foreground}" StrokeThickness="2.2" Margin="1" Visibility="Collapsed"/>
                  </Border>
                  <ContentPresenter Grid.Column="1" VerticalAlignment="Center" RecognizesAccessKey="True" Content="{TemplateBinding Content}" TextElement.Foreground="{TemplateBinding Foreground}"/>
                </Grid>
                <ControlTemplate.Triggers><Trigger Property="IsKeyboardFocused" Value="True"><Setter TargetName="Box" Property="BorderThickness" Value="2.5"/></Trigger><Trigger Property="IsChecked" Value="True"><Setter TargetName="CheckMark" Property="Visibility" Value="Visible"/></Trigger><Trigger Property="IsEnabled" Value="False"><Setter Property="Opacity" Value="0.5"/></Trigger></ControlTemplate.Triggers>
              </ControlTemplate></Setter.Value></Setter>
            </Style>
            """);
        BuildNetworkPage();
    }

    // Theme and layout come after the window controls on the appearance page.
    void BuildThemeSettings()
    {
        Section(desktopSettings, "主题与布局（高级）");
        System.Windows.Automation.AutomationProperties.SetName(themePreset, "主题"); themePreset.DisplayMemberPath = "Label"; themePreset.SelectedValuePath = "Id"; themePreset.Margin = new Thickness(0, 0, 0, 8);
        themePreset.SelectionChanged += async (_, _) => { if (!changingWorkspace && themePreset.SelectedValue is string id && workspaceDefaults.ValueKind == JsonValueKind.Object && workspaceDefaults.GetProperty("themes").TryGetProperty(id, out var preset)) await Mutate("/api/workspace", new { theme = preset }, true); };
        desktopSettings.Children.Add(themePreset);
        desktopSettings.Children.Add(SidebarButton("编辑模块布局 ↗", () => Open(address + "?appearance=layout")));
        desktopSettings.Children.Add(SidebarButton("自定义颜色与字号 ↗", () => Open(address + "?appearance=theme")));
        desktopSettings.Children.Add(SidebarButton("恢复默认布局", async () => { if (workspaceDefaults.ValueKind == JsonValueKind.Object) await Mutate("/api/workspace", new { layout = workspaceDefaults.GetProperty("layout") }, true); }));
    }

    void BuildNetworkPage()
    {
        Section(networkSettings, "联网采集状态");
        networkStatus.FontSize = 10; networkStatus.TextWrapping = TextWrapping.Wrap; networkStatus.Margin = new Thickness(0, 0, 0, 8); networkSettings.Children.Add(networkStatus);
        networkSettings.Children.Add(SidebarButton("暂停 / 恢复联网", async () => await Mutate("/api/network", new { paused = !networkPaused }, true)));
        networkSettings.Children.Add(SidebarButton("站点配额与冷却 ↗", () => Open(address + "?appearance=network")));
        networkSettings.Children.Add(SidebarButton("同步当前游戏", async () => await Mutate("/api/sync", new { game = selectedGame })));
        BuildSyncAndBackupControls();
    }

    bool ModuleVisible(string id) => !workspaceModules.TryGetValue(id, out var m) || (m.Visible && !m.Collapsed);

    void UpdateWorkspace(JsonElement state)
    {
        if (state.TryGetProperty("network", out var net))
        {
            networkPaused = net.GetProperty("paused").GetBoolean();
            var p = net.GetProperty("policy");
            networkStatus.Text = (networkPaused ? "联网已暂停，缓存和提醒继续运行。" : "联网已启用。") + $"\n自动复查 {state.GetProperty("settings").GetProperty("sync_interval_minutes").GetInt32() / 60} 小时后，另随机错峰 0–2 小时。\n同站至少间隔 {p.GetProperty("interval_seconds").GetInt32()} 秒，另加 0–5 秒错峰。\n每小时上限 {p.GetProperty("hourly_limit").GetInt32()} 次；倒计时在本地运行。";
            if (state.TryGetProperty("sync_schedule", out var schedules) && schedules.TryGetProperty(selectedGame, out var scheduled))
            {
                if (!scheduled.GetProperty("enabled").GetBoolean()) networkStatus.Text += "\n自动复查已由启动参数关闭。";
                else if (DateTimeOffset.TryParse(scheduled.GetProperty("next_check_at").GetString(), out var nextCheck)) networkStatus.Text += "\n当前游戏下次复查：" + nextCheck.ToLocalTime().ToString("MM/dd HH:mm");
            }
        }
        if (state.TryGetProperty("workspace_defaults", out var defaults)) workspaceDefaults = defaults.Clone();
        var workspace = state.GetProperty("workspace");
        string signature = workspace.GetProperty("layout").GetRawText() + workspace.GetProperty("theme").GetRawText();
        if (signature == workspaceSignature) return;
        CaptureTextRoles(desktopLayout);
        var theme = workspace.GetProperty("theme");
        Color ColorValue(string key) => (Color)ColorConverter.ConvertFromString(theme.GetProperty(key).GetString()!);
        Ink = new SolidColorBrush(ColorValue("text")); Muted = new SolidColorBrush(ColorValue("muted"));
        Foreground = Ink;
        Surface = new SolidColorBrush(ColorValue("surface")); PageSurface = new SolidColorBrush(ColorValue("background")); ThemeBorder = new SolidColorBrush(ColorValue("border"));
        Green = new SolidColorBrush(ColorValue("accent")); AccentInk = new SolidColorBrush(ColorValue("accent_text"));
        NavSurface = new SolidColorBrush(ColorValue("nav_background")); NavInk = new SolidColorBrush(ColorValue("nav_text"));
        PanelAlt = new SolidColorBrush(ColorValue("panel_alt")); LinkInk = new SolidColorBrush(ColorValue("link"));
        TrackSurface = new SolidColorBrush(ColorValue("bar_background"));
        foreach (string category in new[] { "event", "gacha", "exchange", "update", "maintenance" }) categoryBrushes[category] = new SolidColorBrush(ColorValue(category + "_color"));
        NavMuted = new SolidColorBrush(Color.FromRgb((byte)(NavInk.Color.R*.65+NavSurface.Color.R*.35),(byte)(NavInk.Color.G*.65+NavSurface.Color.G*.35),(byte)(NavInk.Color.B*.65+NavSurface.Color.B*.35)));
        workspaceDark = PageSurface.Color.R*.2126 + PageSurface.Color.G*.7152 + PageSurface.Color.B*.0722 < 128;
        themeRadius = theme.GetProperty("desktop_radius").GetDouble(); themeSpacing = theme.GetProperty("spacing").GetDouble(); fontScale = theme.GetProperty("font_size").GetDouble() / 14; surfaceOpacity = theme.GetProperty("card_opacity").GetDouble();
        themeControlRadius = theme.GetProperty("desktop_control_radius").GetDouble(); themeBorderWidth = theme.GetProperty("border_width").GetDouble(); themeHeadingWeight = theme.GetProperty("heading_weight").GetDouble();
        themeTexture = theme.GetProperty("texture").GetString()!; themeTextureOpacity = theme.GetProperty("texture_opacity").GetDouble() / 100;
        Resources["ControlCorner"] = new CornerRadius(themeControlRadius); Resources["FocusInk"] = Ink;
        changingWorkspace = true;
        var names = workspaceDefaults.GetProperty("theme_names");
        var choices = workspaceDefaults.GetProperty("themes").EnumerateObject().Select(t => new GameChoice(t.Name, names.GetProperty(t.Name).GetString()!)).ToList();
        string presetId = theme.GetProperty("preset").GetString()!;
        var baseTheme = workspaceDefaults.GetProperty("themes").GetProperty(presetId);
        bool customized = theme.EnumerateObject().Any(t => !JsonElement.DeepEquals(t.Value, baseTheme.GetProperty(t.Name)));
        if (customized) choices.Add(new GameChoice("custom", "自定义 · 当前"));
        themePreset.ItemsSource = choices; themePreset.SelectedValue = customized ? "custom" : presetId;
        changingWorkspace = false;
        Resources[SystemColors.WindowBrushKey] = Surface; Resources[SystemColors.WindowTextBrushKey] = Ink; Resources[SystemColors.ControlBrushKey] = Surface; Resources[SystemColors.ControlTextBrushKey] = Ink;
        var layout = workspace.GetProperty("layout");
        workspaceModules.Clear(); moduleOrder = layout.GetProperty("modules").EnumerateArray().Select(m => { string id = m.GetProperty("id").GetString()!; workspaceModules[id] = (m.GetProperty("visible").GetBoolean(),m.GetProperty("collapsed").GetBoolean()); return id; }).ToArray();
        sidebarPosition = layout.GetProperty("sidebar").GetProperty("position").GetString()!;
        workspacePanelWidth = layout.GetProperty("sidebar").GetProperty("width").GetDouble(); calendarPosition = layout.GetProperty("desktop_calendar").GetString()!;
        ApplyPresentation();
        workspaceSignature = signature;
    }

    void ApplyWorkspaceLayout()
    {
        if (desktopShell.Child is not Grid grid) return;
        if (rewardHeader.Parent is Panel parent && parent != grid) { parent.Children.Remove(rewardHeader); grid.Children.Add(rewardHeader); }
        if (!grid.Children.Contains(personalPlansPanel)) grid.Children.Add(personalPlansPanel);
        var modules = new Dictionary<string, FrameworkElement> { ["calendar"] = content, ["sources"] = footer, ["rewards"] = rewardHeader, ["deadlines"] = summaryPanel, ["personal-plans"] = personalPlansPanel };
        grid.RowDefinitions.Clear(); grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto }); Grid.SetRow(heading, 0);
        for (int i = 0; i < moduleOrder.Length; i++)
        {
            string id = moduleOrder[i]; var node = modules[id]; Grid.SetRow(node, i + 1);
            grid.RowDefinitions.Add(new RowDefinition { Height = id == "calendar" ? new GridLength(1, GridUnitType.Star) : GridLength.Auto });
            if (id != "rewards") node.Visibility = (!bubbleMode || id == "calendar") && ModuleVisible(id) ? Visibility.Visible : Visibility.Collapsed;
        }
        desktopLayout.ColumnDefinitions.Clear();
        if (sidebarPosition == "right") { desktopLayout.ColumnDefinitions.Add(new ColumnDefinition()); desktopLayout.ColumnDefinitions.Add(sidebarColumn); }
        else { desktopLayout.ColumnDefinitions.Add(sidebarColumn); desktopLayout.ColumnDefinitions.Add(new ColumnDefinition()); }
        Grid.SetColumn(sidebar, sidebarPosition == "right" ? 1 : 0); Grid.SetColumn(desktopShell, sidebarPosition == "right" ? 0 : 1);
        content.ColumnDefinitions.Clear(); content.RowDefinitions.Clear();
        if (calendarPosition == "top")
        {
            content.ColumnDefinitions.Add(new ColumnDefinition()); content.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto }); content.RowDefinitions.Add(new RowDefinition());
            Grid.SetColumn(month, 0); Grid.SetRow(month, 0); Grid.SetColumn(cardScroll, 0); Grid.SetRow(cardScroll, 1); month.Margin = new Thickness(0, 0, 0, 12);
        }
        else
        {
            content.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto }); content.ColumnDefinitions.Add(new ColumnDefinition()); content.RowDefinitions.Add(new RowDefinition());
            Grid.SetColumn(month, 0); Grid.SetRow(month, 0); Grid.SetColumn(cardScroll, 1); Grid.SetRow(cardScroll, 0); month.Margin = new Thickness(0, 0, 16, 0);
        }
    }

    void CaptureTextRoles(DependencyObject node)
    {
        if (node is TextBlock text && text.Foreground == Muted) text.SetValue(MutedRoleProperty, true);
        for (int i = 0; i < VisualTreeHelper.GetChildrenCount(node); i++) CaptureTextRoles(VisualTreeHelper.GetChild(node, i));
    }

    void ApplyWorkspaceVisuals(DependencyObject node)
    {
        bool inNav = node == sidebar;
        for (var parent = node; !inNav && parent != null; parent = VisualTreeHelper.GetParent(parent)) inNav = parent == sidebar;
        bool inCalendar = node == month;
        for (var parent = node; !inCalendar && parent != null; parent = VisualTreeHelper.GetParent(parent)) inCalendar = parent == month;
        bool inAccent = false;
        for (var parent = node; !inAccent && parent != null; parent = VisualTreeHelper.GetParent(parent)) inAccent = (parent is System.Windows.Controls.Primitives.CalendarDayButton day && (day.IsToday || day.IsSelected)) || (parent is Button b && ((b == navigationTab && sidebarPage == "calendar") || (b == settingsTab && sidebarPage == "appearance") || (b == remindersTab && sidebarPage == "reminders") || (b == networkTab && sidebarPage == "network") || (b == bubbleQuick && bubbleMode)));
        if (node is TextBlock text)
        {
            double size = (double)text.GetValue(BaseFontProperty); if (double.IsNaN(size)) { size = text.FontSize; text.SetValue(BaseFontProperty, size); } text.FontSize = size * fontScale * systemTextScale;
            if (text.Foreground == Muted) text.SetValue(MutedRoleProperty, true);
            text.Foreground = inAccent ? AccentInk : (bool)text.GetValue(MutedRoleProperty) ? (inNav ? NavMuted : Muted) : (inNav ? NavInk : Ink);
            if (text.FontSize >= 16) { text.FontWeight = FontWeight.FromOpenTypeWeight((int)themeHeadingWeight); text.FontFamily = new FontFamily("Bahnschrift, Microsoft YaHei UI"); }
        }
        if (node is Control control)
        {
            if (control is ComboBox combo) { combo.Style = (Style)Resources[typeof(ComboBox)]; combo.Resources[SystemColors.WindowBrushKey] = inNav ? NavSurface : Surface; combo.Resources[SystemColors.WindowTextBrushKey] = combo.Resources[SystemColors.ControlTextBrushKey] = inNav ? NavInk : Ink; combo.ApplyTemplate(); }
            if (control is CheckBox check) { check.Style = (Style)Resources[typeof(CheckBox)]; check.ApplyTemplate(); }
            if (control is Slider slider) { slider.Style = (Style)Resources[typeof(Slider)]; slider.ApplyTemplate(); }
            if (control is Button button && !inCalendar) { button.Style = (Style)Resources[typeof(Button)]; button.BorderThickness = new Thickness(themeBorderWidth); button.ApplyTemplate(); }
            if (control is not ProgressBar) control.Foreground = inAccent ? AccentInk : inNav ? NavInk : Ink;
            if (control is not ProgressBar and not ScrollViewer) { control.BorderBrush = ThemeBorder; control.Background = inAccent ? Green : inNav ? NavSurface : Surface; }
        }
        if (node == sidebar) sidebar.Background = NavSurface;
        if (node == sidebarPanel) sidebarPanel.Background = NavSurface;
        if (inCalendar && node is System.Windows.Shapes.Path arrow && arrow.Fill != null) arrow.Fill = Ink;
        for (int i = 0; i < VisualTreeHelper.GetChildrenCount(node); i++) ApplyWorkspaceVisuals(VisualTreeHelper.GetChild(node, i));
    }

    Brush DesktopPageBrush()
    {
        if (themeTexture == "none" || themeTextureOpacity == 0) return PageSurface;
        double size = themeTexture == "grid" ? 32 : 8;
        var drawing = new DrawingGroup();
        drawing.Children.Add(new GeometryDrawing(PageSurface, null, new RectangleGeometry(new Rect(0, 0, size, size))));
        var ink = new SolidColorBrush(((SolidColorBrush)Ink).Color) { Opacity = themeTextureOpacity };
        if (themeTexture == "dots") drawing.Children.Add(new GeometryDrawing(ink, null, new EllipseGeometry(new Point(1, 1), .8, .8)));
        else
        {
            var pen = new Pen(ink, 1);
            if (themeTexture == "grid")
            {
                drawing.Children.Add(new GeometryDrawing(null, pen, new LineGeometry(new Point(), new Point(size, 0))));
                drawing.Children.Add(new GeometryDrawing(null, pen, new LineGeometry(new Point(), new Point(0, size))));
            }
            else drawing.Children.Add(new GeometryDrawing(null, pen, new LineGeometry(new Point(0, size), new Point(size, 0))));
        }
        return new DrawingBrush(drawing) { TileMode = TileMode.Tile, ViewportUnits = BrushMappingMode.Absolute, Viewport = new Rect(0, 0, size, size), Stretch = Stretch.Fill };
    }

    async Task<bool> WorkspaceSmokeTest()
    {
        using var initial = JsonDocument.Parse(await http.GetStringAsync(address + "/api/state"));
        var defaults = initial.RootElement.GetProperty("workspace_defaults");
        var layout = JsonNode.Parse(defaults.GetProperty("layout").GetRawText())!;
        layout["sidebar"]!["position"] = "right"; layout["sidebar"]!["width"] = 260; layout["desktop_calendar"] = "top";
        var source = layout["modules"]!.AsArray().First(m => m!["id"]!.GetValue<string>() == "sources")!; source["visible"] = false;
        await Mutate("/api/workspace", new { layout, theme = new { preset = "dark", font_size = 16, radius = 12, spacing = 18 } }, true);
        ShowMode(false); Render(); SaveRender("workspace-dark.png");
        if (!workspaceDark || themePreset.SelectedValue as string != "custom" || Grid.GetColumn(sidebar) != 1 || Grid.GetRow(cardScroll) != 1 || footer.Visibility != Visibility.Collapsed || ((SolidColorBrush)Ink).Color != (Color)ColorConverter.ConvertFromString("#E3EBDD")) throw new Exception("Shared theme/layout did not reach the desktop");
        await Mutate("/api/network", new { paused = true }, true);
        if (!networkPaused || !networkStatus.Text.Contains("已暂停")) throw new Exception("Desktop network pause control failed");
        await Mutate("/api/network", new { paused = false }, true);
        OpenSidebar(true); SaveRender("workspace-settings.png");
        await Mutate("/api/workspace", new { theme = defaults.GetProperty("theme") }, true);
        if (workspaceDark || sidebarPosition != "right") throw new Exception("Resetting theme unexpectedly reset layout");
        await Mutate("/api/workspace", new { layout = defaults.GetProperty("layout") }, true);
        if (sidebarPosition != "left" || calendarPosition != "left") throw new Exception("Layout reset failed");
        using var final = JsonDocument.Parse(await http.GetStringAsync(address + "/api/state"));
        if (final.RootElement.GetProperty("provider_api_version").GetInt32() != 1) throw new Exception("Plugin API changed");
        return true;
    }
}
