"""
萬星·词元 grok 媒体车道 (xAI/OpenAI video shape) helper for litellm-bridge.

与 `_ciyuan_video.py` 是**不同协议族**，别混：
  ciyuan 档  POST /v1/contents/generations/tasks  {model, content:[{type:text,...}]}
  grok 档    POST /v1/videos                      {model, prompt, duration, resolution}
             GET  /v1/videos/<id>                   → status
             GET  /v1/videos/<id>/content           → 视频字节（需鉴权）

实测（2026-11-14/15，组 19 `fed-workbuddy-media`，acct 57 联邦入口）：
  `grok-imagine-video` → 202 {status:"queued"} → in_progress → completed + `url`
  产物是真 MP4（3,693,439 B / ftyp isom / 720P / 3s）。

三个必须写死在代码里的上游事实（都与 ciyuan 档不同）：
  1. **时长下限是 3 秒**（给 1 报 `时长必须在 3 到 15 秒之间`）；本档夹紧到 [3,15] 并披露，
     不把 1 秒静默改成别的值。本地计费侧允许 1 秒，两处口径不同——这里是真调出来的。
  2. **终态叫 `completed`，不叫 `done`**（sub2api 自己的计费谓词等的是 `done`，所以视频
     完成也不落账；那是上游仓的缺陷，本驱动只按上游真实词表判终态）。
  3. **`url` 是相对路径**（`/v1/videos/<id>/content`），取件要拼 base_url **并带 Authorization**；
     ciyuan 档那套「绝对签名 URL、免鉴权」在这里不成立。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

# 上游实测边界（错误文案原文：时长必须在 3 到 15 秒之间）
MIN_DURATION_S = 3
MAX_DURATION_S = 15

# 上游终态词表 → 本仓统一词表（succeeded/failed/cancelled）。
# `completed` 是实测值；`done` 是 sub2api 文档里 xAI 官方形状，一起认，
# 免得上游改回官方词表时这条车道静默判不出。
_TERMINAL = {
    "completed": "succeeded",
    "succeeded": "succeeded",
    "done": "succeeded",
    "failed": "failed",
    "error": "failed",
    "cancelled": "cancelled",
    "canceled": "cancelled",
}

# 产物魔数：容器必须是 MP4/ISO-BMFF（`....ftyp`）。
_MP4_MAGIC_MIN = 12


def _headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def clamp_duration(duration: int | None) -> tuple[int | None, str | None]:
    """夹紧到上游可接受区间；返回 (生效值, 披露文本或 None)。"""
    if duration is None:
        return None, None
    try:
        value = int(duration)
    except (TypeError, ValueError):
        raise ValueError(f"duration must be an integer number of seconds, got {duration!r}")
    clamped = max(MIN_DURATION_S, min(MAX_DURATION_S, value))
    if clamped != value:
        return clamped, (
            f"duration {value}s is outside the upstream range "
            f"[{MIN_DURATION_S},{MAX_DURATION_S}]s; sent {clamped}s"
        )
    return clamped, None


def _absolute(base_url: str, path_or_url: str) -> str:
    """上游常回相对路径（/v1/videos/<id>/content），拼成绝对地址。"""
    if path_or_url.startswith(("http://", "https://")):
        return path_or_url
    return base_url.rstrip("/") + "/" + path_or_url.lstrip("/")


def _is_relative(path_or_url: str) -> bool:
    return not path_or_url.startswith(("http://", "https://"))


def video_url_from(doc: dict) -> str | None:
    """终态文档里的取件地址：扁平 `url` 优先，兼容嵌套 `video.url`。"""
    url = (doc.get("url") or "").strip()
    if url:
        return url
    video = doc.get("video")
    if isinstance(video, dict):
        url = (video.get("url") or "").strip()
        if url:
            return url
    return None


def submit(
    *,
    prompt: str,
    model: str,
    duration: int | None,
    ratio: str | None,
    resolution: str | None,
    first_frame: str | None,
    last_frame: str | None,
    api_key: str,
    base_url: str,
) -> dict:
    """创建任务，秒级返回 {task_id, model, status, warnings}。"""
    if not api_key:
        raise ValueError("GROK_API_KEY (or the lane credential) is required for grok video")
    if not base_url:
        raise ValueError("grok video requires base_url (the 词元 gateway base)")
    if not prompt:
        raise ValueError("prompt is required for grok video")

    effective_duration, warning = clamp_duration(duration)
    warnings: list[str] = [warning] if warning else []

    payload: dict = {"model": model, "prompt": prompt}
    if effective_duration is not None:
        payload["duration"] = effective_duration
    if ratio:
        payload["aspect_ratio"] = ratio
    if resolution:
        payload["resolution"] = resolution
    # 首尾帧：本协议族用 image 字段（ciyuan 档用的是 content[].role），形状不同。
    if first_frame:
        payload.setdefault("image", first_frame)
    if last_frame and not first_frame:
        warnings.append("last_frame is not supported by the /v1/videos shape; ignored")

    req = urllib.request.Request(
        base_url.rstrip("/") + "/v1/videos",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers=_headers(api_key),
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            doc = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"grok video submit failed (HTTP {e.code}): {body}") from e

    task_id = doc.get("id") or doc.get("request_id") or doc.get("task_id")
    if not task_id:
        raise RuntimeError(f"grok video submit returned no task id: {json.dumps(doc)[:300]}")
    status = _TERMINAL.get(str(doc.get("status") or "").lower(), str(doc.get("status") or "queued"))
    return {
        "task_id": task_id,
        "model": doc.get("model") or model,
        "status": status,
        "upstream_status": doc.get("status"),
        "warnings": warnings,
    }


def status_once(
    *,
    task_id: str,
    api_key: str,
    base_url: str,
) -> dict:
    """Fast single status read (hosted poll tool must return in seconds)."""
    if not api_key:
        raise ValueError("grok video status requires an api key")
    if not base_url:
        raise ValueError("grok video status requires base_url")
    req = urllib.request.Request(
        base_url.rstrip("/") + "/v1/videos/" + urllib.parse.quote(task_id),
        method="GET",
        headers=_headers(api_key),
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        doc = json.loads(resp.read().decode("utf-8"))
    raw_status = str(doc.get("status") or "").lower()
    doc["upstream_status"] = doc.get("status")
    doc.setdefault("id", task_id)
    doc["status"] = _TERMINAL.get(raw_status, raw_status or "queued")
    candidate = video_url_from(doc)
    if candidate:
        doc["video_url"] = candidate
    return doc


def poll(
    *,
    task_id: str,
    api_key: str,
    base_url: str,
    timeout_seconds: int = 600,
    interval_seconds: int = 10,
) -> dict:
    """Poll until a terminal state. Returns the normalized final document."""
    deadline = time.time() + timeout_seconds
    last: dict = {}
    while time.time() < deadline:
        last = status_once(task_id=task_id, api_key=api_key, base_url=base_url)
        if last.get("status") in ("succeeded", "failed", "cancelled"):
            return last
        time.sleep(interval_seconds)
    raise RuntimeError(
        f"grok video poll timed out after {timeout_seconds}s "
        f"(last status: {last.get('upstream_status') or last.get('status')})"
    )


def download(*, video_url: str, output_path, api_key: str = "", base_url: str = "",
             timeout_seconds: int = 300) -> dict:
    """取件。相对路径要拼 base_url 并带鉴权；绝对签名 URL 免鉴权（ciyuan 那种）。"""
    url = _absolute(base_url, video_url) if base_url else video_url
    headers = _headers(api_key) if (api_key and _is_relative(video_url)) else {}
    req = urllib.request.Request(url, method="GET", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            content_type = (resp.headers.get("Content-Type") or "").lower()
            raw = resp.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"grok video download failed (HTTP {e.code})") from e
    if not raw:
        raise RuntimeError("grok video download returned empty body")
    # 与图像车道同一纪律：字节必须真是视频容器，否则不写盘（避免产出打不开的假产物）。
    if len(raw) < _MP4_MAGIC_MIN or raw[4:8] != b"ftyp":
        raise RuntimeError(
            "grok video download is not an MP4 container "
            f"(Content-Type: {content_type or '<none>'}, first bytes: {raw[:12].hex()}) — 不写盘"
        )
    path = str(output_path)
    with open(path, "wb") as f:
        f.write(raw)
    return {
        "local_path": path,
        "file_size_bytes": len(raw),
        "content_type": content_type or "video/mp4",
    }


def generate_and_wait(
    *,
    prompt: str,
    model: str,
    duration: int | None,
    ratio: str | None,
    resolution: str | None,
    first_frame: str | None,
    last_frame: str | None,
    api_key: str,
    base_url: str,
    output_path,
    timeout_seconds: int = 600,
) -> dict:
    """同步便捷入口：submit + poll + download（结果形状对齐 volcengine/ciyuan 档）。"""
    start = time.time()
    sub = submit(
        prompt=prompt, model=model, duration=duration, ratio=ratio, resolution=resolution,
        first_frame=first_frame, last_frame=last_frame,
        api_key=api_key, base_url=base_url,
    )
    final = poll(task_id=sub["task_id"], api_key=api_key, base_url=base_url,
                 timeout_seconds=timeout_seconds)
    if final.get("status") != "succeeded":
        raise RuntimeError(
            f"grok video task {sub['task_id']} ended {final.get('upstream_status') or final.get('status')}: "
            f"{json.dumps(final)[:400]}"
        )
    url = final.get("video_url")
    if not url:
        raise RuntimeError(
            f"grok video task succeeded but has no video url: {json.dumps(final)[:400]}"
        )
    dl = download(video_url=url, output_path=output_path, api_key=api_key, base_url=base_url)
    warnings = list(sub.get("warnings") or [])
    # 上游可能无视请求的分辨率（实测要 480p 给 720P）：如实披露，不假装按请求交付。
    reported_resolution = final.get("resolution")
    if resolution and reported_resolution and str(reported_resolution).lower() != resolution.lower():
        warnings.append(
            f"upstream produced {reported_resolution} for a {resolution} request"
        )
    return {
        "ok": True,
        "provider": "grok",
        "model": final.get("model") or model,
        "task_id": sub["task_id"],
        "status": "succeeded",
        **dl,
        "seconds": final.get("seconds"),
        "resolution": reported_resolution,
        "warnings": warnings,
        "api_latency_ms": round((time.time() - start) * 1000),
    }
