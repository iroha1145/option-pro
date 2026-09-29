"""Whole minute-bar freeze in one kernel-occupying run (the kernel must stay busy or Colab reclaims the machine).

Steps: restore the replay cache from Drive, rebuild the census feature table and the fetch plan, then fetch the
smoke plan and the main plan year by year (newest first). After each batch the raw files and the batch's manifest
lines are packed into a tar, copied to Drive and hash-checked. Resumable from the Drive copies.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

sys.path.insert(0, "/content")
import minute_fetch as mf  # noqa: E402

ARC = "/content/drive/MyDrive/option-pro-data/replay_archive_2026-09-28"
OUT = Path("/content/drive/MyDrive/option-pro-data/massive_minute_frozen_2026-09-28")
BUNDLE_SHA = "9e20ac818ee4af6e77a4679d910536cf333461dba539d7652d325e61888e2ef4"
PACK = Path("/content/pack")
started = time.time()


def say(text):
    mf.log(f"[all] {text}")
    print(f"{time.time() - started:8.0f}s {text}", flush=True)


def sha(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 22), b""):
            digest.update(chunk)
    return digest.hexdigest()


OUT.mkdir(parents=True, exist_ok=True)
PACK.mkdir(parents=True, exist_ok=True)
if not Path("/content/data/replay.sqlite").exists():
    shutil.copyfile(f"{ARC}/replay_bundle.tar.gz", "/content/replay_bundle.tar.gz")
    if sha("/content/replay_bundle.tar.gz") != BUNDLE_SHA:
        raise SystemExit("replay bundle sha mismatch")
    subprocess.run(["tar", "-xzf", "/content/replay_bundle.tar.gz", "-C", "/content"], check=True)
    os.remove("/content/replay_bundle.tar.gz")
    say("replay cache restored")
if not Path("/content/minute_plan_main.jsonl").exists():
    import sqlite3

    conn = sqlite3.connect("/content/census.sqlite")
    conn.execute("ATTACH 'file:/content/data/replay.sqlite?mode=ro' AS src")
    conn.execute("DROP TABLE IF EXISTS feat")
    conn.execute("""
    CREATE TABLE feat AS
    WITH bars AS (
      SELECT ticker, session_date, open, high, close, volume,
             LAG(close) OVER w AS prev_close,
             AVG(volume) OVER (PARTITION BY ticker ORDER BY session_date ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING) AS avg_vol_10,
             COUNT(volume) OVER (PARTITION BY ticker ORDER BY session_date ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING) AS n_prior,
             AVG(close * volume) OVER (PARTITION BY ticker ORDER BY session_date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) AS adv_20
      FROM src.raw_daily_bars
      WINDOW w AS (PARTITION BY ticker ORDER BY session_date)
    ),
    split_day AS (
      SELECT ticker, execution_date, MIN(split_from / split_to) AS factor FROM src.splits GROUP BY ticker, execution_date
    )
    SELECT b.ticker, b.session_date, b.open, b.high, b.close, b.volume, b.n_prior, b.adv_20,
           b.prev_close * COALESCE(s.factor, 1.0) AS prev_close_adj,
           b.avg_vol_10 / COALESCE(s.factor, 1.0) AS avg_vol_10_adj
    FROM bars b LEFT JOIN split_day s ON s.ticker = b.ticker AND s.execution_date = b.session_date
    """)
    conn.commit()
    conn.close()
    say("census feature table rebuilt")
    mf.plan()
    say("plan rebuilt")
for name in ("minute_plan_main.jsonl", "minute_plan_smoke.jsonl"):
    shutil.copyfile(f"/content/{name}", OUT / name)

# Resume: unpack any batch already on Drive so the manifest skips its pages.
done_batches = set()
for tar_path in sorted(OUT.glob("minute_*.tar")):
    recorded = (OUT / (tar_path.name + ".sha256")).read_text().split()[0] if (OUT / (tar_path.name + ".sha256")).exists() else None
    if recorded and sha(tar_path) == recorded:
        with tarfile.open(tar_path) as archive:
            archive.extractall("/content", filter="data")
        done_batches.add(tar_path.name[len("minute_"):-len(".tar")])
if done_batches:
    manifest_lines = []
    for batch in sorted(done_batches):
        manifest_lines += Path(f"/content/pack_manifest_{batch}.jsonl").read_text().splitlines(keepends=True)
    Path("/content/minute_manifest.jsonl").write_text("".join(manifest_lines))
    say(f"resumed batches from Drive: {sorted(done_batches)}")

main = [json.loads(line) for line in open("/content/minute_plan_main.jsonl")]
batches = {"smoke": [json.loads(line) for line in open("/content/minute_plan_smoke.jsonl")]}
for year in sorted({page["to"][:4] for page in main}, reverse=True):
    batches[year] = [page for page in main if page["to"][:4] == year]

for batch, pages in batches.items():
    if batch in done_batches:
        continue
    plan_path = f"/content/plan_{batch}.jsonl"
    with open(plan_path, "w") as handle:
        handle.writelines(json.dumps(page) + "\n" for page in pages)
    say(f"batch {batch}: {len(pages)} pages")
    mf.fetch(plan_path, 24)
    keys = {(page["ticker"], page["from"], page["to"]) for page in pages}
    lines = [line for line in open("/content/minute_manifest.jsonl")
             if (lambda r: (r["ticker"], r["from"], r["to"]) in keys)(json.loads(line))]
    incomplete = len(keys) - len({(r["ticker"], r["from"], r["to"]) for r in map(json.loads, lines) if r.get("complete")})
    manifest_name = f"pack_manifest_{batch}.jsonl"
    Path(f"/content/{manifest_name}").write_text("".join(lines))
    files = sorted({json.loads(line)["file"] for line in lines})
    tar_path = PACK / f"minute_{batch}.tar"
    with tarfile.open(tar_path, "w") as archive:
        for rel in files:
            archive.add(f"/content/minute_raw/{rel}", arcname=f"minute_raw/{rel}")
        archive.add(f"/content/{manifest_name}", arcname=manifest_name)
    digest = sha(tar_path)
    shutil.copyfile(tar_path, OUT / tar_path.name)
    copied = sha(OUT / tar_path.name)
    (OUT / (tar_path.name + ".sha256")).write_text(f"{digest}  {tar_path.name}\n")
    shutil.copyfile("/content/minute_manifest.jsonl", OUT / "minute_manifest.jsonl")
    say(f"batch {batch}: {len(files)} files, {incomplete} incomplete pages, tar {tar_path.stat().st_size / 1e9:.2f} GB, "
        f"drive copy {'sha ok' if copied == digest else 'SHA MISMATCH'}")
    tar_path.unlink()

from google.colab import drive  # noqa: E402

drive.flush_and_unmount()
say("DONE fetch_all; drive flushed and unmounted")
