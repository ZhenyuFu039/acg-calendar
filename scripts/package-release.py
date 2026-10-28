"""Package a committed Git tree and published Windows binaries; never read live data/."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ref', required=True, help='Commit or tree to archive')
    parser.add_argument('--publish-dir', type=Path, required=True)
    parser.add_argument('--work-dir', type=Path, required=True)
    args = parser.parse_args()
    work, publish = args.work_dir.resolve(), args.publish_dir.resolve()
    for directory in (work, publish):
        if not directory.is_relative_to(ROOT) or directory == ROOT:
            raise ValueError('Release directories must be inside the workspace')
    stage, verify = work / 'package', work / 'verify'
    for directory in (stage, verify):
        directory.mkdir(parents=True, exist_ok=True)
        if any(directory.iterdir()):
            raise ValueError('Refusing to overwrite a nonempty release directory')
    source_zip = work / 'committed-source.zip'
    subprocess.run(['git', 'archive', '--format=zip', '--output', str(source_zip), args.ref], cwd=ROOT, check=True)
    forbidden = {'.git', '.claude', 'data', 'logs', 'dist', 'promo', 'node_modules'}
    with zipfile.ZipFile(source_zip) as source:
        for item in source.infolist():
            if not (stage / item.filename).resolve().is_relative_to(stage) or item.filename.split('/')[0] in forbidden:
                raise ValueError('Non-release content in source tree: ' + item.filename)
        source.extractall(stage)
    version = (stage / 'VERSION').read_text(encoding='utf-8').strip()
    if not version or any(c not in '0123456789.' for c in version):
        raise ValueError('Invalid release version')
    name = f'Playtime-V{version}-windows-x64.zip'
    out = ROOT / 'dist/releases'
    out.mkdir(parents=True, exist_ok=True)
    archive_path = out / name
    if archive_path.exists():
        raise ValueError('Release ZIP already exists; not overwriting')
    plugins = ('nikke-global', 'azurlane-cn', 'zzz-cn')
    for plugin in plugins:
        shutil.copytree(stage / 'plugins' / plugin, stage / 'data/plugins' / plugin)
    desktop = stage / 'dist/desktop'
    desktop.mkdir(parents=True)
    for item in publish.iterdir():
        if item.is_file() and item.suffix != '.pdb':
            shutil.copy2(item, desktop / item.name)
    tree = subprocess.check_output(['git', 'rev-parse', args.ref + '^{tree}'], cwd=ROOT, text=True).strip()
    (stage / 'BUILD-INFO.json').write_text(json.dumps({'version': version, 'source_tree': tree, 'runtime': 'Python 3.11+; .NET 10 Desktop Runtime x64', 'self_contained': False}, indent=2), encoding='utf-8')
    (stage / 'START-HERE.txt').write_text(
        f'游时 Playtime V{version}\n\n'
        'Windows x64 便携包，需要 Python 3.11+ 与 .NET 10 Desktop Runtime x64。\n'
        '首次运行：python -m pip install -r requirements.txt，然后双击 Playtime.cmd。\n'
        '已编译程序在 dist/desktop/Playtime.exe，不需要 .NET SDK；网页模式可运行 start.ps1。\n\n'
        '日程、个人计划、游戏管理、设置为独立入口；备份在设置的“备份与恢复”。\n'
        '桌面底部固定提供气泡、同步、完整日历、置顶、更多。\n'
        '本包含终末地与明日方舟内置日历，并预装 NIKKE 国际服、碧蓝航线国服、绝区零国服插件。\n'
        'data/ 只包含公开插件，没有个人数据库、设置或日志。\n\n'
        '升级前退出旧程序并备份整个旧 data/；新版解压到新目录后迁移旧数据，不要用初始数据覆盖它。\n'
        f'运行环境、升级和已知边界：docs/releases/v{version}.md。\n'
        '来源致谢、非商业用途与免责：docs/development.md、NOTICE.md；源码 MIT 与第三方素材许可分开适用。\n', encoding='utf-8-sig')
    with zipfile.ZipFile(archive_path, 'x', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for item in sorted(stage.rglob('*')):
            if item.is_file():
                archive.write(item, item.relative_to(stage).as_posix())
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.testzip() is None
        names = set(archive.namelist())
        required = {'VERSION', 'app.py', 'Playtime.cmd', 'requirements.txt', 'BUILD-INFO.json', 'dist/desktop/Playtime.exe', 'dist/desktop/Playtime.dll', 'web/navigation.js', 'web/ux.css', f'docs/releases/v{version}.md', 'LICENSE', 'NOTICE.md'}
        assert required <= names
        assert archive.read('VERSION').decode().strip() == version
        assert not any(n.split('/')[0] in {'.git', '.claude', 'logs', 'promo'} for n in names)
        assert not any(n.startswith('data/') and not n.startswith('data/plugins/') for n in names)
        assert all(f'data/plugins/{g}/manifest.json' in names for g in plugins)
        archive.extractall(verify)
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    checksum = out / (name + '.sha256')
    checksum.write_text(digest + '  ' + name + '\n', encoding='ascii')
    result = {'archive': str(archive_path), 'checksum': str(checksum), 'sha256': digest, 'bytes': archive_path.stat().st_size, 'source_tree': tree, 'verify_root': str(verify)}
    (work / 'package-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
