import shutil

from .config import Profile
from .errors import DbkeeperError
from .logging_utils import SafeLog
from .postgres import Runner, preflight
from .storage import MANIFEST, allocate_run, finish_run, record_failure, utc_now, write_json


def doctor(profile: Profile) -> dict:
    directory = allocate_run(profile.backup_dir / "diagnostics")
    report = {"operation": "doctor", "profile": profile.name, "started_at_utc": utc_now(),
              "status": "running", "checks": {"local_storage": "ok"}}
    log = None
    try:
        write_json(directory / MANIFEST, report)
        log = SafeLog(directory / "doctor.log")
        paths, versions, _, warnings = preflight(profile, Runner(log))
        report.update(versions=versions, tools=paths, warnings=warnings,
                      free_bytes=shutil.disk_usage(profile.backup_dir).free)
        report["checks"].update(dependencies="ok", connectivity="ok", authentication="ok", versions="ok")
        log.close()
        report.update(status="complete", ended_at_utc=utc_now())
        write_json(directory / MANIFEST, report)
        finish_run(directory)
        return {**report, "report_directory": str(directory)}
    except BaseException as exc:
        record_failure(directory, report, "doctor")
        if log:
            try:
                log.close()
            except OSError:
                pass
        detail = str(exc) if isinstance(exc, DbkeeperError) else "Error local, timeout o interrupción."
        raise DbkeeperError(f"doctor falló. {detail} Reporte: {directory}") from None

