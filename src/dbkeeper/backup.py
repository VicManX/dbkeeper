from pathlib import Path

from .config import Profile
from .errors import DbkeeperError
from .logging_utils import SafeLog
from .postgres import Runner, catalog_args, dump_args, preflight
from .storage import (DUMP, MANIFEST, allocate_run, exclusive_file, finish_run,
                      nonempty_file, record_failure, rename_dump, sha256, utc_now, write_json)


def backup(profile: Profile) -> Path:
    if profile.purpose != "backup":
        raise DbkeeperError("backup exige un perfil con purpose = backup.")
    directory = allocate_run(profile.backup_dir)
    metadata = {"schema_version": 1, "backup_id": directory.name, "source": profile.source(),
                "started_at_utc": utc_now(), "ended_at_utc": None, "versions": {},
                "format": "custom", "file": DUMP, "size_bytes": None, "sha256": None,
                "status": "running", "checks": {}, "full_read": "not_run",
                "test_restore": "not_run"}
    stage, log = "initial_metadata", None
    try:
        write_json(directory / MANIFEST, metadata)
        log = SafeLog(directory / "backup.log")
        runner = Runner(log)
        stage = "preflight"
        log.write("Comprobando dependencias, conexión y versiones.")
        paths, versions, env, warnings = preflight(profile, runner)
        metadata.update(versions=versions, warnings=warnings)
        metadata["checks"][stage] = "ok"
        for warning in warnings:
            log.write(warning)
        write_json(directory / MANIFEST, metadata)
        partial = directory / (DUMP + ".partial")
        with exclusive_file(partial):
            pass
        stage = "pg_dump"
        log.write("Iniciando pg_dump hacia el archivo local parcial.")
        runner.run(dump_args(paths["pg_dump"], profile, partial), env=env)
        metadata["checks"][stage] = "ok"
        stage = "nonempty_file"
        metadata["size_bytes"] = nonempty_file(partial)
        metadata["checks"][stage] = "ok"
        stage = "catalog"
        log.write("Comprobando legibilidad del catálogo.")
        runner.run(catalog_args(paths["pg_restore"], partial))
        metadata["checks"][stage] = "ok"
        stage = "sha256"
        log.write("Calculando SHA-256 mediante lectura por bloques.")
        metadata["sha256"] = sha256(partial)
        metadata["checks"][stage] = "ok"
        metadata["status"] = "finalizing"
        write_json(directory / MANIFEST, metadata)
        stage = "rename"
        rename_dump(partial, directory / DUMP)
        metadata["checks"][stage] = "ok"
        stage = "final_metadata"
        log.write("pg_dump finalizado; catálogo legible; SHA-256 calculado. Lectura completa pendiente.")
        log.close()
        metadata.update(status="complete", ended_at_utc=utc_now())
        write_json(directory / MANIFEST, metadata)
        finish_run(directory)
        return directory
    except BaseException as exc:
        metadata["checks"][stage] = "failed"
        saved = record_failure(directory, metadata, stage)
        if log:
            try:
                log.close()
            except OSError:
                pass
        detail = str(exc) if isinstance(exc, DbkeeperError) else "Error local o interrupción."
        suffix = " No se pudieron guardar todos los metadatos de fallo." if not saved else ""
        raise DbkeeperError(f"Backup fallido en {stage}. {detail} Directorio: {directory}.{suffix}") from None
