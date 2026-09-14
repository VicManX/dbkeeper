"""Perfiles estrictos: no se aceptan DSN, contraseñas ni opciones arbitrarias."""

from dataclasses import dataclass, field
from pathlib import Path
import re
import tomllib

from .errors import DbkeeperError

TOOLS = {"pg_dump", "pg_restore", "psql"}
SSLMODES = {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}


def identifier(value: object, field_name: str) -> str:
    # Un nombre literal nunca debe reinterpretarse como conninfo por libpq.
    if (not isinstance(value, str) or not value or len(value.encode()) > 63
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
            or any(c in value for c in "=:/\\") or value.startswith("-")):
        raise DbkeeperError(f"{field_name}: use un nombre literal de 1 a 63 bytes, sin DSN.")
    return value


def host_name(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_.:\-]+", value):
        raise DbkeeperError("host: se requiere un único hostname o dirección IP, sin DSN.")
    return value


def integer(value: object, name: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise DbkeeperError(f"{name}: entero requerido entre {low} y {high}.")
    return value


def path_value(value: object, base: Path) -> Path:
    if not isinstance(value, str) or not value or any(ord(c) < 32 for c in value):
        raise DbkeeperError("Ruta de configuración inválida.")
    path = Path(value).expanduser()
    return (base / path).absolute() if not path.is_absolute() else path


@dataclass(frozen=True)
class Profile:
    name: str
    purpose: str
    host: str
    port: int
    database: str
    user: str
    backup_dir: Path
    sslmode: str = "verify-full"
    connect_timeout: int = 10
    lock_wait_timeout_ms: int = 30000
    sslrootcert: Path | None = None
    sslcert: Path | None = None
    sslkey: Path | None = None
    tools: dict[str, str] = field(default_factory=dict)

    def source(self) -> dict:
        return {"profile": self.name, "host": self.host, "port": self.port,
                "database": self.database}


def load_profiles(path: Path) -> dict[str, Profile]:
    try:
        with path.open("rb") as stream:
            raw = tomllib.load(stream)
    except (OSError, ValueError):
        # TOMLDecodeError puede incluir una línea con una contraseña accidental.
        raise DbkeeperError("No se pudo leer el TOML o su sintaxis es inválida.") from None
    if set(raw) != {"profiles"} or not isinstance(raw["profiles"], dict) or not raw["profiles"]:
        raise DbkeeperError("Se requiere una tabla [profiles.nombre].")
    profiles = {}
    allowed = set(Profile.__dataclass_fields__) - {"name"}
    required = {"purpose", "host", "port", "database", "user", "backup_dir"}
    for name, data in raw["profiles"].items():
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise DbkeeperError("Nombre de perfil inválido.")
        if not isinstance(data, dict) or set(data) - allowed or required - set(data):
            raise DbkeeperError("Perfil con claves desconocidas o faltantes; no se admiten contraseñas.")
        data = dict(data)
        if data["purpose"] not in ("backup", "restore"):
            raise DbkeeperError("purpose debe ser backup o restore.")
        data["host"] = host_name(data["host"])
        for key in ("database", "user"):
            data[key] = identifier(data[key], key)
        data["port"] = integer(data["port"], "port", 1, 65535)
        data["connect_timeout"] = integer(data.get("connect_timeout", 10), "connect_timeout", 1, 3600)
        data["lock_wait_timeout_ms"] = integer(data.get("lock_wait_timeout_ms", 30000),
                                               "lock_wait_timeout_ms", 1, 2147483647)
        if not isinstance(data.get("sslmode", "verify-full"), str) or data.get("sslmode", "verify-full") not in SSLMODES:
            raise DbkeeperError("sslmode inválido.")
        for key in ("backup_dir", "sslrootcert", "sslcert", "sslkey"):
            if key in data:
                data[key] = path_value(data[key], path.absolute().parent)
        if bool(data.get("sslcert")) != bool(data.get("sslkey")):
            raise DbkeeperError("sslcert y sslkey deben configurarse juntos.")
        configured = data.get("tools", {})
        if not isinstance(configured, dict) or set(configured) - TOOLS:
            raise DbkeeperError("tools solo admite pg_dump, pg_restore y psql.")
        data["tools"] = {tool: str(path_value(value, path.absolute().parent))
                         for tool, value in configured.items()}
        profiles[name] = Profile(name=name, **data)
    return profiles


def select_profile(profiles: dict[str, Profile], name: str) -> Profile:
    try:
        return profiles[name]
    except KeyError:
        raise DbkeeperError("El perfil solicitado no existe.") from None

