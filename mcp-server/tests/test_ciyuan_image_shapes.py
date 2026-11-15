"""图像车道产物形态回归：`_ciyuan_image` 必须认两种形态，且 url 形态自带安全包封。

背景（2026-11-14 实况）：组 9 `image-video` 只有一个上游账号且被 openai-next 按
browser signature 封禁 ⇒ 图像车道整条 down。同刻词元里另一条上游
（组 19 `fed-workbuddy-media` / `grok-imagine-image`）是活的，但它只回
`data[].url`，而旧代码只认 `b64_json`，于是**能用的车道也用不上**。

判据不手抄：直接 import 真实脚本、把 `urllib.request.urlopen` 换成假响应，
跑真实分支。`_ciyuan_image.py` 顶层只 import stdlib，可安全加载。

红证要求：其中 `test_url_form_is_persisted` 在旧代码上必然失败
（旧代码会报「仅支持 b64_json 形态」）——这就是本 change 的存在理由。
"""
from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CIYUAN_IMAGE = ROOT / "scripts/_ciyuan_image.py"

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32


def _load():
    spec = importlib.util.spec_from_file_location("ciyuan_image_under_test", CIYUAN_IMAGE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Resp:
    """Minimal urlopen() response: headers.get + read(n) semantics."""

    def __init__(self, body: bytes, content_type: str):
        self._body = body
        self._pos = 0
        self.headers = {"Content-Type": content_type}

    def read(self, n: int | None = -1) -> bytes:
        if n is None or n < 0:
            out = self._body[self._pos:]
            self._pos = len(self._body)
            return out
        out = self._body[self._pos:self._pos + n]
        self._pos += len(out)
        return out

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _patch(monkeypatch, mod, api_payload: dict, image_body: bytes = PNG,
           image_type: str = "image/png"):
    """Route the POST to the images API payload, and the artifact GET to image_body."""

    def fake_urlopen(req, timeout=None):
        url = getattr(req, "full_url", str(req))
        if url.endswith("/v1/images/generations"):
            return _Resp(json.dumps(api_payload).encode(), "application/json")
        return _Resp(image_body, image_type)

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)


def _call(mod, tmp_path: Path) -> dict:
    return mod.generate(
        prompt="一只猫",
        model="grok-imagine-image",
        size=None,
        n=1,
        output_paths=[tmp_path / "out.png"],
        api_key="test-key",
        base_url="http://gw.example",
    )


def test_b64_form_still_works(monkeypatch, tmp_path):
    mod = _load()
    _patch(monkeypatch, mod, {"data": [{"b64_json": base64.b64encode(JPEG).decode()}]})
    res = _call(mod, tmp_path)
    assert (tmp_path / "out.png").read_bytes() == JPEG
    assert res["images"][0]["file_size_bytes"] == len(JPEG)


def test_url_form_is_persisted(monkeypatch, tmp_path):
    """红证：旧代码在此必败（只认 b64_json）。"""
    mod = _load()
    _patch(monkeypatch, mod, {"data": [{"url": "http://cdn.example/a.png?sig=secret"}]})
    res = _call(mod, tmp_path)
    assert (tmp_path / "out.png").read_bytes() == PNG
    assert res["images"][0]["local_path"].endswith("out.png")
    # 产物路径必须是本地文件，不能把上游 URL 回给调用者
    assert "http" not in res["images"][0]["local_path"]


def test_b64_wins_when_both_present(monkeypatch, tmp_path):
    mod = _load()
    payload = {"data": [{"b64_json": base64.b64encode(JPEG).decode(),
                         "url": "http://cdn.example/a.png"}]}
    _patch(monkeypatch, mod, payload, image_body=PNG)
    _call(mod, tmp_path)
    # b64 优先（design D1）：落盘的是 JPEG，不是 url 指向的 PNG
    assert (tmp_path / "out.png").read_bytes() == JPEG


def test_url_form_rejects_html(monkeypatch, tmp_path):
    """上游把 HTML 标成 image/* 也要拒——魔数与 Content-Type 双校验。"""
    mod = _load()
    _patch(monkeypatch, mod, {"data": [{"url": "http://cdn.example/a.png"}]},
           image_body=b"<html>not an image</html>", image_type="image/png")
    with pytest.raises(RuntimeError, match="magic number"):
        _call(mod, tmp_path)


def test_url_form_rejects_non_image_content_type(monkeypatch, tmp_path):
    mod = _load()
    _patch(monkeypatch, mod, {"data": [{"url": "http://cdn.example/a.png"}]},
           image_body=PNG, image_type="text/html")
    with pytest.raises(RuntimeError, match="not an image"):
        _call(mod, tmp_path)


def test_url_form_enforces_size_cap(monkeypatch, tmp_path):
    mod = _load()
    monkeypatch.setattr(mod, "MAX_URL_BYTES", 16)
    _patch(monkeypatch, mod, {"data": [{"url": "http://cdn.example/a.png"}]})
    with pytest.raises(RuntimeError, match="exceeded"):
        _call(mod, tmp_path)


def test_url_form_rejects_non_http_scheme(monkeypatch, tmp_path):
    mod = _load()
    _patch(monkeypatch, mod, {"data": [{"url": "file:///etc/passwd"}]})
    with pytest.raises(RuntimeError, match="unsupported scheme"):
        _call(mod, tmp_path)


def test_item_without_either_shape_fails(monkeypatch, tmp_path):
    mod = _load()
    _patch(monkeypatch, mod, {"data": [{"revised_prompt": "x"}]})
    with pytest.raises(RuntimeError, match="neither b64_json nor url"):
        _call(mod, tmp_path)


def test_error_message_hides_query_string(monkeypatch, tmp_path):
    """签名下载 URL 的凭据在 query 里，报错不许带出去。"""
    mod = _load()
    _patch(monkeypatch, mod, {"data": [{"url": "http://cdn.example/a.png?sig=topsecret"}]},
           image_body=PNG, image_type="text/html")
    with pytest.raises(RuntimeError) as exc:
        _call(mod, tmp_path)
    assert "topsecret" not in str(exc.value)
    assert "cdn.example" in str(exc.value)


# ── 两种形态的校验必须同等级别 ────────────────────────────────────────────────

def test_b64_payload_must_be_a_real_image(monkeypatch, tmp_path):
    """b64 里塞 HTML 的 base64：旧代码会原样写盘，产出一个打不开的「图片」。"""
    mod = _load()
    fake = base64.b64encode(b"<html>nope</html>").decode()
    _patch(monkeypatch, mod, {"data": [{"b64_json": fake}]})
    with pytest.raises(RuntimeError, match="not a known image format"):
        _call(mod, tmp_path)


def test_b64_reports_detected_format(monkeypatch, tmp_path):
    mod = _load()
    _patch(monkeypatch, mod, {"data": [{"b64_json": base64.b64encode(PNG).decode()}]})
    res = _call(mod, tmp_path)
    assert res["images"][0]["format"] == "png"


def test_url_reports_detected_format(monkeypatch, tmp_path):
    mod = _load()
    _patch(monkeypatch, mod, {"data": [{"url": "http://cdn.example/a.png"}]},
           image_body=JPEG, image_type="image/jpeg")
    res = _call(mod, tmp_path)
    assert res["images"][0]["format"] == "jpeg"


def test_detect_format_covers_webp_and_avif():
    mod = _load()
    webp = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 8
    avif = b"\x00\x00\x00\x20ftypavif" + b"\x00" * 8
    assert mod._detect_format(webp) == "webp"
    assert mod._detect_format(avif) == "avif"
    assert mod._detect_format(b"plain text") is None
