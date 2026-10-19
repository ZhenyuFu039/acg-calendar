# 来源说明

完整的网站致谢、非商业用途与免责声明见 [开发文档](docs/development.md)。源码继续沿用 MIT；第三方 Wiki、游戏公告、图片和商标不因此获得 MIT 授权，须分别遵守来源许可。

`web/assets/arknights-app-icon.png`、`web/assets/endfield-app-icon.png`，以及 `plugins/nikke-global/assets/app-icon.png`、`plugins/azurlane-cn/assets/app-icon.png` 为用户上传的四款游戏 APP 图标，原样用于游戏身份标记。相关商标与美术素材归原权利人所有，不属于 MIT 代码许可。

`samples/endfield-cn-6094.txt` 来自《明日方舟：终末地》国服官网 [「丹青渡」版本更新说明](https://endfield.hypergryph.com/news/6094)，于 2026-10-18 抓取，用于展示与测试公告结构解析。对应的解析 JSON 含原文片段。

这些公告内容、游戏名称及相关权利归原权利人所有，不属于本项目 MIT 代码许可的授权范围。程序在运行时从官方公告 CDN 缓存活动封面到本地 `data/artwork/`，该目录不纳入 Git；文档界面预览中出现的官方封面同样不属于 MIT 许可范围。项目不打包游戏客户端资源。

本项目是独立的个人日历工具，与鹰角网络无隶属关系。

明日方舟适配器以用户指定的 [PRTS Wiki](https://prts.wiki/w/首页)为社区数据来源。`samples/arknights-cn-calendar.json` 和 `tests/fixtures/prts-pages.json` 保留了少量国服时间字段及必要公告时间片段，并记录原页面、修订号和固定修订链接；对应 Wiki 编辑内容遵循 PRTS 声明的 [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/) 条款，不属于本项目 MIT 代码许可。引用的官方游戏文字及活动头图权利归原权利人所有。运行时图片缓存在本地，未随源代码打包；文档截图中出现的素材也不属于 MIT 许可范围。

`web/assets/oroberyl.png` 为用户提供的嵌晶玉游戏图标，原样用于日历奖励标记。该游戏素材及界面预览内的相关素材权利归原权利人所有，不属于本项目 MIT 代码许可范围。

`web/assets/endfield-icon-zh.svg` 与 `web/assets/arknights-logo.png` 为用户提供的游戏标识，原样用于区分两款游戏。它们是原权利人的游戏素材，亦不属于 MIT 代码许可。明日方舟活动横幅优先取自 [明日方舟官网公告](https://ak.hypergryph.com/#information)，按公告章节名称明确匹配；运行时缓存不纳入 Git。

`tests/fixtures/official-briefing-v1d6.json` 是从官方 [「丹青渡」新版本导览](https://endfield.hypergryph.com/version_briefing/latest?source_from=official) 脚本提取的少量活动配置，用于验证名称、国服时间语义和素材映射。其原始内容与素材权利同样不属于 MIT 代码许可；程序只静态读取公开配置，不执行脚本或登录领取奖励。

`plugins/nikke-global/` 采用用户指定的 [GameKee NIKKE 国际服活动日历](https://www.gamekee.com/nikke/huodong/19)及其链接的社区转载公告，保留调度事实、必要时间依据、图片和来源 URL，以展示和验证日历插件；`tests/fixtures/gamekee-nikke-cards.json` 为少量公开活动字段。`assets/logo.png` 是用户上传的 NIKKE 标识原图，其他图片取自对应活动卡片或公告章节。这些商标、游戏素材及来源内容归原权利人所有，不属于本项目 MIT 代码许可。GameKee 来源并未标为游戏官方网站；本项目与 GameKee、NIKKE 发行及开发方无隶属关系。

`plugins/azurlane-cn/` 使用用户指定的 [BWIKI 碧蓝航线活动表](https://wiki.biligame.com/blhx/游戏活动表)及其链接的国服公告提取活动时间，参考 [Azur Lane Wiki Recent News](https://azurlane.koumakan.jp/wiki/Recent_News)链接的对应专题获取头图。BWIKI 保留来源及固定修订链接；英文 Wiki 页面声明 CC BY-NC-SA 4.0，页面与素材出处保存在规则及来源 JSON。图片中的外服日期不用于国服日历。相关游戏素材、公告与 Wiki 内容的权利分别归原权利人所有，不属于 MIT 代码许可，详见包内来源说明。
# 绝区零插件素材

`plugins/zzz-cn/sources/official-news.json` 中的公告及 `assets/` 中的官方活动图片、用户提供的 APP 图标属于米哈游/HoYoverse及相关权利人，不受本项目 MIT 许可覆盖。来源为 https://zenless.hoyoverse.com/zh-cn/news?category=296 。

`plugins/zzz-cn/sources/wiki-calendar.json` 为 https://wiki.biligame.com/zzz/活动一览 的社区整理渲染快照；页面标注 CC BY-NC-SA 4.0，贡献者署名与图片各自来源以该页面为准。动态查询结果随子页面变化，模板修订号不等于所有子页面的固定修订。包内 README 说明使用限制。
