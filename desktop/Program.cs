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
        using var instance = new Mutex(true, "Local\\PlaytimeDesktop-" + StableKey(Path.GetFullPath(root)), out bool first);
        if (!first) { MessageBox.Show("游时已在运行，请点击系统托盘中的日历图标。", "游时"); return; }
        var app = new System.Windows.Application { ShutdownMode = ShutdownMode.OnExplicitShutdown };
        var window = new CalendarWindow(Path.GetFullPath(root), args.Contains("--offline"), args.Contains("--smoke-test"));
        app.Run(window);
    }

    static string StableKey(string text) => Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(System.Text.Encoding.UTF8.GetBytes(text.ToUpperInvariant())))[..16];
}

public record Endpoint(string? At, string State);
public record CalendarEvent(string Id, string Title, string Category, Endpoint Start, Endpoint End, bool Completed, bool Ignored, string Time_Kind, JsonElement Source)
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
        var toolbar = new WrapPanel { Margin = new Thickness(0, 10, 0, 0) };
        mode = Button("展开月历", ToggleMode); toolbar.Children.Add(mode);
        toolbar.Children.Add(Button("近期", () => { selected = null; month.SelectedDate = null; Render(); }));
        toolbar.Children.Add(Button("同步", async () => await Mutate("/api/sync", new { })));
        toolbar.Children.Add(Button("完整时间轴 ↗", () => Open(address)));
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
        menu.Items.Add("打开完整时间轴", null, (_, _) => Open(address));
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
        }
        catch { }
    }
    void SavePreferences()
    {
        try { Directory.CreateDirectory(Path.GetDirectoryName(preferences)!); File.WriteAllText(preferences, JsonSerializer.Serialize(new { left = Left, top = Top, topmost = Topmost })); }
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
            events = events.Select(e => e with { Source = e.Source.Clone() }).ToArray();
            lastRefresh = DateTimeOffset.Now; Render();
        }
        catch (Exception error) { status.Text = "连接中断，已保留上次数据。"; Log(error.Message); }
        finally { refreshing = false; }
    }
    void Render()
    {
        clock.Text = DateTime.Now.ToString("M月d日 dddd HH:mm") + " · 本机时区";
        if (events.Length == 0) return;
        subtitle.Text = selected is DateTime day ? day.ToString("M月d日") + "的活动" : "接下来，别错过";
        cards.Children.Clear();
        var now = DateTimeOffset.Now;
        var shown = events.Where(e => !e.Completed && !e.Ignored && e.Category != "maintenance");
        if (selected is DateTime chosen)
            shown = shown.Where(e => e.Begin != null && e.Begin.Value.LocalDateTime.Date <= chosen.Date && (e.Finish?.LocalDateTime.Date >= chosen.Date || (e.Time_Kind == "point" ? e.Begin.Value.LocalDateTime.Date == chosen.Date : e.Finish == null)));
        else shown = shown.Where(e => e.Finish > now || e.Begin > now || (e.Finish == null && e.Time_Kind != "point"));
        var ordered = shown.OrderBy(e => e.Next ?? DateTimeOffset.MaxValue).ToArray();
        foreach (var entry in ordered)
        {
            var card = new Border { Background = Brushes.White, CornerRadius = new CornerRadius(9), Padding = new Thickness(13), Margin = new Thickness(0, 0, 0, 9), BorderBrush = Brush("#E3E8DA"), BorderThickness = new Thickness(1) };
            var body = new StackPanel();
            string type = entry.Category switch { "gacha" => "寻访", "exchange" => "兑换", "update" => "更新", _ => "活动" };
            var tag = Text(type, 10); tag.Foreground = Muted; body.Children.Add(tag);
            body.Children.Add(Text(entry.Title, 14, true));
            bool upcoming = entry.Begin > now;
            var due = upcoming ? entry.Begin : entry.Finish;
            string certainty = (upcoming ? entry.Start.State : entry.End.State) == "estimated" ? "预计 " : "";
            bool point = entry.Time_Kind == "point";
            string remaining = point ? (upcoming ? Countdown(entry.Begin!.Value - now) + "后上线" : "已上线") : due == null ? "截止待定" : (due <= now ? "已结束" : Countdown(due.Value - now) + (upcoming ? "后开始" : "后截止"));
            var countdown = Text(certainty + remaining, 12, true); countdown.Foreground = Brush(due != null && due - now < TimeSpan.FromDays(1) ? "#BA542E" : "#4B692E"); countdown.Margin = new Thickness(0, 7, 0, 3); body.Children.Add(countdown);
            var dates = Text((point ? "更新 " : "开始 ") + Format(entry.Begin) + (entry.Start.State == "estimated" ? "（预计）" : "") + (point ? "" : "\n截止 " + Format(entry.Finish) + (entry.End.State == "estimated" ? "（预计）" : "")), 11); dates.Foreground = Muted; body.Children.Add(dates);
            if (entry.Begin != null && entry.Finish > entry.Begin)
            {
                double progress = Math.Clamp((now - entry.Begin.Value).TotalSeconds / (entry.Finish.Value - entry.Begin.Value).TotalSeconds * 100, 0, 100);
                body.Children.Add(new ProgressBar { Value = progress, Height = 4, Foreground = Green, Background = Brush("#EDF1E6"), BorderThickness = new Thickness(0), Margin = new Thickness(0, 9, 0, 7) });
            }
            var actions = new WrapPanel { Margin = new Thickness(0, 7, 0, 0) };
            actions.Children.Add(Button("标记完成", async () => await Mutate("/api/events/" + entry.Id, new { completed = true }, true)));
            if (entry.Source.TryGetProperty("url", out var source)) actions.Children.Add(Button("公告 ↗", () => Open(source.GetString()!)));
            body.Children.Add(actions); card.Child = body; cards.Children.Add(card);
        }
        if (ordered.Length == 0) cards.Children.Add(Text("这一天没有待办活动，换一天看看。", 13));
        status.Text = $"终末地国服 · {ordered.Length} 项活动 · {lastRefresh:HH:mm:ss} 更新\n收起窗口后继续运行，右键托盘图标可退出。";
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
            var dialog = new Microsoft.Win32.SaveFileDialog { FileName = "游时-终末地.ics", Filter = "iCalendar 日历 (*.ics)|*.ics" };
            if (dialog.ShowDialog(this) != true) return;
            var bytes = await http.GetByteArrayAsync(address + "/api/calendar.ics");
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
            SaveRender("widget.png"); ShowMode(false); SaveRender("month.png");
            month.SelectedDate = DateTime.Today.AddDays(1);
            if (selected != DateTime.Today.AddDays(1)) throw new Exception("Calendar selection did not update");
            await File.WriteAllTextAsync(Path.Combine(dataDirectory, "result.json"), JsonSerializer.Serialize(new { ok = true, entries = events.Length, exportBytes = ics.Length, backendPid = backend?.Id }));
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
