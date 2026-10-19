# 来源说明

`samples/endfield-cn-6094.txt` 来自《明日方舟：终末地》国服官网 [「丹青渡」版本更新说明](https://endfield.hypergryph.com/news/6094)，于 2026-10-18 抓取，用于展示与测试公告结构解析。对应的解析 JSON 含原文片段。

这些公告内容、游戏名称及相关权利归原权利人所有，不属于本项目 MIT 代码许可的授权范围。程序在运行时从官方公告 CDN 缓存活动封面到本地 `data/artwork/`，该目录不纳入 Git；文档界面预览中出现的官方封面同样不属于 MIT 许可范围。项目不打包游戏客户端资源。

本项目是独立的个人日历工具，与鹰角网络无隶属关系。

明日方舟适配器以用户指定的 [PRTS Wiki](https://prts.wiki/w/首页)为社区数据来源。`samples/arknights-cn-calendar.json` 和 `tests/fixtures/prts-pages.json` 保留了少量国服时间字段及必要公告时间片段，并记录原页面、修订号和固定修订链接；对应 Wiki 编辑内容遵循 PRTS 声明的 [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/) 条款，不属于本项目 MIT 代码许可。引用的官方游戏文字及活动头图权利归原权利人所有。运行时图片缓存在本地，未随源代码打包；文档截图中出现的素材也不属于 MIT 许可范围。

`web/assets/oroberyl.png` 为用户提供的嵌晶玉游戏图标，原样用于日历奖励标记。该游戏素材及界面预览内的相关素材权利归原权利人所有，不属于本项目 MIT 代码许可范围。

`tests/fixtures/official-briefing-v1d6.json` 是从官方 [「丹青渡」新版本导览](https://endfield.hypergryph.com/version_briefing/latest?source_from=official) 脚本提取的少量活动配置，用于验证名称、国服时间语义和素材映射。其原始内容与素材权利同样不属于 MIT 代码许可；程序只静态读取公开配置，不执行脚本或登录领取奖励。
