# 本地游戏插件 API 1

本阶段支持两类适配器：随主程序发布的可信 Python 采集器，以及无需改源码的 `local-json` 数据插件。外部插件不执行 Python/JS，不安装依赖，也不触发任意网址请求。它读取本地 `events.json`；自行编写的采集程序可以在软件外运行并生成该文件。现有两款游戏的自动公告抓取继续由内置适配器完成，不需要 AI。

这提供了新增游戏、通用显示、同步隔离与协议兼容的实际路径；任意网站的抓取器仍需有人编写，复制一个 manifest 不会自动理解该网站。在线插件市场、自动安装/更新、任意代码插件与热加载尚未开放。

## 安装及移除

在当前使用的 `--data-dir` 下建立 `plugins/<文件夹名>/`，放置 `manifest.json` 和 `events.json`。默认位置是项目的 `data/plugins/`。**退出桌面托盘程序/停止网页后台，再重新启动**，注册表会重新发现插件；不用重新编译桌面端。

可先复制项目内的虚构示例测试：

```powershell
New-Item -ItemType Directory -Path '.\data\plugins' -Force | Out-Null
Copy-Item -LiteralPath '.\examples\plugins\sample-game' -Destination '.\data\plugins\sample-game' -Recurse
```

示例仅供演示，不能当作真实活动。`manifest.json` 的 `enabled:false` 会禁用插件；移走该目录也可卸载。缓存与个人状态保留。游戏 ID 不应在后续版本中改名，不能覆盖内置游戏 ID。

## manifest.json

```json
{
  "api_version": 1,
  "kind": "local-json",
  "enabled": true,
  "game": {
    "id": "my-game-global",
    "name": "我的游戏",
    "short_name": "我的游戏",
    "server": "国际服",
    "timezone": "America/New_York",
    "source_label": "游戏官网公告",
    "color": "#8873AE"
  }
}
```

`id` 为 2–64 位小写字母、数字、连字符，以字母开头；时区采用 IANA 名称。游戏名称、区服、来源都从这些元数据生成。当前外部插件以短名称作图标占位，不读取任意 SVG/图标路径，也不把其他游戏奖励标成嵌晶玉。JSON 文件各不超过 2 MB。

Windows 的 Python 需要 IANA 时区数据（首次部署）：

```powershell
python -m pip install -r requirements.txt
```

## events.json

```json
{
  "schema_version": 1,
  "versions": [{
    "id": "season-1",
    "name": "第一期",
    "source_url": "https://example.com/",
    "events": [{
      "id": "festival-001",
      "title": "节日活动",
      "category": "event",
      "start": {"at": "2026-10-20T10:00:00-04:00", "state": "exact"},
      "end": {"at": "2026-11-02T10:00:00-05:00", "state": "exact"},
      "source_url": "https://example.com/",
      "description": "活动说明"
    }]
  }]
}
```

- `versions` 为完整快照，1–100 个版本，每版本至多 2000 项；空的 `events:[]` 合法。被移出快照的官方活动不再显示，但个人标记不会删除。
- 每个游戏内部，活动 `id` 必须跨版本唯一且稳定。主程序将 `游戏ID + 发布者活动ID` 命名空间化；标题、日期或所属版本变化不应改变活动 ID。
- `category` 为 `event / gacha / exchange / update / maintenance`。API 1 的本地 JSON 活动为时间区间；单点更新、奖励、图片、维护引用解析暂由内置适配器提供。
- 时间必须带偏移或 `Z`，写入时统一转换 UTC；`exact` 为来源明确、`estimated` 为预计、`unknown` 必须搭配 `at:null`。不把预计截止当作确定提醒。
- 可选 `parent_id` 引用同版本中另一活动的发布者 ID，用于活动和兑换等子项关系；不得循环。
- `source_url` 必须为 HTTPS，程序不会自动请求它。插件作者负责提供准确的来源、许可和更新时间，程序不会验证该来源是否官方。
- 改好文件后点击“同步资料”或等待自动同步。推荐采集程序先写临时文件，再原子替换 `events.json`，避免读到一半。任何校验失败都保留上次成功快照，并在该游戏的同步状态中记录原因。

不兼容 manifest、重复游戏 ID、读取失败等可通过 `GET /api/plugins` 的 `errors` 查看；活动文件错误在 `/api/state.sync_by_game[游戏ID]` 查看。插件之间独立存储同步状态，不会替换其他游戏日历。

## 内置代码适配器

`Provider` 的接口是 `descriptor / sync_key / collector / rules() / prepare(version)`；采集器提供 `seed()` 和 `start(force=False)`。描述中声明 ID、名称、来源、图标、时区与能力。新可信采集器只在 `create_registry` 注册，主窗口和网页不必添加新的游戏分支。

当前内置采集器仍在主程序进程中运行，并使用现有 Store，因此属于可信代码边界。未来若开放外部可执行插件，应增加独立进程协议、权限策略和故障处理；进程隔离本身不能称作安全沙箱。
