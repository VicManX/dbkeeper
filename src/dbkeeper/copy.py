from pathlib import Path
import os
import shutil

from .errors import DbkeeperError
from .manifest import backup_directory
from .storage import (DUMP, MANIFEST, allocate_run, exclusive_file, finish_run,
                      record_failure, rename_dump, sha256, utc_now, write_json)
from .verify import integrity


def copy_backup(path: Path, destination: Path) -> Path:
    directory = backup_directory(path)
    metadata = integrity(directory)
    # No cambiar permisos de una carpeta Windows preexistente del usuario.
    destination = destination.expanduser().absolute()
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    copied = allocate_run(destination, private_root=False)
    stage = "copy"
    copy_metadata = dict(metadata)
    copy_metadata.update(status="copying", copy={"started_at_utc": utc_now(), "status": "running"})
    try:
        write_json(copied / MANIFEST, copy_metadata)
        partial = copied / (DUMP + ".partial")
        with (directory / DUMP).open("rb") as source, exclusive_file(partial) as output:
            shutil.copyfileobj(source, output, length=1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        stage = "copy_hash"
        if sha256(partial) != metadata["sha256"] or sha256(directory / DUMP) != metadata["sha256"]:
            raise DbkeeperError("Los hashes de origen, copia y manifiesto no coinciden.")
        stage = "copy_rename"
        rename_dump(partial, copied / DUMP)
        stage = "copy_metadata"
        copy_metadata.update(status="complete", copy={"started_at_utc": copy_metadata["copy"]["started_at_utc"],
                                                     "ended_at_utc": utc_now(), "status": "complete",
                                                     "sha256_comparison": "ok"})
        write_json(copied / MANIFEST, copy_metadata)
        finish_run(copied)
        return copied
    except BaseException as exc:
        record_failure(copied, copy_metadata, stage)
        detail = str(exc) if isinstance(exc, DbkeeperError) else "Error local o interrupción."
        raise DbkeeperError(f"Copia fallida en {stage}. {detail} Copia incompleta: {copied}") from None

