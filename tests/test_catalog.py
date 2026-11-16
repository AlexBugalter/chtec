from chtec.backends.profiles import default_profiles


def test_unsloth_catalog_discovers_gguf_and_excludes_projector(tmp_path):
    from chtec.backends.catalog import local_catalog

    model = tmp_path / "vision.gguf"
    model.write_bytes(b"GGUF")
    (tmp_path / "mmproj-vision.gguf").write_bytes(b"GGUF")
    profile = default_profiles().profiles["unsloth"]
    profile.model_path = str(model)
    entries = local_catalog(profile, roots=[tmp_path])
    assert [e.path for e in entries] == [str(model)]
    assert entries[0].vision is True


def test_lm_catalog_uses_installed_models_without_inference_api(monkeypatch):
    import json
    from types import SimpleNamespace

    from chtec.backends.catalog import local_catalog

    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                [
                    {"modelKey": "vision", "vision": True, "displayName": "Vision"},
                    {"modelKey": "text", "vision": False},
                ]
            ),
        )

    monkeypatch.setattr("chtec.backends.catalog.subprocess.run", run)
    entries = local_catalog(default_profiles().active)
    assert [e.id for e in entries] == ["text", "vision"]
    assert calls[0][-3:] == ["ls", "--llm", "--json"]


def test_installed_catalog_excludes_audio_and_image_generation(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    from chtec.backends.catalog import local_catalog

    for name in ("qwen3-asr.gguf", "mmproj.gguf", "qwen-image.gguf", "vision-chat.gguf"):
        (tmp_path / name).write_bytes(b"GGUF")
    assert [
        e.title for e in local_catalog(default_profiles().profiles["unsloth"], roots=[tmp_path])
    ] == ["vision-chat.gguf"]
    rows = [
        {"modelKey": "qwen3-asr", "vision": True},
        {"modelKey": "wan", "architecture": "wan"},
        {"modelKey": "vision-chat", "vision": True},
    ]
    monkeypatch.setattr(
        "chtec.backends.catalog.subprocess.run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=json.dumps(rows)),
    )
    assert [e.id for e in local_catalog(default_profiles().active)] == ["vision-chat"]
