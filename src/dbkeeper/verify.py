from pathlib import Path

from .errors import DbkeeperError
from .logging_utils import SafeLog
from .manifest import backup_directory, completed_manifest
from .postgres import Runner, catalog_args, full_read_args, major, tool_versions
from .storage import DUMP, MANIFEST, allocate_run, finish_run, record_failure, sha256, utc_now, write_json


def integrity(directory: Path) -> dict:
    metadata = completed_manifest(directory)
    if sha256(directory / DUMP) != metadata["sha256"]:
        raise DbkeeperError("SHA-256 distinto del manifiesto; archivo modificado o corrupto.")
    return metadata


def verify(path: Path, *, full: bool = False, tools: dict | None = None) -> dict:
    directory = backup_directory(path)
    # Reportes independientes: no convierten un backup incompleto en completo.
    report_dir = allocate_run(directory / "verifications")
    report = {"operation": "verify", "started_at_utc": utc_now(), "status": "running",
              "checks": {}, "full_read": "not_run"}
    stage, log = "manifest_and_hash", None
    try:
        write_json(report_dir / MANIFEST, report)
        log = SafeLog(report_dir / "verify.log")
        runner = Runner(log)
        metadata = integrity(directory)
        report.update(backup_id=metadata["backup_id"], sha256=metadata["sha256"])
        report["checks"].update(manifest="ok", sha256="ok")
        stage = "tool_version"
        paths, versions = tool_versions(runner, tools or {}, ("pg_restore",))
        if major(versions["pg_restore"]) < major(metadata["versions"]["pg_dump"]):
            raise DbkeeperError("pg_restore es anterior al pg_dump que generó el archivo.")
        report["versions"] = versions
        stage = "catalog"
        runner.run(catalog_args(paths["pg_restore"], directory / DUMP))
        report["checks"][stage] = "ok"
        if full:
            stage = "full_read"
            runner.run(full_read_args(paths["pg_restore"], directory / DUMP))
            report["checks"][stage] = "ok"
            report["full_read"] = "ok"
        log.close()
        stage = "final_metadata"
        report.update(status="complete", ended_at_utc=utc_now())
        write_json(report_dir / MANIFEST, report)
        finish_run(report_dir)
        return {**report, "report_directory": str(report_dir)}
    except BaseException as exc:
        report["checks"][stage] = "failed"
        record_failure(report_dir, report, stage)
        if log:
            try:
                log.close()
            except OSError:
                pass
        detail = str(exc) if isinstance(exc, DbkeeperError) else "Error local o interrupción."
        raise DbkeeperError(f"Verificación fallida en {stage}. {detail} Reporte: {report_dir}") from None

