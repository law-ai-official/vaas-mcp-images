"""generator 层的产物命名回归：扩展名必须跟着**真实容器**走。

为什么这条要在 generator 层测而不是 driver 层：driver 只负责把字节写到临时路径，
`image.py` 随后把它**重命名**成 `{slug}_{i:02d}.{output_format}`。旧代码用请求的
`output_format` 拼扩展名，于是「要 jpeg、上游回 PNG」会产出一个叫 `.jpeg` 的 PNG。
下游（发布技能按扩展名选上传类型）会因此走错路，而文件本身打不开。

这里把 `run_bridge` / 目录 / 账本全部打桩，只跑 `image.py` 的真实分支：
不启子进程、不写库、不写仓内目录。
"""
from __future__ import annotations

import json

import pytest

from mcp_server.generators import image as gen_image

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """Stub the side-effecting edges of generate_image; keep the real logic."""
    out_dir = tmp_path / "images" / "slug"
    out_dir.mkdir(parents=True)
    produced = out_dir / "raw_output.jpeg"
    produced.write_bytes(PNG)

    monkeypatch.setattr(gen_image, "ensure_directory_rel", lambda *a, **k: out_dir)
    monkeypatch.setattr(
        gen_image, "get_relative_path", lambda *a, **k: "/".join(str(x) for x in a)
    )
    monkeypatch.setattr(gen_image, "insert_content", lambda **kw: kw.get("id"))
    monkeypatch.setattr(gen_image, "finish", lambda *a, **k: {"credential_source": "env"})
    monkeypatch.setenv("IMAGE_PROVIDER", "ciyuan")
    return produced


def _bridge_returning(produced, fmt):
    payload = {
        "model": "grok-imagine-image",
        "images": [
            {
                "local_path": str(produced),
                "file_size_bytes": produced.stat().st_size,
                "format": fmt,
            }
        ],
    }
    return lambda cmd, ctx: json.dumps(payload)


def test_extension_follows_actual_container(monkeypatch, isolated):
    """要 jpeg、上游给 PNG ⇒ 落成 .png 并如实告警，而不是叫 .jpeg 的 PNG。"""
    monkeypatch.setattr(gen_image, "run_bridge", _bridge_returning(isolated, "png"))
    res = gen_image.generate(prompt="一只猫", output_format="jpeg")
    assert res["file_paths"] and res["file_paths"][0].endswith(".png")
    assert any("different container" in w for w in res["warnings"])


def test_matching_container_has_no_warning(monkeypatch, isolated):
    monkeypatch.setattr(gen_image, "run_bridge", _bridge_returning(isolated, "jpeg"))
    res = gen_image.generate(prompt="一只猫", output_format="jpeg")
    assert res["file_paths"][0].endswith(".jpeg")
    assert not any("different container" in w for w in res["warnings"])


def test_missing_format_falls_back_to_requested(monkeypatch, isolated):
    """老驱动/其他 provider 不回 format 时，行为与之前完全一致。"""
    payload = {
        "model": "volcengine-seedream",
        "images": [{"local_path": str(isolated), "file_size_bytes": isolated.stat().st_size}],
    }
    monkeypatch.setattr(gen_image, "run_bridge", lambda cmd, ctx: json.dumps(payload))
    res = gen_image.generate(prompt="一只猫", output_format="jpeg")
    assert res["file_paths"][0].endswith(".jpeg")
    assert not any("different container" in w for w in res["warnings"])


def test_container_outside_contract_is_disclosed(monkeypatch, isolated):
    """契约外容器（tiff/heic/avif）不丢产物，但要点名，别让它在发布环节才炸。"""
    monkeypatch.setattr(gen_image, "run_bridge", _bridge_returning(isolated, "tiff"))
    res = gen_image.generate(prompt="一只猫", output_format="jpeg")
    assert res["file_paths"][0].endswith(".tiff")
    assert any("outside the supported output formats" in w for w in res["warnings"])
    assert any("different container" in w for w in res["warnings"])
