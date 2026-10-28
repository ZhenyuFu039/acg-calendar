using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;

namespace Playtime;

// Desktop parity with the web page: sync stages and results, reminder game scope,
// personal backups and the plugin list, all through the same local API.
public sealed partial class CalendarWindow
{
    readonly TextBlock syncProgress = Text("", 10), backupStatus = Text("", 10), pluginStatus = Text("", 10);
    readonly StackPanel pluginList = new(), reminderGames = new();
    Button restartButton = null!;
    readonly RadioButton[] scopeRadios = [new() { Content = "全部已安装游戏", Tag = "all" }, new() { Content = "跟随当前游戏组合", Tag = "follow" }, new() { Content = "自选游戏", Tag = "custom" }];
    JsonElement reminderScope;
    string[] workspaceGames = [];
    string reminderGamesSignature = "";
    bool pluginsLoaded;

    void BuildReminderScopeControls()
    {
        foreach (var radio in scopeRadios)
        {
            radio.GroupName = "reminder-scope"; radio.FontSize = 11; radio.Margin = new Thickness(12, 0, 0, 5);
            radio.Click += async (_, _) => { if (!updatingSettings) await SaveReminderScope((string)radio.Tag); };
            reminderSettings.Children.Add(radio);
        }
        reminderGames.Margin = new Thickness(28, 0, 0, 6); reminderSettings.Children.Add(reminderGames);
    }

    void BuildPluginControls()
    {
        var title = Text("已安装插件", 10, true); title.Foreground = Muted; title.SetValue(MutedRoleProperty, true); title.Margin = new Thickness(0, 6, 0, 4);
        navigation.Children.Add(title); navigation.Children.Add(pluginList);
        pluginStatus.Foreground = Muted; pluginStatus.SetValue(MutedRoleProperty, true); pluginStatus.Margin = new Thickness(0, 0, 0, 6); navigation.Children.Add(pluginStatus);
        restartButton = SidebarButton("重启游时，应用插件更改", Restart); restartButton.Visibility = Visibility.Collapsed; navigation.Children.Add(restartButton);
    }

    void BuildSyncAndBackupControls()
    {
        Section(networkSettings, "同步进度");
        syncProgress.Margin = new Thickness(0, 0, 0, 8); networkSettings.Children.Add(syncProgress);
        Section(networkSettings, "个人数据备份");
        var help = Text("备份收藏、完成、备注、校正、个人计划与设置，不含官方活动和缓存。恢复在网页中确认后进行，恢复前会自动备份当前数据。", 10);
        help.Foreground = Muted; help.SetValue(MutedRoleProperty, true); help.Margin = new Thickness(0, 0, 0, 6); networkSettings.Children.Add(help);
        backupStatus.Margin = new Thickness(0, 0, 0, 6); networkSettings.Children.Add(backupStatus);
        networkSettings.Children.Add(SidebarButton("立即备份", async () => await CreateBackup()));
        networkSettings.Children.Add(SidebarButton("备份与恢复 ↗", () => Open(address + "?appearance=network")));
    }

    async Task<JsonDocument> Send(HttpMethod method, string path, object? payload = null)
    {
        using var request = new HttpRequestMessage(method, address + path);
        if (payload != null) { request.Content = new StringContent(JsonSerializer.Serialize(payload), System.Text.Encoding.UTF8, "application/json"); request.Headers.Add("X-Calendar-Token", csrf); }
        using var response = await http.SendAsync(request);
        var body = await response.Content.ReadAsStringAsync();
        var document = JsonDocument.Parse(body);
        if (!response.IsSuccessStatusCode) throw new Exception(document.RootElement.TryGetProperty("error", out var error) ? error.GetString() : response.ReasonPhrase);
        return document;
    }

