import argparse
import json
import os
from pathlib import Path
import signal
import sys

from . import __version__
from .backup import backup
from .config import load_profiles, select_profile
from .copy import copy_backup
from .doctor import doctor
from .errors import DbkeeperError
from .listing import list_backups
from .restore import restore
from .verify import verify


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # argparse puede repetir valores arbitrarios, incluidas credenciales
        # que alguien pasó accidentalmente como una opción no admitida.
        self.print_usage(sys.stderr)
        self.exit(2, "Argumentos inválidos o faltantes; consulte --help. No se admiten contraseñas por CLI.\n")


def parser() -> argparse.ArgumentParser:
    cli = SafeArgumentParser(prog="dbkeeper", description="Backups locales de PostgreSQL remoto.")
    cli.add_argument("--version", action="version", version=__version__)
    cli.add_argument("--config", type=Path, default=Path("config/profiles.toml"), help="Archivo de perfiles TOML.")
    sub = cli.add_subparsers(dest="command", required=True)
    for name, help_text in (("doctor", "Comprobar dependencias, almacenamiento y conexión."),
                            ("backup", "Generar un backup Custom local."),
                            ("list", "Listar estados locales sin conectar al servidor.")):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--profile", required=True)
    check = sub.add_parser("verify", help="Comprobar manifiesto, hash y catálogo.")
    check.add_argument("backup", type=Path)
    check.add_argument("--full", action="store_true", help="Leer todo el archivo generando SQL hacia /dev/null.")
    check.add_argument("--profile", help="Usar las rutas de herramientas de este perfil; no conecta al origen.")
    check.add_argument("--pg-restore", help="Ruta del cliente para verificar sin un TOML.")
    target = sub.add_parser("restore", help="Restaurar explícitamente en una base NUEVA de pruebas.")
    target.add_argument("backup", type=Path)
    target.add_argument("--target-profile", required=True)
    target.add_argument("--database", required=True, help="Nombre literal de la base nueva de pruebas.")
    target.add_argument("--keep-ownership", action="store_true", help="Conservar propietarios y privilegios originales.")
    target.add_argument("--keep-tablespaces", action="store_true", help="Conservar asignaciones originales de tablespaces.")
    copy = sub.add_parser("copy", help="Copiar a otra carpeta local comparando SHA-256.")
    copy.add_argument("backup", type=Path)
    copy.add_argument("--destination", type=Path, required=True)
    return cli


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    old_umask = os.umask(0o077)
    previous_handler = signal.getsignal(signal.SIGTERM)

    def interrupted(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    try:
        profiles = None
        if args.command not in ("copy", "verify") or getattr(args, "profile", None):
            profiles = load_profiles(args.config)
        if args.command in ("doctor", "backup", "list"):
            profile = select_profile(profiles, args.profile)
            if args.command == "doctor":
                result = doctor(profile)
            elif args.command == "list":
                result = list_backups(profile.backup_dir)
            else:
                directory = backup(profile)
                result = {"message": "Respaldo generado; catálogo legible. Lectura completa y restauración pendientes.",
                          "directory": str(directory)}
        elif args.command == "verify":
            configured = dict(select_profile(profiles, args.profile).tools) if args.profile else {}
            if args.pg_restore:
                configured["pg_restore"] = str(Path(args.pg_restore).expanduser().absolute())
            result = verify(args.backup, full=args.full, tools=configured)
        elif args.command == "restore":
            result = restore(args.backup, select_profile(profiles, args.target_profile), args.database,
                             keep_ownership=args.keep_ownership, keep_tablespaces=args.keep_tablespaces)
        else:
            result = {"message": "Copia terminada; hashes SHA-256 iguales.",
                      "directory": str(copy_backup(args.backup, args.destination))}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except DbkeeperError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError):
        print("Error local de archivo, permisos o datos. La operación no se completó.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Operación interrumpida; revise los directorios incompletos.", file=sys.stderr)
        return 130
    finally:
        signal.signal(signal.SIGTERM, previous_handler)
        os.umask(old_umask)
