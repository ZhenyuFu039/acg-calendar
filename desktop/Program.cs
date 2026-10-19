using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Threading;
using Forms = System.Windows.Forms;

namespace Playtime;

public static class Program
{
    [STAThread]
    public static void Main(string[] args)
    {
        string? root = null;
        for (int i = 0; i + 1 < args.Length; i++) if (args[i] == "--project") root = args[i + 1];
        var directory = new DirectoryInfo(AppContext.BaseDirectory);
        while (root == null && directory != null)
        {
            if (File.Exists(Path.Combine(directory.FullName, "app.py"))) root = directory.FullName;
            directory = directory.Parent;
        }
        if (root == null) { MessageBox.Show("找不到 app.py，请从项目目录运行 start-desktop.ps1。", "游时"); return; }
        using var instance = new Mutex(true, "Local\\PlaytimeDesktop-" + StableKey(Path.GetFullPath(root)) + (args.Contains("--smoke-test") ? "-QA-" + Guid.NewGuid().ToString("N") : ""), out bool first);
        if (!first) { MessageBox.Show("游时已在运行，请点击系统托盘中的日历图标。", "游时"); return; }
        var app = new System.Windows.Application { ShutdownMode = ShutdownMode.OnExplicitShutdown };
        var window = new CalendarWindow(Path.GetFullPath(root), args.Contains("--offline"), args.Contains("--smoke-test"));
        app.Run(window);
    }

    static string StableKey(string text) => Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(System.Text.Encoding.UTF8.GetBytes(text.ToUpperInvariant())))[..16];
}

public record Endpoint(string? At, string State);
public record CalendarEvent(string Id, string Title, string Category, Endpoint Start, Endpoint End, bool Completed, bool Ignored, string Time_Kind, JsonElement Source, JsonElement Artwork, JsonElement Reward, string Game)
{
    public DateTimeOffset? Begin => Parse(Start.At);
    public DateTimeOffset? Finish => Parse(End.At);
    public DateTimeOffset? Next => Begin > DateTimeOffset.Now ? Begin : Finish;
    public static DateTimeOffset? Parse(string? value) => DateTimeOffset.TryParse(value, out var date) ? date : null;
}

public sealed class CalendarWindow : Window
{
    readonly string root;
    readonly bool offline;
    readonly bool smoke;
    readonly string dataDirectory;
    readonly HttpClient http = new() { Timeout = TimeSpan.FromSeconds(4) };
    readonly JsonSerializerOptions json = new() { PropertyNameCaseInsensitive = true };
    readonly StackPanel cards = new();
    readonly TextBlock subtitle = new();
    readonly TextBlock status = new();
    readonly TextBlock clock = new();
    readonly TextBlock rewardTotal = new();
    readonly StackPanel rewardHeader = new();
    readonly ComboBox gameSelect = new();
    string selectedGame = "endfield-cn";
    int verifiedRewardTotal;
    readonly Calendar month = new();
    readonly Grid content = new();
    readonly DispatcherTimer timer = new() { Interval = TimeSpan.FromSeconds(15) };
    readonly Forms.NotifyIcon tray;
    readonly Forms.ToolStripMenuItem notificationsMenu;
    readonly string preferences;
    Process? backend;
    string address = "";
    string csrf = "";
    CalendarEvent[] events = [];
    bool refreshing, exiting, compact = true;
    bool showAll, dense = true;
    readonly Button allButton, densityButton;
    readonly Dictionary<string, ImageSource> pictures = new();
    DateTime? selected;
    DateTimeOffset lastRefresh;
    readonly Button mode;
    readonly Button pin;
    static readonly Brush Ink = Brush("#233126"), Muted = Brush("#758073"), Green = Brush("#D8ED9A");

