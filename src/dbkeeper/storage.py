"""Archivos locales con permisos privados y publicación transaccional."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import uuid

from .errors import DbkeeperError

MARKER = "INCOMPLETE"
DUMP = "backup.dump"
MANIFEST = "manifest.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def new_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "_" + uuid.uuid4().hex


def sync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def private_dir(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise DbkeeperError("Se requiere un directorio real de almacenamiento.")
    path.chmod(0o700)
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise DbkeeperError("El almacenamiento no permite permisos 0700; use el filesystem Linux de WSL.")


def exclusive_file(path: Path):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    return os.fdopen(fd, "wb")


def write_marker(directory: Path) -> None:
    path = directory / MARKER
    if not path.exists():
        with exclusive_file(path) as stream:
            stream.write(b"Ejecucion incompleta. No utilizar como respaldo finalizado.\n")
            stream.flush()
            os.fsync(stream.fileno())
    sync_dir(directory)


def allocate_run(root: Path, prefix: str = "", *, private_root: bool = True) -> Path:
    if private_root:
        private_dir(root)
    for _ in range(10):
        directory = root / (prefix + new_id())
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            continue
        if private_root:
            private_dir(directory)
        sync_dir(root)
        write_marker(directory)
        return directory
    raise DbkeeperError("Colisiones repetidas al crear un directorio; no se sobrescribió nada.")


def write_json(path: Path, value: dict) -> None:
    # Si falla, el temporal queda explícitamente identificado como .partial.
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".partial")
    payload = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
    with exclusive_file(temporary) as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    sync_dir(path.parent)


def rename_dump(source: Path, target: Path) -> None:
    # Reserva exclusiva: replace solo sustituye nuestro archivo vacío, nunca
    # un backup previo. Ambos nombres están en un directorio privado único.
    with exclusive_file(target):
        pass
    os.replace(source, target)
    sync_dir(target.parent)


def finish_run(directory: Path) -> None:
    (directory / MARKER).unlink()
    sync_dir(directory)


def nonempty_file(path: Path) -> int:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size <= 0:
        raise DbkeeperError("El respaldo no es un archivo regular no vacío.")
    return info.st_size


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def record_failure(directory: Path, metadata: dict, stage: str) -> bool:
    metadata.update(status="failed", ended_at_utc=utc_now(), failed_stage=stage)
    saved = True
    try:
        write_marker(directory)
    except OSError:
        saved = False
    try:
        write_json(directory / MANIFEST, metadata)
    except OSError:
        saved = False
    return saved
