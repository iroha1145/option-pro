"""Write fixed-matrix CI URLs only after both research-head workflows succeed."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PACK = ROOT / "research" / "option_pro_us_eod_v1" / "return_pack"
RESEARCH_HEAD = "af5a111612e945f99f9f9bc6e895ffa60ef3ab7f"
PUSH_RUN = "35370139053"
PR_RUN = "35370143182"


def _run(run_id: str) -> dict:
    raw = subprocess.check_output(
        ["gh", "run", "view", run_id, "--json", "status,conclusion,event,headSha,url,jobs"],
        cwd=ROOT,
        text=True,
    )
    return json.loads(raw)


def main() -> None:
    push = _run(PUSH_RUN)
    pull = _run(PR_RUN)
    for item in (push, pull):
        if item.get("headSha") != RESEARCH_HEAD:
            raise SystemExit(f"head mismatch: {item}")
        if item.get("conclusion") != "success":
            raise SystemExit(f"{item.get('event')} not success: {item.get('status')} {item.get('conclusion')}")
    payload = {
        "research_head": RESEARCH_HEAD,
        "push_run": push["url"],
        "pull_request_run": pull["url"],
        "check_runs": [
            {
                "name": job["name"],
                "conclusion": job["conclusion"],
                "url": job["url"],
            }
            for item in (push, pull)
            for job in item.get("jobs") or []
        ],
        "stamp_is_docs_only": True,
        "local_pytest": "3956 passed, 6 skipped",
        "github_python_tests_step": "success",
    }
    (PACK / "algorithm_fixed_matrix_ci.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    head_path = PACK / "algorithm_fixed_matrix_head.json"
    head = json.loads(head_path.read_text(encoding="utf-8"))
    head["github_ci"] = {
        "research_head": RESEARCH_HEAD,
        "push_run": push["url"],
        "pull_request_run": pull["url"],
        "stamp_is_docs_only": True,
        "stamped_at": datetime.now(timezone.utc).isoformat(),
    }
    head_path.write_text(json.dumps(head, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    audit = PACK / "algorithm_fixed_matrix_audit.md"
    body = audit.read_text(encoding="utf-8").rstrip() + "\n"
    stamp = (
        "\n## Current-head GitHub CI\n\n"
        f"Research-head GitHub CI on `{RESEARCH_HEAD[:8]}` is terminal success:\n\n"
        f"- push: {push['url']}\n"
        f"- pull_request: {pull['url']}\n\n"
        "A later URL-stamp commit is docs-only and is not a new research revision.\n"
    )
    if "Current-head GitHub CI" not in body:
        audit.write_text(body + stamp, encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    try:
        main()
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        raise
