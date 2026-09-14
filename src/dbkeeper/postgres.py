"""Construcción de argumentos y ejecución de clientes PostgreSQL oficiales."""

import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import threading

from .config import Profile, host_name, identifier
from .errors import DbkeeperError
from .logging_utils import SafeLog, password_file


def clean_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
    env.update(LC_ALL="C", PGAPPNAME="dbkeeper")
    return env


def connection_env(profile: Profile) -> dict[str, str]:
    if os.environ.get("PGPASSWORD"):
        raise DbkeeperError("Quite PGPASSWORD y use .pgpass o PGPASSFILE.")
    passfile = password_file()
    if passfile.exists():
        if not passfile.is_file() or passfile.stat().st_mode & 0o077:
            raise DbkeeperError("El archivo de contraseñas requiere permisos 0600 en WSL.")
        with passfile.open("rb"):
            pass
    elif "PGPASSFILE" in os.environ:
        raise DbkeeperError("PGPASSFILE no existe.")
    for key in ("sslrootcert", "sslcert", "sslkey"):
        path = getattr(profile, key)
        if path:
            if not path.is_file():
                raise DbkeeperError("Un certificado SSL configurado no existe o no es un archivo.")
            with path.open("rb"):
                pass
            if key == "sslkey" and path.stat().st_mode & 0o077:
                raise DbkeeperError("La clave SSL privada requiere permisos 0600.")
    env = clean_env()
    env.update(PGCONNECT_TIMEOUT=str(profile.connect_timeout), PGSSLMODE=profile.sslmode,
               PGPASSFILE=str(passfile))
    for key in ("sslrootcert", "sslcert", "sslkey"):
        if getattr(profile, key):
            env["PG" + key.upper()] = str(getattr(profile, key))
    return env


def connection_args(profile: Profile, database: str | None = None) -> list[str]:
    host_name(profile.host)
    identifier(database if database is not None else profile.database, "database")
    identifier(profile.user, "user")
    return ["--no-password", f"--host={profile.host}", f"--port={profile.port}",
            f"--username={profile.user}", f"--dbname={database or profile.database}"]


def dump_args(executable: str, profile: Profile, path: Path) -> list[str]:
    return [executable, *connection_args(profile), "--format=custom",
            f"--file={path}", f"--lock-wait-timeout={profile.lock_wait_timeout_ms}",
            "--quote-all-identifiers"]


def catalog_args(executable: str, path: Path) -> list[str]:
    return [executable, "--no-password", "--list", str(path.absolute())]


def full_read_args(executable: str, path: Path) -> list[str]:
    return [executable, "--no-password", "--exit-on-error", "--file=/dev/null", str(path.absolute())]


def restore_args(executable: str, profile: Profile, database: str, path: Path,
                 keep_ownership: bool = False, keep_tablespaces: bool = False) -> list[str]:
    args = [executable, *connection_args(profile, database), "--exit-on-error"]
    if not keep_ownership:
        args += ["--no-owner", "--no-privileges"]
    if not keep_tablespaces:
        args += ["--no-tablespaces"]
    return [*args, str(path.absolute())]


class Runner:
    def __init__(self, log: SafeLog):
        self.log = log

    def run(self, args: list[str], *, env: dict | None = None,
            capture: bool = False, timeout: int | None = None) -> str:
        errors = []

        def drain(pipe):
            suppress_sql = False
            try:
                while chunk := pipe.readline(65537):
                    if len(chunk) > 65536:
                        while chunk and not chunk.endswith(b"\n"):
                            chunk = pipe.readline(65537)
                        message = "[Diagnóstico demasiado largo omitido]"
                    else:
                        message = chunk.decode("utf-8", errors="replace")
                    # Los errores de restore pueden incluir SQL o datos COPY.
                    if "Command was:" in message or "CONTEXT:" in message:
                        suppress_sql = True
                        message = "[SQL/datos del diagnóstico omitidos]"
                    elif suppress_sql:
                        if not re.match(r"^(pg_dump|pg_restore|psql):", message):
                            continue
                        suppress_sql = False
                    try:
                        self.log.write(message)
                    except OSError as exc:
                        if not errors:
                            errors.append(exc)
                        # Seguir drenando evita bloquear al proceso por un pipe lleno.
            except OSError as exc:
                if not errors:
                    errors.append(exc)
            finally:
                pipe.close()

        with tempfile.TemporaryFile() as output:
            try:
                process = subprocess.Popen(args, stdin=subprocess.DEVNULL,
                                           stdout=output if capture else subprocess.DEVNULL,
                                           stderr=subprocess.PIPE, env=env if env is not None else clean_env(),
                                           shell=False, start_new_session=True, umask=0o077)
            except OSError:
                raise DbkeeperError("No se pudo iniciar la herramienta PostgreSQL.") from None
            reader = threading.Thread(target=drain, args=(process.stderr,), daemon=True)
            reader.start()
            try:
                code = process.wait(timeout=timeout)
            except BaseException:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                raise
            finally:
                reader.join()
            if errors:
                raise DbkeeperError("Falló la escritura del log local de la herramienta.")
            if code:
                raise DbkeeperError(f"La herramienta PostgreSQL terminó con código {code}; revise el log local.")
            output.seek(0)
            result = output.read(16385) if capture else b""
            if len(result) > 16384:
                raise DbkeeperError("Respuesta inesperadamente extensa de la herramienta.")
            return result.decode("utf-8", errors="replace").strip()


