import ipaddress
from pathlib import Path

from .config import Profile, identifier
from .errors import DbkeeperError
from .logging_utils import SafeLog
from .manifest import backup_directory, completed_manifest
from .postgres import Runner, connection_args, major, preflight, restore_args
from .storage import DUMP, MANIFEST, allocate_run, finish_run, record_failure, utc_now, write_json
from .verify import verify


def canonical_host(host: str) -> str:
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return host.lower().rstrip(".")


def protect_target(source: dict, target: Profile, database: str) -> None:
    identifier(database, "Base de pruebas")
    if target.purpose != "restore":
        raise DbkeeperError("Se exige un perfil independiente con purpose = restore.")
    if target.name == source["profile"]:
        raise DbkeeperError("El perfil destino debe ser independiente del perfil origen.")
    if (canonical_host(target.host), target.port, database) == (
            canonical_host(source["host"]), source["port"], source["database"]):
        raise DbkeeperError("Destino rechazado: coincide con host, puerto y base del origen.")


def create_database_sql(database: str) -> str:
    identifier(database, "Base de pruebas")
    return 'CREATE DATABASE "' + database.replace('"', '""') + '" TEMPLATE template0'


def restore(path: Path, target: Profile, database: str, *, keep_ownership: bool = False,
            keep_tablespaces: bool = False) -> dict:
    directory = backup_directory(path)
    metadata = completed_manifest(directory)
    protect_target(metadata["source"], target, database)
    report_dir = allocate_run(directory / "restores")
    report = {"operation": "restore", "backup_id": metadata["backup_id"],
              "started_at_utc": utc_now(), "status": "running", "checks": {},
              "target": {"profile": target.name, "host": target.host, "port": target.port,
                         "database": database}, "keep_ownership": keep_ownership,
              "keep_tablespaces": keep_tablespaces, "database_creation": "not_attempted"}
    stage, log = "preflight", None
    try:
        write_json(report_dir / MANIFEST, report)
        log = SafeLog(report_dir / "restore.log")
        runner = Runner(log)
        paths, versions, env, warnings = preflight(target, runner)
        if major(versions["pg_restore"]) < major(metadata["versions"]["pg_dump"]):
            raise DbkeeperError("pg_restore destino es anterior al pg_dump del respaldo.")
        if major(versions["server"]) < max(major(metadata["versions"]["server"]),
                                              major(metadata["versions"]["pg_dump"]),
                                              major(versions["pg_restore"])):
            raise DbkeeperError("La restauración hacia una versión mayor anterior no está admitida.")
        report.update(versions=versions, warnings=warnings)
        report["checks"][stage] = "ok"
        stage = "verify"
        verification = verify(directory, full=True, tools=target.tools)
        report["verification"] = verification["report_directory"]
        report["checks"][stage] = "ok"
        stage = "create_database"
        report["database_creation"] = "attempted"
        write_json(report_dir / MANIFEST, report)
        env["PGOPTIONS"] = f"-c lock_timeout={target.lock_wait_timeout_ms}ms"
        # CREATE falla si la base ya existe; no hay DROP ni comprobación sujeta a carrera.
        runner.run([paths["psql"], *connection_args(target), "--no-psqlrc", "--set=ON_ERROR_STOP=1",
                    "--command=" + create_database_sql(database)], env=env)
        report["database_creation"] = "created"
        report["checks"][stage] = "ok"
        write_json(report_dir / MANIFEST, report)
        stage = "restore"
        runner.run(restore_args(paths["pg_restore"], target, database, directory / DUMP,
                                keep_ownership, keep_tablespaces), env=env)
        report["checks"][stage] = "ok"
        stage = "final_metadata"
        log.close()
        report.update(status="complete", ended_at_utc=utc_now(), test_restore="ok")
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
        raise DbkeeperError(f"Restauración fallida en {stage}. {detail} No se borró ninguna base. "
                            f"Revise el destino: puede existir una base parcialmente restaurada. Reporte: {report_dir}") from None