    public CalendarWindow(string root, bool offline, bool smoke = false)
    {
        this.root = root; this.offline = offline; this.smoke = smoke;
        dataDirectory = smoke ? Path.Combine(root, "logs", "desktop-smoke", DateTime.UtcNow.ToString("yyyyMMdd-HHmmss-fff")) : Path.Combine(root, "data");
        preferences = Path.Combine(dataDirectory, "desktop-window.json");
        Title = "游时 · 桌面日历"; Width = 390; Height = 620;
        MinWidth = 350; MinHeight = 440; WindowStyle = WindowStyle.None;
        ResizeMode = ResizeMode.CanResizeWithGrip; Background = Brush("#F6F8F1");
        FontFamily = new FontFamily("Microsoft YaHei UI"); FontSize = 13;
        Foreground = Ink; WindowStartupLocation = WindowStartupLocation.Manual;
        var area = SystemParameters.WorkArea; Left = area.Right - Width - 24; Top = area.Top + 40;
        LoadPreferences();
        var shell = new Border { Background = Background, BorderBrush = Brush("#DDE4D3"), BorderThickness = new Thickness(1), Padding = new Thickness(18) };
        var layout = new Grid();
        foreach (var height in new[] { GridLength.Auto, GridLength.Auto, new GridLength(1, GridUnitType.Star), GridLength.Auto }) layout.RowDefinitions.Add(new RowDefinition { Height = height });
        var heading = new DockPanel { Margin = new Thickness(0, 0, 0, 16) };
        var close = Button("×", () => Hide()); close.ToolTip = "收起到系统托盘，提醒继续运行"; DockPanel.SetDock(close, Dock.Right); heading.Children.Add(close);
        pin = Button("置顶", () => { Topmost = !Topmost; PinLabel(); SavePreferences(); }); DockPanel.SetDock(pin, Dock.Right); heading.Children.Add(pin); PinLabel();
        var brand = new StackPanel();
        brand.Children.Add(Text("游时  PLAYTIME", 19, true));
        clock.FontSize = 11; clock.Foreground = Muted; clock.Margin = new Thickness(0, 4, 0, 0); brand.Children.Add(clock);
        brand.MouseLeftButtonDown += (_, e) => { if (e.ClickCount == 2) ToggleMode(); else DragMove(); };
        heading.Children.Add(brand); layout.Children.Add(heading);
        var controls = new StackPanel { Margin = new Thickness(0, 0, 0, 14) };
        subtitle.FontSize = 17; subtitle.FontWeight = FontWeights.SemiBold; subtitle.Text = "正在启动本地日历…"; controls.Children.Add(subtitle);
        var rewards = rewardHeader; rewards.Orientation = Orientation.Horizontal; rewards.Margin = new Thickness(0, 6, 0, 0); rewards.ToolTip = "终末地活动已核实小计，不含地图、信用商店、维护补偿及通行证；未知数量不按零计算。完整明细见时间轴。";
        if (RewardIcon() is ImageSource currency) rewards.Children.Add(new Image { Source = currency, Width = 27, Height = 27 });
        rewardTotal.FontSize = 11; rewardTotal.Foreground = Brush("#805B42"); rewardTotal.VerticalAlignment = VerticalAlignment.Center; rewards.Children.Add(rewardTotal); controls.Children.Add(rewards);
        gameSelect.Items.Add("终末地国服 · 官方"); gameSelect.Items.Add("明日方舟国服 · PRTS"); gameSelect.Items.Add("全部游戏 · 对照排期");
        gameSelect.SelectedIndex = selectedGame == "arknights-cn" ? 1 : selectedGame == "all" ? 2 : 0;
        gameSelect.Margin = new Thickness(0, 8, 0, 0); gameSelect.Padding = new Thickness(5, 3, 5, 3);
        gameSelect.SelectionChanged += (_, _) => { selectedGame = gameSelect.SelectedIndex == 1 ? "arknights-cn" : gameSelect.SelectedIndex == 2 ? "all" : "endfield-cn"; selected = null; month.SelectedDate = null; Render(); SavePreferences(); };
        controls.Children.Add(gameSelect);
        var toolbar = new WrapPanel { Margin = new Thickness(0, 10, 0, 0) };
        mode = Button("展开月历", ToggleMode); toolbar.Children.Add(mode);
        toolbar.Children.Add(Button("近期", () => { showAll = false; selected = null; month.SelectedDate = null; Render(); }));
        allButton = Button("全部活动", () => { showAll = !showAll; selected = null; month.SelectedDate = null; if (showAll && compact) ShowMode(false); Render(); });
        toolbar.Children.Add(allButton);
        densityButton = Button("图文模式", () => { dense = !dense; Render(); }); toolbar.Children.Add(densityButton);
        toolbar.Children.Add(Button("同步", async () => await Mutate("/api/sync", new { game = selectedGame })));
        toolbar.Children.Add(Button("完整时间轴 ↗", () => Open(address + "?game=" + selectedGame)));
        controls.Children.Add(toolbar); Grid.SetRow(controls, 1); layout.Children.Add(controls);
        content.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        content.ColumnDefinitions.Add(new ColumnDefinition());
        month.Margin = new Thickness(0, 0, 18, 0); month.VerticalAlignment = VerticalAlignment.Top; month.Visibility = Visibility.Collapsed;
        month.SelectedDatesChanged += (_, _) => { selected = month.SelectedDate; Render(); };
        content.Children.Add(month);
        var scroll = new ScrollViewer { Content = cards, VerticalScrollBarVisibility = ScrollBarVisibility.Auto, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled };
        Grid.SetColumn(scroll, 1); content.Children.Add(scroll); Grid.SetRow(content, 2); layout.Children.Add(content);
        var footer = new StackPanel { Margin = new Thickness(0, 12, 0, 0) };
        status.FontSize = 11; status.Foreground = Muted; status.TextWrapping = TextWrapping.Wrap; footer.Children.Add(status);
        var links = new WrapPanel { Margin = new Thickness(0, 6, 0, 0) };
        links.Children.Add(Button("导出日历 .ics", async () => await Export()));
        links.Children.Add(Button("系统日历说明", ExplainIntegration)); footer.Children.Add(links);
        Grid.SetRow(footer, 3); layout.Children.Add(footer); shell.Child = layout; Content = shell;
        tray = new Forms.NotifyIcon { Text = "游时 · 活动日历", Icon = System.Drawing.SystemIcons.Application, Visible = true };
        var menu = new Forms.ContextMenuStrip();
        menu.Items.Add("显示桌面挂件", null, (_, _) => Dispatcher.Invoke(() => ShowMode(true)));
        menu.Items.Add("显示月历", null, (_, _) => Dispatcher.Invoke(() => ShowMode(false)));
        menu.Items.Add("打开完整时间轴", null, (_, _) => Open(address + "?game=" + selectedGame));
        menu.Items.Add("导出日历文件", null, async (_, _) => await Export());
        notificationsMenu = new Forms.ToolStripMenuItem("启用 Windows 通知");
        notificationsMenu.Click += async (_, _) => await Mutate("/api/settings", new { native_notifications = !notificationsMenu.Checked });
        menu.Items.Add(notificationsMenu);
        menu.Items.Add("测试提醒", null, async (_, _) => await Mutate("/api/notifications/test", new { }));
        menu.Items.Add(new Forms.ToolStripSeparator());
        menu.Items.Add("退出游时", null, (_, _) => Dispatcher.Invoke(Exit));
        tray.ContextMenuStrip = menu;
        tray.MouseClick += (_, e) => { if (e.Button == Forms.MouseButtons.Left) Dispatcher.Invoke(() => { if (IsVisible && IsActive) Hide(); else { Show(); Activate(); } }); };
        Closing += (_, e) => { if (!exiting) { e.Cancel = true; SavePreferences(); Hide(); } };
        LocationChanged += (_, _) => { if (IsLoaded) SavePreferences(); };
        timer.Tick += async (_, _) => await Refresh();
        Loaded += async (_, _) =>
        {
            try
            {
                await StartBackend();
                if (smoke) await SmokeTest(); else timer.Start();
            }
            catch (Exception error)
            {
                subtitle.Text = "无法启动本地服务"; status.Text = error.Message; Log(error.ToString());
                if (smoke)
                {
                    Directory.CreateDirectory(dataDirectory);
                    File.WriteAllText(Path.Combine(dataDirectory, "result.json"), JsonSerializer.Serialize(new { ok = false, error = error.ToString() }));
                    Environment.ExitCode = 1; Exit();
                }
            }
        };
        Dispatcher.UnhandledException += (_, e) => { status.Text = "操作失败：" + e.Exception.Message; e.Handled = true; };
    }

