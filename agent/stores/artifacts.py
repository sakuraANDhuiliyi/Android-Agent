from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse


class ArtifactStore(Protocol):
    def put(self, key: str, data: bytes, *, content_type: str = "") -> str: ...

    def get(self, key: str) -> bytes | None: ...

    def exists(self, key: str) -> bool: ...

    def digest(self, key: str) -> str | None: ...


def _safe_key(key: str) -> str:
    cleaned = (key or "").replace("\\", "/").lstrip("/")
    parts = [part for part in cleaned.split("/") if part not in {"", ".", ".."}]
    if not parts:
        raise ValueError("artifact key is empty")
    return "/".join(parts)


class LocalArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        rel = _safe_key(key)
        path = (self.root / rel).resolve()
        if self.root.resolve() not in path.parents and path != self.root.resolve():
            raise ValueError("artifact key escapes store root")
        return path

    def put(self, key: str, data: bytes, *, content_type: str = "") -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".part")
        tmp.write_bytes(data)
        tmp.replace(path)
        meta = path.with_suffix(path.suffix + ".meta")
        meta.write_text(content_type or "application/octet-stream", encoding="utf-8")
        return key

    def get(self, key: str) -> bytes | None:
        path = self._path(key)
        if not path.is_file():
            return None
        return path.read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def digest(self, key: str) -> str | None:
        data = self.get(key)
        if data is None:
            return None
        return hashlib.sha256(data).hexdigest()


class ObjectArtifactStore:
    """S3-compatible or in-memory object adapter.

    `memory://` is for tests. `file://` uses a directory (local stand-in for a
    bucket). `s3://` requires boto3 at runtime and is validated at startup.
    """

    def __init__(self, blobs: dict[str, bytes] | None = None, *, prefix: str = "") -> None:
        self._blobs = blobs if blobs is not None else {}
        self._prefix = prefix.strip("/")

    @classmethod
    def from_url(cls, url: str, *, prefix: str = "") -> "ObjectArtifactStore | LocalArtifactStore":
        parsed = urlparse(url or "")
        if parsed.scheme in {"memory", "mem"}:
            return cls(prefix=prefix)
        if parsed.scheme == "file":
            root = Path(parsed.path or parsed.netloc)
            return LocalArtifactStore(root)
        if parsed.scheme in {"s3", "https"}:
            try:
                import boto3  # type: ignore
            except ImportError as exc:
                raise RuntimeError(
                    "artifact_backend=object 且使用 s3:// 时需要 boto3"
                ) from exc
            return S3ArtifactStore(url, prefix=prefix, client=boto3.client("s3"))
        raise RuntimeError(f"不支持的 object_store_url: {url}")

    def _full(self, key: str) -> str:
        rel = _safe_key(key)
        return f"{self._prefix}/{rel}" if self._prefix else rel

    def put(self, key: str, data: bytes, *, content_type: str = "") -> str:
        self._blobs[self._full(key)] = data
        return key

    def get(self, key: str) -> bytes | None:
        return self._blobs.get(self._full(key))

    def exists(self, key: str) -> bool:
        return self._full(key) in self._blobs

    def digest(self, key: str) -> str | None:
        data = self.get(key)
        if data is None:
            return None
        return hashlib.sha256(data).hexdigest()


class S3ArtifactStore:
    def __init__(self, url: str, *, prefix: str, client: object) -> None:
        parsed = urlparse(url)
        self.bucket = parsed.netloc or parsed.path.lstrip("/").split("/")[0]
        self.prefix = prefix.strip("/")
        self._client = client

    def _name(self, key: str) -> str:
        rel = _safe_key(key)
        return f"{self.prefix}/{rel}" if self.prefix else rel

    def put(self, key: str, data: bytes, *, content_type: str = "") -> str:
        extra = {"ContentType": content_type} if content_type else {}
        self._client.put_object(
            Bucket=self.bucket,
            Key=self._name(key),
            Body=data,
            **extra,
        )
        return key

    def get(self, key: str) -> bytes | None:
        try:
            body = self._client.get_object(Bucket=self.bucket, Key=self._name(key))["Body"]
        except Exception:
            return None
        read = getattr(body, "read", None)
        return read() if callable(read) else None

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self.bucket, Key=self._name(key))
            return True
        except Exception:
            return False

    def digest(self, key: str) -> str | None:
        data = self.get(key)
        if data is None:
            return None
        return hashlib.sha256(data).hexdigest()
