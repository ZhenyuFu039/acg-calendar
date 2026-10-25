"""Offline historical replay. No network, database writes or installed-plugin edits."""
import argparse
import copy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from acg_calendar.azurlane import compile_feed
from acg_calendar.azurlane_artwork import snapshot_candidates


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def replay():
    plugin = ROOT / 'plugins/azurlane-cn'
    rules = read(plugin / 'rules.json')
    config = read(plugin / 'artwork-alignment.json')
    pages = read(plugin / 'sources/cn-bulletins.json')['pages']
    catalog = snapshot_candidates(read(ROOT / 'samples/azurlane-artwork-history.json'), config)
    # Ground truth is the old independently verified mappings, not algorithm output.
    expected = {m['title']: m['image_url'] for m in rules['artwork'] if m.get('language') == 'en'}
    main = '幽影迷城'
    for child in (' · 活动商店', ' · 累计奖励领取'):
        expected[main + child] = expected[main]
    rules['artwork'] = []
    version = compile_feed(pages, rules, 'offline-replay', artwork_catalog=catalog, alignment_rules=config)['versions'][0]
    report = version['analysis']['artwork_alignment']
    for result in report['results']:
        result['ground_truth'] = ('correct' if result.get('image_url') == expected.get(result['title']) else 'wrong') if result['status'] == 'matched' else 'not_assigned'
    held_out = copy.deepcopy(config)
    held_out['aliases'] = []
    blind = compile_feed(pages, rules, 'offline-replay', artwork_catalog=catalog, alignment_rules=held_out)['versions'][0]
    report['validation'] = {
        'cn_announcements': len(pages), 'cn_events': len(version['events']),
        'known_en_banner_events_including_children': len(expected),
        'correct': sum(r['ground_truth'] == 'correct' for r in report['results']),
        'wrong': sum(r['ground_truth'] == 'wrong' for r in report['results']),
        'old_image_url_mappings_removed': True,
        'title_aliases_are_curated': True,
        'no_alias_matches': [{k: r[k] for k in ('title', 'image_url', 'shared_missions', 'evidence')}
                             for r in blind['analysis']['artwork_alignment']['results'] if r['status'] == 'matched'],
        'scope': 'Limited historical sample; abstention is not full coverage. Real cross-server delay beyond these samples is not inferred as a global constant.'
    }
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = replay()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: result[k] for k in ('matched', 'ambiguous', 'unmatched', 'validation')}, ensure_ascii=False, indent=2))
    if result['validation']['wrong']:
        sys.exit(1)
