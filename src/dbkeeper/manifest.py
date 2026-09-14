import json
import re
import stat
from datetime import datetime
from pathlib import Path

from .config import host_name, identifier
from .errors import DbkeeperError
from .storage import DUMP, MANIFEST, MARKER, nonempty_file


def backup_directory(path: Path) -> Path:
    path = path.expanduser().absolute()
    if path.is_dir():
        return path
    if path.name not in (DUMP, MANIFEST):
        raise DbkeeperError("Indique el directorio del backup, backup.dump o manifest.json; no un parcial.")
    if not path.parent.is_dir():
        raise DbkeeperError("El directorio del backup no existe.")
    return path.parent


def read_manifest(directory: Path) -> dict:
    try:
        path = directory / MANIFEST
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > 1024 * 1024:
            raise ValueError
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError
        return data
    except (OSError, ValueError):
        raise DbkeeperError("Manifiesto ausente, ilegible o inválido.") from None


def completed_manifest(directory: Path) -> dict:
    data = read_manifest(directory)
    try:
        if ((directory / MARKER).exists() or any(directory.glob("*.partial"))
                or data["status"] != "complete" or data["schema_version"] != 1
                or data["format"] != "custom" or data["file"] != DUMP):
            raise ValueError
        if not re.fullmatch(r"[A-Za-z0-9_-]+", data["backup_id"]):
            raise ValueError
        if not re.fullmatch(r"[0-9a-f]{64}", data["sha256"]):
            raise ValueError
        if type(data["size_bytes"]) is not int or data["size_bytes"] != nonempty_file(directory / DUMP):
            raise ValueError
        source = data["source"]
        host_name(source["host"])
        identifier(source["database"], "database")
        if not isinstance(source["profile"], str) or not re.fullmatch(r"[A-Za-z0-9_-]+", source["profile"]):
            raise ValueError
        if type(source["port"]) is not int or not 1 <= source["port"] <= 65535:
            raise ValueError
        for key in ("server", "pg_dump", "pg_restore", "psql"):
            if not re.fullmatch(r"\d+(?:\.\d+){0,2}", data["versions"][key]):
                raise ValueError
        dates = []
        for key in ("started_at_utc", "ended_at_utc"):
            if not data[key].endswith("Z"):
                raise ValueError
            dates.append(datetime.fromisoformat(data[key].replace("Z", "+00:00")))
        if dates[1] < dates[0]:
            raise ValueError
        for key in ("pg_dump", "nonempty_file", "catalog", "sha256", "rename"):
            if data["checks"][key] != "ok":
                raise ValueError
    except (KeyError, TypeError, ValueError, AttributeError, OSError):
        raise DbkeeperError("Backup incompleto o manifiesto inconsistente; no es utilizable como finalizado.") from None
    return data
