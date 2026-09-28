"""Turn frozen Massive 5-minute pages into the replay's per-ticker parquet store.

    python build_minute_store.py --manifest /content/minute_manifest.jsonl \
        --raw-root /content/minute_raw --out /content/minute_store
    python build_minute_store.py --tar smoke_minute.tar --out /tmp/minute_store

The manifest is JSON lines with ticker, from, to, part, http, status, count, bytes,
sha256, file and complete; only complete HTTP 200 pages are read. Pages of one ticker
overlap at their joins, so bars are de-duplicated by timestamp (later pages win, as
``massive.ticker_range`` does). Nothing here reimplements a production computation.
"""

from __future__ import annotations

import argparse
import sys
import tarfile
import tempfile
from pathlib import Path

PACK = Path(__file__).resolve().parents[1]
REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(PACK))

from harness.stores import build_minute_store  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--raw-root", type=Path)
    parser.add_argument("--tar", type=Path, help="a tar holding the pages and a *manifest.jsonl (smoke data)")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.tar is not None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with tarfile.open(args.tar) as archive:
                archive.extractall(root, filter="data")
            manifests = sorted(root.rglob("*manifest.jsonl"))
            if not manifests:
                raise SystemExit("no manifest inside the tar")
            summary = build_minute_store(manifests[0], manifests[0].parent, args.out)
    else:
        if args.manifest is None or args.raw_root is None:
            raise SystemExit("--manifest and --raw-root are required without --tar")
        summary = build_minute_store(args.manifest, args.raw_root, args.out)
    print(summary)
    print("DONE build_minute_store")


if __name__ == "__main__":
    main()
