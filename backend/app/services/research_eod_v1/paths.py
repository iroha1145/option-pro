from __future__ import annotations

from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = PACKAGE_DIR.parents[2]
REPO_ROOT = BACKEND_ROOT.parent
RESEARCH_ROOT = REPO_ROOT / "research" / "option_pro_us_eod_v1"
CONFIG_DIR = RESEARCH_ROOT / "config"
REFERENCE_DIR = RESEARCH_ROOT / "reference"
TEMPLATES_DIR = RESEARCH_ROOT / "templates"
REPORTS_DIR = RESEARCH_ROOT / "reports"
RETURN_PACK_DIR = RESEARCH_ROOT / "return_pack"

REGISTRY_PATH = CONFIG_DIR / "registry.json"
EXPERIMENT_MANIFEST_PATH = CONFIG_DIR / "experiment_manifest.json"
ETF_SUBASSET_MANIFEST_PATH = CONFIG_DIR / "etf_subasset_manifest.json"
COMPOSITE_MANIFEST_PATH = CONFIG_DIR / "composite_manifest.json"
REFERENCE_REGISTRY_PATH = REFERENCE_DIR / "registry.py"
RETURN_SUMMARY_TEMPLATE_PATH = TEMPLATES_DIR / "return_summary.json"


def ensure_reference_on_path() -> None:
    import sys

    if str(REFERENCE_DIR) not in sys.path:
        sys.path.insert(0, str(REFERENCE_DIR))