    // Same wording as the web page: named stages and waits while running, the last round's result afterwards.
    internal static string SyncProgressText(JsonElement s)
    {
        if (s.ValueKind != JsonValueKind.Object) return "";
        if (s.TryGetProperty("running", out var running) && running.ValueKind == JsonValueKind.True)
        {
            var parts = new List<string>();
            var stage = s.TryGetProperty("stage", out var st) && st.ValueKind == JsonValueKind.Object ? st : default;
            if (stage.ValueKind == JsonValueKind.Object && stage.GetProperty("name").GetString() == "images" && s.TryGetProperty("calendar_saved_at", out var saved) && saved.ValueKind == JsonValueKind.String) parts.Add("日历已更新");
            parts.Add((stage.ValueKind == JsonValueKind.Object ? stage.GetProperty("label").GetString() : "正在同步") + "…");
            if (s.TryGetProperty("waiting", out var wait) && wait.ValueKind == JsonValueKind.Object) parts.Add($"等待 {wait.GetProperty("site").GetString()} 请求间隔约 {wait.GetProperty("seconds").GetInt32()} 秒");
            return string.Join(" · ", parts);
        }
        if (s.TryGetProperty("error", out var error) && error.ValueKind == JsonValueKind.String && error.GetString() != "") return "上次同步失败，保留本地日历：" + error.GetString();
        if (!s.TryGetProperty("result", out var result) || result.ValueKind != JsonValueKind.Object || !result.TryGetProperty("calendar", out var c)) return "";
        int Count(JsonElement e, string key) => e.TryGetProperty(key, out var v) && v.ValueKind == JsonValueKind.Number ? v.GetInt32() : 0;
        int added = Count(c, "added"), updated = Count(c, "updated"), removed = Count(c, "removed");
        var text = "上次同步：" + (added + updated + removed > 0 ? $"新增 {added} · 更新 {updated}" + (removed > 0 ? $" · 移除 {removed}" : "") : "活动无变化");
        if (result.TryGetProperty("images", out var images))
        {
            if (Count(images, "added") > 0) text += $" · 补齐图片 {Count(images, "added")} 张";
            if (Count(images, "failed") > 0) text += $" · {Count(images, "failed")} 张图片暂不可用";
            if (images.TryGetProperty("notes", out var notes) && notes.ValueKind == JsonValueKind.Array) foreach (var note in notes.EnumerateArray()) text += " · " + note.GetString();
        }
        return text;
    }

    void UpdateSyncProgress(JsonElement state)
    {
        if (!state.TryGetProperty("sync_by_game", out var all)) return;
        var lines = games.Where(g => InScope(g.Id) && all.TryGetProperty(g.Id, out _)).Select(g => (g, text: SyncProgressText(all.GetProperty(g.Id)))).Where(x => x.text != "")
            .Select(x => (selectedGame is "all" or "selected" ? x.g.Short_Name + "：" : "") + x.text).ToArray();
        syncProgress.Text = lines.Length > 0 ? string.Join("\n", lines) : "还没有本轮同步记录。";
        UpdateQuickSync(all);
    }

    void UpdateReminderScope(JsonElement state)
    {
        reminderScope = state.GetProperty("settings").TryGetProperty("reminder_scope", out var scope) ? scope.Clone() : default;
        workspaceGames = state.GetProperty("workspace").GetProperty("view").GetProperty("selected_games").EnumerateArray().Select(g => g.GetString()!).ToArray();
        var mode = reminderScope.ValueKind == JsonValueKind.Object ? reminderScope.GetProperty("mode").GetString() : "all";
        var chosen = ScopeGames() ?? [];
        var options = games.Select(g => (g.Id, Label: g.Short_Name + " · " + g.Server)).Concat(chosen.Where(id => games.All(g => g.Id != id)).Select(id => (Id: id, Label: id + "（当前不可用，保留选择）"))).ToArray();
        updatingSettings = true;
        try
        {
            foreach (var radio in scopeRadios) radio.IsChecked = (string)radio.Tag == mode;
            var signature = string.Join("|", options.Select(o => o.Id + "=" + o.Label));
            if (signature != reminderGamesSignature)
            {
                reminderGamesSignature = signature; reminderGames.Children.Clear();
                foreach (var (id, label) in options)
                {
                    var box = new CheckBox { Content = label, Tag = id, FontSize = 11, Margin = new Thickness(0, 0, 0, 4) };
                    box.Click += async (_, _) => { if (!updatingSettings) await SaveReminderScope("custom"); };
                    reminderGames.Children.Add(box);
                }
            }
            foreach (CheckBox box in reminderGames.Children) box.IsChecked = chosen.Contains((string)box.Tag);
            reminderGames.Visibility = mode == "custom" ? Visibility.Visible : Visibility.Collapsed;
        }
        finally { updatingSettings = false; }
    }

