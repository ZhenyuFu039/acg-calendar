"""Read the public version briefing as data, without executing website JS."""
from __future__ import annotations

import ast
import copy
import datetime as dt
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import urllib.parse

from .official import CDN_PREFIX, official_url
from .parser import iso, stamp

LITERAL = re.compile(r'''JSON\.parse\(\s*('(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*")\s*\)''')


class Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hrefs, self.scripts = [], []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and attrs.get("href"):
            self.hrefs.append(attrs["href"])
        if tag == "script" and attrs.get("src"):
            self.scripts.append(attrs["src"])


def normalize(value):
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", (value or "").casefold())


def briefing_link(html, news_url):
    links = Links()
    links.feed(html)
    for href in links.hrefs:
        url = urllib.parse.urljoin(news_url, href)
        p = urllib.parse.urlsplit(url)
        if official_url(url) and p.hostname == "endfield.hypergryph.com" and p.path.startswith("/version_briefing/"):
            return url
    raise ValueError("导览新闻中未找到官方专题入口")


def parse_bundle(script, script_url):
    if not official_url(script_url) or not urllib.parse.urlsplit(script_url).path.startswith(CDN_PREFIX):
        raise ValueError("导览脚本来源不受支持")
    config, translations = None, None
    for match in LITERAL.finditer(script):
        if len(match.group(1)) > 1_500_000:
            continue
        try:
            # literal_eval only decodes a quoted string; website code is never run.
            decoded = ast.literal_eval(match.group(1))
            value = json.loads(decoded)
        except (ValueError, SyntaxError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            if isinstance(value.get("gameplay"), list) and isinstance(value.get("meta"), dict):
                config = value
            if isinstance(value.get("page.title"), dict):
                translations = value
    if not config or not translations:
        raise ValueError("官方导览配置结构变化")
    title = translations["page.title"].get("zh-cn", "")
    name_match = re.search(r"「([^」]+)」", title)
    tag = config["meta"].get("version", "")
    if not name_match or not isinstance(tag, str) or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,40}", tag):
        raise ValueError("导览缺少可核对的版本名称或标识")
    assets = dict(re.findall(r'"(gameplay\.\d+\.(?:thumbnail|visual))":\w+\.p\+"([^"]+)"', script))
    entries = []
    for row in config["gameplay"][:100]:
        name = row.get("name", {}).get("zh-cn")
        activity_id = row.get("id")
        if not isinstance(name, str) or not isinstance(activity_id, str):
            continue
        thumbnail = row.get("thumbnail", {})
        asset_path = assets.get(thumbnail.get("assetKey"))
        image = urllib.parse.urljoin(script_url, asset_path) if asset_path else None
        if image and not official_url(image):
            image = None
        cn = row.get("timeByServer", {}).get("cn", {})
        times = {key: cn[key] for key in ("openTime", "closeTime", "startsAfterUpdate", "endTimeDisplay") if key in cn and isinstance(cn[key], (str, bool))}
        entries.append({"id": activity_id, "title": name, "cn": times, "thumbnail_url": image})
    if not entries:
        raise ValueError("导览中未找到可识别的国服活动配置")
    return {"schema_version": 1, "name": name_match.group(1), "version_tag": tag, "page_title": title, "script_url": script_url, "script_sha256": hashlib.sha256(script.encode()).hexdigest(), "source_environment": config["meta"].get("sourceEnvironment"), "entries": entries}


class BriefingReader:
    def __init__(self, data_dir: Path, fetcher):
        self.directory = data_dir / "sources" / "briefings"
        self.fetcher = fetcher
        self.responses = {}

    def fetch(self, url):
        if url not in self.responses:
            self.responses[url] = self.fetcher(url)
        return self.responses[url]

    def cached(self):
        result = []
        if self.directory.is_dir():
            for path in sorted(self.directory.glob("*.json"))[:100]:
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    required = ("name", "version_tag", "page_title", "news_cid", "news_url", "source_url", "script_url", "fetched_at")
                    if data.get("schema_version") == 1 and isinstance(data.get("entries"), list) and all(isinstance(data.get(key), str) for key in required) and official_url(data["source_url"]) and official_url(data["script_url"]):
                        result.append(data)
                except (OSError, ValueError, KeyError):
                    continue
        return result

    def read(self, article, detail_template, fetched_at):
        cid = str(article.get("cid", ""))
        if not re.fullmatch(r"\d+", cid):
            raise ValueError("导览新闻 ID 格式异常")
        expected = re.search(r"「([^」]+)」", article.get("title", ""))
        if not expected:
            raise ValueError("导览新闻缺少版本名称，暂不自动关联")
        news_url = detail_template.format(cid=cid)
        page_url = briefing_link(self.fetch(news_url), news_url)
        links = Links()
        links.feed(self.fetch(page_url))
        scripts = [urllib.parse.urljoin(page_url, src) for src in links.scripts]
        scripts = [url for url in scripts if official_url(url) and urllib.parse.urlsplit(url).hostname == "web.hycdn.cn" and urllib.parse.urlsplit(url).path.startswith(CDN_PREFIX) and urllib.parse.urlsplit(url).path.endswith(".js")]
        if not scripts:
            raise ValueError("未发现官方导览配置脚本")
        cache = next((x for x in self.cached() if x.get("news_cid") == cid and normalize(x["name"]) == normalize(expected.group(1))), None)
        last_error = None
        for script_url in scripts[:3]:
            if cache and cache["script_url"] == script_url:
                return {**cache, "checked_at": fetched_at, "cache_reused": True}
            try:
                parsed = parse_bundle(self.fetch(script_url), script_url)
            except ValueError as error:
                last_error = error
                continue
            if normalize(parsed["name"]) != normalize(expected.group(1)):
                raise ValueError("专题 latest 已切换版本，拒绝关联到旧版；保留已缓存的版本快照")
            parsed.update(news_cid=cid, news_url=news_url, news_title=article["title"], source_url=page_url, fetched_at=fetched_at, checked_at=fetched_at, cache_reused=False)
            self.directory.mkdir(parents=True, exist_ok=True)
            temporary = self.directory / (cid + ".tmp")
            temporary.write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.directory / (cid + ".json"))
            return parsed
        raise ValueError("导览脚本未包含活动配置：" + str(last_error or "未知结构"))


