using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.IO;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace Playtime;

public record GameDescriptor(string Id, string Name, string Short_Name, string Server, string Timezone, string Source_Label, string? Icon, string Color, string Monogram)
{
    public string Label => Name + " · " + Server;
}
public record GameChoice(string Id, string Label);

public sealed partial class CalendarWindow
{
    GameDescriptor[] games = [];
    HashSet<string> selectedGames = [];
    bool updatingProviders, hideFuture;
    Dictionary<string, int> eventOrder = new();
    readonly StackPanel gameCombination = new();
    string combinationSignature = "";
    readonly Dictionary<string, string> providerPicturePaths = new();

    string PluginDirectory => Path.GetFullPath(Path.Combine(dataDirectory, "plugins"));

    void OpenPluginDirectory()
    {
        try
        {
            Directory.CreateDirectory(PluginDirectory);
            Process.Start(new ProcessStartInfo(PluginDirectory) { UseShellExecute = true });
            status.Text = "已打开日历插件目录。放入或移出完整插件文件夹后，从托盘退出并重新启动游时。";
        }
        catch (Exception error) { status.Text = "无法打开插件目录：" + error.Message; }
    }

    async Task UpdateProviderPictures()
    {
        foreach (var game in games)
        {
            if (game.Icon == null || !Regex.IsMatch(game.Icon, @"^/api/artwork/[a-f0-9]{32}\.(png|jpg|webp)$") || providerPicturePaths.GetValueOrDefault(game.Id) == game.Icon) continue;
            try
            {
                var data = await http.GetByteArrayAsync(address + game.Icon);
                if (data.Length > 5_000_000) continue;
                using var stream = new MemoryStream(data);
                var image = new System.Windows.Media.Imaging.BitmapImage(); image.BeginInit(); image.CacheOption = System.Windows.Media.Imaging.BitmapCacheOption.OnLoad;
                image.StreamSource = stream; image.EndInit(); image.Freeze();
                gamePictures[game.Id] = image; providerPicturePaths[game.Id] = game.Icon;
            }
            catch { /* Missing plugin media must not interrupt the calendar. */ }
        }
    }

    string GameLabel(string id) => games.FirstOrDefault(g => g.Id == id)?.Short_Name ?? id;
    string GameColor(string id) => games.FirstOrDefault(g => g.Id == id)?.Color ?? "#6D8658";
    bool InScope(string game) => selectedGame == "all" || (selectedGame == "selected" ? selectedGames.Contains(game) : game == selectedGame);

    void UpdateProviders(JsonElement state)
    {
        var next = state.GetProperty("games").Deserialize<GameDescriptor[]>(json) ?? [];
        var view = state.GetProperty("workspace").GetProperty("view");
        var scopeSignature = view.GetProperty("scope").GetString() + view.GetProperty("selected_games").GetRawText();
        if (scopeSignature != sharedScopeSignature)
        {
            sharedScopeSignature = scopeSignature;
            var firstSelected = view.GetProperty("selected_games").EnumerateArray().FirstOrDefault();
            selectedGame = view.GetProperty("scope").GetString() == "single" ? (firstSelected.ValueKind == JsonValueKind.String ? firstSelected.GetString()! : "selected") : view.GetProperty("scope").GetString()!;
            updatingProviders = true; gameSelect.SelectedValue = selectedGame; updatingProviders = false;
        }
        selectedGames = view.GetProperty("selected_games").EnumerateArray().Select(g => g.GetString()!).ToHashSet();
        hideFuture = view.GetProperty("hide_future").GetBoolean();
        eventOrder = state.GetProperty("event_order").EnumerateArray().Select((e, i) => (id: e.GetString()!, index: i)).ToDictionary(e => e.id, e => e.index);
        var signature = string.Join(",", next.Select(g => g.Id)) + ":" + string.Join(",", selectedGames.OrderBy(g => g));
        if (signature != combinationSignature)
        {
            combinationSignature = signature;
            gameCombination.Children.Clear();
            foreach (var game in next)
            {
                var check = new CheckBox { Content = game.Short_Name + " · " + game.Server, Tag = game.Id, IsChecked = selectedGames.Contains(game.Id), Margin = new Thickness(0, 3, 0, 3), FontSize = 11 };
                check.Click += async (_, _) =>
                {
                    var selectedIds = gameCombination.Children.OfType<CheckBox>().Where(c => c.IsChecked == true).Select(c => (string)c.Tag).ToArray();
                    selectedGame = "selected";
                    await Mutate("/api/workspace", new { view = new { selected_games = selectedIds, scope = "selected" } }, true);
                    gameSelect.SelectedValue = "selected"; Render(); SavePreferences();
                };
                gameCombination.Children.Add(check);
            }
        }
        if (next.Select(g => g.Id).SequenceEqual(games.Select(g => g.Id))) { games = next; return; }
        games = next;
        updatingProviders = true;
        gameSelect.Items.Clear();
        foreach (var game in games) gameSelect.Items.Add(new GameChoice(game.Id, game.Label));
        gameSelect.Items.Add(new GameChoice("all", "全部游戏 · 对照排期"));
        gameSelect.Items.Add(new GameChoice("selected", "自选游戏组合"));
        if (selectedGame != "all" && selectedGame != "selected" && !games.Any(g => g.Id == selectedGame)) selectedGame = games.FirstOrDefault()?.Id ?? "all";
        gameSelect.SelectedValue = selectedGame;
        updatingProviders = false;
    }

    object GameIdentity(string id)
    {
        if (GamePicture(id) is ImageSource image) return new Border { CornerRadius = new CornerRadius(3), Background = new ImageBrush(image) { Stretch = Stretch.UniformToFill } };
        var label = Text(games.FirstOrDefault(g => g.Id == id)?.Monogram ?? "?", 14, true);
        label.HorizontalAlignment = HorizontalAlignment.Center; label.VerticalAlignment = VerticalAlignment.Center;
        return label;
    }

}