    string[]? ScopeGames() => reminderScope.ValueKind == JsonValueKind.Object && reminderScope.GetProperty("games").ValueKind == JsonValueKind.Array
        ? reminderScope.GetProperty("games").EnumerateArray().Select(g => g.GetString()!).ToArray() : null;

    // Same rule as the web page: the custom list (even an empty one) is kept across modes;
    // only a list that was never saved starts from the current game combination.
    internal async Task SaveReminderScope(string mode)
    {
        string[]? chosen = ScopeGames();
        if (mode == "custom" && reminderGames.Visibility == Visibility.Visible) chosen = reminderGames.Children.OfType<CheckBox>().Where(b => b.IsChecked == true).Select(b => (string)b.Tag).ToArray();
        else if (mode == "custom" && chosen == null) chosen = workspaceGames;
        await Mutate("/api/settings", new { reminder_scope = new { mode, games = chosen } });
    }

    internal async Task CreateBackup()
    {
        try
        {
            using var result = await Send(HttpMethod.Post, "/api/backups", new { });
            backupStatus.Text = "已备份到本机：" + result.RootElement.GetProperty("backup").GetProperty("name").GetString();
        }
        catch (Exception error) { backupStatus.Text = "备份失败：" + error.Message; }
    }

    async Task LoadBackupStatus()
    {
        try
        {
            using var list = await Send(HttpMethod.Get, "/api/backups");
            var latest = list.RootElement.GetProperty("backups").EnumerateArray().FirstOrDefault(b => b.TryGetProperty("created_at", out _));
            var reasons = new Dictionary<string, string> { ["manual"] = "手动备份", ["upgrade"] = "升级前自动备份", ["before-restore"] = "恢复前自动备份", ["before-clear"] = "清除记录前自动备份" };
            backupStatus.Text = latest.ValueKind != JsonValueKind.Object ? "还没有备份。" : "最近备份：" + DateTimeOffset.Parse(latest.GetProperty("created_at").GetString()!).ToLocalTime().ToString("MM/dd HH:mm") + " · " + reasons.GetValueOrDefault(latest.GetProperty("reason").GetString()!, "备份");
        }
        catch (Exception error) { backupStatus.Text = "无法读取备份：" + error.Message; }
    }

