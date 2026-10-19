"""Auditable oroberyl totals: explicit amounts, shared claims, no estimates."""
from __future__ import annotations

import hashlib
import re

DEFAULT_PATTERN = r"(?:【|〖)?嵌晶玉(?:】|〗)?\s*[×xX*]\s*([0-9][0-9,，]*)"


def summarize_rewards(version, rules=None, text=None):
    policy = rules or version.get("reward_policy", {})
    pattern = re.compile(policy.get("amount_pattern", DEFAULT_PATTERN))
    version["reward_policy"] = policy
    if text is not None:
        mentions, section = [], ""
        for number, line in enumerate((x.strip() for x in text.splitlines() if x.strip()), 1):
            if line.startswith("■") or line.startswith("▼"):
                section = line.lstrip("■▼ ")
            if "嵌晶玉" in line:
                mentions.append({"line": number, "text": line, "section": section})
        version["reward_mentions"] = mentions
    claims, conflicts = {}, []
    events = version["events"]
    for event in events:
        evidence = []
        excluded = event["category"] == "maintenance" or any(word in event["title"] for word in ("信用商店", "地图探索", "区域探索奖励"))
        lines = event["source"].get("reward_lines")
        if lines is None:  # Old local snapshots still show their explicit amounts.
            lines = [{"line": None, "text": x} for x in event["source"].get("block_text", "").splitlines() if "嵌晶玉" in x]
        for source in lines:
            raw = source["text"]
            if "信用商店" in raw or ("地图探索" in raw and "活动" not in raw):
                continue
            per_unit = re.search(policy.get("per_unit_pattern", r"每日|每天|每次|每完成|每关"), raw)
            if per_unit and (len(list(pattern.finditer(raw))) != 1 or not re.search(r"累计|合计|总计|总共", raw)):
                # A per-day/per-run amount is not a full-event total.
                continue
            for index, match in enumerate(pattern.finditer(raw)):
                amount = int(match.group(1).replace(",", "").replace("，", ""))
                if not 0 < amount <= 10_000_000:
                    continue
                # The same source line can occur on both an activity and its shop.
                identity = str(source["line"]) if source.get("line") else raw
                key = hashlib.sha256((version["id"] + "|" + identity + "|" + str(index)).encode()).hexdigest()[:20]
                evidence.append({"id": key, "amount": amount, "source": "announcement", "url": event["source"]["url"], "line": source.get("line"), "text": raw})
        guide = event.get("briefing", {})
        # quantity=1 is a display placeholder on current official guide cards.
        amounts = [r["quantity"] for r in guide.get("reward_items", []) if r.get("id") == policy.get("currency_id", "item_diamond") and type(r.get("quantity")) is int and 1 < r["quantity"] <= 10_000_000]
        if guide.get("match") == "activity_name" and amounts:
            expected = sum(amounts)
            if evidence and sum(e["amount"] for e in evidence) != expected:
                conflicts.append({"event_id": event["id"], "title": event["title"], "announcement": sum(e["amount"] for e in evidence), "briefing": expected})
            elif not evidence:
                key = hashlib.sha256((version["id"] + "|guide|" + guide["activity_id"]).encode()).hexdigest()[:20]
                evidence.append({"id": key, "amount": expected, "source": "briefing", "url": guide["source_url"], "line": None, "text": "官方导览活动卡 item_diamond ×" + str(expected)})
        amount = sum(e["amount"] for e in evidence) if evidence else None
        excluded_note = "维护补偿单列" if event["category"] == "maintenance" else "信用商店购买不计入" if "信用商店" in event["title"] else "新地图探索奖励不计入"
        event["reward"] = {"amount": amount, "state": "excluded" if excluded else "verified" if evidence else "unlisted", "claims": evidence, "note": excluded_note if excluded else "含干员试用奖励" if event["category"] == "gacha" and evidence else "公告未列明嵌晶玉数量；不表示为零" if not evidence else "活动可获得总量，非账号未领取余额"}
        if not excluded:
            for claim in evidence:
                if claim["id"] not in claims:
                    claims[claim["id"]] = {**claim, "event_id": event["id"], "title": event["title"]}
    # A separately timed exchange is a second deadline for the same reward pool.
    for event in events:
        parent = next((e for e in events if e["title"] == event.get("parent_title") and e["category"] != "exchange"), None)
        if event["category"] == "exchange" and event["reward"]["state"] != "excluded" and parent and event["reward"]["amount"] is None and parent["reward"]["amount"] is not None:
            event["reward"] = {**parent["reward"], "state": "shared", "shared_with": parent["id"], "note": "与「" + parent["title"] + "」共享奖励池，版本合计只计一次"}
        elif event["reward"]["state"] == "verified" and event["reward"]["claims"]:
            owners = {claims[c["id"]]["event_id"] for c in event["reward"]["claims"]}
            if event["id"] not in owners and len(owners) == 1:
                event["reward"].update(state="shared", shared_with=next(iter(owners)), note="共享奖励，版本合计只计一次")
    # Keep unnumbered/non-activity mentions visible instead of fabricating a total.
    covered = {c.get("line") for c in claims.values() if c.get("line")}
    other = []
    for mention in version.get("reward_mentions", []):
        if mention["line"] in covered:
            continue
        reason = "信用商店" if "信用商店" in mention["text"] else "新地图奖励" if mention["section"] in {"全新区域", "全新地图"} else "维护补偿" if "补偿" in mention["text"] else "通行证（未明确免费部分数量）" if "通行证" in mention["text"] else "未归属到日历活动，待核实"
        values = [int(m.group(1).replace(",", "").replace("，", "")) for m in pattern.finditer(mention["text"])]
        other.append({**mention, "amount": sum(values) if values else None, "reason": reason, "url": version["source"]["url"]})
    guide = version.get("briefing", {})
    for mention in guide.get("news_reward_mentions", []):
        values = [int(m.group(1).replace(",", "").replace("，", "")) for m in pattern.finditer(mention["text"])]
        other.append({**mention, "amount": sum(values) if values else None, "reason": "版本前导览浏览奖励（本版本维护前截止，单列）" if guide.get("news_reward_before_update") else "导览上线奖励（未列入日历活动，单列）", "url": guide["news_url"]})
    unlisted = [{"event_id": e["id"], "title": e["title"]} for e in events if e["reward"]["state"] == "unlisted" and e["category"] in {"event", "update", "exchange"}]
    version["reward_summary"] = {"currency": "嵌晶玉", "verified_total": sum(c["amount"] for c in claims.values()), "claims": list(claims.values()), "unlisted": unlisted, "other_mentions": other, "conflicts": conflicts, "scope": "版本内活动、干员试用、赛季及玩法新增挑战；活动兑换仅计一次。不含新地图探索、信用商店、维护补偿和通行证；版本前导览领取单列，不折算寻访凭证。", "complete": False, "status": "已核实小计，非所有免费资源总数"}
    return version
