"""图像车道复验探针的判据回归：三种结论必须各自成立，且不能互相冒充。

为什么要这组用例：这个探针是「车道还封着吗」的唯一人类入口。它判错的两个方向代价不同——
把 `still-blocked` 判成 `restored` 会让人去翻一个没恢复的车道（白忙）；
把网络故障判成 `still-blocked` 会让人误以为上游还封着（方向错，且会拖住账号处置）。
所以 `cannot-judge` 单独成态，且必须能被证出来。

全部离线：把 `urllib.request.urlopen` 换掉，跑真实 `_ciyuan_image.generate` 与真实
探针 `main()`，不碰网络、不需要 key。
"""
from __future__ import annotations

import importlib.util
import json
import sys
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Resp:
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


def _load_probe():
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    return _load("probe_image_lane_under_test", SCRIPTS / "probe_image_lane.py")


def _patch_post(monkeypatch, *, payload: dict | None = None, body: bytes = b"",
                content_type: str = "application/json", http_error: int | None = None):
    def fake_urlopen(req, timeout=None):
        url = getattr(req, "full_url", str(req))
        if http_error is not None:
            raise urllib.error.HTTPError(url, http_error, "boom", {}, None)
        if url.endswith("/v1/images/generations"):
            data = body if body else json.dumps(payload or {}).encode()
            return _Resp(data, content_type)
        return _Resp(PNG, "image/png")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)


def _run(probe, monkeypatch, tmp_path, capsys, model="grok-imagine-image"):
    monkeypatch.setenv("PROBE_KEY", "test-key")
    code = probe.main([
        "--base", "https://router.example",
        "--model", model,
        "--api-key-env", "PROBE_KEY",
        "--out", str(tmp_path / "p.png"),
        "--json",
    ])
    return code, json.loads(capsys.readouterr().out)


def test_restored_b64(monkeypatch, tmp_path, capsys):
    import base64
    probe = _load_probe()
    _patch_post(monkeypatch, payload={"data": [{"b64_json": base64.b64encode(PNG).decode()}]})
    code, out = _run(probe, monkeypatch, tmp_path, capsys)
    assert code == 0 and out["verdict"] == "restored"
    assert out["file_size_bytes"] == len(PNG)


def test_restored_url_form(monkeypatch, tmp_path, capsys):
    """url 形态也要能判 restored——否则换了上游仍是假红。"""
    probe = _load_probe()
    _patch_post(monkeypatch, payload={"data": [{"url": "http://cdn.example/a.png?sig=x"}]})
    code, out = _run(probe, monkeypatch, tmp_path, capsys)
    assert code == 0 and out["verdict"] == "restored"


def test_html_is_still_blocked(monkeypatch, tmp_path, capsys):
    probe = _load_probe()
    _patch_post(monkeypatch, body=b"<html>spa</html>", content_type="text/html")
    code, out = _run(probe, monkeypatch, tmp_path, capsys)
    assert code == 1 and out["verdict"] == "still-blocked"
    assert out["verdict_class"] == "html_or_nonjson"


def test_5xx_is_still_blocked(monkeypatch, tmp_path, capsys):
    probe = _load_probe()
    _patch_post(monkeypatch, http_error=502)
    code, out = _run(probe, monkeypatch, tmp_path, capsys)
    assert code == 1 and out["verdict_class"] == "http_5xx"


def test_invalid_key_is_cannot_judge_not_still_blocked(monkeypatch, tmp_path, capsys):
    """方向判据（2026-11-14 真打 router 后订正）：key 不对 ≠ 上游还封着。

    旧判据把 401 INVALID_API_KEY 报成 still-blocked，会让人去催上游解封，
    而真正要改的是我们自己的凭证。
    """
    probe = _load_probe()
    body = b'{"code":"INVALID_API_KEY","message":"Invalid API key"}'
    _patch_post(monkeypatch, body=body, content_type="application/json", http_error=401)
    code, out = _run(probe, monkeypatch, tmp_path, capsys)
    assert code == 2 and out["verdict"] == "cannot-judge"
    assert out["verdict_class"] == "credential_rejected"


def test_4xx_bad_request_is_cannot_judge(monkeypatch, tmp_path, capsys):
    """`400 requires an image model` 这类是配置/模型问题，不是封禁。"""
    probe = _load_probe()
    _patch_post(monkeypatch, http_error=400)
    code, out = _run(probe, monkeypatch, tmp_path, capsys)
    assert code == 2 and out["verdict_class"] == "bad_request"


def test_missing_key_is_cannot_judge(monkeypatch, tmp_path, capsys):
    probe = _load_probe()
    monkeypatch.delenv("PROBE_KEY", raising=False)
    code = probe.main([
        "--base", "https://router.example", "--model", "m",
        "--api-key-env", "PROBE_KEY", "--out", str(tmp_path / "p.png"), "--json",
    ])
    out = json.loads(capsys.readouterr().out)
    assert code == 2 and out["verdict"] == "cannot-judge"


def test_network_failure_is_cannot_judge_not_blocked(monkeypatch, tmp_path, capsys):
    """方向判据：网络不通不等于上游仍封着。"""
    probe = _load_probe()

    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError("Name or service not known")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    code, out = _run(probe, monkeypatch, tmp_path, capsys)
    assert code == 2 and out["verdict"] == "cannot-judge"
    assert out["verdict_class"] == "network"


def test_key_never_appears_in_output(monkeypatch, tmp_path, capsys):
    probe = _load_probe()
    _patch_post(monkeypatch, http_error=502)
    code, out = _run(probe, monkeypatch, tmp_path, capsys)
    assert "test-key" not in json.dumps(out, ensure_ascii=False)
