"""Key-value file storage: a local folder or an S3 prefix, behind one small interface.

Keys are POSIX-style relative paths such as "published/manifest.json".
"""

from pathlib import Path
from typing import Protocol


class Storage(Protocol):
    def get(self, key: str) -> bytes | None:
        """The object's bytes, or None when it does not exist."""
        ...

    def put(self, key: str, data: bytes) -> None: ...

    def list(self, prefix: str) -> list[str]:
        """Keys under prefix, sorted."""
        ...


class LocalStorage:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root.resolve() not in path.parents:
            raise ValueError(f"key escapes the storage root: {key!r}")
        return path

    def get(self, key: str) -> bytes | None:
        path = self._path(key)
        return path.read_bytes() if path.is_file() else None

    def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)

    def list(self, prefix: str) -> list[str]:
        base = self._path(prefix) if prefix else self.root
        if not base.exists():
            return []
        files = [base] if base.is_file() else [p for p in base.rglob("*") if p.is_file()]
        return sorted(p.relative_to(self.root).as_posix() for p in files)


class S3Storage:
    """S3 objects under an optional key prefix. boto3 is imported lazily."""

    def __init__(self, bucket: str, prefix: str = "", client=None):
        import boto3

        self.bucket = bucket
        self.prefix = prefix.strip("/") + "/" if prefix.strip("/") else ""
        self.client = client or boto3.client("s3")

    def get(self, key: str) -> bytes | None:
        try:
            return self.client.get_object(Bucket=self.bucket, Key=self.prefix + key)["Body"].read()
        except self.client.exceptions.NoSuchKey:
            return None

    def put(self, key: str, data: bytes) -> None:
        self.client.put_object(Bucket=self.bucket, Key=self.prefix + key, Body=data, ContentType=_content_type(key))

    def list(self, prefix: str) -> list[str]:
        keys = []
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=self.prefix + prefix):
            keys.extend(obj["Key"][len(self.prefix) :] for obj in page.get("Contents", []))
        return sorted(keys)


def _content_type(key: str) -> str:
    if key.endswith(".json"):
        return "application/json"
    if key.endswith(".parquet"):
        return "application/vnd.apache.parquet"
    return "application/octet-stream"
