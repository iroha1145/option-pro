D=/content/drive/MyDrive/option-pro-data/radar_replay_2026-09-29/inputs
mountpoint -q /content/drive || { echo "DRIVE NOT MOUNTED"; exit 1; }
mkdir -p $D
ls -la /content/upload /content/upload/fred 2>/dev/null | grep -v '^total'
for f in /content/upload/pit_shares.jsonl.gz /content/upload/radar_export_2026-09-08_2026-09-25.jsonl.gz /content/upload/replay_smoke_subset.sqlite.gz /content/upload/minute_spy.tar /content/minute_manifest.jsonl /content/pack_manifest_spy.jsonl /content/pack_manifest_smoke.jsonl /content/upload/radar_census.json /content/upload/smoke_tickers.json /content/upload/extra_daily_tickers.json; do [ -f $f ] && cp $f $D/; done
[ -d /content/upload/fred ] && mkdir -p $D/fred && cp /content/upload/fred/*.csv $D/fred/
cd $D && sha256sum $(find . -type f ! -name SHA256SUMS | sort) > SHA256SUMS && cat SHA256SUMS | cut -c1-16,65-
