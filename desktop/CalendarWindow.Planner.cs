using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace Playtime;

public sealed partial class CalendarWindow
{
    JsonElement plannerState;
    readonly WrapPanel nativeDeadlineBand = new();
    readonly TextBlock nativeFocusLabel = new() { TextWrapping = TextWrapping.Wrap, FontSize = 10 };
    readonly Dictionary<string, string> activityParents = new();
    readonly HashSet<string> foldedParents = new();
    readonly Dictionary<string, int> familyDepth = new();
    string focusSignature = "", sharedScopeSignature = "", focusCaption = "";
    bool updatingFocus, starredFirst = true;
    Task scopeUpdate = Task.CompletedTask, dateUpdate = Task.CompletedTask;
    DateTimeOffset? focusStart, focusEnd;

    void BuildPlannerControls()
    {
        navigation.Children.Add(SidebarButton("清除日期 / 活动聚焦", async () => await SaveDateFocus(null)));
        navigation.Children.Add(SidebarButton("◇ 个人计划 · 新建 / 编辑 ↗", () => Open(address + "?planner=1")));
    }

    void UpdatePlanner(JsonElement state)
    {
        plannerState = state.Clone();
        var view = state.GetProperty("workspace").GetProperty("view");
        starredFirst = view.GetProperty("starred_first").GetBoolean();
        foldedParents.Clear(); foreach (var id in view.GetProperty("collapsed_parents").EnumerateArray()) foldedParents.Add(id.GetString()!);
        activityParents.Clear();
        var presentation = state.GetProperty("presentation");
        foreach (var p in presentation.GetProperty("parents").EnumerateObject()) activityParents[p.Name] = p.Value.GetString()!;
        var focus = view.GetProperty("focus"); string signature = focus.GetRawText() + view.GetProperty("focus_timezone").GetString();
        var bounds = presentation.GetProperty("focus_bounds");
        focusStart = bounds.ValueKind == JsonValueKind.Object ? CalendarEvent.Parse(bounds.GetProperty("start").GetString()) : null;
        focusEnd = bounds.ValueKind == JsonValueKind.Object ? CalendarEvent.Parse(bounds.GetProperty("end").GetString()) : null;
        if (signature != focusSignature)
        {
            focusSignature = signature; updatingFocus = true;
            selected = focus.GetProperty("date").ValueKind == JsonValueKind.String ? DateTime.ParseExact(focus.GetProperty("date").GetString()!, "yyyy-MM-dd", CultureInfo.InvariantCulture) : null;
            month.SelectedDate = selected; if (selected.HasValue) month.DisplayDate = selected.Value;
            updatingFocus = false;
        }
        focusCaption = selected.HasValue ? "聚焦 " + selected.Value.ToString("yyyy/MM/dd") + " · " + view.GetProperty("focus_timezone").GetString() + "（侧栏可清除）" : "点击截止日期或月历可聚焦；截止数量不代表游玩时长。";
    }

    async Task SaveDateFocus(DateTime? day)
    {
        if (updatingFocus || csrf == "") return;
        var zone = TimeZoneInfo.Local.Id;
        if (TimeZoneInfo.TryConvertWindowsIdToIanaId(zone, out var iana)) zone = iana;
        await Mutate("/api/workspace", new { view = new { focus = new { date = day?.ToString("yyyy-MM-dd"), event_id = (string?)null }, focus_timezone = zone } }, true);
    }

    async Task SaveGameScope(string id)
    {
        gameSelect.IsEnabled = false;
        try { await Mutate("/api/workspace", new { view = new { selected_games = id == "all" ? games.Select(g=>g.Id).ToArray() : id == "selected" ? selectedGames.ToArray() : new[] {id}, scope = id is "all" or "selected" ? id : "single" } }, true); }
        finally { gameSelect.IsEnabled = true; }
    }

