# 模块化架构（V0.0.5）

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

网页的 `web/workspace.js` 提供共享上下文、订阅和模块注册。日历、来源、奖励、近期截止各自注册渲染回调；共享游戏组合、日期/事件焦点和主题配置。`personal-plans` 模块 ID 已预留。桌面游戏列表与组合控件放在 `CalendarWindow.Providers.cs`，窗口外观仍由 `CalendarWindow.Appearance.cs` 管理。

## 数据及版本兼容

| 数据 | 所有者 | 升级策略 |
| --- | --- | --- |
| `versions` | 游戏适配器 | 两款内置游戏沿用旧 ID；JSON 插件按游戏命名空间生成稳定 ID |
| `local_state` | 主程序 | 收藏、完成、忽略、备注、时间校正、提醒提前量；采集器不覆盖 |
| `meta.workspace` | 主程序 | `schema_version: 1`，独立保存 `view / layout / theme` |
| `personal_plans` | 用户 | 独立 UUID、时间段、时区、可选游戏/活动关联；同步不修改 |
| 插件 manifest | 插件作者 | `api_version: 1`；不支持的版本报告错误，不加载 |
| 插件活动文件 | 插件作者 | `schema_version: 1`，先完整校验，再事务替换该游戏的快照 |

旧 `settings.active_game` 在首次读取工作区时迁移为游戏 ID 数组，继续兼容旧接口。自选组合可以只选一个游戏或不选游戏；“全部”模式自动包括以后新安装的游戏。卸载或禁用插件后，保留其缓存、个人标记与已保存的选择，重新安装相同 ID 可恢复。

插件 API 版本与工作区/活动格式版本分别演进；普通 UI 或内部实现调整不需要插件改版。将来若出现不兼容的插件协议变化，应增加 API 版本或兼容转换，不能直接让旧插件按新格式执行。

## 新增接口

所有写入接口沿用本机 Host、Origin 与会话 token 校验。以下 PATCH 请求返回完整 `/api/state`，方便现有两端更新；GET 工作区等接口返回对应实体。

| 接口 | 用途 |
| --- | --- |
| `GET /api/plugins` | 游戏描述、插件 API 版本、加载错误 |
| `GET/PATCH /api/workspace` | 游戏组合、排序、筛选、焦点、模块布局、主题 |
| `GET /api/timeline?games=endfield-cn,sample-game&timezone=America/New_York` | 不修改偏好的临时查询，返回活动、分组、按当地日期计算的截止密度与独立计划 |
| `PATCH /api/events/<id>` | 现有个人状态，新增 `starred: true/false` |
| `GET/POST /api/plans` | 列出/创建个人游玩时间段 |
| `PATCH/DELETE /api/plans/<plan-id>` | 修改/删除个人计划 |
| `GET /api/calendar.ics?game=selected` | 仅导出当前游戏组合的已确认活动 |
| `GET /api/export?game=selected` | 组合范围内的官方活动 JSON |
| `POST /api/sync`，`{"game":"selected"}` | 同步当前组合；仍兼容单游戏与 `all` |

### 视图规则

`view` 字段：`selected_games`、`scope`（single/all/selected）、`sort`（start/remaining/title）、`starred_first`、`hide_future`、`group_by`（none/category/game）、`collapsed_groups`、`focus`（date/event_id，可设 null 清除）。

- 收藏先于普通活动。网页时间轴以“★ 收藏”单独置顶，其余活动按选择的规则分组和排序；排序只改变行顺序。
- 剩余时间排序：进行中且截止明确 → 未开放 → 截止待定/预计 → 已结束；同档按相应日期，再按活动 ID 稳定排序。标题排序在后端按 Unicode 大小写折叠排序。
- 隐藏未开放：未来开始及开始待定的活动隐藏；预计开始时刻已过的条目仍展示“待核实”标记。收藏不绕过隐藏规则。“显示全部”会关闭这个筛选。
- 截止密度只统计当前范围内、未完成、时间明确且区间有效的独立截止，兑换截止单列，去重按活动 ID。它表示截止数量，不表示游玩耗时。`timezone=auto` 在服务端查询时回落 UTC，客户端应传具体 IANA 时区。
- 界面选择与隐藏开关不缩小提醒范围；禁用/移除插件才会停止为该插件生成新提醒。
- 个人计划的时间与官方时间完全独立。暂不纳入提醒和 ICS，也没有重复计划规则。

### 布局与主题

`layout` 包含 `schema_version:1` 和模块列表，每项有 `id / visible / collapsed`。`theme` 包含独立版本、`preset / accent / card_opacity`。API 校验已知模块与令牌，不接受 CSS、HTML 或 JavaScript 注入。完整替换 `layout` 或更新 `theme` 不会重置另一类配置。

目前网页接线了模块渲染、显隐、基础颜色令牌；日历主模块必须保留。拖动布局、尺寸网格、完整深色组件、主题编辑器、主题透明度在所有组件上的应用、WPF 主题同步仍待制作。桌面已有气泡透明度等外观设置继续独立保存，不受新工作区配置覆盖。

父子活动：外部 JSON 支持显式 `parent_id`，限制同版本且不允许循环。现有采集器的 `parent_title / phases` 保留原样；没有根据相似标题擅自推断并合并活动。折叠父子时间条与个人计划编辑 UI 属于后续功能。

## 验证

后端覆盖旧数据/ID 保留、第三游戏、A+C/A+B、插件错误回退、协议不兼容、重复 ID、循环关系、时区/DST、个人计划独立存储、排序与提醒范围分离、接口 token 校验。浏览器覆盖第三游戏自动发现、组合恢复、收藏与排序、隐藏未来、模块/主题互不覆盖、详情头图、窄屏布局。桌面隔离自检加载三款共 40 条测试活动，验证第三游戏、任意组合、货币隔离及原有透明气泡/侧栏。

运行方式见 README；插件格式与安装方式见 [插件接口](plugins.md)。示例数据属于虚构测试日历，正常启动不会自动安装它。