def match_entry(event, entries):
    title = normalize(event["title"])
    quoted = {normalize(x) for x in re.findall(r"「([^」]+)」", event["title"])}
    exact = [row for row in entries if normalize(row["title"]) == title or normalize(row["title"]) in quoted]
    if len(exact) == 1:
        return exact[0], "activity_name"
    if len(exact) > 1:
        return None, "ambiguous"
    parent = normalize(event.get("parent_title"))
    inherited = [row for row in entries if parent and normalize(row["title"]) == parent]
    return (inherited[0], "parent_activity") if len(inherited) == 1 else (None, "unmatched")


def explicit_time(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = dt.datetime.strptime(value.strip(), "%Y/%m/%d %H:%M:%S").replace(tzinfo=dt.timezone(dt.timedelta(hours=8)))
        return iso(parsed)
    except ValueError:
        return None


def merge_briefing(version, guide, artwork):
    if normalize(version["name"]) != normalize(guide["name"]):
        raise ValueError("版本名称不一致，拒绝合并导览")
    matched = conflicts = image_errors = supplemented = 0
    matched_ids = set()
    for event in version["events"]:
        row, match = match_entry(event, guide["entries"])
        if row is None:
            continue
        matched += 1
        matched_ids.add(row["id"])
        cn = row["cn"]
        evidence = {"activity_id": row["id"], "title": row["title"], "source_url": guide["source_url"], "news_url": guide["news_url"], "script_url": guide["script_url"], "version_tag": guide["version_tag"], "match": match, "cn_time": copy.deepcopy(cn), "conflicts": [], "supplemented": []}
        # A permanent parent mode's launch date is not a season's update date.
        if match == "activity_name":
            for role, field in (("start", "openTime"), ("end", "closeTime")):
                if (role == "start" and cn.get("startsAfterUpdate")) or (role == "end" and cn.get("endTimeDisplay") in {"beforeUpdate", "alwaysAvailable"}):
                    continue
                value = explicit_time(cn.get(field))
                if not value:
                    continue
                endpoint = event[role]
                if endpoint.get("at") and endpoint.get("state") == "exact" and stamp(endpoint["at"]) != stamp(value):
                    evidence["conflicts"].append({"role": role, "announcement_at": endpoint["at"], "briefing_at": value})
                    event["warnings"].append("官方导览与版本更新公告的" + ("开始" if role == "start" else "结束") + "时间不一致，保留版本更新公告时间")
                    conflicts += 1
                elif not endpoint.get("at") and endpoint.get("reference") in {None, "unpublished"} and event["time_kind"] != "point":
                    other = event["end" if role == "start" else "start"].get("at")
                    if other and ((role == "start" and stamp(value) >= stamp(other)) or (role == "end" and stamp(value) <= stamp(other))):
                        continue
                    endpoint.update(at=value, state="exact", reference="briefing_explicit", raw=cn[field], source_url=guide["source_url"])
                    evidence["supplemented"].append(role)
                    supplemented += 1
        event["briefing"] = evidence
        if row.get("thumbnail_url"):
            try:
                key = artwork.cache(row["thumbnail_url"])
                if key:
                    event["artwork"] = {"key": key, "path": "/api/artwork/" + key, "url": row["thumbnail_url"], "source_url": guide["source_url"], "source_title": guide["page_title"], "match": "official_briefing_" + match}
            except Exception:
                image_errors += 1
    version["briefing"] = {key: guide[key] for key in ("name", "version_tag", "page_title", "source_url", "news_url", "script_url", "fetched_at")}
    version["briefing"].update(matched_events=matched, conflicts=conflicts, supplemented_endpoints=supplemented, image_errors=image_errors)
    version["briefing"]["unmatched_activities"] = [{"id": row["id"], "title": row["title"]} for row in guide["entries"] if row["id"] not in matched_ids]
    version["artwork_summary"] = {"matched": sum(bool(e.get("artwork")) for e in version["events"]), "total": len(version["events"]), "errors": image_errors + version.get("artwork_summary", {}).get("errors", 0)}
    version["analysis"].update(briefing_matched=matched, source_conflicts=conflicts, supplemented_endpoints=supplemented)
