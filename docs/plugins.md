# 本地游戏插件 API 1

在桌面侧栏“日历 → 我的游戏”或网页侧栏“日历游戏”下，点击 **＋ 加载新的日历**，即可打开当前实际使用的 `data/plugins/` 文件夹（自定义数据目录时跟随该目录）。把已解压的完整插件文件夹放入此处以安装；将对应文件夹移出以卸载。完成后从系统托盘“退出游时”，重新运行 `Playtime.cmd` 生效。此入口不会自动删除文件、清除个人记录或刷新注册表。

当前源码的采集限制、布局与主题更新保持 API 1、manifest 与活动 JSON 格式不变，现有包无需修改。主程序可信引擎及其图片请求共用同实例站点限流/缓存/冷却；纯本地 JSON 包不联网，软件外独立采集程序需自行限制。偏好与网络策略归主程序所有，不加入游戏数据包。详见 [设置说明](preferences.md)。

本阶段支持随主程序发布的可信采集器，以及无需改源码的 `local-json` 数据插件。外部插件不执行 Python/JS，也不安装依赖。普通插件只读取本地 `events.json`；可选 `source.type:gamekee` 或 `azurlane-wiki` 会使用主程序内置、限域的公开资料采集引擎。自行编写的其他网站采集程序可以在软件外运行并生成该文件。所有现有采集器均不依赖 AI。

这提供了新增游戏、通用显示、同步隔离与协议兼容的实际路径；任意网站的抓取器仍需有人编写，复制一个 manifest 不会自动理解该网站。在线插件市场、自动安装/更新、任意代码插件与热加载尚未开放。

## 查看、启用与停用（v0.2.0）

网页侧栏点击 **管理日历插件 ↗**，或在设置中选择 **日历插件**。列表展示文件夹、名称、区服、可选版本、插件 API、来源和加载错误。两个内置日历随程序提供，不在此停用。

点击启用/停用后，先从托盘退出程序或停止网页后台，再重新启动；页面筛选不会代替停用。中文与含空格的文件夹名同样可管理，损坏 JSON 的插件也能停用。停用保留收藏、完成、备注、校正和个人计划；移出文件夹只是卸载插件，不等于清除个人记录。个人数据备份包含启停设置，不包含插件文件。

v0.2.0 Windows 便携包在隔离的发布目录中预装三个可选插件；GitHub 源码包按下面的方式安装。升级步骤见 [发布说明](releases/v0.2.0.md)。

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

`id` 为 2–64 位小写字母、数字、连字符，以字母开头；时区采用 IANA 名称。游戏名称、区服、来源都从这些元数据生成。`game.icon` 可指向插件内相对路径的 PNG/JPEG/WebP 静态图片；未指定则用短名称占位。不读取任意 SVG 或外部文件，不把其他游戏奖励标成嵌晶玉。JSON 文件各不超过 2 MB，单张图片最多 5 MB。

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
- `category` 为 `event / gacha / exchange / update / maintenance`。普通本地 JSON 活动为时间区间；单点更新、维护引用由内置来源引擎解析，奖励由对应游戏适配器提供。
- 活动可添加 `artwork:{"file":"assets/banner.png","source_url":"https://example.com/notice"}`，由主程序验证为插件内静态图片后导入本地缓存。网页和桌面端共用该图片，可用于时间条与详情顶部。
- 时间必须带偏移或 `Z`，写入时统一转换 UTC；`exact` 为来源明确、`estimated` 为预计、`unknown` 必须搭配 `at:null`。不把预计截止当作确定提醒。
- 可选 `parent_id` 引用同版本中另一活动的发布者 ID，用于活动和兑换等子项关系；不得循环。
- `source_url` 必须为 HTTPS，程序不会自动请求它。插件作者负责提供准确的来源、许可和更新时间，程序不会验证该来源是否官方。
- 改好文件后点击“同步资料”或等待自动同步。推荐采集程序先写临时文件，再原子替换 `events.json`，避免读到一半。任何校验失败都保留上次成功快照，并在该游戏的同步状态中记录原因。

不兼容 manifest、重复游戏 ID、读取失败等可通过 `GET /api/plugins` 的 `errors` 查看；活动文件错误在 `/api/state.sync_by_game[游戏ID]` 查看。插件之间独立存储同步状态，不会替换其他游戏日历。

## 内置代码适配器

### GameKee 声明式来源

完整实例见 [NIKKE 国际服插件](../plugins/nikke-global/README.md)，包含 manifest、规则、真实来源快照及头图。`source` 声明 `type`、`entry_url`、`game_alias`、`wiki_id`、`server_id`、`server_name` 和规则相对路径；主程序校验区服并使用限定接口，不接受任意网站请求。未来新站点仍需增加可信引擎。此扩展已包含于 V0.0.7，旧 V0.0.5 需升级主程序后才能自动采集并显示本地插件图片。

`Provider` 的接口是 `descriptor / sync_key / collector / rules() / prepare(version)`；采集器提供 `seed()` 和 `start(force=False)`。描述中声明 ID、名称、来源、图标、时区与能力。新可信采集器只在 `create_registry` 注册，主窗口和网页不必添加新的游戏分支。

`azurlane-wiki` 实例见 [碧蓝航线国服包](../plugins/azurlane-cn/README.md)。可信引擎读取 BWIKI 国服活动表及其改建公告，旧规则提供明确图片映射；当前源码新增可选 `artwork_alignment` 规则，使用英文 Wiki 公开只读 parse API 发现候选、按更新批次及独立身份依据对齐，图片仍只允许对应 Wiki CDN。英文日期不进入国服时间字段，插件 API 1 不变。基础引擎已包含于 V0.0.7，新的可选对齐功能需升级主程序，详见 [算法与回测](azurlane-artwork-alignment.md)。

当前内置采集器仍在主程序进程中运行，并使用现有 Store，因此属于可信代码边界。未来若开放外部可执行插件，应增加独立进程协议、权限策略和故障处理；进程隔离本身不能称作安全沙箱。

`zzz-official` 实例见 [绝区零国服包](../plugins/zzz-cn/README.md)。可信引擎固定读取绝区零官网中文公告，按明确活动区间与调频子标题解析时间/独立头图；BWIKI 仅在名称及精确区间完全匹配时补图。预计维护结束保持预计状态。此引擎已包含于 V0.0.7，旧 V0.0.5 需升级。
