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
public record CalendarEvent(string Id, string Title, string Category, Endpoint Start, Endpoint End, bool Completed, bool Ignored, string Time_Kind, JsonElement Source, JsonElement Artwork, JsonElement Reward, string Game, bool Starred = false)
{
    public DateTimeOffset? Begin => Parse(Start.At);
    public DateTimeOffset? Finish => Parse(End.At);
    public DateTimeOffset? Next => Begin > DateTimeOffset.Now ? Begin : Finish;
    public static DateTimeOffset? Parse(string? value) => DateTimeOffset.TryParse(value, out var date) ? date : null;
}

public sealed partial class CalendarWindow : Window
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
    readonly System.Drawing.Icon trayIcon;
    readonly Forms.ToolStripMenuItem notificationsMenu;
    readonly string preferences;
    Process? backend;
    string address = "";
    string csrf = "";
    CalendarEvent[] events = [];
    bool refreshing, exiting, compact = true;
    bool showAll, dense = true;
    Button allButton = null!, densityButton = null!;
    readonly Dictionary<string, ImageSource> pictures = new();
    DateTime? selected;
    DateTimeOffset lastRefresh;
    Button mode = null!, pin = null!;
    static Brush Ink = Brush("#233126"), Muted = Brush("#758073"), Green = Brush("#D8ED9A");

    public CalendarWindow(string root, bool offline, bool smoke = false)
    {
        this.root = root; this.offline = offline; this.smoke = smoke;
        dataDirectory = smoke ? Path.Combine(root, "logs", "desktop-smoke", DateTime.UtcNow.ToString("yyyyMMdd-HHmmss-fff")) : Path.Combine(root, "data");
        preferences = Path.Combine(dataDirectory, "desktop-window.json");
        if (smoke)
        {
            var sample = Path.Combine(dataDirectory, "plugins", "sample-game"); Directory.CreateDirectory(sample);
            foreach (var name in new[] { "manifest.json", "events.json" })
                File.Copy(Path.Combine(root, "examples", "plugins", "sample-game", name), Path.Combine(sample, name));
            foreach (var pluginName in new[] { "nikke-global", "azurlane-cn", "zzz-cn" })
            {
                var plugin = Path.Combine(root, "plugins", pluginName);
                if (!Directory.Exists(plugin)) continue;
                foreach (var source in Directory.GetFiles(plugin, "*", SearchOption.AllDirectories))
                {
                    var target = Path.Combine(dataDirectory, "plugins", pluginName, Path.GetRelativePath(plugin, source));
                    Directory.CreateDirectory(Path.GetDirectoryName(target)!); File.Copy(source, target);
                }
            }
        }
        Title = "游时 · 桌面日历"; Width = 390; Height = 620;
        var iconUri = new Uri("pack://application:,,,/Assets/playtime.ico");
        Icon = System.Windows.Media.Imaging.BitmapDecoder.Create(iconUri, System.Windows.Media.Imaging.BitmapCreateOptions.PreservePixelFormat, System.Windows.Media.Imaging.BitmapCacheOption.OnLoad).Frames.Last();
        Icon.Freeze();
        using (var iconStream = System.Windows.Application.GetResourceStream(iconUri).Stream)
        using (var originalIcon = new System.Drawing.Icon(iconStream, 32, 32)) trayIcon = (System.Drawing.Icon)originalIcon.Clone();
        MinWidth = 350; MinHeight = 440; WindowStyle = WindowStyle.None;
        AllowsTransparency = true; ResizeMode = ResizeMode.CanResizeWithGrip; Background = Brushes.Transparent;
        FontFamily = new FontFamily("Microsoft YaHei UI"); FontSize = 13;
        Foreground = Ink; WindowStartupLocation = WindowStartupLocation.Manual;
        var area = SystemParameters.WorkArea; Left = area.Right - Width - 24; Top = area.Top + 40;
        LoadPreferences();
        InitializeDesktopUI();
        tray = new Forms.NotifyIcon { Text = "游时 · 活动日历", Icon = trayIcon, Visible = true };
        var menu = new Forms.ContextMenuStrip();
        menu.Items.Add("显示桌面挂件", null, (_, _) => Dispatcher.Invoke(() => ShowMode(true)));
        menu.Items.Add("显示月历", null, (_, _) => Dispatcher.Invoke(() => ShowMode(false)));
        menu.Items.Add("打开侧边栏", null, (_, _) => Dispatcher.Invoke(() => OpenSidebar(false)));
        menu.Items.Add("桌面设置", null, (_, _) => Dispatcher.Invoke(() => OpenSidebar(true)));
        bubbleMenu = new Forms.ToolStripMenuItem("仅显示活动气泡");
        bubbleMenu.Click += (_, _) => Dispatcher.Invoke(() => SetBubbleMode(!bubbleMode));
        menu.Items.Add(bubbleMenu);
        menu.Items.Add("打开完整时间轴", null, (_, _) => Open(address + "?game=" + selectedGame));
        menu.Items.Add("导出日历文件", null, async (_, _) => await Export());
        notificationsMenu = new Forms.ToolStripMenuItem("启用 Windows 通知");
        notificationsMenu.Click += async (_, _) => await Mutate("/api/settings", new { native_notifications = !notificationsMenu.Checked });
        menu.Items.Add(notificationsMenu);
        menu.Items.Add("测试提醒", null, async (_, _) => await Mutate("/api/notifications/test", new { }));
        menu.Items.Add(new Forms.ToolStripSeparator());
        menu.Items.Add("退出游时", null, (_, _) => Dispatcher.Invoke(Exit));
        tray.ContextMenuStrip = menu;
        tray.MouseClick += (_, e) => { if (e.Button == Forms.MouseButtons.Left) Dispatcher.Invoke(() => { if (IsVisible && IsActive && !edgeHidden) Hide(); else { RevealEdge(); Show(); Activate(); } }); };
        Closing += (_, e) => { if (!exiting) { e.Cancel = true; Dispatcher.BeginInvoke(new Action(CloseToDesktop)); } };
        LocationChanged += (_, _) => { if (IsLoaded && !edgeHidden && !edgeTransition) SavePreferences(); };
        StateChanged += (_, _) => { if (WindowState == WindowState.Minimized && keepWidgetOnClose) Dispatcher.BeginInvoke(new Action(() => { WindowState = WindowState.Normal; CloseToDesktop(); })); };
        SizeChanged += (_, _) => TrackWindowSize();
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
                cards.Children.Clear(); cards.Children.Add(MessageBubble("本地服务未启动：" + error.Message + "\n右键可打开侧边栏与设置。"));
                if (smoke)
                {
                    Directory.CreateDirectory(dataDirectory);
                    File.WriteAllText(Path.Combine(dataDirectory, "result.json"), JsonSerializer.Serialize(new { ok = false, error = error.ToString() }));
                    Environment.ExitCode = 1; Exit();
                }
            }
        };
        Dispatcher.UnhandledException += (_, e) => { status.Text = "操作失败：" + e.Exception.Message; Log(e.Exception.ToString()); e.Handled = true; };
        appearanceReady = true; ApplyPresentation();
    }

    static Brush Brush(string hex) => new SolidColorBrush((Color)ColorConverter.ConvertFromString(hex));
    static TextBlock Text(string value, double size = 13, bool bold = false) => new() { Text = value, FontSize = size, FontWeight = bold ? FontWeights.SemiBold : FontWeights.Normal, TextWrapping = TextWrapping.Wrap };
    static Button Button(string value, Action action)
    {
        var button = new Button { Content = value, Padding = new Thickness(9, 6, 9, 6), Margin = new Thickness(0, 0, 5, 5), Background = Brush("#EDF1E6"), Foreground = Ink, BorderThickness = new Thickness(0), Cursor = Cursors.Hand };
        button.Click += (_, _) => action(); return button;
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
        throw new InvalidOperationException("需要 Python 3.11+。请安装后重新打开游时。");
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
            UpdateReminderControls(state.RootElement.GetProperty("settings"));
            UpdateProviders(state.RootElement);
            UpdateWorkspace(state.RootElement);
            await UpdateProviderPictures();
            events = state.RootElement.GetProperty("events").Deserialize<CalendarEvent[]>(json)!;
            // Clone source JsonElements before the response document is disposed.
            events = events.Select(e => e with { Source = e.Source.Clone(), Artwork = e.Artwork.ValueKind == JsonValueKind.Undefined ? default : e.Artwork.Clone(), Reward = e.Reward.ValueKind == JsonValueKind.Undefined ? default : e.Reward.Clone() }).ToArray();
            var current = state.RootElement.GetProperty("versions").EnumerateArray().FirstOrDefault(v => v.TryGetProperty("reward_summary", out _));
            if (current.ValueKind == JsonValueKind.Object && current.TryGetProperty("reward_summary", out var summary))
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
        if (events.Length == 0) { cards.Children.Clear(); cards.Children.Add(MessageBubble("当前没有活动数据，可在侧栏同步或检查插件。")); ApplyWorkspaceVisuals(desktopLayout); return; }
        var scoped = events.Where(e => InScope(e.Game)).ToArray();
        rewardHeader.Visibility = !bubbleMode && ModuleVisible("rewards") && scoped.Any(e => e.Reward.ValueKind == JsonValueKind.Object) ? Visibility.Visible : Visibility.Collapsed;
        subtitle.Text = selected is DateTime day ? day.ToString("M月d日") + "的活动" : showAll ? "全部活动 · " + scoped.Length + " 项" : "接下来，别错过";
        allButton.Content = showAll ? "仅看近期" : "全部活动";
        densityButton.Content = dense ? "图文模式" : "紧凑模式";
        cards.Children.Clear();
        var now = DateTimeOffset.Now;
        var shown = showAll ? scoped.AsEnumerable() : scoped.Where(e => !e.Completed && !e.Ignored && e.Category != "maintenance");
        if (selected is DateTime chosen)
            shown = shown.Where(e => e.Begin != null && e.Begin.Value.LocalDateTime.Date <= chosen.Date && (e.Finish?.LocalDateTime.Date >= chosen.Date || (e.Time_Kind == "point" ? e.Begin.Value.LocalDateTime.Date == chosen.Date : e.Finish == null)));
        else if (!showAll) shown = shown.Where(e => e.Finish > now || e.Begin > now || (e.Finish == null && e.Time_Kind != "point"));
        if (hideFuture) shown = shown.Where(e => e.Begin != null && e.Begin <= now);
        var ordered = shown.OrderBy(e => eventOrder.GetValueOrDefault(e.Id, int.MaxValue)).ThenBy(e => e.Id).ToArray();
        foreach (var entry in ordered)
        {
            var color = categoryBrushes.GetValueOrDefault(entry.Category, LinkInk);
            var gameColor = Brush(GameColor(entry.Game));
            var card = new Border { Background = CardBackground(), CornerRadius = new CornerRadius(themeRadius), Padding = new Thickness(dense ? 10 : 14), Margin = new Thickness(0, 0, 0, Math.Max(2, (dense ? 6 : 9) + themeSpacing - 14)), BorderBrush = gameColor, BorderThickness = new Thickness(3, themeBorderWidth, themeBorderWidth, themeBorderWidth), Opacity = entry.Completed ? .65 : 1 };
            AttachBubbleDrag(card);
            var layout = new Grid(); layout.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(53) }); layout.ColumnDefinitions.Add(new ColumnDefinition());
            var badge = new Border { Width = 43, Height = 43, VerticalAlignment = VerticalAlignment.Top, HorizontalAlignment = HorizontalAlignment.Left, Background = Brushes.White, BorderBrush = gameColor, BorderThickness = new Thickness(1), CornerRadius = new CornerRadius(4), ToolTip = GameLabel(entry.Game) };
            badge.Child = (UIElement)GameIdentity(entry.Game); layout.Children.Add(badge);
            var body = new StackPanel();
            string type = entry.Category switch { "gacha" => "寻访", "exchange" => "兑换", "update" => "更新", "maintenance" => "维护", _ => "活动" };
            if (selectedGame is "all" or "selected") type = GameLabel(entry.Game) + " · " + type;
            var heading = new DockPanel();
            var done = Button(entry.Completed ? "↶" : "✓", async () => await Mutate("/api/events/" + entry.Id, new { completed = !entry.Completed }, true));
            done.ToolTip = entry.Completed ? "撤销完成" : "标记完成"; done.Padding = new Thickness(6, 2, 6, 2); DockPanel.SetDock(done, Dock.Right); heading.Children.Add(done);
            var title = new Grid { MinHeight = dense ? 23 : 42 };
            if (Picture(entry) is ImageSource picture)
                title.Children.Add(new Image { Source = picture, Width = 170, Height = dense ? 30 : 46, HorizontalAlignment = HorizontalAlignment.Left, VerticalAlignment = VerticalAlignment.Top, Stretch = Stretch.UniformToFill, Opacity = workspaceDark ? .25 : .42, OpacityMask = new LinearGradientBrush(Colors.Black, Colors.Transparent, new Point(0, .5), new Point(1, .5)), IsHitTestVisible = false });
            title.Children.Add(Text((entry.Starred ? "★ " : "") + entry.Title, dense ? 12 : 14, true)); heading.Children.Add(title); body.Children.Add(heading);
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
                body.Children.Add(new ProgressBar { Value = progress, Height = 3, Foreground = color, Background = TrackSurface, BorderThickness = new Thickness(0), Margin = new Thickness(0, dense ? 5 : 9, 0, dense ? 0 : 7) });
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
            actions.Children.Add(Button(entry.Starred ? "取消收藏" : "☆ 收藏", async () => await Mutate("/api/events/" + entry.Id, new { starred = !entry.Starred }, true)));
            actions.Children.Add(Button("标记完成", async () => await Mutate("/api/events/" + entry.Id, new { completed = true }, true)));
            if (entry.Source.TryGetProperty("url", out var source)) actions.Children.Add(Button(entry.Game == "arknights-cn" ? "PRTS ↗" : "公告 ↗", () => Open(source.GetString()!)));
            if (!dense) body.Children.Add(actions); Grid.SetColumn(body, 1); layout.Children.Add(body); card.Child = layout; cards.Children.Add(card);
        }
        if (ordered.Length == 0) cards.Children.Add(MessageBubble("没有符合当前筛选的待办活动。右键打开侧栏，可切换游戏或显示全部活动。"));
        status.Text = $"当前 {ordered.Length} / 共 {scoped.Length} 项 · {lastRefresh:HH:mm:ss} 更新\n" + (selectedGame == "arknights-cn" ? "来源：PRTS 社区 Wiki · 国服 · 可在完整时间轴查看修订。" : showAll ? "包含已结束、已完成及维护，滚动查看所有条目。" : "近期视图隐藏已结束和已完成，点“全部活动”查看。");
        ApplyWorkspaceVisuals(desktopLayout);
    }
    ImageSource? currencyIcon;
    readonly Dictionary<string, ImageSource> gamePictures = new();
    ImageSource? GamePicture(string game)
    {
        if (gamePictures.TryGetValue(game, out var cached)) return cached;
        try
        {
            var icon = games.FirstOrDefault(g => g.Id == game)?.Icon;
            if (icon == null || !System.Text.RegularExpressions.Regex.IsMatch(icon, @"^/assets/[A-Za-z0-9_.-]+\.png$")) return null;
            var image = new System.Windows.Media.Imaging.BitmapImage(); image.BeginInit();
            image.CacheOption = System.Windows.Media.Imaging.BitmapCacheOption.OnLoad;
            image.UriSource = new Uri(Path.Combine(root, "web", "assets", Path.GetFileName(icon))); image.EndInit();
            image.Freeze(); gamePictures[game] = image; return image;
        }
        catch { return null; }
    }
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
        exiting = true; timer.Stop(); edgeTimer.Stop(); SavePreferences(); tray.Visible = false; tray.Dispose(); trayIcon.Dispose();
        // Only stop the backend created by this desktop process.
        if (backend != null) { try { if (!backend.HasExited) backend.Kill(true); } catch { } backend.Dispose(); }
        http.Dispose(); Close(); System.Windows.Application.Current.Shutdown(Environment.ExitCode);
    }

    async Task SmokeTest()
    {
        try
        {
            if (events.Length < 1 || cards.Children.Count < 1 || csrf == "") throw new Exception("Desktop did not load real calendar data");
            if (GamePicture("arknights-cn") == null || GamePicture("endfield-cn") == null) throw new Exception("Game identity icons did not load");
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
            gameSelect.SelectedValue = "arknights-cn"; Render(); SaveRender("arknights.png");
            if (cards.Children.Count != events.Count(e => e.Game == "arknights-cn") || rewardHeader.Visibility != Visibility.Collapsed) throw new Exception("Arknights filter or currency isolation failed");
            byte[] arkIcs = await http.GetByteArrayAsync(address + "/api/calendar.ics?game=arknights-cn");
            if (!System.Text.Encoding.UTF8.GetString(arkIcs).Contains("PRTS")) throw new Exception("Arknights calendar attribution missing");
            gameSelect.SelectedValue = "sample-game"; Render(); SaveRender("plugin.png");
            if (cards.Children.Count != 2 || rewardHeader.Visibility != Visibility.Collapsed || GamePicture("sample-game") != null) throw new Exception("Third-party game metadata or currency isolation failed");
            if (games.Any(g => g.Id == "nikke-global"))
            {
                gameSelect.SelectedValue = "nikke-global"; Render(); SaveRender("nikke.png");
                if (cards.Children.Count != events.Count(e => e.Game == "nikke-global") || GamePicture("nikke-global") == null || rewardHeader.Visibility != Visibility.Collapsed) throw new Exception("NIKKE plugin, local logo or currency isolation failed");
            }
            if (games.Any(g => g.Id == "azurlane-cn"))
            {
                gameSelect.SelectedValue = "azurlane-cn"; Render(); SaveRender("azurlane.png");
                if (cards.Children.Count != events.Count(e => e.Game == "azurlane-cn") || cards.Children.Count == 0 || rewardHeader.Visibility != Visibility.Collapsed) throw new Exception("Azur Lane plugin or currency isolation failed");
            }
            if (games.Any(g => g.Id == "zzz-cn"))
            {
                gameSelect.SelectedValue = "zzz-cn"; Render(); SaveRender("zzz.png");
                if (cards.Children.Count != events.Count(e => e.Game == "zzz-cn") || cards.Children.Count == 0 || GamePicture("zzz-cn") == null || rewardHeader.Visibility != Visibility.Collapsed) throw new Exception("ZZZ plugin, APP icon or currency isolation failed");
            }
            await Mutate("/api/workspace", new { view = new { selected_games = new[] { "endfield-cn", "sample-game" }, scope = "selected", sort = "remaining" } }, true);
            gameSelect.SelectedValue = "selected"; Render(); SaveRender("selected-games.png");
            if (cards.Children.Count != events.Count(e => e.Game is "endfield-cn" or "sample-game")) throw new Exception("Custom game combination omitted or leaked entries");
            byte[] selectedIcs = await http.GetByteArrayAsync(address + "/api/calendar.ics?game=selected");
            if (System.Text.Encoding.UTF8.GetString(selectedIcs).Contains("PRTS")) throw new Exception("Custom game export leaked unselected games");
            gameSelect.SelectedValue = "all"; Render(); SaveRender("combined.png");
            if (cards.Children.Count != events.Length) throw new Exception("All-activities view omitted entries");
            bool workspaceVerified = await WorkspaceSmokeTest();
            bool appearanceVerified = await AppearanceSmokeTest();
            bool interactionVerified = await InteractionSmokeTest();
            month.SelectedDate = DateTime.Today.AddDays(1);
            if (selected != DateTime.Today.AddDays(1)) throw new Exception("Calendar selection did not update");
            await File.WriteAllTextAsync(Path.Combine(dataDirectory, "result.json"), JsonSerializer.Serialize(new { ok = true, entries = events.Length, workspaceVerified, appearanceVerified, interactionVerified, verifiedRewardTotal, exportBytes = ics.Length, backendPid = backend?.Id }));
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
        var bitmap = CaptureRender();
        var encoder = new System.Windows.Media.Imaging.PngBitmapEncoder();
        encoder.Frames.Add(System.Windows.Media.Imaging.BitmapFrame.Create(bitmap));
        Directory.CreateDirectory(dataDirectory);
        using var stream = File.Create(Path.Combine(dataDirectory, name)); encoder.Save(stream);
    }
}
