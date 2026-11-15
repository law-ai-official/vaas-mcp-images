"""Lane health ledger: the machine-readable truth for per-lane availability.

Contract (deliver-hosted-surface W3; spec ciyuan-lane-providers「车道缺陷的记录
与规避」+ generation-registry「方式可发现性」):
- discovery surfaces (models_voices / registry_list_generators) annotate each
  lane-dependent entry with the lane's health;
- a `down` lane must never be advertised as usable;
- lane_down classification lives in generators.errors (tenant-safe);
- clearing a debt requires a real E2E probe — never a status flip alone.
"""

import json
from functools import lru_cache
from pathlib import Path

_HEALTH_PATH = Path(__file__).parent / "lane_health.json"

# 一条台账判定「多久算旧」。设成 7 天对齐 DEBT-LANE-9-01-REVERIFY 的约定（每次动
# VAAS 相关批次顺手复验），也覆盖「一周没人碰这条线」这个最常见的静默形态。
# 单条 lane 可用 `stale_after_days` 覆盖（小时级车道应更短）。
DEFAULT_STALE_AFTER_DAYS = 7


@lru_cache(maxsize=1)
def load_lane_health(path=_HEALTH_PATH) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError:
        return {"lanes": {}}


def _staleness(entry: dict) -> dict:
    """How old is this verdict, and is it past its own freshness budget?

    Why this exists (2026-11-14): a `down` verdict **blocks upstream probing**
    (the lane gate refuses before the call), so `last_verified` never advances
    on its own — the ledger can hold a three-day-old judgement that nobody
    re-checked, and both directions are wrong when it is stale:
    a stale `down` keeps refusing a lane that may have recovered, a stale
    `active` advertises a lane that may have died.

    Returns {"age_days": float|None, "stale": bool|None}. `None` means "cannot
    judge" (missing/unparseable timestamp) — never silently report that as
    fresh: an unparseable date is a ledger defect, not a green light.
    """
    raw = (entry or {}).get("last_verified")
    budget = (entry or {}).get("stale_after_days") or DEFAULT_STALE_AFTER_DAYS
    if not raw:
        return {"age_days": None, "stale": None}
    try:
        from datetime import date

        verified = date.fromisoformat(str(raw)[:10])
    except (TypeError, ValueError):
        return {"age_days": None, "stale": None}
    age_days = (date.today() - verified).days
    try:
        return {"age_days": age_days, "stale": age_days > int(budget)}
    except (TypeError, ValueError):
        return {"age_days": age_days, "stale": age_days > DEFAULT_STALE_AFTER_DAYS}


def lane_health(kind: str, provider: str) -> dict:
    """Health view for one lane (kind ∈ voice/image/video/render + provider).

    Unknown lane → conservative `degraded` (unknown ≠ advertise as fine).
    Every view carries `age_days`/`stale` so a caller can tell a fresh verdict
    from a ledger entry nobody has touched in a week.
    """
    lanes = (load_lane_health() or {}).get("lanes") or {}
    entry = lanes.get(f"{kind}.{provider}")
    if entry:
        return {
            "health": entry.get("health", "degraded"),
            "reason_class": entry.get("reason_class"),
            "reason": entry.get("reason"),
            "last_verified": entry.get("last_verified"),
            **_staleness(entry),
        }
    return {"health": "degraded", "reason_class": None, "reason": "lane not in health ledger",
            "last_verified": None, "age_days": None, "stale": None}


def lane_down(kind: str, provider: str) -> bool:
    return lane_health(kind, provider).get("health") == "down"


def annotate(entries: list[dict], kind: str, provider_key: str = "provider") -> list[dict]:
    """Attach a `lane` health sub-dict to each registry entry (in place)."""
    for e in entries:
        prov = (e.get(provider_key) or "").lower()
        if prov:
            e["lane"] = lane_health(kind, prov)
    return entries
