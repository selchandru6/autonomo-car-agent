"""Claim-check storage for raw HTML (requirements 7.5).

Bodies are gzipped to /data/snapshots/{run_id}/{dealer_id}_{page}.html.gz. Only pointers and
hashes travel through pipeline state. Embedded chat widgets are dropped before storage.
"""

from __future__ import annotations

import gzip
import re
from dataclasses import dataclass
from pathlib import Path

from carshop.util import sha256_bytes

_CHAT_RE = re.compile(
    r"<(?:div|section|iframe|script)[^>]*(?:id|class|src)=\"[^\"]*(?:chat|gubagoo|carnow|podium|livechat|drift|intercom|activengage)[^\"]*\"[^>]*>.*?</(?:div|section|iframe|script)>",
    re.I | re.S,
)


def scrub(html: str) -> str:
    return _CHAT_RE.sub("", html)


@dataclass(frozen=True, slots=True)
class Artifact:
    page_id: str
    storage_uri: str
    sha256: str
    size_bytes: int


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def put(self, run_id: str, dealer_id: str, page: str, body: str) -> Artifact:
        cleaned = scrub(body)
        data = cleaned.encode("utf-8", errors="replace")
        digest = sha256_bytes(data)
        safe_page = re.sub(r"[^A-Za-z0-9_.-]", "_", page)[:80]
        d = self.root / run_id
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{dealer_id}_{safe_page}.html.gz"
        with gzip.open(path, "wb", compresslevel=6) as f:
            f.write(data)
        return Artifact(
            page_id=f"{dealer_id}:{safe_page}",
            storage_uri=str(path),
            sha256=digest,
            size_bytes=len(data),
        )

    def get(self, storage_uri: str) -> str:
        with gzip.open(storage_uri, "rb") as f:
            return f.read().decode("utf-8", errors="replace")
