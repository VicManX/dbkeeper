"""Diagnósticos redactados antes de tocar disco; nunca se registran argv/env."""

import os
from pathlib import Path
import re

from .errors import DbkeeperError
from .storage import exclusive_file, utc_now


def password_file() -> Path:
    return Path(os.environ.get("PGPASSFILE", "~/.pgpass")).expanduser().absolute()


def pgpass_secrets(path: Path) -> list[str]:
    secrets = []
    if not path.exists():
        return secrets
    if not path.is_file():
        raise OSError("El archivo de autenticación no es regular.")
    # libpq permite escapar ':' y '\\'. No se guardan los valores en metadatos.
    with path.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            if line.startswith("#"):
                continue
            fields, field, escaped = [], "", False
            for char in line.rstrip("\r\n"):
                if escaped:
                    field += char
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == ":":
                    fields.append(field)
                    field = ""
                else:
                    field += char
            fields.append(field)
            if len(fields) == 5 and fields[4]:
                secrets.append(fields[4])
    return secrets


class SafeLog:
    def __init__(self, path: Path):
        try:
            self.secrets = sorted(pgpass_secrets(password_file()), key=len, reverse=True)
        except OSError:
            raise DbkeeperError("No se pudo leer el archivo de autenticación para proteger los logs.") from None
        if os.environ.get("PGPASSWORD"):
            self.secrets.append(os.environ["PGPASSWORD"])
        self.path = path
        self.stream = exclusive_file(path)

    def redact(self, message: str) -> str:
        for secret in self.secrets:
            message = message.replace(secret, "[SECRETO OMITIDO]")
        message = re.sub(r"(?i)postgres(?:ql)?://[^\s]+", "[CONEXION OMITIDA]", message)
        # Omite el resto de la línea para cubrir espacios/escapes en conninfo.
        message = re.sub(r"(?i)\b(?:password|passwd|sslpassword)\b[\"']?\s*(?:=|:)\s*.*",
                         "password=[SECRETO OMITIDO]", message)
        message = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "?", message)
        return message

    def write(self, message: str) -> None:
        self.stream.write((utc_now() + " " + self.redact(message).rstrip() + "\n").encode())
        self.stream.flush()

    def close(self) -> None:
        if not self.stream.closed:
            try:
                self.stream.flush()
                os.fsync(self.stream.fileno())
            finally:
                self.stream.close()
