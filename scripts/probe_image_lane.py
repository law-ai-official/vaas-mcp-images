#!/usr/bin/env python3
"""图像车道复验探针 —— 把 DEBT-LANE-9-01-REVERIFY 从手工仪式变成一条命令。

背景（2026-11-14）：图像车道 `down` 之后会**自我维持**——车道闸先拒，请求打不到上游，
`last_verified` 永远不会前进。所以「还封着吗」这个问题没有任何自动机制会回答，
只能由人定期真打一发。此前这个动作写在债务台账里当文字配方，靠人按记忆执行。

判据只认三种结论（与台账口径一致）：
  restored      —— 200 + 合规 JSON + `data[]` 里拿到**真图像字节**（魔数校验过）
  still-blocked —— 上游没恢复，细分两种形态：
                     html_or_nonjson  软封锁：200 + 网页 HTML（历史上还伴随空计费）
                     http_5xx         router 侧 fail-closed（比软封锁好，但仍是坏）
  cannot-judge  —— 缺凭据 / 网络不通（**不**等于「仍然封着」）

安全：key 只从环境变量读，绝不落盘、绝不打印；报错只带主机名，不带查询串
（签名下载 URL 的凭据常在 query 里）。探针复用的是**真实** `_ciyuan_image.generate`，
不是一份平行实现——否则探针绿了而产线仍坏。

用法：
  # 凭据放环境变量，别放命令行（命令行会进 ps/shell history）
  CIYUAN_API_KEY=... python3 scripts/probe_image_lane.py \\
      --base https://router.finddatatech.cloud --model grok-imagine-image

  # 机器可读
  ... --json

退出码：0 restored / 1 still-blocked / 2 cannot-judge
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _ciyuan_image import _looks_like_image, _host_only, generate  # noqa: E402

RESTORED = 0
STILL_BLOCKED = 1
CANNOT_JUDGE = 2

DEFAULT_PROMPT = "a plain grey square, minimal, flat"


def _classify_error(msg: str) -> tuple[str, str, str]:
    """Map a driver failure to (verdict, verdict_class, short_reason).

    判据方向（2026-11-14 实测后订正）：**4xx 一律不是「车道被封」**。
    4xx 说的是「我们这次请求不对」——key 无效、模型不受支持、参数不合规，
    处置方向是改我们的配置；把它报成 still-blocked 会让人去催上游解封，
    方向完全反了。真正指向「上游没恢复」的只有三种：
      200+HTML（软封锁）、5xx（router fail-closed）、data[] 空或形态不合规。
    """
    if "non-JSON response" in msg or "text/html" in msg:
        return "still-blocked", "html_or_nonjson", "upstream answered with HTML / non-JSON (soft block)"
    if "failed (HTTP 5" in msg:
        return "still-blocked", "http_5xx", "router/upstream returned 5xx (fail-closed, still broken)"
    if "INVALID_API_KEY" in msg or "Invalid API key" in msg or "failed (HTTP 401" in msg:
        return "cannot-judge", "credential_rejected", "lane key rejected — fix the credential, not the upstream account"
    if "failed (HTTP 4" in msg:
        return "cannot-judge", "bad_request", "upstream rejected the request shape/model — our config, not an upstream ban"
    if "neither b64_json nor url" in msg:
        return "still-blocked", "shape_violation", "response carried neither b64_json nor url"
    if "no data items" in msg:
        return "still-blocked", "empty_data", "response had an empty data[] (no image produced)"
    return "cannot-judge", "error", "unexpected failure — inspect the detail before drawing a conclusion"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Probe the image generation lane end-to-end")
    ap.add_argument("--base", required=True, help="lane base url (e.g. https://router.finddatatech.cloud)")
    ap.add_argument("--model", required=True, help="model to probe (e.g. grok-imagine-image)")
    ap.add_argument(
        "--api-key-env",
        default="CIYUAN_API_KEY",
        help="env var holding the lane key (default CIYUAN_API_KEY); never pass a key on argv",
    )
    ap.add_argument("--prompt", default=DEFAULT_PROMPT)
    ap.add_argument("--size", default=None, help="optional size (e.g. 2K)")
    ap.add_argument("--out", default=None, help="where to write the probe image (temp file if omitted)")
    ap.add_argument("--json", action="store_true", help="emit a machine-readable summary")
    args = ap.parse_args(argv)

    api_key = os.environ.get(args.api_key_env, "").strip()
    if not api_key:
        summary = {
            "verdict": "cannot-judge",
            "reason": f"env var {args.api_key_env} is empty — probe needs a lane key",
            "model": args.model,
            "host": _host_only(args.base),
        }
        print(json.dumps(summary, ensure_ascii=False) if args.json else
              f"cannot-judge: {summary['reason']}")
        return CANNOT_JUDGE

    out = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="fd-lane-probe-")) / "probe.png"
    out.parent.mkdir(parents=True, exist_ok=True)

    try:
        result = generate(
            prompt=args.prompt,
            model=args.model,
            size=args.size,
            n=1,
            output_paths=[out],
            api_key=api_key,
            base_url=args.base,
        )
    except Exception as exc:  # noqa: BLE001 — every failure is a verdict, not a crash
        msg = str(exc)
        verdict, verdict_class, reason = _classify_error(msg)
        network = any(t in msg for t in ("urlopen error", "Name or service", "timed out", "Temporary failure"))
        if network:
            verdict, verdict_class = "cannot-judge", "network"
            reason = "network unreachable — this does not mean the lane recovered"
        summary = {
            "verdict": verdict,
            "verdict_class": verdict_class,
            "reason": reason,
            "model": args.model,
            "host": _host_only(args.base),
            "detail": msg[:400],
        }
        print(json.dumps(summary, ensure_ascii=False, indent=1) if args.json else
              f"{verdict} [{verdict_class}]: {reason} — {msg[:200]}")
        return STILL_BLOCKED if verdict == "still-blocked" else CANNOT_JUDGE

    # 成功路径也要校验：产物必须真的在盘上、非空、且是图像字节。
    images = result.get("images") or []
    if not images:
        summary = {"verdict": "still-blocked", "verdict_class": "no_images",
                   "reason": "driver reported success but produced no images",
                   "model": args.model, "host": _host_only(args.base)}
        print(json.dumps(summary, ensure_ascii=False, indent=1) if args.json else
              f"still-blocked: {summary['reason']}")
        return STILL_BLOCKED

    local = Path(images[0]["local_path"])
    head = local.read_bytes()[:32] if local.exists() else b""  # read_bytes closes the handle
    if not head or not _looks_like_image(head):
        summary = {"verdict": "still-blocked", "verdict_class": "not_an_image",
                   "reason": "produced file is empty or not a known image format",
                   "model": args.model, "host": _host_only(args.base)}
        print(json.dumps(summary, ensure_ascii=False, indent=1) if args.json else
              f"still-blocked: {summary['reason']}")
        return STILL_BLOCKED

    summary = {
        "verdict": "restored",
        "verdict_class": "ok",
        "reason": "real image bytes produced and persisted",
        "model": result.get("model"),
        "host": _host_only(args.base),
        "file": str(local),
        "file_size_bytes": images[0].get("file_size_bytes"),
        "latency_ms": result.get("api_latency_ms"),
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=1))
    else:
        print(
            f"restored: {summary['model']} -> {summary['file']} "
            f"({summary['file_size_bytes']} bytes, {summary['latency_ms']} ms)"
        )
    return RESTORED


if __name__ == "__main__":
    raise SystemExit(main())
