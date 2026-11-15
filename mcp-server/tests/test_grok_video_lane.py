"""grok 车道（/v1/videos 协议族）的判据回归：驱动四函数 + 桥接分派。

为什么要这组用例：这条车道与 ciyuan 档是**不同协议族**，最容易错的三处都是「假设
和 ciyuan 档一样」——终态词表（上游回 completed 不是 succeeded）、取件 URL
（相对路径、要带鉴权，不是签名 URL 免鉴权）、时长下限（3 秒不是 1 秒）。
每一个假设错都静默：状态判不出=轮询到超时、不带鉴权=401、发 1 秒=400。

全部离线：把 `urllib.request.urlopen` 换掉，跑真实 `_grok_video.py` 与真实桥接
分派，不碰网络、不需要 key。
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"

MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Resp:
    """最小 urlopen 替身：dict 形态的 headers 就够驱动读 Content-Type。"""

    def __init__(self, body: bytes, content_type: str = "application/json"):
        self._body = body
        self.headers = {"Content-Type": content_type}

    def read(self, n: int | None = -1) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeHTTP:
    """按路由返回：POST /v1/videos → 提交应答；GET /v1/videos/<id> → 状态；
    GET /v1/videos/<id>/content → MP4 字节。记录每个请求供断言。"""

    def __init__(self, *, submit_doc: dict, status_doc: dict, content: bytes = MP4):
        self.submit_doc = submit_doc
        self.status_doc = status_doc
        self.content = content
        self.requests: list = []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        url = req.full_url
        method = req.get_method()
        if method == "POST" and url.endswith("/v1/videos"):
            return _Resp(json.dumps(self.submit_doc).encode())
        if url.endswith("/content"):
            return _Resp(self.content, "video/mp4")
        if method == "GET" and "/v1/videos/" in url:
            return _Resp(json.dumps(self.status_doc).encode())
        if method == "GET":
            # 兜底：绝对签名 URL（COS 那种外链）不在 /v1/videos 路由内
            return _Resp(self.content, "video/mp4")
        raise AssertionError(f"unexpected request: {method} {url}")

    def raise_http(self, code: int):
        def fn(req, timeout=None):
            self.requests.append(req)
            raise urllib.error.HTTPError(req.full_url, code, "boom", {}, None)
        return fn


# ---------------------------------------------------------------------------
# 驱动：_grok_video.py
# ---------------------------------------------------------------------------


def _driver():
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    return _load("grok_video_under_test", SCRIPTS / "_grok_video.py")


def test_status_completed_maps_to_succeeded(monkeypatch):
    """上游真实词表是 completed——按 succeeded/done 判会轮询到超时。"""
    drv = _driver()
    fake = _FakeHTTP(
        submit_doc={}, status_doc={"id": "v1", "status": "completed",
                                   "url": "/v1/videos/v1/content"},
    )
    monkeypatch.setattr("urllib.request.urlopen", fake)
    doc = drv.status_once(task_id="v1", api_key="k", base_url="https://gw.example")
    assert doc["status"] == "succeeded"
    assert doc["upstream_status"] == "completed"
    assert doc["video_url"] == "/v1/videos/v1/content"


def test_status_done_and_failed_map_too(monkeypatch):
    """官方文档形状 done 一起认（上游改回旧词表时不静默判不出）；failed 照映。"""
    drv = _driver()
    fake = _FakeHTTP(submit_doc={}, status_doc={"id": "v", "status": "done"})
    monkeypatch.setattr("urllib.request.urlopen", fake)
    assert drv.status_once(task_id="v", api_key="k", base_url="https://g")["status"] == "succeeded"

    fake2 = _FakeHTTP(submit_doc={}, status_doc={"id": "v", "status": "failed",
                                                 "error": "x"})
    monkeypatch.setattr("urllib.request.urlopen", fake2)
    assert drv.status_once(task_id="v", api_key="k", base_url="https://g")["status"] == "failed"


def test_url_extraction_prefers_flat_falls_back_to_nested():
    drv = _driver()
    assert drv.video_url_from({"url": "/a"}) == "/a"
    assert drv.video_url_from({"video": {"url": "/b"}}) == "/b"
    assert drv.video_url_from({}) is None
    # 扁平优先：两处都有时以扁平为准（实测形状）
    assert drv.video_url_from({"url": "/a", "video": {"url": "/b"}}) == "/a"


def test_download_relative_url_joins_base_and_carries_auth(monkeypatch, tmp_path):
    """相对取件地址必须拼 base_url 且带 Authorization——不带就是 401。"""
    drv = _driver()
    fake = _FakeHTTP(submit_doc={}, status_doc={})
    monkeypatch.setattr("urllib.request.urlopen", fake)
    out = tmp_path / "v.mp4"
    result = drv.download(video_url="/v1/videos/v1/content", output_path=out,
                          api_key="sk-test", base_url="https://gw.example")
    req = fake.requests[0]
    assert req.full_url == "https://gw.example/v1/videos/v1/content"
    assert req.headers.get("Authorization") == "Bearer sk-test"
    assert out.read_bytes() == MP4
    assert result["file_size_bytes"] == len(MP4)


def test_download_absolute_signed_url_skips_auth(monkeypatch, tmp_path):
    """绝对签名 URL（COS 那种）免鉴权直取——加头无害但按真实形状不加。"""
    drv = _driver()
    fake = _FakeHTTP(submit_doc={}, status_doc={})
    monkeypatch.setattr("urllib.request.urlopen", fake)
    out = tmp_path / "v.mp4"
    drv.download(video_url="https://cos.example/a.mp4?sig=x", output_path=out,
                 api_key="sk-test", base_url="https://gw.example")
    req = fake.requests[0]
    assert req.full_url == "https://cos.example/a.mp4?sig=x"
    assert req.headers.get("Authorization") is None


def test_download_non_mp4_fails_without_writing(monkeypatch, tmp_path):
    """非 MP4 容器（HTML 错误体/网关兜底页）不写盘——与图像车道同一纪律。"""
    drv = _driver()
    fake = _FakeHTTP(submit_doc={}, status_doc={}, content=b"<html>spa fallback</html>")
    monkeypatch.setattr("urllib.request.urlopen", fake)
    out = tmp_path / "v.mp4"
    try:
        drv.download(video_url="/v1/videos/v1/content", output_path=out,
                     api_key="k", base_url="https://gw.example")
        raise AssertionError("expected RuntimeError")
    except RuntimeError as e:
        assert "not an MP4" in str(e)
    assert not out.exists()


def test_clamp_duration_three_way():
    """时长夹紧三向：1→3、20→15、界内不动。None（未指定）原样透传。"""
    drv = _driver()
    assert drv.clamp_duration(1) == (3, drv.clamp_duration(1)[1]) and "1s" in drv.clamp_duration(1)[1]
    assert drv.clamp_duration(20)[0] == 15 and "20s" in drv.clamp_duration(20)[1]
    assert drv.clamp_duration(5) == (5, None)
    assert drv.clamp_duration(None) == (None, None)


def test_submit_requires_key_and_base_url():
    drv = _driver()
    kw = dict(prompt="p", model="grok-imagine-video", duration=3, ratio=None,
              resolution=None, first_frame=None, last_frame=None,
              api_key="", base_url="https://g")
    try:
        drv.submit(**kw)
        raise AssertionError("expected ValueError for missing key")
    except ValueError as e:
        assert "key" in str(e).lower()
    kw2 = {**kw, "api_key": "k", "base_url": ""}
    try:
        drv.submit(**kw2)
        raise AssertionError("expected ValueError for missing base_url")
    except ValueError as e:
        assert "base_url" in str(e).lower()


def test_generate_and_wait_end_to_end_with_warnings(monkeypatch, tmp_path):
    """全链：submit(queued) → poll(completed) → 相对 URL 取件；
    分辨率不符（要 480p 实得 720p）进 warnings，不静默。"""
    drv = _driver()
    fake = _FakeHTTP(
        submit_doc={"id": "v1", "status": "queued", "model": "grok-imagine-video"},
        status_doc={"id": "v1", "status": "completed", "model": "kling-video-v3",
                    "url": "/v1/videos/v1/content", "resolution": "720p", "seconds": 3},
    )
    monkeypatch.setattr("urllib.request.urlopen", fake)
    result = drv.generate_and_wait(
        prompt="一只猫", model="grok-imagine-video", duration=3, ratio=None,
        resolution="480p", first_frame=None, last_frame=None,
        api_key="k", base_url="https://gw.example", output_path=tmp_path / "v.mp4",
    )
    assert result["ok"] is True and result["provider"] == "grok"
    assert result["status"] == "succeeded" and result["model"] == "kling-video-v3"
    assert (tmp_path / "v.mp4").read_bytes() == MP4
    assert any("720p" in w and "480p" in w for w in result["warnings"])
    # 提交体带了请求参数（duration/resolution/aspect_ratio）
    post = fake.requests[0]
    payload = json.loads(post.data.decode())
    assert payload["duration"] == 3 and payload["resolution"] == "480p"


# ---------------------------------------------------------------------------
# 桥接分派：litellm-bridge.py
# ---------------------------------------------------------------------------


def _bridge(monkeypatch, env: dict):
    bridge = _load("bridge_under_test", SCRIPTS / "litellm-bridge.py")
    monkeypatch.setattr(bridge, "ENV", dict(env))
    return bridge


def test_video_grok_status_two_phase(monkeypatch, tmp_path):
    """--status 只查态不提交：ok=True + video_url + 相对地址取件落盘。"""
    fake = _FakeHTTP(
        submit_doc={},
        status_doc={"id": "v1", "status": "completed", "model": "kling-video-v3",
                    "url": "/v1/videos/v1/content", "resolution": "720p", "seconds": 3},
    )
    monkeypatch.setattr("urllib.request.urlopen", fake)
    bridge = _bridge(monkeypatch, {
        "VIDEO_PROVIDER": "grok", "GROK_API_KEY": "sk-t", "GROK_BASE_URL": "https://gw.example",
    })
    args = bridge.build_parser().parse_args(
        ["video", "--status", "v1", "--output", str(tmp_path / "o.mp4")])
    code = bridge.cmd_video(args)
    assert code == 0
    # 只有状态读与取件两个 GET，没有 POST（防重复计费的语义底线）
    assert [r.get_method() for r in fake.requests] == ["GET", "GET"]
    assert (tmp_path / "o.mp4").read_bytes() == MP4


def test_video_unknown_provider_message_lists_grok(monkeypatch, capsys):
    """未受支持 provider 的报错文案必须列出 grok——别让人以为只有两条路。"""
    bridge = _bridge(monkeypatch, {"VIDEO_PROVIDER": "bogus"})
    args = bridge.build_parser().parse_args(
        ["video", "--prompt", "x", "--output", "/tmp/x.mp4"])
    code = bridge.cmd_video(args)
    assert code == 3
    err = capsys.readouterr().err
    assert "bogus" in err and "volcengine, ciyuan, grok" in err


def test_image_grok_delegates_and_falls_back_to_ciyuan_creds(monkeypatch, tmp_path, capsys):
    """provider=grok 图像委托同一驱动（端点形状相同），凭据回落 CIYUAN_*。"""
    captured = {}

    def fake_generate(**kw):
        captured.update(kw)
        return {"ok": True, "images": [{"local_path": str(tmp_path / "i.png"),
                                        "file_size_bytes": 1}]}

    stub = types.ModuleType("_ciyuan_image")
    stub.generate = fake_generate
    monkeypatch.setitem(sys.modules, "_ciyuan_image", stub)

    bridge = _bridge(monkeypatch, {
        "IMAGE_PROVIDER": "grok", "CIYUAN_API_KEY": "sk-ciyuan", "CIYUAN_BASE_URL": "https://gw.example",
    })
    args = bridge.build_parser().parse_args(
        ["image", "--prompt", "一只猫", "--output", str(tmp_path / "i.png")])
    code = bridge.cmd_image(args)
    assert code == 0
    assert captured["model"] == "grok-imagine-image"  # 无 GROK_IMAGE_MODEL 时的缺省
    assert captured["api_key"] == "sk-ciyuan"         # GROK_API_KEY 缺省回落 CIYUAN_API_KEY
    assert captured["base_url"] == "https://gw.example"
    out = json.loads(capsys.readouterr().out)
    assert out["provider"] == "grok" and out["model"] == "grok-imagine-image"


def test_image_unknown_provider_message_lists_grok(monkeypatch, capsys):
    bridge = _bridge(monkeypatch, {"IMAGE_PROVIDER": "bogus"})
    args = bridge.build_parser().parse_args(
        ["image", "--prompt", "x", "--output", "/tmp/x.png"])
    try:
        bridge.cmd_image(args)
        raise AssertionError("expected SystemExit")
    except SystemExit as e:
        assert "volcengine, ciyuan, grok" in str(e)