    bool PlanInFocus(DateTimeOffset start, DateTimeOffset end) => focusStart == null || (start < focusEnd && end > focusStart);
    bool EventInFocus(CalendarEvent e)
    {
        if (focusStart != null) return e.Begin != null && (e.Time_Kind == "point" ? e.Begin >= focusStart && e.Begin < focusEnd : e.Begin < focusEnd && (e.Finish == null || e.Finish > focusStart));
        if (selected is not DateTime chosen) return true;
        var lo = new DateTimeOffset(chosen.Date); var hi = new DateTimeOffset(chosen.Date.AddDays(1));
        return e.Begin != null && (e.Time_Kind == "point" ? e.Begin >= lo && e.Begin < hi : e.Begin < hi && (e.Finish == null || e.Finish > lo));
    }

    CalendarEvent[] ArrangeFamilies(CalendarEvent[] rows)
    {
        familyDepth.Clear(); var byId = rows.ToDictionary(e => e.Id);
        string? Parent(CalendarEvent e) => starredFirst && e.Starred ? null : activityParents.TryGetValue(e.Id, out var p) && byId.ContainsKey(p) ? p : null;
        var result = new List<CalendarEvent>(); var seen = new HashSet<string>();
        void Visit(CalendarEvent entry, int depth)
        {
            if (!seen.Add(entry.Id)) return;
            result.Add(entry); familyDepth[entry.Id] = depth;
            if (!foldedParents.Contains(entry.Id)) foreach (var child in rows.Where(e => Parent(e) == entry.Id)) Visit(child, depth + 1);
        }
        foreach (var entry in rows.Where(e => Parent(e) == null)) Visit(entry, 0);
        return result.ToArray();
    }

    void AddFamilyControls(StackPanel body, CalendarEvent entry, HashSet<string> eligible)
    {
        var children = activityParents.Where(p => p.Value == entry.Id).Select(p => p.Key).ToArray();
        if (children.Length == 0) return;
        bool folded = foldedParents.Contains(entry.Id);
        body.Children.Add(Button((folded ? "▸ 展开 " : "▾ 折叠 ") + children.Length + " 个子活动", async () =>
        {
            var next = foldedParents.ToHashSet(); if (!next.Add(entry.Id)) next.Remove(entry.Id);
            await Mutate("/api/workspace", new { view = new { collapsed_parents = next.ToArray() } }, true);
        }));
        if (!folded || plannerState.ValueKind != JsonValueKind.Object) return;
        var family = new HashSet<string> { entry.Id }; bool grew;
        do { int count = family.Count; foreach (var p in activityParents) if (family.Contains(p.Value)) family.Add(p.Key); grew = count != family.Count; } while (grew);
        foreach (var node in plannerState.GetProperty("presentation").GetProperty("deadline_nodes").EnumerateArray())
        {
            string id = node.GetProperty("event_id").GetString()!;
            if (!family.Contains(id) || !eligible.Contains(id)) continue;
            var label = Text((node.GetProperty("kind").GetString() == "exchange" ? "兑换关闭 " : "活动关闭 ") + Format(CalendarEvent.Parse(node.GetProperty("at").GetString())) + " · " + node.GetProperty("title").GetString(), 10);
            label.Foreground = Muted; body.Children.Add(label);
        }
    }

