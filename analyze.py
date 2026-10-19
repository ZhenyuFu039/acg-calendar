"""Analyze a saved announcement with the JSON rules, without network or AI."""
import argparse
import json
from pathlib import Path

from acg_calendar.parser import AnnouncementParser, ROOT, html_to_text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "samples/endfield-cn-6094.txt")
    parser.add_argument("--output", type=Path, default=ROOT / "data/analysis.json")
    parser.add_argument("--rules", type=Path, default=ROOT / "rules/endfield-cn.json")
    parser.add_argument("--cid", default="6094")
    parser.add_argument("--url")
    args = parser.parse_args()
    rules = json.loads(args.rules.read_text(encoding="utf-8"))
    text = args.input.read_text(encoding="utf-8")
    if args.input.suffix.lower() in {".html", ".htm"}:
        text = html_to_text(text)
    result = AnnouncementParser(rules).parse(text, cid=args.cid, source_url=args.url or "https://endfield.hypergryph.com/news/" + args.cid, origin="local-file")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Parsed entries:", len(result["events"]))
    print("Output:", args.output.resolve())


if __name__ == "__main__":
    main()
