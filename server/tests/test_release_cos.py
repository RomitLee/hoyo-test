"""Existing signed update URLs can redirect directly to private COS objects."""

import hashlib
import json

import pytest
from app.config import Settings
from app.main import create_app
from app.releases import ReleaseStore
from fastapi.testclient import TestClient


def test_release_key_is_content_addressed_and_rejects_traversal():
    package = {"filename": "从机.zip", "sha256": "a" * 64}
    assert ReleaseStore.object_key("slave", "V2.14.0", package) == f"releases/slave/V2.14.0/{'a' * 64}/从机.zip"
    with pytest.raises(ValueError):
        ReleaseStore.object_key("slave", "V2.14.0", {**package, "filename": "../evil.zip"})
    with pytest.raises(ValueError):
        ReleaseStore.object_key("slave", "V2.14.0", {**package, "sha256": "bad"})


def test_download_redirects_to_cos_and_local_legacy_still_works(tmp_path, monkeypatch):
    releases = tmp_path / "releases"
    releases.mkdir()
    digest = hashlib.sha256(b"cos-package").hexdigest()
    manifest = {"schema_version": 3, "release_role": "slave", "client_version": "V2.14.0",
                "packages": {"slave": {"filename": "new.zip", "sha256": digest},
                             "master": {"filename": "old-master.zip"}}}
    (releases / "release-manifest-slave.json").write_text(json.dumps(manifest), encoding="utf-8")
    (releases / "legacy-release-manifest.json").write_text(json.dumps({"schema_version": 2,
        "client_version": "V1.2.0", "packages": {"slave": {"filename": "old.zip"}}}), encoding="utf-8")
    (releases / "old.zip").write_bytes(b"old package")
    monkeypatch.setattr(ReleaseStore, "configured", property(lambda self: True))
    monkeypatch.setattr(ReleaseStore, "exists", lambda self, key: key.endswith("/new.zip"))
    monkeypatch.setattr(ReleaseStore, "download_url", lambda self, key: "https://mh.example.com/" + key + "?signature=redacted")
    app = create_app(Settings(tmp_path / "relay.sqlite3", "m" * 40, {}, update_directory=releases))
    with TestClient(app) as client:
        result = client.get("/api/v1/updates/download/slave/V2.14.0", follow_redirects=False)
        assert result.status_code == 302
        assert result.headers["location"].startswith("https://mh.example.com/releases/slave/V2.14.0/")
        assert result.headers["cache-control"] == "no-store"
        assert client.get("/api/v1/updates/download/slave/V1.2.0").content == b"old package"


def test_upload_checks_signed_manifest_size_and_hash_before_cos_call(tmp_path, monkeypatch):
    archive = tmp_path / "release.zip"
    archive.write_bytes(b"valid")
    digest = hashlib.sha256(b"valid").hexdigest()
    store = ReleaseStore(Settings(tmp_path / "db", "m" * 40, {}, cos_release_secret_id="id",
                                  cos_release_secret_key="key", cos_release_bucket="bucket", cos_release_region="ap-hongkong"))
    calls = []
    class Client:
        def put_object(self, **kwargs):
            calls.append(kwargs["Key"])
    monkeypatch.setattr(store, "_client", lambda: Client())
    monkeypatch.setattr(store, "exists", lambda key: True)
    with pytest.raises(ValueError):
        store.upload_verified("releases/a", archive, "0" * 64, 5)
    assert calls == []
    store.upload_verified("releases/a", archive, digest, 5)
    assert calls == ["releases/a"]