    void RenderPlannerLayers(IEnumerable<CalendarEvent> scope)
    {
        if (plannerState.ValueKind != JsonValueKind.Object) return;
        var eligible = scope.Where(e => !e.Ignored && (!hideFuture || e.Begin <= DateTimeOffset.Now)).Select(e => e.Id).ToHashSet();
        var nodes = plannerState.GetProperty("presentation").GetProperty("deadline_nodes").EnumerateArray().Where(n => eligible.Contains(n.GetProperty("event_id").GetString()!)).ToArray();
        int uncertain = plannerState.GetProperty("presentation").GetProperty("uncertain_deadlines").EnumerateArray().Count(e=>eligible.Contains(e.GetString()!));
        nativeFocusLabel.Text = focusCaption + $" · 预计/待定 {uncertain}（不计入）";
        if (focusStart != null)
        {
            var claims=new Dictionary<string,int>();
            foreach(var e in scope.Where(EventInFocus))if(e.Reward.ValueKind==JsonValueKind.Object && e.Reward.GetProperty("state").GetString()!="excluded")foreach(var c in e.Reward.GetProperty("claims").EnumerateArray())claims[c.GetProperty("id").GetString()!]=c.GetProperty("amount").GetInt32();
            if(claims.Count>0)nativeFocusLabel.Text += $" · 关联已核实 {claims.Values.Sum():N0} 嵌晶玉（共享去重，版本小计不变）";
        }
        nativeDeadlineBand.Children.Clear();
        var dateGroups = nodes.GroupBy(n => CalendarEvent.Parse(n.GetProperty("at").GetString())!.Value.LocalDateTime.Date).Where(g => g.Key >= DateTime.Today).OrderBy(g => g.Key).Take(7);
        foreach (var g in dateGroups)
        {
            int exchanges = g.Count(n => n.GetProperty("kind").GetString() == "exchange");
            var b = Button(g.Key.ToString("MM/dd") + $" 活{g.Count()-exchanges}/兑{exchanges}", async () =>
            {
                await SaveDateFocus(g.Key);
                var menu = new ContextMenu { PlacementTarget = nativeDeadlineBand };
                foreach(var n in g)
                {
                    string eventId=n.GetProperty("event_id").GetString()!;
                    var item=new MenuItem { Header=(n.GetProperty("kind").GetString()=="exchange"?"兑换关闭 · ":"活动关闭 · ")+n.GetProperty("title").GetString()+" · "+Format(CalendarEvent.Parse(n.GetProperty("at").GetString())) };
                    item.Click+=(_,_)=>Open(address+"?event="+eventId);menu.Items.Add(item);
                }
                nativeDeadlineBand.ContextMenu=menu;menu.IsOpen=true;
            });
            b.ToolTip = string.Join("\n", g.Select(n => n.GetProperty("title").GetString() + " · " + Format(CalendarEvent.Parse(n.GetProperty("at").GetString()))));
            b.Margin = new Thickness(0,0,4,4); nativeDeadlineBand.Children.Add(b);
        }
        personalPlansPanel.Children.Clear();
        var plans = plannerState.GetProperty("plans").EnumerateArray().Where(p => (p.GetProperty("game_id").ValueKind != JsonValueKind.String || InScope(p.GetProperty("game_id").GetString()!)) && PlanInFocus(CalendarEvent.Parse(p.GetProperty("start").GetString())!.Value, CalendarEvent.Parse(p.GetProperty("end").GetString())!.Value)).ToArray();
        if (plans.Length != 0)
        {
            var list = new StackPanel(); list.Children.Add(Text("◇ 个人游玩计划 · 本机时区", 11, true));
            foreach (var p in plans)
            {
                string id = p.GetProperty("id").GetString()!;
                var card = new StackPanel(); card.Children.Add(Button("◇ " + p.GetProperty("title").GetString() + " ↗", () => Open(address + "?plan=" + id)));
                var start = CalendarEvent.Parse(p.GetProperty("start").GetString())!.Value; var end = CalendarEvent.Parse(p.GetProperty("end").GetString())!.Value;
                card.Children.Add(Text(Format(start) + " → " + Format(end) + " · 个人安排", 10));
                card.Children.Add(new ProgressBar { Value = Math.Clamp((DateTimeOffset.Now-start).TotalSeconds/(end-start).TotalSeconds*100,0,100), Height = 3, Foreground = LinkInk, Background = TrackSurface });
                list.Children.Add(new Border { Child = card, BorderBrush = LinkInk, BorderThickness = new Thickness(1), CornerRadius = new CornerRadius(themeRadius), Padding = new Thickness(6), Margin = new Thickness(0,3,0,3) });
            }
            personalPlansPanel.Children.Add(new ScrollViewer { Content = list, MaxHeight = 120, VerticalScrollBarVisibility = ScrollBarVisibility.Auto });
        }
        // Day buttons show planned time separately from official deadlines.
        foreach (var button in VisualDescendants<System.Windows.Controls.Primitives.CalendarDayButton>(month))
        {
            if (button.DataContext is not DateTime date) continue;
            var dayNodes = nodes.Where(n => CalendarEvent.Parse(n.GetProperty("at").GetString())!.Value.LocalDateTime.Date == date.Date).ToArray();
            int planCount = plans.Count(p => CalendarEvent.Parse(p.GetProperty("start").GetString())!.Value.LocalDateTime.Date <= date.Date && CalendarEvent.Parse(p.GetProperty("end").GetString())!.Value > new DateTimeOffset(date.Date));
            button.ToolTip = $"{date:yyyy/MM/dd} · 已明确截止 {dayNodes.Length} · 个人计划 {planCount}";
            button.FontWeight = planCount > 0 || dayNodes.Length > 0 ? FontWeights.Bold : FontWeights.Normal;
        }
    }

