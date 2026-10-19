"""Portable iCalendar snapshots; no access to a user's system calendar store."""
from __future__ import annotations

import datetime as dt

from acg_calendar.parser import stamp

TRUSTED = {"exact", "manual", "confirmed"}


def text(value):
    return str(value or "").replace("\\", "\\\\").replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n").replace(";", "\\;").replace(",", "\\,")


def fold(line):
    """RFC 5545: fold at 75 UTF-8 octets without breaking a character."""
    lines, current = [], ""
    for character in line:
        if len((current + character).encode("utf-8")) > 75:
            lines.append(current)
            current = " "
        current += character
    return "\r\n".join([*lines, current])


def calendar_bytes(versions, settings, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    utc = lambda value: stamp(value).astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Playtime//Game Calendar//ZH", "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "X-WR-CALNAME:游时 · 终末地国服"]
    skipped = 0
    for version in versions:
        for event in version["events"]:
            if event.get("completed") or event.get("ignored"):
                continue
            start, end = event["start"], event["end"]
            point = event.get("time_kind") == "point"
            if (not start.get("at") or start["state"] not in TRUSTED or event.get("invalid_interval") or
                    (not point and (not end.get("at") or end["state"] not in TRUSTED or stamp(end["at"]) <= stamp(start["at"])))):
                skipped += 1
                continue
            lines += ["BEGIN:VEVENT", "UID:" + event["id"] + "@playtime.local", "DTSTAMP:" + now.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ"), "DTSTART:" + utc(start["at"])]
            if not point:
                lines.append("DTEND:" + utc(end["at"]))
            description = "终末地国服 · " + version["name"] + "\n" + event.get("description", "")
            if event.get("notes"):
                description += "\n备注：" + event["notes"]
            description += "\n公告：" + event["source"]["url"] + "\n本文件为导出快照，时间变更需重新导入。"
            lines += ["SUMMARY:" + text(event["title"]), "DESCRIPTION:" + text(description), "URL:" + event["source"]["url"], "STATUS:CONFIRMED", "TRANSP:TRANSPARENT"]
            if settings.get("reminders_enabled") and not point:
                offsets = event.get("reminder_minutes")
                if offsets is None:
                    offsets = settings.get("reminder_offsets", [])
                for minutes in offsets:
                    lines += ["BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:" + text(event["title"] + " 即将截止"), f"TRIGGER;RELATED=END:-PT{minutes}M", "END:VALARM"]
            lines.append("END:VEVENT")
    lines += [f"X-PLAYTIME-SKIPPED:{skipped}", "END:VCALENDAR"]
    return ("\r\n".join(fold(line) for line in lines) + "\r\n").encode("utf-8")
