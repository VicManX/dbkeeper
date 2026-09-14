from pathlib import Path

from .errors import DbkeeperError
from .manifest import completed_manifest, read_manifest
from .storage import MARKER


def latest_report(directory: Path, kind: str) -> dict:
    root = directory / kind
    if not root.exists():
        return {"status": "not_run"}
    reports = sorted((p for p in root.iterdir() if p.is_dir()), reverse=True)
    if not reports:
        return {"status": "not_run"}
    try:
        data = read_manifest(reports[0])
        if (reports[0] / MARKER).exists() or any(reports[0].glob("*.partial")):
            return {"status": "incomplete", "recorded_status": data.get("status")}
        return {"status": data.get("status", "unknown"), "full_read": data.get("full_read", "not_run")}
    except DbkeeperError:
        return {"status": "invalid"}


def list_backups(root: Path) -> list[dict]:
    if not root.exists():
        return []
    results = []
    for directory in sorted(root.iterdir(), reverse=True):
        if not directory.is_dir() or directory.name == "diagnostics":
            continue
        row = {"directory": str(directory), "status": "incomplete"}
        try:
            data = completed_manifest(directory)
            row.update(status="complete", backup_id=data["backup_id"], source=data["source"],
                       size_bytes=data["size_bytes"], catalog="ok", hash="not_rechecked")
        except (DbkeeperError, OSError):
            try:
                row["recorded_status"] = read_manifest(directory).get("status", "unknown")
            except DbkeeperError:
                row["recorded_status"] = "missing_or_invalid_manifest"
        row["last_verification"] = latest_report(directory, "verifications")
        row["last_test_restore"] = latest_report(directory, "restores")
        results.append(row)
    return results

