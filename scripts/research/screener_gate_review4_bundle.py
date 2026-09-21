"""Assemble the review-4 compact zip from repo-external research outputs.

Does not rescore, and does not include raw Yahoo bars. The commands listed in
README.md are templates; this script only packages files that already exist.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any, Mapping

LOCKED_RETURN_PACK = {
    "examples.csv": "d2f3596c9120378a8969e641f457b136caa07f95846de6584694e583928d8d34",
    "gate_attribution.csv": "e47ec06f3533ad740defa856f1d47c5aa18bfc8ed286903d54ec12ba68d1e9d0",
    "manifest.json": "4862aa09a70e73bf3d5acb645e34f701629acfb963e8c5403c6ce9fe3c1a476c",
    "paired_daily.csv": "494527bbd5756156dbf85d0d80c0b42f2b92bf0d697966490a33eb7f8f43dbfd",
    "summary.json": "f3e9c5ee8b508ee488d75c761e2553dc3d82f86ff778220a9c96bf3a49734357",
    "validation.md": "4384f5131fdbef4e5041a368d0d399594675e750171ba444c7b028a05bd874af",
}

def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_locked_pack(pack: Path, locked: Mapping[str, str] = LOCKED_RETURN_PACK) -> dict[str, str]:
    found = {}
    for name, expected in locked.items():
        actual = sha256_file(pack / name)
        if actual != expected:
            raise RuntimeError(f"old pack hash changed for {name}: expected {expected} actual {actual}")
        found[name] = actual
    return found


def technical_relabel_delta(old_summary: Mapping[str, Any], v2_summary: Mapping[str, Any]) -> dict[str, Any]:
    old_b0 = old_summary["bootstrap_h20_close"]["B0_current"]["H"]
    v2_b0 = v2_summary["level_means"]["B0_current"]["20"]["close_full"]
    paired = v2_summary["bootstrap_paired_top20"]["G1_stock_reference"]["20"]["close"]
    return {
        "old_b0_h20_close_absolute_mean": old_b0.get("mean"),
        "old_b0_h20_close_ci95": old_b0.get("ci95"),
        "v2_b0_h20_close_full_mean": v2_b0,
        "close_level_matches_old_absolute": v2_b0 == old_b0.get("mean"),
        "g1_minus_b0_h20_close_paired": paired,
        "coverage_top20": v2_summary.get("coverage_top20"),
        "note": (
            "The close full-basket level can match the old absolute mean while the open label "
            "and the paired increment are the corrected statistics. A paired interval that "
            "includes zero is not a winner."
        ),
    }


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def build_review4_bundle(
    research: Path,
    dest_zip: Path,
    *,
    require_discovery: bool = True,
    locked: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    pack = research / "return_pack"
    metrics = research / "return_pack_metrics_v2"
    discovery = research / "return_pack_discovery_v2"
    locked = verify_locked_pack(pack, LOCKED_RETURN_PACK if locked is None else locked)
    if not (metrics / "summary.json").is_file():
        raise FileNotFoundError(metrics / "summary.json")
    discovery_ready = (discovery / "summary.json").is_file()
    if require_discovery and not discovery_ready:
        raise FileNotFoundError(discovery / "summary.json")
    delta = technical_relabel_delta(_read_json(pack / "summary.json"), _read_json(metrics / "summary.json"))
    dest_zip.parent.mkdir(parents=True, exist_ok=True)
    included = []
    with zipfile.ZipFile(dest_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("review4/old_return_pack_sha256.json", json.dumps(locked, indent=2))
        archive.writestr("review4/technical_relabel_delta.json", json.dumps(delta, indent=2))
        for folder, prefix in (
            (metrics, "return_pack_metrics_v2"),
            (discovery, "return_pack_discovery_v2"),
        ):
            if not folder.is_dir():
                continue
            for path in sorted(folder.rglob("*")):
                if not path.is_file() or "checkpoints" in path.relative_to(folder).parts:
                    continue
                relative = path.relative_to(research)
                archive.write(path, arcname=str(relative))
                included.append(str(relative))
        for name in (
            "alignment_v2/report.json",
            "alignment_v2/full_technical_top20.json",
            "command_log_review4.txt",
            "pytest_review4.log",
        ):
            path = research / name
            if path.is_file():
                archive.write(path, arcname=name)
                included.append(name)
        note = {
            "raw_bars_included": False,
            "checkpoints_included": False,
            "discovery_summary_included": discovery_ready,
            "old_pack_sha256": locked,
            "technical_relabel_delta": delta,
        }
        archive.writestr("review4/bundle_note.json", json.dumps(note, indent=2))
    return {"zip": str(dest_zip), "included": included, "delta": delta}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-dir", default=str(Path.home() / "optix-research" / "screener-gate-v1"))
    parser.add_argument("--dest", default="")
    parser.add_argument("--allow-missing-discovery", action="store_true")
    args = parser.parse_args()
    research = Path(args.research_dir)
    dest = Path(args.dest) if args.dest else research / "screener_gate_review4_compact.zip"
    print(json.dumps(build_review4_bundle(research, dest, require_discovery=not args.allow_missing_discovery), indent=2))


if __name__ == "__main__":
    main()