    internal async Task LoadPlugins()
    {
        try
        {
            using var report = await Send(HttpMethod.Get, "/api/plugins");
            var rows = report.RootElement.GetProperty("plugins").EnumerateArray().Select(r => r.Clone()).ToArray();
            var labels = new Dictionary<string, string> { ["loaded"] = "已加载", ["disabled"] = "已停用", ["manifest_disabled"] = "插件自身设为停用", ["error"] = "加载失败" };
            pluginList.Children.Clear();
            foreach (var row in rows)
            {
                string Get(string key) => row.TryGetProperty(key, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString()! : "";
                var folder = Get("folder"); var status = Get("status");
                bool wantOn = row.GetProperty("enabled_setting").GetBoolean();
                var line = Text((Get("name") == "" ? folder : Get("name")) + " · " + labels.GetValueOrDefault(status, status) + (row.GetProperty("restart_required").GetBoolean() ? (wantOn ? " · 已设为启用，重启后生效" : " · 已设为停用，重启后生效") : "") + (Get("error") != "" ? " · " + Get("error") : ""), 10);
                line.TextWrapping = TextWrapping.Wrap;
                var panel = new DockPanel { Margin = new Thickness(0, 0, 0, 5), Tag = folder };
                if (status != "manifest_disabled")
                {
                    var toggle = Button(wantOn ? "停用" : "启用", async () => await TogglePlugin(folder, !wantOn));
                    toggle.Padding = new Thickness(6, 1, 6, 1); toggle.Margin = new Thickness(6, 0, 0, 0); DockPanel.SetDock(toggle, Dock.Right); panel.Children.Add(toggle);
                }
                panel.Children.Add(line); pluginList.Children.Add(panel);
            }
            if (rows.Length == 0) pluginList.Children.Add(Text("插件目录中还没有插件。", 10));
            restartButton.Visibility = rows.Any(r => r.GetProperty("restart_required").GetBoolean()) ? Visibility.Visible : Visibility.Collapsed;
            pluginsLoaded = true;
        }
        catch (Exception error) { pluginStatus.Text = "无法读取插件：" + error.Message; }
    }

    // One plugin per request, the list is disabled meanwhile; never removes personal records.
    internal async Task TogglePlugin(string folder, bool enabled)
    {
        pluginList.IsEnabled = false;
        try
        {
            using var _ = await Send(HttpMethod.Post, "/api/plugins/state", new { folder, enabled });
            pluginStatus.Text = "已保存，重启游时后生效；个人记录不会被删除。";
        }
        catch (Exception error) { pluginStatus.Text = "操作失败：" + error.Message; }
        finally { pluginList.IsEnabled = true; await LoadPlugins(); }
    }

    string appliedWindowRestore = "";
    string confirmedWindowRestore = "";

    // A restored backup hands its window preferences over through the state; the desktop owns
    // its window file, so it writes and loads them itself and then confirms the hand-over.
    async Task ApplyPendingWindow(JsonElement state)
    {
        if (!state.TryGetProperty("desktop_window_pending", out var pending) || pending.ValueKind != JsonValueKind.Object) return;
        bool hasId = pending.TryGetProperty("id", out var id) && id.ValueKind == JsonValueKind.String;
        var restoreId = hasId ? id.GetString()! : pending.GetProperty("restored_at").GetString() ?? "";
        if (restoreId == "" || restoreId == confirmedWindowRestore) return;
        try
        {
            if (restoreId != appliedWindowRestore)
            {
                Directory.CreateDirectory(Path.GetDirectoryName(preferences)!);
                string temporary = preferences + ".restore.tmp";
                File.WriteAllText(temporary, pending.GetProperty("preferences").GetRawText());
                File.Move(temporary, preferences, true);
                LoadPreferences(); ApplyPresentation(); SavePreferences();
                appliedWindowRestore = restoreId;
                status.Text = "已应用备份中的桌面窗口位置与外观。";
            }
            // A failed acknowledgement retries delivery without reapplying geometry over
            // changes the user made after the successful application.
            object acknowledgement = hasId ? new { id = restoreId } : new { restored_at = restoreId };
            using var _ = await Send(HttpMethod.Post, "/api/desktop/window-applied", acknowledgement);
            confirmedWindowRestore = restoreId;
        }
        catch (Exception error) { status.Text = (appliedWindowRestore == restoreId ? "窗口偏好已应用，确认未完成，将自动重试：" : "无法应用窗口偏好，将自动重试：") + error.Message; }
    }

    // Lists that are not part of the regular poll load when their page opens.
    async Task LoadPageData(string page)
    {
        if (address == "" || csrf == "") return;
        if (page == "calendar") await LoadPlugins();
        if (page == "network") await LoadBackupStatus();
    }

    async Task WindowRestoreRetrySmokeTest()
    {
        bool timerWasRunning = timer.IsEnabled;
        timer.Stop();
        double originalOpacity = cardOpacity;
        string originalCsrf = csrf;
        try
        {
            for (int i = 0; i < 40 && refreshing; i++) await Task.Delay(50);
            if (refreshing) throw new Exception("Refresh did not finish before window restore test");
            SavePreferences();
            using var made = await Send(HttpMethod.Post, "/api/backups", new { });
            string name = made.RootElement.GetProperty("backup").GetProperty("name").GetString()!;
            double changed = Math.Abs(originalOpacity - .5) < .01 ? .7 : .5;
            cardOpacity = changed; SavePreferences();
            using var restored = await Send(HttpMethod.Post, "/api/backups/restore", new { name });
            using var queued = await Send(HttpMethod.Get, "/api/state");
            string restoreId = queued.RootElement.GetProperty("desktop_window_pending").GetProperty("id").GetString()!;
            // A transient file lock must leave the restore eligible for the next refresh.
            using (var locked = new FileStream(preferences, FileMode.Open, FileAccess.ReadWrite, FileShare.None))
                await ApplyPendingWindow(queued.RootElement);
            if (appliedWindowRestore == restoreId || Math.Abs(cardOpacity - changed) > .001) throw new Exception("Failed window restore was marked as applied");
            // Force only the acknowledgement to fail, after the successful application.
            csrf = "smoke-test-invalid-token";
            await ApplyPendingWindow(queued.RootElement);
            if (appliedWindowRestore != restoreId || confirmedWindowRestore == restoreId || Math.Abs(cardOpacity - originalOpacity) > .001) throw new Exception("Window restore did not retry after the file lock");
            csrf = originalCsrf;
            double userEdit = Math.Abs(originalOpacity - .6) < .01 ? .7 : .6;
            cardOpacity = userEdit; SavePreferences();
            await ApplyPendingWindow(queued.RootElement);
            if (confirmedWindowRestore != restoreId || Math.Abs(cardOpacity - userEdit) > .001) throw new Exception("Acknowledgement retry reapplied window preferences over a user edit");
            using (var after = await Send(HttpMethod.Get, "/api/state"))
                if (after.RootElement.GetProperty("desktop_window_pending").ValueKind != JsonValueKind.Null) throw new Exception("Retried window acknowledgement did not clear pending");
            // Distinct IDs with an identical display timestamp must both apply. These local
            // fixtures have no server pending, so their acknowledgement failures are expected.
            foreach (double opacity in new[] { .4, .8 })
            {
                using var fixture = JsonDocument.Parse(JsonSerializer.Serialize(new { desktop_window_pending = new { id = Guid.NewGuid().ToString("N"), restored_at = "2026-10-26T00:00:00Z", preferences = new { card_opacity = opacity } } }));
                await ApplyPendingWindow(fixture.RootElement);
                if (Math.Abs(cardOpacity - opacity) > .001) throw new Exception("A distinct window restore was skipped because its timestamp matched");
            }
        }
        finally
        {
            csrf = originalCsrf; cardOpacity = originalOpacity; SavePreferences();
            if (timerWasRunning) timer.Start();
        }
    }

    async Task<bool> ParitySmokeTest()
    {
        using (var running = JsonDocument.Parse("{\"running\":true,\"stage\":{\"name\":\"images\",\"label\":\"补齐活动图片\"},\"calendar_saved_at\":\"2026-10-25T00:00:00Z\",\"waiting\":{\"site\":\"web.hycdn.cn\",\"seconds\":10}}"))
            if (SyncProgressText(running.RootElement) != "日历已更新 · 补齐活动图片… · 等待 web.hycdn.cn 请求间隔约 10 秒") throw new Exception("Desktop sync stage text differs from the web page");
        using (var done = JsonDocument.Parse("{\"running\":false,\"error\":null,\"result\":{\"calendar\":{\"added\":2,\"updated\":1,\"removed\":0},\"images\":{\"added\":3,\"failed\":1,\"notes\":[]}}}"))
            if (SyncProgressText(done.RootElement) != "上次同步：新增 2 · 更新 1 · 补齐图片 3 张 · 1 张图片暂不可用") throw new Exception("Desktop sync result text differs from the web page");
        if (string.IsNullOrEmpty(syncProgress.Text)) throw new Exception("Sync progress line is empty");
        // Reminder scope: custom starts from the combination, an emptied list stays empty, all restores.
        await SaveReminderScope("custom");
        if (scopeRadios[2].IsChecked != true || reminderGames.Visibility != Visibility.Visible || ScopeGames() is not { Length: > 0 }) throw new Exception("Desktop custom reminder scope did not start from the combination");
        foreach (CheckBox box in reminderGames.Children) box.IsChecked = false;
        await SaveReminderScope("custom"); await SaveReminderScope("all"); await SaveReminderScope("custom");
        if (ScopeGames() is not { Length: 0 }) throw new Exception("An emptied desktop reminder list was refilled");
        await SaveReminderScope("all");
        if (scopeRadios[0].IsChecked != true) throw new Exception("Desktop reminder scope did not return to all games");
        // Backup entry.
        await CreateBackup(); await LoadBackupStatus();
        if (!backupStatus.Text.StartsWith("最近备份：")) throw new Exception("Desktop backup was not listed");
        // Plugins: the sample plugin can be turned off and back on (applies after a restart).
        await LoadPlugins();
        var row = pluginList.Children.OfType<DockPanel>().FirstOrDefault(p => (string)p.Tag == "sample-game") ?? throw new Exception("Desktop plugin list misses the sample plugin");
        await TogglePlugin("sample-game", false);
        if (restartButton.Visibility != Visibility.Visible) throw new Exception("Restart button did not appear for a pending plugin change");
        using (var report = await Send(HttpMethod.Get, "/api/plugins"))
            if (!report.RootElement.GetProperty("plugins").EnumerateArray().Any(r => r.GetProperty("folder").GetString() == "sample-game" && !r.GetProperty("enabled_setting").GetBoolean() && r.GetProperty("restart_required").GetBoolean())) throw new Exception("Desktop plugin toggle did not save");
        await TogglePlugin("sample-game", true);
        if (restartButton.Visibility != Visibility.Collapsed) throw new Exception("Restart button stayed after the change was undone");
        // Window preferences come back with a restore: back up, change the card opacity, restore.
        double opacity = cardOpacity;
        using (var made = await Send(HttpMethod.Post, "/api/backups", new { }))
        {
            var name = made.RootElement.GetProperty("backup").GetProperty("name").GetString();
            cardOpacity = Math.Abs(opacity - .5) < .01 ? .7 : .5; SavePreferences();
            using var restored = await Send(HttpMethod.Post, "/api/backups/restore", new { name });
        }
        // A timer refresh may already be running, in which case Refresh() returns at once; poll a little.
        for (int i = 0; i < 10 && Math.Abs(cardOpacity - opacity) > .001; i++) { await Refresh(); await Task.Delay(200); }
        if (Math.Abs(cardOpacity - opacity) > .001) throw new Exception("Restored window preferences were not applied");
        using (var after = JsonDocument.Parse(await http.GetStringAsync(address + "/api/state")))
            if (after.RootElement.GetProperty("desktop_window_pending").ValueKind != JsonValueKind.Null) throw new Exception("Window hand-over was not confirmed");
        await WindowRestoreRetrySmokeTest();
        OpenSidebarPage("reminders"); SaveRender("parity-reminders.png"); OpenSidebarPage("network"); await LoadPageData("network"); SaveRender("parity-network.png");
        OpenSidebarPage("calendar"); await LoadPageData("calendar"); SaveRender("parity-plugins.png");
        return pluginsLoaded;
    }
}
