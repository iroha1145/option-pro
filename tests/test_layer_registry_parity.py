"""后端 layer_registry 与前端 registry.ts 的镜像一致性。

前端那份是手抄的，之前没有任何测试盯着：一边改了 id，图层就在图上悄悄消失，
没有报错也没有日志。这里在运行时读前端源码，逐条比对 id / 分组 / kind /
预设成员——两边任意一侧增删改名都会挂在这里，而不是挂在产品上。
"""

from __future__ import annotations

import re
from pathlib import Path

from app.services.technical.layer_registry import LAYERS, PRESETS

_REGISTRY_TS = (
    Path(__file__).resolve().parents[1]
    / "frontend-src/src/components/detail/chart-drawings/analysis/registry.ts"
)

_LAYER_RE = re.compile(
    r"\{\s*id:\s*'(?P<id>[^']+)',\s*group:\s*'(?P<group>[^']+)',\s*kind:\s*'(?P<kind>[^']+)'"
)
_PRESET_RE = re.compile(
    r"^  (?P<name>[a-z_]+):\s*\{(?P<body>.*?)^  \},",
    re.DOTALL | re.MULTILINE,
)
_ENABLED_RE = re.compile(r"enabled:\s*(?P<value>\[[^\]]*\]|LAYERS\.map\([^)]*\))")
_KIND_MAP_RE = re.compile(
    r"OVERLAY_LAYER_BY_KIND[^=]*=\s*\{(?P<body>.*?)\n\};", re.DOTALL
)


def _source() -> str:
    assert _REGISTRY_TS.exists(), f"frontend registry moved: {_REGISTRY_TS}"
    return _REGISTRY_TS.read_text(encoding="utf-8")


def _frontend_layers(source: str) -> list[tuple[str, str, str]]:
    block = source.split("export const LAYERS", 1)[1].split("];", 1)[0]
    return [(m["id"], m["group"], m["kind"]) for m in _LAYER_RE.finditer(block)]


def _frontend_presets(source: str, layer_ids: list[str]) -> dict[str, list[str]]:
    block = source.split("export const PRESETS", 1)[1].split("\n};", 1)[0]
    presets: dict[str, list[str]] = {}
    for match in _PRESET_RE.finditer(block):
        enabled = _ENABLED_RE.search(match["body"])
        if enabled is None:
            continue
        raw = enabled["value"]
        if raw.startswith("LAYERS.map"):
            presets[match["name"]] = list(layer_ids)
        else:
            presets[match["name"]] = re.findall(r"'([^']+)'", raw)
    return presets


def test_frontend_registry_lists_exactly_the_backend_layers() -> None:
    source = _source()
    frontend = _frontend_layers(source)
    backend = [(row["id"], row["group"], row["kind"]) for row in LAYERS]
    assert frontend, "no layers parsed from registry.ts — parser or file shape changed"
    frontend_ids = [row[0] for row in frontend]
    backend_ids = [row[0] for row in backend]
    assert sorted(frontend_ids) == sorted(backend_ids)
    assert len(set(frontend_ids)) == len(frontend_ids)
    assert dict((i, (g, k)) for i, g, k in frontend) == dict(
        (i, (g, k)) for i, g, k in backend
    )


def test_presets_enable_the_same_layers_on_both_sides() -> None:
    source = _source()
    layer_ids = [row[0] for row in _frontend_layers(source)]
    frontend = _frontend_presets(source, layer_ids)
    assert set(frontend) == set(PRESETS), (sorted(frontend), sorted(PRESETS))
    for name, enabled in frontend.items():
        assert sorted(enabled) == sorted(PRESETS[name]["enabled"]), name
        # 预设不能启用一个注册表里不存在的图层——那等于勾了个空开关。
        assert set(enabled) <= set(layer_ids), name


def test_overlay_kind_map_points_at_live_layers() -> None:
    source = _source()
    body = _KIND_MAP_RE.search(source)
    assert body is not None, "OVERLAY_LAYER_BY_KIND not found in registry.ts"
    targets = set(re.findall(r":\s*'([^']+)'", body["body"]))
    assert targets
    assert targets <= {row["id"] for row in LAYERS}
