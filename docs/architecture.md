# 模块化架构（插件 API 1）

本次升级保留原数据库、活动 ID、完成状态、备注、手动校正与提醒记录。新增表通过 `CREATE TABLE IF NOT EXISTS` 创建；个人配置采用独立的版本号，和软件版本分开。

## 分层与依赖方向

```text
内置终末地 / 内置明日方舟 / 本地 JSON 插件
                  ↓ ProviderRegistry（插件 API 1）
             公告版本、统一活动、游戏元数据
                  ↓
         Store + resolve_versions + WorkspaceService
         ├─ 官方数据与个人状态分开保存
         ├─ 排序、游戏组合、筛选、截止密度
         ├─ 个人计划、布局、主题、聚焦状态
         └─ 提醒、JSON / ICS 导出
                  ↓ 本机 HTTP API
       网页模块注册表 / WPF 桌面端
```

`acg_calendar/providers.py` 是游戏接入边界，`create_registry` 是内置适配器的唯一组装入口。两款已有采集器沿用经过测试的公告规则、来源策略与缓存逻辑。终末地特有的旧奖励数据补全及森空岛可用性检查归到终末地适配器。主程序不再根据游戏名选择规则文件、同步状态键或图片缓存。

`acg_calendar/workspace.py` 管理个人视图与投影。排序统一在 Python 执行，`/api/state.event_order` 提供稳定的行顺序，网页与 WPF 按此顺序展示；无需两端各实现一份剩余时间算法。刷新间隔沿用网页 10 秒、桌面 15 秒。

网页的 `web/workspace.js` 提供共享上下文、订阅和模块注册。日历、来源、奖励、近期截止、个人计划各自注册渲染回调，作为同级模块排序和折叠；个人计划目前仅显示已有计划列表。`web/customize.js` 管理主程序偏好编辑，不依赖具体游戏。桌面游戏列表与组合控件放在 `CalendarWindow.Providers.cs`，窗口外观由 `CalendarWindow.Appearance.cs` 管理，共享主题与布局适配放在 `CalendarWindow.Workspace.cs`。

当前源码新增 `acg_calendar/network.py`，由主程序在创建注册表前注入 Store，可信采集器通过 `network_job` 共享每轮预算；已有六类 HTTP 客户端与图片传输统一委托该网络层。域名白名单、跳转验证、字节上限与解析规则仍由各来源客户端负责。插件协议和来源 JSON 不增加必填字段；纯本地 JSON 包不发起请求。独立外部采集程序不在主程序限流范围内。

## 数据及版本兼容

| 数据 | 所有者 | 升级策略 |
| --- | --- | --- |
| `versions` | 游戏适配器 | 两款内置游戏沿用旧 ID；JSON 插件按游戏命名空间生成稳定 ID |
| `local_state` | 主程序 | 收藏、完成、忽略、备注、时间校正、提醒提前量；采集器不覆盖 |
| `meta.workspace` | 主程序 | `schema_version: 1`，独立保存 `view / layout / theme` |
| `personal_plans` | 用户 | 独立 UUID、时间段、时区、可选游戏/活动关联；同步不修改 |
| 插件 manifest | 插件作者 | `api_version: 1`；不支持的版本报告错误，不加载 |
| 插件活动文件 | 插件作者 | `schema_version: 1`，先完整校验，再事务替换该游戏的快照 |
| `backups/*.json` | 主程序 | `format: playtime-personal-backup`、`schema_version: 1`；只含 `local_state`、`personal_plans`、`meta.settings / workspace` 与采集策略，版本变化后首次启动先备份再迁移；拒绝更新格式 |

旧 `settings.active_game` 在首次读取工作区时迁移为游戏 ID 数组，继续兼容旧接口。自选组合可以只选一个游戏或不选游戏；“全部”模式自动包括以后新安装的游戏。卸载或禁用插件后，保留其缓存、个人标记与已保存的选择，重新安装相同 ID 可恢复。

插件 API 版本与工作区/活动格式版本分别演进；普通 UI 或内部实现调整不需要插件改版。将来若出现不兼容的插件协议变化，应增加 API 版本或兼容转换，不能直接让旧插件按新格式执行。

## 新增接口

所有写入接口沿用本机 Host、Origin 与会话 token 校验。以下 PATCH 请求返回完整 `/api/state`，方便现有两端更新；GET 工作区等接口返回对应实体。

| 接口 | 用途 |
| --- | --- |
| `GET /api/plugins` | 游戏描述、插件 API 版本、加载错误 |
| `GET/PATCH /api/workspace` | 游戏组合、排序、筛选、焦点、模块布局、主题 |
| `GET/PATCH /api/network` | 主程序统一采集策略、站点状态、暂停/恢复 |
| `GET /api/timeline?games=endfield-cn,sample-game&timezone=America/New_York` | 不修改偏好的临时查询，返回活动、分组、按当地日期计算的截止密度与独立计划 |
| `PATCH /api/events/<id>` | 现有个人状态，新增 `starred: true/false` |
| `GET/POST /api/plans` | 列出/创建个人游玩时间段 |
| `PATCH/DELETE /api/plans/<plan-id>` | 修改/删除个人计划 |
| `GET /api/calendar.ics?game=selected` | 仅导出当前游戏组合的已确认活动 |
| `GET /api/export?game=selected` | 组合范围内的官方活动 JSON |
| `POST /api/sync`，`{"game":"selected"}` | 同步当前组合；仍兼容单游戏与 `all` |

