# Yahoo 当前池诊断快照

本目录保留 2026-09-16 13:24 NY 抓取的审计证据。该次采集被标为 `INVALID_EOD_CAPTURE`：当时距常规 16:00 收盘还有约 2 小时 35 分，`as_of_after_close` 把评估时间推进到了尚未发生的收盘后。

- 证券数：214（`security_master.csv`）
- 历史日线条数：582198（已从公开 Git 移除；字节哈希见 `hashes.json`）
- 被撤销的同日 A/balanced/mid 主题快照：24
- 最近完整交易日（按该采集时钟）：2026-09-15
- 大文件不进公开仓库；离线重放使用受控工件 + `LocalParquetProvider`

不是十年 PIT，不是退市并集，Close 未核验为未复权成交价。