    static Brush Brush(string hex) => new SolidColorBrush((Color)ColorConverter.ConvertFromString(hex));
    static TextBlock Text(string value, double size = 13, bool bold = false) => new() { Text = value, FontSize = size, FontWeight = bold ? FontWeights.SemiBold : FontWeights.Normal, TextWrapping = TextWrapping.Wrap };
    static Button Button(string value, Action action)
    {
        var button = new Button { Content = value, Padding = new Thickness(9, 6, 9, 6), Margin = new Thickness(0, 0, 5, 5), Background = Brush("#EDF1E6"), Foreground = Ink, BorderThickness = new Thickness(0), Cursor = Cursors.Hand };
        button.Click += (_, _) => action(); return button;
    }
    void PinLabel() => pin.Content = Topmost ? "已置顶" : "置顶";
    void ToggleMode() => ShowMode(!compact);
    void ShowMode(bool mini)
    {
        compact = mini; month.Visibility = mini ? Visibility.Collapsed : Visibility.Visible;
        Width = mini ? 390 : 820; Height = mini ? 620 : 720;
        mode.Content = mini ? "展开月历" : "桌面挂件";
        var area = SystemParameters.WorkArea;
        Left = Math.Clamp(Left, area.Left, Math.Max(area.Left, area.Right - Width));
        Top = Math.Clamp(Top, area.Top, Math.Max(area.Top, area.Bottom - Height));
        selected = null; month.SelectedDate = null; Show(); Activate(); Render(); SavePreferences();
    }
    void LoadPreferences()
    {
        try
        {
            using var doc = JsonDocument.Parse(File.ReadAllText(preferences));
            var p = doc.RootElement; var area = SystemParameters.WorkArea;
            Left = Math.Clamp(p.GetProperty("left").GetDouble(), area.Left, Math.Max(area.Left, area.Right - Width));
            Top = Math.Clamp(p.GetProperty("top").GetDouble(), area.Top, Math.Max(area.Top, area.Bottom - Height));
            Topmost = p.GetProperty("topmost").GetBoolean();
            if (p.TryGetProperty("game", out var game) && game.GetString() is "endfield-cn" or "arknights-cn" or "all") selectedGame = game.GetString()!;
        }
        catch { }
    }
    void SavePreferences()
    {
        try { Directory.CreateDirectory(Path.GetDirectoryName(preferences)!); File.WriteAllText(preferences, JsonSerializer.Serialize(new { left = Left, top = Top, topmost = Topmost, game = selectedGame })); }
        catch { /* Position persistence must not stop the calendar. */ }
    }