    static IEnumerable<T> VisualDescendants<T>(DependencyObject parent) where T : DependencyObject
    {
        for (int i=0;i<VisualTreeHelper.GetChildrenCount(parent);i++)
        { var c=VisualTreeHelper.GetChild(parent,i); if(c is T typed)yield return typed; foreach(var d in VisualDescendants<T>(c))yield return d; }
    }

    async Task<bool> PlannerSmokeTest()
    {
        ShowMode(false); showAll = true;
        gameSelect.SelectedValue = "sample-game"; await scopeUpdate;
        await SaveDateFocus(null);
        var parent = events.First(e=>e.Game=="sample-game" && e.Category=="event");
        var child = events.First(e=>e.Game=="sample-game" && e.Category=="exchange");
        var originalStart = parent.Start; var originalEnd = parent.End;
        await Mutate("/api/workspace",new {view=new {collapsed_parents=new[]{parent.Id}}},true);
        if(cards.Children.Count!=1 || !VisualDescendants<TextBlock>(cards).Any(t=>t.Text.Contains("兑换关闭")))throw new Exception("Folded family lost its exchange deadline");
        await Mutate("/api/events/"+child.Id,new {starred=true},true);
        if(cards.Children.Count!=2)throw new Exception("Starred child remained hidden in its folded parent");
        await Mutate("/api/events/"+child.Id,new {starred=false},true);
        if(nativeDeadlineBand.Children.Count==0)throw new Exception("Native deadline density has no date controls");
        await Mutate("/api/workspace",new {view=new {focus=new {date="2026-11-05",event_id=(string?)null},focus_timezone="America/New_York"}},true);
        if(selected?.Date!=new DateTime(2026,11,5) || cards.Children.Count!=1 || !VisualDescendants<TextBlock>(cards).Any(t=>t.Text.Contains(child.Title)))throw new Exception("Shared date focus did not select the active exchange child");
        await Mutate("/api/plans",new {title="个人计划桌面验证",local_start="2026-11-05T18:00",local_end="2026-11-05T19:30",timezone="America/New_York",game_id="sample-game",event_id=parent.Id});
        UpdateLayout(); await Dispatcher.InvokeAsync(()=>{},System.Windows.Threading.DispatcherPriority.Render);
        var plan=plannerState.GetProperty("plans").EnumerateArray().Single();string id=plan.GetProperty("id").GetString()!;
        if(!VisualDescendants<ProgressBar>(personalPlansPanel).Any() || !VisualDescendants<Button>(personalPlansPanel).Any())throw new Exception("Personal plan layer has no time bar or edit entry");
        await Mutate("/api/plans/"+id,new {move_minutes=60},true);
        if(plannerState.GetProperty("plans")[0].GetProperty("start").GetString()!="2026-11-06T00:00:00Z")throw new Exception("Personal plan move did not persist");
        if(events.Single(e=>e.Id==parent.Id).Start!=originalStart || events.Single(e=>e.Id==parent.Id).End!=originalEnd)throw new Exception("Moving a personal plan changed official dates");
        await SaveDateFocus(null); Render(); SaveRender("planner-linked.png");
        using(var request=new System.Net.Http.HttpRequestMessage(System.Net.Http.HttpMethod.Delete,address+"/api/plans/"+id))
        { request.Headers.Add("X-Calendar-Token",csrf);using var response=await http.SendAsync(request);response.EnsureSuccessStatusCode(); }
        await Refresh();
        if(plannerState.GetProperty("plans").GetArrayLength()!=0)throw new Exception("Personal plan delete failed");
        await Mutate("/api/workspace",new {view=new {collapsed_parents=Array.Empty<string>()}},true);
        return true;
    }
}
