"""Game/source identity is separate from calendar periods and currencies."""
GAMES = [
    {'id': 'endfield-cn', 'name': '明日方舟：终末地', 'short_name': '终末地', 'monogram': 'EF', 'server': '国服', 'timezone': 'Asia/Shanghai', 'source_label': '官方公告与版本导览', 'source_kind': 'official'},
    {'id': 'arknights-cn', 'name': '明日方舟', 'short_name': '明日方舟', 'monogram': 'AK', 'server': '国服', 'timezone': 'Asia/Shanghai', 'source_label': 'PRTS 社区 Wiki', 'source_kind': 'community_wiki'},
]
GAME_IDS = {game['id'] for game in GAMES}


def game_name(game_id):
    return next((g['name'] for g in GAMES if g['id'] == game_id), game_id)
