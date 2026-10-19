"""Static local plugin images become content-addressed host assets."""
from __future__ import annotations

import hashlib
from pathlib import Path

from .artwork import MAX_BYTES, image_kind


def local_file(directory, name):
    if not isinstance(name, str) or not name or len(name) > 250 or "\\" in name or ":" in name or Path(name).is_absolute():
        raise ValueError("插件资源必须使用插件目录内的相对路径")
    root = directory.resolve()
    path = (root / name).resolve()
    if not path.is_relative_to(root):
        raise ValueError("插件资源不得指向插件目录外")
    return path


def import_image(directory, name, data_dir):
    path = local_file(directory, name)
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("插件图片超过 5 MB")
    data = path.read_bytes()
    key = hashlib.sha256(data).hexdigest()[:32] + "." + image_kind(data)
    target = data_dir / "artwork" / key
    if not target.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_bytes(data)
        temporary.replace(target)
    return {"key": key, "path": "/api/artwork/" + key}
