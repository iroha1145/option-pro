import json

from app.services.research_eod_v1.reporting import build_return_pack
from app.services.research_eod_v1.paths import RETURN_PACK_DIR


def test_return_pack_has_required_row_counts(tmp_path, monkeypatch) -> None:
    from app.services.research_eod_v1 import reporting

    target = tmp_path / "pack"
    monkeypatch.setattr(reporting, "RETURN_PACK_DIR", target)
    monkeypatch.setattr(reporting, "REPORTS_DIR", tmp_path / "reports")
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "audit.md").write_text("# audit\n", encoding="utf-8")
    summary = build_return_pack(test_log="ok\n")
    assert summary["primary"] == 864
    assert summary["etf"] == 180
    assert summary["composite"] == 12
    assert summary["ledger"] == 864 + 180 + 12 + 864
    assert summary["event_overlay"] == 864
    assert (target / "README.md").is_file()
    assert (target / "code_diff_stat.txt").is_file()
    ledger = json.loads((target / "trial_ledger.json").read_text(encoding="utf-8"))
    assert ledger["event_overlay"] == 864
    winners = [row["winner"] for row in __import__("csv").DictReader((target / "all_experiments.csv").open(encoding="utf-8"))]
    assert winners and all(value in {"False", "false"} for value in winners)
    text = (target / "all_experiments.csv").read_text(encoding="utf-8")
    assert text.count("\n") == 865
    assert "DATA_INSUFFICIENT" in text
    assert (target / "sector_reports" / "semiconductors.md").is_file()
    assert (target / "return_summary.json").is_file()
