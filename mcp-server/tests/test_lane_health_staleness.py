"""车道台账陈旧度回归：`down` 会阻断上游探测，所以判定会自己变旧，而旧判定两个方向都错。

背景（2026-11-14 实况）：图像车道 11-11 判 `down` 之后，再没有任何东西去打上游
（车道闸先拒 ⇒ 请求到不了上游 ⇒ `last_verified` 不前进）。三天后这条 `down`
既可能仍然为真，也可能上游早就解封——**光看台账分不出来**。
所以台账必须自带「多久算旧」，且**判不出时不许当新鲜**。

判据不手抄：直接 import 真实模块，只替换 `load_lane_health`（数据来源），
`lane_health()` / `annotate()` 的真实分支全部跑到。
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from mcp_server import lane_health as lh


def _with_ledger(monkeypatch, lanes: dict):
    monkeypatch.setattr(lh, "load_lane_health", lambda *a, **k: {"lanes": lanes})


def _iso(days_ago: int) -> str:
    return (date.today() - timedelta(days=days_ago)).isoformat()


def test_fresh_verdict_is_not_stale(monkeypatch):
    _with_ledger(monkeypatch, {"image.ciyuan": {"health": "down", "last_verified": _iso(1)}})
    view = lh.lane_health("image", "ciyuan")
    assert view["health"] == "down"
    assert view["stale"] is False
    assert view["age_days"] == 1


def test_old_verdict_is_stale(monkeypatch):
    """11-11 → 11-14 正是这个形态（3 天）。"""
    _with_ledger(monkeypatch, {"image.ciyuan": {"health": "down", "last_verified": _iso(3)}})
    assert lh.lane_health("image", "ciyuan")["stale"] is False

    _with_ledger(monkeypatch, {"image.ciyuan": {"health": "down", "last_verified": _iso(9)}})
    view = lh.lane_health("image", "ciyuan")
    assert view["stale"] is True and view["age_days"] == 9


def test_per_entry_budget_overrides_default(monkeypatch):
    """小时级车道可以把自己的复验周期压短。"""
    _with_ledger(monkeypatch, {"image.ciyuan": {
        "health": "active", "last_verified": _iso(2), "stale_after_days": 1}})
    assert lh.lane_health("image", "ciyuan")["stale"] is True


def test_unparseable_date_is_unknown_not_fresh(monkeypatch):
    """判不出不许当绿：日期坏了要如实报 None。"""
    _with_ledger(monkeypatch, {"image.ciyuan": {"health": "down", "last_verified": "not-a-date"}})
    view = lh.lane_health("image", "ciyuan")
    assert view["stale"] is None and view["age_days"] is None


def test_missing_date_is_unknown_not_fresh(monkeypatch):
    _with_ledger(monkeypatch, {"image.ciyuan": {"health": "active"}})
    assert lh.lane_health("image", "ciyuan")["stale"] is None


def test_unknown_lane_stays_degraded_and_unknown(monkeypatch):
    _with_ledger(monkeypatch, {})
    view = lh.lane_health("image", "ghost")
    assert view["health"] == "degraded"
    assert view["stale"] is None


def test_annotate_carries_staleness_to_discovery_surface(monkeypatch):
    """发现面（registry_list_generators）要能看见 stale，否则等于没有。"""
    _with_ledger(monkeypatch, {"image.ciyuan": {"health": "down", "last_verified": _iso(30)}})
    entries = [{"name": "seedream-ciyuan", "provider": "ciyuan"}]
    lh.annotate(entries, "image")
    assert entries[0]["lane"]["health"] == "down"
    assert entries[0]["lane"]["stale"] is True
    assert entries[0]["lane"]["age_days"] == 30


def test_lane_down_predicate_unaffected(monkeypatch):
    _with_ledger(monkeypatch, {"image.ciyuan": {"health": "down", "last_verified": _iso(99)}})
    assert lh.lane_down("image", "ciyuan") is True
    assert lh.lane_down("image", "volcengine") is False  # unknown ≠ down