def resolve_tool(name: str, configured: dict[str, str]) -> str:
    found = shutil.which(configured.get(name, name))
    if not found:
        raise DbkeeperError(f"Dependencia ausente o no ejecutable: {name}.")
    return found


def parse_version(output: str) -> str:
    match = re.search(r"\(PostgreSQL\)\s+(\d+(?:\.\d+){0,2})(?=\s|$)", output)
    if not match:
        raise DbkeeperError("No se pudo reconocer la versión estable del cliente PostgreSQL.")
    return match.group(1)


def major(version: str) -> tuple[int, int]:
    parts = [int(p) for p in version.split(".")]
    return (parts[0], parts[1] if parts[0] < 10 and len(parts) > 1 else 0)


def server_version(runner: Runner, executable: str, profile: Profile, env: dict) -> str:
    result = runner.run([executable, *connection_args(profile), "--no-psqlrc", "--tuples-only",
                         "--no-align", "--set=ON_ERROR_STOP=1", "--command=SHOW server_version_num"],
                        env=env, capture=True, timeout=profile.connect_timeout + 5)
    if not re.fullmatch(r"\d{5,6}", result):
        raise DbkeeperError("Respuesta inválida al consultar la versión del servidor.")
    number = int(result)
    return (f"{number // 10000}.{number % 10000}" if number >= 100000
            else f"{number // 10000}.{number // 100 % 100}.{number % 100}")


def tool_versions(runner: Runner, configured: dict[str, str], names: tuple) -> tuple[dict, dict]:
    paths = {name: resolve_tool(name, configured) for name in names}
    versions = {name: parse_version(runner.run([path, "--version"], capture=True, timeout=10))
                for name, path in paths.items()}
    return paths, versions


def check_backup_versions(versions: dict) -> list[str]:
    if major(versions["pg_dump"]) < major(versions["server"]):
        raise DbkeeperError("pg_dump es más antiguo que el servidor; instale clientes compatibles.")
    if major(versions["pg_restore"]) != major(versions["pg_dump"]):
        raise DbkeeperError("Use pg_dump y pg_restore de la misma versión mayor.")
    return (["Se prefieren clientes de la misma versión mayor que el origen."]
            if major(versions["pg_dump"]) != major(versions["server"]) else [])


def preflight(profile: Profile, runner: Runner) -> tuple[dict, dict, dict, list[str]]:
    env = connection_env(profile)
    names = ("psql", "pg_dump", "pg_restore") if profile.purpose == "backup" else ("psql", "pg_restore")
    paths, versions = tool_versions(runner, profile.tools, names)
    versions["server"] = server_version(runner, paths["psql"], profile, env)
    warnings = check_backup_versions(versions) if profile.purpose == "backup" else []
    if major(versions["psql"]) != major(versions["server"]):
        warnings.append("Se recomienda psql de la misma versión mayor que el servidor.")
    if profile.purpose == "restore" and major(versions["pg_restore"]) > major(versions["server"]):
        raise DbkeeperError("El servidor de pruebas es anterior al cliente pg_restore.")
    if not password_file().exists():
        warnings.append("No existe .pgpass; la conexión comprobada utilizó otro método de autenticación.")
    return paths, versions, env, warnings
