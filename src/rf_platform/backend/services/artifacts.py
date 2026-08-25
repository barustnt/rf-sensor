from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from fastapi import UploadFile
from PIL import Image, UnidentifiedImageError

from rf_platform.common.config import Settings
from rf_platform.contracts.capture import ArtifactDescriptor, CaptureEnvelope

_SAFE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


class ArtifactError(ValueError):
    pass


@dataclass(frozen=True)
class StoredArtifact:
    descriptor: ArtifactDescriptor
    object_key: str


def sanitize_component(value: str) -> str:
    if not value or value in {".", ".."} or not _SAFE_COMPONENT_RE.match(value):
        raise ArtifactError(f"unsafe path component: {value!r}")
    return value


def artifact_key(envelope: CaptureEnvelope, filename: str) -> str:
    sensor = sanitize_component(envelope.sensor_id)
    capture = sanitize_component(envelope.capture_id)
    basename = sanitize_component(filename)
    day = envelope.started_at_utc
    return f"{sensor}/{day:%Y}/{day:%m}/{day:%d}/{capture}/{basename}"


class FilesystemArtifactStore:
    def __init__(self, settings: Settings) -> None:
        self.root = settings.artifact_root
        self.max_upload_bytes = settings.max_upload_bytes
        self.root.mkdir(parents=True, exist_ok=True)

    def _path_for_key(self, object_key: str) -> Path:
        parts = object_key.split("/")
        safe_parts = [sanitize_component(part) for part in parts]
        path = self.root.joinpath(*safe_parts)
        resolved_root = self.root.resolve()
        resolved_path = path.resolve(strict=False)
        if resolved_root not in [resolved_path, *resolved_path.parents]:
            raise ArtifactError("artifact path escapes configured root")
        return path

    async def store_upload(
        self,
        envelope: CaptureEnvelope,
        upload: UploadFile,
        declared: ArtifactDescriptor,
    ) -> StoredArtifact:
        if declared.mime_type != "image/png" or declared.kind != "spectrogram":
            raise ArtifactError("Milestone 1 accepts spectrogram PNG artifacts only")
        if not upload.filename:
            raise ArtifactError("uploaded artifact must have a filename")
        filename = sanitize_component(upload.filename)
        if filename != declared.filename:
            raise ArtifactError("uploaded filename does not match metadata descriptor")
        object_key = artifact_key(envelope, filename)
        final_path = self._path_for_key(object_key)
        temp_path = final_path.with_suffix(final_path.suffix + ".tmp")
        final_path.parent.mkdir(parents=True, exist_ok=True)

        sha = hashlib.sha256()
        size = 0
        with temp_path.open("wb") as handle:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > self.max_upload_bytes:
                    temp_path.unlink(missing_ok=True)
                    raise ArtifactError("uploaded artifact exceeds configured maximum size")
                sha.update(chunk)
                handle.write(chunk)
        digest = sha.hexdigest()
        if declared.size_bytes and declared.size_bytes != size:
            temp_path.unlink(missing_ok=True)
            raise ArtifactError("uploaded artifact size does not match metadata descriptor")
        if declared.sha256 != digest:
            temp_path.unlink(missing_ok=True)
            raise ArtifactError("uploaded artifact SHA-256 does not match metadata descriptor")
        try:
            with Image.open(temp_path) as image:
                image.verify()
        except (UnidentifiedImageError, OSError) as exc:
            temp_path.unlink(missing_ok=True)
            raise ArtifactError("uploaded artifact is not a valid PNG image") from exc
        temp_path.replace(final_path)
        return StoredArtifact(
            descriptor=ArtifactDescriptor(
                kind=declared.kind,
                filename=filename,
                mime_type=declared.mime_type,
                size_bytes=size,
                sha256=digest,
            ),
            object_key=object_key,
        )

    def store_metadata(self, envelope: CaptureEnvelope) -> str:
        object_key = artifact_key(envelope, "metadata.json")
        final_path = self._path_for_key(object_key)
        temp_path = final_path.with_suffix(".json.tmp")
        final_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path.write_text(envelope.model_dump_json(indent=2), encoding="utf-8")
        temp_path.replace(final_path)
        return object_key

    def open(self, object_key: str) -> Path:
        path = self._path_for_key(object_key)
        if not path.exists():
            raise FileNotFoundError(object_key)
        return path

    def verify_existing(self, object_key: str, sha256: str, size_bytes: int) -> bool:
        path = self.open(object_key)
        actual_size = path.stat().st_size
        if actual_size != size_bytes:
            return False
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return digest == sha256

    def storage_summary(self) -> dict[str, object]:
        self.root.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(self.root)
        total_bytes = 0
        file_count = 0
        for path in self.root.rglob("*"):
            if path.is_file():
                file_count += 1
                total_bytes += path.stat().st_size
        return {
            "backend": "filesystem",
            "label": "Laptop (all-in-one)",
            "artifact_root": str(self.root),
            "file_count": file_count,
            "artifact_bytes": total_bytes,
            "disk_total_bytes": usage.total,
            "disk_free_bytes": usage.free,
            "disk_used_percent": round((usage.used / usage.total) * 100, 2) if usage.total else 0,
        }


def fingerprint_metadata(envelope: CaptureEnvelope, descriptors: list[ArtifactDescriptor]) -> str:
    payload = envelope.model_dump(mode="json")
    payload["artifacts"] = [descriptor.model_dump(mode="json") for descriptor in descriptors]
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def file_sha256(handle: BinaryIO) -> str:
    position = handle.tell()
    handle.seek(0)
    digest = hashlib.sha256(handle.read()).hexdigest()
    handle.seek(position)
    return digest
