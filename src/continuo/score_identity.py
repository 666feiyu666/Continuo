from __future__ import annotations

import hashlib
import json

from .domain import MusicProject


def score_sha256(project: MusicProject) -> str:
    payload = json.dumps(
        project.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
