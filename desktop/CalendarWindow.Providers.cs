using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
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

    string GameLabel(string id) => games.FirstOrDefault(g => g.Id == id)?.Short_Name ?? id;
    string GameColor(string id) => games.FirstOrDefault(g => g.Id == id)?.Color ?? "#6D8658";
    bool InScope(string game) => selectedGame == "all" || (selectedGame == "selected" ? selectedGames.Contains(game) : game == selectedGame);

    void UpdateProviders(JsonElement state)
    {
        var next = state.GetProperty("games").Deserialize<GameDescriptor[]>(json) ?? [];
        var view = state.GetProperty("workspace").GetProperty("view");
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
        if (GamePicture(id) is ImageSource image) return new Image { Source = image, Stretch = Stretch.Uniform };
        var label = Text(games.FirstOrDefault(g => g.Id == id)?.Monogram ?? "?", 14, true);
        label.HorizontalAlignment = HorizontalAlignment.Center; label.VerticalAlignment = VerticalAlignment.Center;
        return label;
    }

}
