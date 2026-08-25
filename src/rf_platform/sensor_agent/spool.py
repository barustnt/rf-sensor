from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from rf_platform.contracts.capture import CaptureEnvelope
from rf_platform.sensor_agent.adapters.base import CaptureBundle


@dataclass(frozen=True)
class SpoolItem:
    path: Path
    envelope: CaptureEnvelope
    artifact_path: Path


class DurableSpool:
    def __init__(self, root: Path, max_bytes: int) -> None:
        self.root = root
        self.max_bytes = max_bytes
        self.ready_root = self.root / "ready"
        self.quarantine_root = self.root / "quarantine"
        self.root.mkdir(parents=True, exist_ok=True)
        self.ready_root.mkdir(parents=True, exist_ok=True)
        self.quarantine_root.mkdir(parents=True, exist_ok=True)

    def usage_bytes(self) -> int:
        return sum(path.stat().st_size for path in self.root.rglob("*") if path.is_file())

    def pending_items(self) -> list[SpoolItem]:
        items: list[SpoolItem] = []
        for item_dir in sorted(self.ready_root.iterdir() if self.ready_root.exists() else []):
            if not item_dir.is_dir():
                continue
            try:
                metadata = item_dir / "metadata.json"
                artifact = item_dir / "spectrogram.png"
                envelope = CaptureEnvelope.model_validate_json(metadata.read_text(encoding="utf-8"))
                if not artifact.exists():
                    raise ValueError("missing spectrogram")
                items.append(SpoolItem(path=item_dir, envelope=envelope, artifact_path=artifact))
            except Exception:
                target = self.quarantine_root / item_dir.name
                if target.exists():
                    shutil.rmtree(target)
                item_dir.replace(target)
        return items

    def status(self) -> dict[str, object]:
        items = self.pending_items()
        pending_bytes = self.usage_bytes()
        oldest = min((item.envelope.created_at_utc for item in items), default=None)
        return {
            "pending_items": len(items),
            "pending_bytes": pending_bytes,
            "oldest_item_utc": oldest.isoformat() if oldest else None,
        }

    def put(self, bundle: CaptureBundle) -> SpoolItem:
        if self.usage_bytes() > self.max_bytes:
            raise RuntimeError("spool exceeds configured maximum bytes")
        writing = self.root / f"{bundle.envelope.capture_id}.writing"
        ready = self.ready_root / bundle.envelope.capture_id
        if writing.exists():
            shutil.rmtree(writing)
        writing.mkdir(parents=True)
        artifact_dest = writing / "spectrogram.png"
        shutil.copy2(bundle.artifact_path, artifact_dest)
        (writing / "metadata.json").write_text(
            bundle.envelope.model_dump_json(indent=2), encoding="utf-8"
        )
        if ready.exists():
            shutil.rmtree(ready)
        writing.replace(ready)
        return SpoolItem(
            path=ready, envelope=bundle.envelope, artifact_path=ready / "spectrogram.png"
        )

    def delete(self, item: SpoolItem) -> None:
        shutil.rmtree(item.path, ignore_errors=True)

    def export_item(self, item: SpoolItem) -> dict[str, object]:
        return {
            "path": str(item.path),
            "capture_id": item.envelope.capture_id,
            "metadata": json.loads(item.envelope.model_dump_json()),
            "artifact_path": str(item.artifact_path),
        }