### 视图规则

`view` 字段：`selected_games`、`scope`（single/all/selected）、`sort`（start/remaining/title）、`starred_first`、`hide_future`、`group_by`（none/category/game）、`collapsed_groups`、`collapsed_parents`、`focus_timezone`、`focus`（date/event_id，可设 null 清除）。新增字段在 schema 1 中自动补齐。`presentation.py` 提供主程序的日期范围、显式关系和截止节点投影，前端 `planner.js` / WPF `CalendarWindow.Planner.cs` 消费相同元数据。

- 收藏先于普通活动。网页时间轴以“★ 收藏”单独置顶，其余活动按选择的规则分组和排序；排序只改变行顺序。
- 剩余时间排序：进行中且截止明确 → 未开放 → 截止待定/预计 → 已结束；同档按相应日期，再按活动 ID 稳定排序。标题排序在后端按 Unicode 大小写折叠排序。
- 隐藏未开放：未来开始及开始待定的活动隐藏；预计开始时刻已过的条目仍展示“待核实”标记。收藏不绕过隐藏规则。“显示全部”会关闭这个筛选。
- 截止密度只统计当前游戏和筛选范围内、未完成、时间明确且区间有效的截止与关闭阶段，兑换截止单列，去重按活动 ID + UTC 时刻；不受父活动折叠或临时日期聚焦影响。它表示截止数量，不表示游玩耗时。`timezone=auto` 在服务端查询时回落 UTC，客户端应传具体 IANA 时区。
- 界面选择与隐藏开关不缩小提醒范围；禁用/移除插件才会停止为该插件生成新提醒。
- 个人计划的时间与官方时间完全独立。暂不纳入提醒和 ICS，也没有重复计划规则。

### 布局与主题

`layout` 保持 `schema_version:1` 和模块列表，每项有 `id / visible / collapsed`，增加可选侧栏与尺寸字段。`theme` 保持独立版本，扩展为 19 个语义颜色、分别配置网页与桌面的面板/控件圆角、字重、边框和受控纹理；旧配置和 JSON 主题自动补齐，未改动工厂皮肤升级、自定义旧值保留。API 校验已知字段和语义对比度，不接受 CSS、HTML 或 JavaScript 注入。布局和主题互不覆盖。网页壳层固定到视口，旧布局 `max_width` 只保留导入兼容；展开是临时显示状态，不保存到模块布局。模块按已保存顺序安排网格，日历占用弹性行，周边模块保留自动高度。组件映射见 [设计体系](design-system.md)。

网页提供纵向模块流中的拖动/按钮排序、显隐、折叠、侧栏显隐/位置/宽度及页面宽度。日历主模块必须保留，折叠后留有展开入口；隐藏侧栏后仍能从页头恢复。WPF 使用相同模块顺序、可见性和主题，另提供月历在左侧或上方；其原生侧栏收起与气泡模式保持原有控制。没有任意坐标、尺寸网格或自由缩放。桌面气泡底色透明度独立保存，主题不透明度用于普通活动卡，不会降低文字/图标透明度。使用方式与全部令牌见 [preferences.md](preferences.md)。

父子活动：外部 JSON 支持显式 `parent_id`，限制同版本且不允许循环。现有采集器的 `parent_title` 仅在同游戏同版本唯一完全匹配时作为关联，`phases` 保留原样；没有根据相似标题擅自推断并合并活动。网页和桌面已提供折叠、明确截止摘要及个人计划界面；排序、收藏、日期聚焦与时区规则见 [规划说明](planning.md)。

## 验证

当前源码通过 214 项后端测试和九组浏览器回归，覆盖统一限流/缓存/冷却、旧偏好与语义主题迁移、导入导出、对比度、固定日期表头、布局与筛选栏恢复；不向真实源站发起压力请求。WPF 隔离自检使用五款真实游戏快照及虚构测试活动，共 109 条，验证三种风格、原生控件、共享主题、联网暂停、奖励隔离、分组导航、置顶、常驻、边缘唤出和透明气泡。原生交互位于 `CalendarWindow.Interaction.cs`，不进入 Provider 接口。

运行方式见 README；插件格式与安装方式见 [插件接口](plugins.md)。示例数据属于虚构测试日历，正常启动不会自动安装它。

## 保守自动复查（v0.2.0）

`scheduling.py` 独立保存每个来源的随机复查时间，默认 24 小时、支持 18 小时，另加 0–2 小时随机延迟。首次/过期补查错峰 1–10 分钟；使用最近尝试/成功时间生成计划，重启复用；同一 SQLite 数据库通过 `BEGIN IMMEDIATE` 领取任务，避免两个服务重复自动检查。手动同步完成会更新自动检查基准，提醒循环仍每 15 秒执行，不依赖源站。不同进程的 HTTP 配额仍未合并。插件 API 1 不变。

保守复查调度修订曾通过 206 项后端测试、采集偏好与日历提醒两组浏览器回归及 WPF 离线自检；使用模拟时钟覆盖首次错峰、24 小时间隔、重启保留、同库重复领取与旧设置迁移，没有向源站发起压力请求。

本轮 F08–F11 另覆盖明确关系与折叠、共享焦点/时区、截止去重、夏令时 23/25 小时日期和重复/不存在时刻、计划创建/修改/移动/删除与来源独立性；WPF 自检增加 `plannerVerified`。个人计划编辑器位于本地网页，原生桌面显示计划卡与时间进度。详见 [规划说明](planning.md)。