    async Task StartBackend()
    {
        // A private ephemeral port isolates this process from any web-mode instance.
        var socket = new System.Net.Sockets.TcpListener(System.Net.IPAddress.Loopback, 0);
        socket.Start(); int port = ((System.Net.IPEndPoint)socket.LocalEndpoint).Port; socket.Stop();
        address = "http://127.0.0.1:" + port;
        var candidates = new List<(string Path, string? Prefix)> { ("py", "-3"), ("python", null), ("python3", null) };
        string bundled = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".cache", "codex-runtimes", "codex-primary-runtime", "dependencies", "python", "python.exe");
        if (File.Exists(bundled)) candidates.Insert(0, (bundled, null));
        foreach (var candidate in candidates)
        {
            try
            {
                var check = new ProcessStartInfo(candidate.Path) { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true };
                if (candidate.Prefix != null) check.ArgumentList.Add(candidate.Prefix);
                check.ArgumentList.Add("-c"); check.ArgumentList.Add("import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)");
                using var test = Process.Start(check)!;
                using var limit = new CancellationTokenSource(TimeSpan.FromSeconds(5));
                try { await test.WaitForExitAsync(limit.Token); }
                catch (OperationCanceledException) { test.Kill(true); continue; }
                if (test.ExitCode != 0) continue;
                var info = new ProcessStartInfo(candidate.Path) { WorkingDirectory = root, UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true };
                if (candidate.Prefix != null) info.ArgumentList.Add(candidate.Prefix);
                foreach (string arg in new[] { Path.Combine(root, "app.py"), "--port", port.ToString(), "--data-dir", dataDirectory }) info.ArgumentList.Add(arg);
                if (offline) info.ArgumentList.Add("--no-sync");
                backend = Process.Start(info)!;
                backend.OutputDataReceived += (_, e) => Log(e.Data);
                backend.ErrorDataReceived += (_, e) => Log(e.Data);
                backend.BeginOutputReadLine(); backend.BeginErrorReadLine();
                for (int retry = 0; retry < 40; retry++)
                {
                    if (backend.HasExited) throw new InvalidOperationException("本地服务启动失败，请查看 logs/desktop.log");
                    try { using var response = await http.GetAsync(address + "/api/health"); if (response.IsSuccessStatusCode) { await Refresh(); return; } } catch (HttpRequestException) { }
                    await Task.Delay(250);
                }
                throw new InvalidOperationException("本地服务启动超时");
            }
            catch (System.ComponentModel.Win32Exception) { }
        }
        subtitle.Text = "无法启动日历"; status.Text = "需要 Python 3.11+。请安装后重新打开游时。";
    }
    readonly object logLock = new();
    void Log(string? line)
    {
        if (line == null) return;
        lock (logLock) try { Directory.CreateDirectory(Path.Combine(root, "logs")); File.AppendAllText(Path.Combine(root, "logs", "desktop.log"), line + Environment.NewLine); } catch { }
    }
    async Task Refresh()
    {
        if (refreshing || address == "" || exiting) return;
        refreshing = true;
        try
        {
            using var state = JsonDocument.Parse(await http.GetStringAsync(address + "/api/state"));
            csrf = state.RootElement.GetProperty("csrf_token").GetString()!;
            notificationsMenu.Checked = state.RootElement.GetProperty("settings").GetProperty("native_notifications").GetBoolean();
            events = state.RootElement.GetProperty("events").Deserialize<CalendarEvent[]>(json)!;
            // Clone source JsonElements before the response document is disposed.
            events = events.Select(e => e with { Source = e.Source.Clone(), Artwork = e.Artwork.ValueKind == JsonValueKind.Undefined ? default : e.Artwork.Clone(), Reward = e.Reward.ValueKind == JsonValueKind.Undefined ? default : e.Reward.Clone() }).ToArray();
            var current = state.RootElement.GetProperty("versions").EnumerateArray().First(v => v.GetProperty("game").GetString() == "endfield-cn");
            if (current.TryGetProperty("reward_summary", out var summary))
            {
                verifiedRewardTotal = summary.GetProperty("verified_total").GetInt32();
                rewardTotal.Text = current.GetProperty("name").GetString() + $" · 活动已核实 {verifiedRewardTotal:N0} 玉";
            }
            lastRefresh = DateTimeOffset.Now; Render();
        }
        catch (Exception error) { status.Text = "连接中断，已保留上次数据。"; Log(error.Message); }
        finally { refreshing = false; }
    }
    void Render()
    {
        clock.Text = DateTime.Now.ToString("M月d日 dddd HH:mm") + " · 本机时区";
        if (events.Length == 0) return;
        var scoped = events.Where(e => selectedGame == "all" || e.Game == selectedGame).ToArray();
        rewardHeader.Visibility = selectedGame == "arknights-cn" ? Visibility.Collapsed : Visibility.Visible;
        subtitle.Text = selected is DateTime day ? day.ToString("M月d日") + "的活动" : showAll ? "全部活动 · " + scoped.Length + " 项" : "接下来，别错过";
        allButton.Content = showAll ? "仅看近期" : "全部活动";
        densityButton.Content = dense ? "图文模式" : "紧凑模式";
        cards.Children.Clear();
        var now = DateTimeOffset.Now;
        var shown = showAll ? scoped.AsEnumerable() : scoped.Where(e => !e.Completed && !e.Ignored && e.Category != "maintenance");
        if (selected is DateTime chosen)
            shown = shown.Where(e => e.Begin != null && e.Begin.Value.LocalDateTime.Date <= chosen.Date && (e.Finish?.LocalDateTime.Date >= chosen.Date || (e.Time_Kind == "point" ? e.Begin.Value.LocalDateTime.Date == chosen.Date : e.Finish == null)));
        else if (!showAll) shown = shown.Where(e => e.Finish > now || e.Begin > now || (e.Finish == null && e.Time_Kind != "point"));
        var ordered = shown.OrderBy(e => e.Next ?? DateTimeOffset.MaxValue).ToArray();
        foreach (var entry in ordered)
        {
            string[] palette = ["#69946e", "#5e8ea3", "#ae825d", "#8f7bac", "#9f8f4c", "#668f91", "#b07679", "#7b8e57"];
            uint hash = 0; foreach (char letter in entry.Id) hash = unchecked(hash * 31 + letter);
            var color = Brush(palette[hash % 8]);
            var card = new Border { Background = Brushes.White, CornerRadius = new CornerRadius(6), Padding = new Thickness(dense ? 9 : 13), Margin = new Thickness(0, 0, 0, dense ? 6 : 9), BorderBrush = color, BorderThickness = new Thickness(3, 0, 0, 0), Opacity = entry.Completed ? .65 : 1 };
            var body = new StackPanel();
            string type = entry.Category switch { "gacha" => "寻访", "exchange" => "兑换", "update" => "更新", "maintenance" => "维护", _ => "活动" };
            if (selectedGame == "all") type = (entry.Game == "arknights-cn" ? "明日方舟" : "终末地") + " · " + type;
            var heading = new DockPanel();
            var done = Button(entry.Completed ? "↶" : "✓", async () => await Mutate("/api/events/" + entry.Id, new { completed = !entry.Completed }, true));
            done.ToolTip = entry.Completed ? "撤销完成" : "标记完成"; done.Padding = new Thickness(6, 2, 6, 2); DockPanel.SetDock(done, Dock.Right); heading.Children.Add(done);
            var title = new Grid { MinHeight = dense ? 23 : 42 };
            if (Picture(entry) is ImageSource picture)
                title.Children.Add(new Image { Source = picture, Width = 170, Height = dense ? 30 : 46, HorizontalAlignment = HorizontalAlignment.Left, VerticalAlignment = VerticalAlignment.Top, Stretch = Stretch.UniformToFill, Opacity = .5, OpacityMask = new LinearGradientBrush(Colors.Black, Colors.Transparent, new Point(0, .5), new Point(1, .5)), IsHitTestVisible = false });
            title.Children.Add(Text(entry.Title, dense ? 12 : 14, true)); heading.Children.Add(title); body.Children.Add(heading);
            bool upcoming = entry.Begin > now;
            var due = upcoming ? entry.Begin : entry.Finish;
            string certainty = (upcoming ? entry.Start.State : entry.End.State) == "estimated" ? "预计 " : "";
            bool point = entry.Time_Kind == "point";
            string remaining = point ? (upcoming ? Countdown(entry.Begin!.Value - now) + "后上线" : "已上线") : due == null ? "截止待定" : (due <= now ? "已结束" : Countdown(due.Value - now) + (upcoming ? "后开始" : "后截止"));
            var countdown = Text(type + " · " + (entry.Completed ? "已完成" : certainty + remaining), dense ? 10 : 12, true); countdown.Foreground = color; countdown.Margin = new Thickness(0, dense ? 3 : 7, 0, 3); body.Children.Add(countdown);
            var dates = Text((point ? "更新 " : "开始 ") + Format(entry.Begin) + (entry.Start.State == "estimated" ? "（预计）" : "") + (point ? "" : (dense ? " → " : "\n截止 ") + Format(entry.Finish) + (entry.End.State == "estimated" ? "（预计）" : "")), dense ? 10 : 11); dates.Foreground = Muted; body.Children.Add(dates);
            if (entry.Begin != null && entry.Finish > entry.Begin)
            {
                double progress = Math.Clamp((now - entry.Begin.Value).TotalSeconds / (entry.Finish.Value - entry.Begin.Value).TotalSeconds * 100, 0, 100);
                body.Children.Add(new ProgressBar { Value = progress, Height = 3, Foreground = color, Background = Brush("#EDF1E6"), BorderThickness = new Thickness(0), Margin = new Thickness(0, dense ? 5 : 9, 0, dense ? 0 : 7) });
            }
            if (entry.Reward.ValueKind == JsonValueKind.Object)
            {
                var reward = entry.Reward;
                var line = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right, Margin = new Thickness(0, 3, 0, 0), ToolTip = reward.GetProperty("note").GetString() };
                if (reward.GetProperty("amount").ValueKind == JsonValueKind.Number)
                {
                    if (RewardIcon() is ImageSource icon) line.Children.Add(new Image { Source = icon, Width = 24, Height = 24 });
                    string shared = reward.GetProperty("state").GetString() == "shared" ? " · 共享" : entry.Category == "gacha" ? " · 试用" : "";
                    var value = Text(reward.GetProperty("amount").GetInt32().ToString("N0") + shared, 11, true); value.VerticalAlignment = VerticalAlignment.Center; value.Foreground = Brush("#805B42"); line.Children.Add(value);
                }
                else line.Children.Add(Text(entry.Category == "maintenance" ? "补偿单列" : "嵌晶玉未列明", 10));
                body.Children.Add(line);
            }
            var actions = new WrapPanel { Margin = new Thickness(0, 7, 0, 0) };
            actions.Children.Add(Button("标记完成", async () => await Mutate("/api/events/" + entry.Id, new { completed = true }, true)));
            if (entry.Source.TryGetProperty("url", out var source)) actions.Children.Add(Button(entry.Game == "arknights-cn" ? "PRTS ↗" : "公告 ↗", () => Open(source.GetString()!)));
            if (!dense) body.Children.Add(actions); card.Child = body; cards.Children.Add(card);
        }
        if (ordered.Length == 0) cards.Children.Add(Text("这一天没有待办活动，换一天看看。", 13));
        status.Text = $"当前 {ordered.Length} / 共 {scoped.Length} 项 · {lastRefresh:HH:mm:ss} 更新\n" + (selectedGame == "arknights-cn" ? "来源：PRTS 社区 Wiki · 国服 · 可在完整时间轴查看修订。" : showAll ? "包含已结束、已完成及维护，滚动查看所有条目。" : "近期视图隐藏已结束和已完成，点“全部活动”查看。");
    }
    ImageSource? currencyIcon;
    ImageSource? RewardIcon()
    {
        if (currencyIcon != null) return currencyIcon;
        string file = Path.Combine(root, "web", "assets", "oroberyl.png");
        if (!File.Exists(file)) return null;
        var image = new System.Windows.Media.Imaging.BitmapImage(); image.BeginInit();
        image.CacheOption = System.Windows.Media.Imaging.BitmapCacheOption.OnLoad;
        image.UriSource = new Uri(file); image.EndInit(); image.Freeze(); return currencyIcon = image;
    }
    ImageSource? Picture(CalendarEvent entry)
    {
        try
        {
            if (entry.Artwork.ValueKind != JsonValueKind.Object || !entry.Artwork.TryGetProperty("key", out var value)) return null;
            string key = value.GetString() ?? "";
            if (!System.Text.RegularExpressions.Regex.IsMatch(key, @"\A[a-f0-9]{32}\.(jpg|png|webp)\z")) return null;
            if (pictures.TryGetValue(key, out var cached)) return cached;
            string file = Path.Combine(dataDirectory, "artwork", key);
            if (!File.Exists(file)) return null;
            var image = new System.Windows.Media.Imaging.BitmapImage(); image.BeginInit();
            image.CacheOption = System.Windows.Media.Imaging.BitmapCacheOption.OnLoad; image.DecodePixelWidth = 320;
            image.UriSource = new Uri(file); image.EndInit(); image.Freeze(); pictures[key] = image; return image;
        }
        catch { return null; }
    }
    static string Format(DateTimeOffset? date) => date?.ToLocalTime().ToString("MM/dd HH:mm") ?? "未公布";
    public static string Countdown(TimeSpan span) => span.TotalDays >= 1 ? $"{(int)span.TotalDays}天{span.Hours}小时" : span.TotalHours >= 1 ? $"{(int)span.TotalHours}小时{span.Minutes}分" : $"{Math.Max(1, (int)Math.Ceiling(span.TotalMinutes))}分钟";
    async Task Mutate(string path, object payload, bool patch = false)
    {
        try
        {
            using var request = new HttpRequestMessage(patch ? HttpMethod.Patch : HttpMethod.Post, address + path) { Content = new StringContent(JsonSerializer.Serialize(payload), System.Text.Encoding.UTF8, "application/json") };
            request.Headers.Add("X-Calendar-Token", csrf);
            using var response = await http.SendAsync(request); response.EnsureSuccessStatusCode(); await Refresh();
        }
        catch (Exception error) { status.Text = "操作失败：" + error.Message; }
    }
    async Task Export()
    {
        try
        {
            var dialog = new Microsoft.Win32.SaveFileDialog { FileName = "游时-" + selectedGame + ".ics", Filter = "iCalendar 日历 (*.ics)|*.ics" };
            if (dialog.ShowDialog(this) != true) return;
            var bytes = await http.GetByteArrayAsync(address + "/api/calendar.ics?game=" + selectedGame);
            await File.WriteAllBytesAsync(dialog.FileName, bytes);
            status.Text = "已导出。日历应用导入的是快照，不会自动同步到系统时钟面板。";
        }
        catch (Exception error) { status.Text = "导出失败：" + error.Message; }
    }
    void ExplainIntegration() => MessageBox.Show(this, "点击 Windows 时钟出现的面板由系统管理。此版本提供自己的桌面挂件和托盘月历，尚未写入 Windows 本地日程库。\n\n可导出 .ics 文件到兼容的日历应用；导入是快照，不保证出现在系统时钟面板。旧版 Windows 日历已停止支持，Windows 各版本的面板能力也不同。", "关于 Windows 系统日历");
    static void Open(string url)
    {
        if (Uri.TryCreate(url, UriKind.Absolute, out var uri) && uri.Scheme is "http" or "https")
            try { Process.Start(new ProcessStartInfo(uri.AbsoluteUri) { UseShellExecute = true }); } catch { }
    }
    void Exit()
    {
        exiting = true; timer.Stop(); SavePreferences(); tray.Visible = false; tray.Dispose();
        // Only stop the backend created by this desktop process.
        if (backend != null) { try { if (!backend.HasExited) backend.Kill(true); } catch { } backend.Dispose(); }
        http.Dispose(); Close(); System.Windows.Application.Current.Shutdown(Environment.ExitCode);
    }

    async Task SmokeTest()
    {
        try
        {
            if (events.Length < 1 || cards.Children.Count < 1 || csrf == "") throw new Exception("Desktop did not load real calendar data");
            if (verifiedRewardTotal != 11000 || RewardIcon() == null || !events.Any(e => e.Reward.ValueKind == JsonValueKind.Object && e.Reward.GetProperty("state").GetString() == "shared")) throw new Exception("Reward summary, shared pool, or icon did not load");
            Hide();
            using (var hidden = await http.GetAsync(address + "/api/health")) hidden.EnsureSuccessStatusCode();
            if (IsVisible) throw new Exception("Desktop did not hide to the tray");
            Show();
            var entry = events.First(e => !e.Completed && e.Category == "event");
            await Mutate("/api/events/" + entry.Id, new { completed = true }, true);
            if (!events.Single(e => e.Id == entry.Id).Completed) throw new Exception("Desktop completion did not persist");
            await Mutate("/api/events/" + entry.Id, new { completed = false }, true);
            if (events.Single(e => e.Id == entry.Id).Completed) throw new Exception("Desktop completion restore failed");
            byte[] ics = await http.GetByteArrayAsync(address + "/api/calendar.ics");
            if (!System.Text.Encoding.UTF8.GetString(ics).StartsWith("BEGIN:VCALENDAR")) throw new Exception("Calendar export failed");
            SaveRender("widget.png"); ShowMode(false); showAll = true; Render(); SaveRender("month.png");
            gameSelect.SelectedIndex = 1; Render(); SaveRender("arknights.png");
            if (cards.Children.Count != events.Count(e => e.Game == "arknights-cn") || rewardHeader.Visibility != Visibility.Collapsed) throw new Exception("Arknights filter or currency isolation failed");
            byte[] arkIcs = await http.GetByteArrayAsync(address + "/api/calendar.ics?game=arknights-cn");
            if (!System.Text.Encoding.UTF8.GetString(arkIcs).Contains("PRTS")) throw new Exception("Arknights calendar attribution missing");
            gameSelect.SelectedIndex = 2; Render(); SaveRender("combined.png");
            if (cards.Children.Count != events.Length) throw new Exception("All-activities view omitted entries");
            month.SelectedDate = DateTime.Today.AddDays(1);
            if (selected != DateTime.Today.AddDays(1)) throw new Exception("Calendar selection did not update");
            await File.WriteAllTextAsync(Path.Combine(dataDirectory, "result.json"), JsonSerializer.Serialize(new { ok = true, entries = events.Length, verifiedRewardTotal, exportBytes = ics.Length, backendPid = backend?.Id }));
        }
        catch (Exception error)
        {
            Directory.CreateDirectory(dataDirectory);
            await File.WriteAllTextAsync(Path.Combine(dataDirectory, "result.json"), JsonSerializer.Serialize(new { ok = false, error = error.ToString() }));
            Environment.ExitCode = 1;
        }
        finally { Exit(); }
    }
    void SaveRender(string name)
    {
        UpdateLayout();
        var visual = (FrameworkElement)Content;
        var bitmap = new System.Windows.Media.Imaging.RenderTargetBitmap((int)visual.ActualWidth, (int)visual.ActualHeight, 96, 96, PixelFormats.Pbgra32);
        bitmap.Render(visual);
        var encoder = new System.Windows.Media.Imaging.PngBitmapEncoder();
        encoder.Frames.Add(System.Windows.Media.Imaging.BitmapFrame.Create(bitmap));
        Directory.CreateDirectory(dataDirectory);
        using var stream = File.Create(Path.Combine(dataDirectory, name)); encoder.Save(stream);
    }
}
