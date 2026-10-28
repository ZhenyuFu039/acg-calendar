"""Personal-data backups: favourites, completion, notes, corrections, plans and preferences.

Official activity snapshots, image and HTTP caches are not included; they are
re-created by the providers. Backups are plain JSON files under data/backups/.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import re
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .parser import iso, stamp
from .reminders import check_event_offsets

FORMAT = "playtime-personal-backup"
SCHEMA_VERSION = 1
SECTIONS = {"local_state", "personal_plans", "settings", "workspace", "network_policy"}
REASONS = {"manual", "upgrade", "before-restore", "before-clear"}
AUTOMATIC = {"upgrade", "before-restore", "before-clear"}
KEEP_AUTOMATIC = 20
MAX_BYTES = 8_000_000
NAME = re.compile(r"playtime-backup-\d{8}-\d{6}(?:-\d+)?-(?:manual|upgrade|before-restore|before-clear)\.json")
PLAN_ID = re.compile(r"plan-[a-f0-9]{32}")


def directory(data_dir: Path) -> Path:
    return data_dir / "backups"


def snapshot(store):
    """Read every personal table in one transaction so the copy is consistent."""
    with store.connection() as conn:
        conn.execute("BEGIN")
        state = {row[0]: json.loads(row[1]) for row in conn.execute("SELECT id,payload FROM local_state ORDER BY id")}
        plans = [json.loads(row[0]) for row in conn.execute("SELECT payload FROM personal_plans ORDER BY id")]
        meta = {row[0]: json.loads(row[1]) for row in conn.execute("SELECT id,payload FROM meta WHERE id IN ('settings','workspace','network-policy')")}
    network = meta.get("network-policy")
    return {
        "local_state": state,
        "personal_plans": plans,
        "settings": meta.get("settings"),
        "workspace": meta.get("workspace"),
        # Site cooldowns and quotas are runtime limits, never restored from an old copy.
        "network_policy": {"policy": network.get("policy", {}), "paused": network.get("paused", False)} if network else None,
    }


def has_personal_data(contents):
    return bool(contents["local_state"] or contents["personal_plans"] or contents["settings"] or contents["workspace"])


def counts(contents):
    state = contents["local_state"].values()
    return {
        "starred": sum(bool(s.get("starred")) for s in state),
        "completed": sum(bool(s.get("completed")) for s in state),
        "notes": sum(bool(s.get("notes")) for s in state),
        "corrections": sum(bool(s.get("start_override") or s.get("end_override") or s.get("actual_end")) for s in state),
        "plans": len(contents["personal_plans"]),
    }


def create(store, data_dir: Path, reason, *, app_version, plugins=(), note=None, now=None):
    if reason not in REASONS:
        raise ValueError("未知的备份类型")
    now = now or dt.datetime.now(dt.timezone.utc)
    contents = snapshot(store)
    desktop = data_dir / "desktop-window.json"
    try:
        desktop_window = json.loads(desktop.read_text(encoding="utf-8")) if desktop.is_file() else None
    except (OSError, ValueError):
        desktop_window = None
    document = {
        "format": FORMAT, "schema_version": SCHEMA_VERSION, "app_version": app_version,
        "created_at": iso(now), "reason": reason, "note": note,
        "contents": contents, "counts": counts(contents),
        "plugins": [{"id": g["id"], "name": g.get("name", g["id"])} for g in plugins],
        # Kept for manual recovery; the desktop app rewrites this file while running.
        "desktop_window": desktop_window,
    }
    folder = directory(data_dir)
    folder.mkdir(parents=True, exist_ok=True)
    base = "playtime-backup-" + now.strftime("%Y%m%d-%H%M%S")
    # Each writer has its own temporary file; the final name is claimed atomically, so
    # concurrent requests or processes in the same second never overwrite each other.
    temporary = folder / f".{base}-{uuid.uuid4().hex}.tmp"
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        index = 0
        while True:
            path = folder / (f"{base}-{reason}.json" if index == 0 else f"{base}-{index}-{reason}.json")
            if _claim(temporary, path):
                break
            index += 1
    finally:
        temporary.unlink(missing_ok=True)
    prune(data_dir)
    return describe(path, document)


def _claim(temporary: Path, path: Path):
    """Publish a complete file under a name only if nobody else holds it."""
    try:
        os.link(temporary, path)
        return True
    except FileExistsError:
        return False
    except OSError:
        pass  # File systems without hard links: exclusive create, then write.
    try:
        with open(path, "xb") as handle:
            handle.write(temporary.read_bytes())
        return True
    except FileExistsError:
        return False


def describe(path: Path, document):
    return {"name": path.name, "created_at": document.get("created_at"), "reason": document.get("reason"),
            "app_version": document.get("app_version"), "note": document.get("note"),
            "counts": document.get("counts", {}), "size": path.stat().st_size}


def listing(data_dir: Path):
    folder = directory(data_dir)
    rows = []
    for path in sorted(folder.glob("playtime-backup-*.json"), reverse=True) if folder.is_dir() else []:
        if not NAME.fullmatch(path.name):
            continue
        try:
            rows.append(describe(path, json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            rows.append({"name": path.name, "error": "文件无法读取"})
    return rows


def prune(data_dir: Path):
    """Manual backups are never deleted; automatic ones keep the newest few."""
    automatic = [p for p in sorted(directory(data_dir).glob("playtime-backup-*.json"), reverse=True)
                 if NAME.fullmatch(p.name) and p.stem.endswith(tuple("-" + r for r in AUTOMATIC))]
    for path in automatic[KEEP_AUTOMATIC:]:
        path.unlink(missing_ok=True)


def read(data_dir: Path, name):
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError("备份文件名无效")
    path = directory(data_dir) / name
    if not path.is_file():
        raise LookupError("备份不存在")
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("备份文件超过 8 MB")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        raise ValueError("备份文件不是有效的 JSON") from None


def _time(value, label):
    try:
        if not isinstance(value, str) or stamp(value) is None:
            raise ValueError()
        return stamp(value)
    except (TypeError, ValueError):
        raise ValueError(label + "不是带时区的 ISO 时间") from None


def validate(document):
    """Structural checks before anything is written; returns the contents to apply."""
    if not isinstance(document, dict) or document.get("format") != FORMAT:
        raise ValueError("这不是游时个人数据备份文件（JSON / ICS 导出不能用于恢复）")
    version = document.get("schema_version")
    if type(version) is not int or version < 1:
        raise ValueError("备份格式版本无效")
    if version > SCHEMA_VERSION:
        raise ValueError(f"备份来自更新的版本（格式 {version}），请先升级游时再恢复")
    contents = document.get("contents")
    # Schema 1 always writes every section; a missing one must never be read as "clear it".
    if not isinstance(contents, dict) or set(contents) != SECTIONS:
        missing = SECTIONS - set(contents) if isinstance(contents, dict) else SECTIONS
        raise ValueError("备份内容不完整或含未知字段" + ("，缺少：" + "、".join(sorted(missing)) if missing else ""))
    state = contents["local_state"]
    if not isinstance(state, dict) or len(state) > 100_000:
        raise ValueError("个人标记格式无效")
    for key, value in state.items():
        if not isinstance(key, str) or not key or len(key) > 300 or not isinstance(value, dict):
            raise ValueError("个人标记格式无效")
        check_state(key, value)
    plans = contents["personal_plans"]
    if not isinstance(plans, list) or len(plans) > 10_000:
        raise ValueError("个人计划格式无效")
    seen = set()
    for plan in plans:
        if not isinstance(plan, dict) or not isinstance(plan.get("id"), str) or not PLAN_ID.fullmatch(plan["id"]) or plan["id"] in seen:
            raise ValueError("个人计划 ID 无效或重复")
        seen.add(plan["id"])
        if not isinstance(plan.get("title"), str) or not plan["title"].strip() or len(plan["title"]) > 200:
            raise ValueError("个人计划标题无效")
        if _time(plan.get("end"), "计划结束") <= _time(plan.get("start"), "计划开始"):
            raise ValueError("个人计划结束时间需晚于开始")
        try:
            ZoneInfo(plan.get("timezone"))
        except (ZoneInfoNotFoundError, TypeError, ValueError):
            raise ValueError("个人计划时区无效") from None
    for key in ("settings", "workspace"):
        if contents[key] is not None and not isinstance(contents[key], dict):
            raise ValueError("设置格式无效")
    workspace = contents["workspace"]
    if workspace and (workspace.get("schema_version", 1) != 1 or set(workspace) - {"schema_version", "view", "layout", "theme"}):
        raise ValueError("工作区配置版本不受支持")
    network = contents["network_policy"]
    if network is not None and (not isinstance(network, dict) or set(network) - {"policy", "paused"}):
        raise ValueError("采集设置格式无效")
    return {"local_state": state, "personal_plans": plans, "settings": contents["settings"],
            "workspace": workspace, "network_policy": network}


def desktop_window(value):
    """Desktop window preferences from a backup, or None when absent or not a small flat object."""
    if not isinstance(value, dict) or len(value) > 40:
        return None
    for key, item in value.items():
        if not isinstance(key, str) or len(key) > 40 or not (item is None or isinstance(item, (bool, int, float)) or (isinstance(item, str) and len(item) <= 64)):
            return None
    return value


def check_state(key, value):
    """The same field rules as Application.update_event and the maintenance endpoint."""
    allowed = {"actual_end"} if key.startswith("version:") else {"completed", "ignored", "starred", "notes", "reminder_minutes", "start_override", "end_override"}
    if set(value) - allowed:
        raise ValueError("个人标记含不支持的字段：" + "、".join(sorted(set(value) - allowed)))
    if "notes" in value and (not isinstance(value["notes"], str) or len(value["notes"]) > 2000):
        raise ValueError("备注格式无效或超过 2000 字")
    for flag in ("completed", "ignored", "starred"):
        if flag in value and type(value[flag]) is not bool:
            raise ValueError("完成、忽略与收藏状态必须为布尔值")
    if "reminder_minutes" in value:
        check_event_offsets(value["reminder_minutes"])
    times = {field: _time(value[field], "校正时间") for field in ("start_override", "end_override", "actual_end") if value.get(field) is not None}
    if "start_override" in times and "end_override" in times and times["end_override"] <= times["start_override"]:
        raise ValueError("校正的结束时间必须晚于开始时间")


def apply(store, contents, *, desktop_window_pending=None):
    """Replace personal tables in one transaction; any error leaves current data unchanged."""
    with store.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM local_state")
        conn.execute("DELETE FROM personal_plans")
        conn.executemany("INSERT INTO local_state VALUES (?,?)", [(k, json.dumps(v, ensure_ascii=False)) for k, v in contents["local_state"].items()])
        conn.executemany("INSERT INTO personal_plans VALUES (?,?)", [(p["id"], json.dumps(p, ensure_ascii=False)) for p in contents["personal_plans"]])
        for key in ("settings", "workspace"):
            if contents[key] is None:
                conn.execute("DELETE FROM meta WHERE id=?", (key,))
            else:
                conn.execute("INSERT INTO meta VALUES (?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload", (key, json.dumps(contents[key], ensure_ascii=False)))
        # Publish the window hand-over in the same commit as the personal data. A restore
        # without window preferences cancels an older, not-yet-applied hand-over.
        if desktop_window_pending is None:
            conn.execute("DELETE FROM meta WHERE id='desktop_window_pending'")
        else:
            conn.execute("INSERT INTO meta VALUES ('desktop_window_pending',?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                         (json.dumps(desktop_window_pending, ensure_ascii=False),))
