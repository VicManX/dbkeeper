from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dbkeeper.backup import backup
from dbkeeper.cli import main
from dbkeeper.config import Profile, load_profiles
from dbkeeper.copy import copy_backup
from dbkeeper.doctor import doctor
from dbkeeper.errors import DbkeeperError
from dbkeeper.listing import list_backups
from dbkeeper.logging_utils import SafeLog
from dbkeeper.manifest import completed_manifest
from dbkeeper.postgres import (Runner, catalog_args, check_backup_versions, clean_env,
                               connection_env, dump_args, full_read_args, major,
                               parse_version, resolve_tool, restore_args)
from dbkeeper.restore import create_database_sql, protect_target, restore
from dbkeeper.storage import (DUMP, MANIFEST, MARKER, allocate_run, rename_dump,
                              sha256, write_json)
from dbkeeper.verify import verify


class FakeRunner:
    failure = None
    calls = []

    def __init__(self, log):
        self.log = log

    def run(self, args, **kwargs):
        self.calls.append((args, kwargs))
        if "--version" in args:
            return f"{Path(args[0]).name} (PostgreSQL) 16.4"
        if "--command=SHOW server_version_num" in args:
            return "160004"
        if "--format=custom" in args:
            output = Path(next(a.removeprefix("--file=") for a in args if a.startswith("--file=")))
            output.write_bytes(b"PGDMP synthetic content for unit testing")
            if self.failure == "dump":
                raise DbkeeperError("pg_dump simulado falló.")
        if "--list" in args and self.failure == "catalog":
            raise DbkeeperError("Catálogo ilegible.")
        if "--file=/dev/null" in args and self.failure == "full":
            raise DbkeeperError("Datos corruptos.")
        if any(a.startswith("--command=CREATE DATABASE") for a in args) and self.failure == "create":
            raise DbkeeperError("Base existente.")
        if "--exit-on-error" in args and any(a.startswith("--dbname=") for a in args) and self.failure == "restore":
            raise DbkeeperError("Restauración fallida.")
        return ""


@contextmanager
def fake_postgres(failure=None):
    FakeRunner.failure, FakeRunner.calls = failure, []
    with patch("dbkeeper.postgres.resolve_tool", side_effect=lambda name, conf: name), \
            patch("dbkeeper.backup.Runner", FakeRunner), \
            patch("dbkeeper.verify.Runner", FakeRunner), \
            patch("dbkeeper.restore.Runner", FakeRunner), \
            patch("dbkeeper.doctor.Runner", FakeRunner):
        yield


class BaseTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="dbkeeper-unit-", dir="/tmp")
        self.root = Path(self.temporary.name)
        self.passfile = self.root / ".pgpass"
        self.passfile.write_text("host:5432:db:user:s3cr3t\\:value\n")
        self.passfile.chmod(0o600)
        self.env_patch = patch.dict(os.environ, {"PGPASSFILE": str(self.passfile), "PGPASSWORD": ""})
        self.env_patch.start()
        self.profile = Profile("source", "backup", "vpn.example", 5432, "app", "reader",
                               self.root / "backups", sslmode="require")

    def tearDown(self):
        self.env_patch.stop()
        self.temporary.cleanup()

    def generated(self):
        with fake_postgres():
            return backup(self.profile)


class ConfigTests(BaseTest):
    def config(self, extra="", **values):
        data = {"purpose": "backup", "host": "vpn.example", "port": 5432,
                "database": "app", "user": "reader", "backup_dir": "./backups"}
        data.update(values)
        content = "[profiles.source]\n" + "\n".join(f"{k} = {json.dumps(v)}" for k, v in data.items())
        path = self.root / "profiles.toml"
        path.write_text(content + "\n" + extra)
        return path

    def test_relative_paths_and_defaults(self):
        profile = load_profiles(self.config())["source"]
        self.assertEqual(profile.backup_dir, self.root / "backups")
        self.assertEqual(profile.sslmode, "verify-full")

    def test_invalid_values_and_no_secrets_in_errors(self):
        cases = [{"port": 0}, {"port": True}, {"host": "postgres://user:SECRET@server/db"},
                 {"database": "host=server password=SECRET"}, {"database": "-dbname"},
                 {"database": "x" * 64}, {"user": "a\nb"}, {"purpose": "other"},
                 {"sslmode": "invalid"}, {"connect_timeout": 0}, {"lock_wait_timeout_ms": -1},
                 {"password": "SECRET"}, {"tools": "SECRET"}, {"sslkey": "key.pem"}]
        for case in cases:
            with self.subTest(case=case):
                with self.assertRaises(DbkeeperError) as caught:
                    load_profiles(self.config(**case))
                self.assertNotIn("SECRET", str(caught.exception))

    def test_invalid_toml_does_not_echo_input(self):
        path = self.config(extra='password = "SECRET')
        with self.assertRaises(DbkeeperError) as caught:
            load_profiles(path)
        self.assertNotIn("SECRET", str(caught.exception))

    def test_unknown_top_level_and_tool(self):
        for extra in ('[secrets]\npassword="SECRET"', '[profiles.source.tools]\nother="SECRET"'):
            with self.assertRaises(DbkeeperError):
                load_profiles(self.config(extra=extra))


class PostgresTests(BaseTest):
    def test_arguments_preserve_literals_and_have_no_shell_or_filters(self):
        profile = replace(self.profile, database='my db;$(touch HACKED) "x"', user="user with space")
        path = self.root / "a ; b.partial"
        args = dump_args("pg_dump", profile, path)
        self.assertIn('--dbname=my db;$(touch HACKED) "x"', args)
        self.assertIn(f"--file={path}", args)
        self.assertIn("--no-password", args)
        self.assertIn("--lock-wait-timeout=30000", args)
        self.assertFalse(any(a.startswith(("--table", "--schema", "--no-sync")) for a in args))
        full = full_read_args("pg_restore", path)
        self.assertIn("--file=/dev/null", full)
        self.assertFalse(any(a.startswith("--dbname") for a in full))
        restore_command = restore_args("pg_restore", profile, "test", path)
        for forbidden in ("--clean", "--create"):
            self.assertNotIn(forbidden, restore_command)
        for needed in ("--exit-on-error", "--no-owner", "--no-privileges", "--no-tablespaces"):
            self.assertIn(needed, restore_command)

    def test_missing_dependency(self):
        with patch("dbkeeper.postgres.shutil.which", return_value=None):
            with self.assertRaisesRegex(DbkeeperError, "Dependencia ausente"):
                resolve_tool("pg_dump", {})

    def test_versions(self):
        self.assertEqual(parse_version("pg_dump (PostgreSQL) 16.4 (Ubuntu)"), "16.4")
        self.assertEqual(major("9.6.24"), (9, 6))
        self.assertLess(major("9.6.24"), major("10.2"))
        for value in ("nonsense", "pg_dump (PostgreSQL) 19beta1"):
            with self.assertRaises(DbkeeperError):
                parse_version(value)
        with self.assertRaises(DbkeeperError):
            check_backup_versions({"server": "17.1", "pg_dump": "16.4", "pg_restore": "16.4"})
        with self.assertRaises(DbkeeperError):
            check_backup_versions({"server": "16.1", "pg_dump": "16.4", "pg_restore": "15.8"})
        self.assertTrue(check_backup_versions({"server": "15.1", "pg_dump": "16.4", "pg_restore": "16.4"}))

    def test_auth_permissions_and_environment_isolation(self):
        with patch.dict(os.environ, {"PGHOST": "wrong", "PGSERVICE": "wrong", "PGOPTIONS": "secret", "PGDATABASE": "wrong"}):
            env = connection_env(self.profile)
            for key in ("PGHOST", "PGDATABASE", "PGSERVICE", "PGOPTIONS", "PGPASSWORD"):
                self.assertNotIn(key, env)
            self.assertEqual(env["PGPASSFILE"], str(self.passfile))
        self.passfile.chmod(0o644)
        with self.assertRaisesRegex(DbkeeperError, "0600"):
            connection_env(self.profile)
        self.passfile.unlink()
        with self.assertRaisesRegex(DbkeeperError, "no existe"):
            connection_env(self.profile)
        with patch.dict(os.environ, {"PGPASSWORD": "SECRET"}):
            with self.assertRaises(DbkeeperError) as caught:
                connection_env(self.profile)
            self.assertNotIn("SECRET", str(caught.exception))

    def test_ssl_files(self):
        with self.assertRaises(DbkeeperError):
            connection_env(replace(self.profile, sslrootcert=self.root / "absent.crt"))

    def test_dsn_is_rejected_even_for_programmatically_created_profile(self):
        with self.assertRaises(DbkeeperError):
            dump_args("pg_dump", replace(self.profile, database="host=wrong password=SECRET"), self.root / "x")

    def test_runner_streams_redacted_errors_and_never_shell_expands(self):
        log = SafeLog(self.root / "safe.log")
        payload = "s3cr3t:value password='other secret' postgresql://u:pw@host/db"
        script = "import sys; print(sys.argv[1], file=sys.stderr); sys.exit(2)"
        with self.assertRaises(DbkeeperError):
            Runner(log).run([sys.executable, "-c", script, payload])
        log.close()
        text = log.path.read_text()
        for secret in ("s3cr3t:value", "other secret", "pw@host"):
            self.assertNotIn(secret, text)
        self.assertIn("OMITIDO", text)
        self.assertEqual(log.path.stat().st_mode & 0o777, 0o600)

    def test_runner_redacts_uri_and_sql_data(self):
        log = SafeLog(self.root / "sql.log")
        script = "import sys; sys.stderr.write('postgresql://u:SECRET@h/db\\npg_restore: error: Command was: SELECT SECRET2\\nSECRET3\\npg_restore: error: failed\\n')"
        Runner(log).run([sys.executable, "-c", script])
        log.close()
        self.assertNotIn("SECRET", log.path.read_text())
        self.assertIn("failed", log.path.read_text())

    def test_quoted_password_fields_are_redacted(self):
        log = SafeLog(self.root / "quoted.log")
        log.write('"password": "clave_unica_123"')
        log.write("'password' = 'otra_clave_456'")
        log.close()
        self.assertNotIn("clave_unica_123", log.path.read_text())
        self.assertNotIn("otra_clave_456", log.path.read_text())

    def test_runner_captures_small_stdout_and_passes_literal_argument(self):
        log = SafeLog(self.root / "stdout.log")
        literal = "$(touch should_not_exist); `date`"
        result = Runner(log).run([sys.executable, "-c", "import sys; print(sys.argv[1])", literal], capture=True)
        self.assertEqual(result, literal)
        log.close()

    def test_runner_timeout_and_log_failure(self):
        log = SafeLog(self.root / "runner.log")
        with self.assertRaises(subprocess.TimeoutExpired):
            Runner(log).run([sys.executable, "-c", "import time; time.sleep(10)"], timeout=0.05)
        with patch.object(log, "write", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(DbkeeperError, "log"):
                Runner(log).run([sys.executable, "-c", "import sys; print('error',file=sys.stderr)"])
        log.close()


class BackupTests(BaseTest):
    def test_success_has_manifest_hash_permissions_and_no_automatic_operations(self):
        directory = self.generated()
        data = completed_manifest(directory)
        self.assertEqual(data["sha256"], sha256(directory / DUMP))
        self.assertEqual(data["full_read"], "not_run")
        self.assertEqual(data["test_restore"], "not_run")
        self.assertFalse((directory / MARKER).exists())
        self.assertFalse(any(directory.glob("*.partial")))
        self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        for path in directory.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertFalse(any("--file=/dev/null" in args or "--exit-on-error" in args for args, _ in FakeRunner.calls))

    def assert_failed_backup(self, context, stage):
        with context:
            with self.assertRaises(DbkeeperError):
                backup(self.profile)
        directory = next(self.profile.backup_dir.iterdir())
        self.assertTrue((directory / MARKER).exists())
        data = json.loads((directory / MANIFEST).read_text())
        self.assertEqual(data["status"], "failed")
        self.assertEqual(data["failed_stage"], stage)
        with self.assertRaises(DbkeeperError):
            completed_manifest(directory)

    def test_pg_dump_failure(self):
        self.assert_failed_backup(fake_postgres("dump"), "pg_dump")

    def test_catalog_failure(self):
        self.assert_failed_backup(fake_postgres("catalog"), "catalog")

    def test_hash_failure(self):
        with fake_postgres():
            self.assert_failed_backup(patch("dbkeeper.backup.sha256", side_effect=OSError("read failed")), "sha256")

    def test_rename_failure(self):
        with fake_postgres():
            self.assert_failed_backup(patch("dbkeeper.backup.rename_dump", side_effect=OSError("rename failed")), "rename")

    def test_empty_and_missing_dump(self):
        original = FakeRunner.run
        for missing in (True, False):
            with self.subTest(missing=missing):
                self.profile = replace(self.profile, backup_dir=self.root / str(missing))
                def empty(runner, args, **kwargs):
                    result = original(runner, args, **kwargs)
                    if "--format=custom" in args:
                        path = Path(next(a[7:] for a in args if a.startswith("--file=")))
                        path.unlink() if missing else path.write_bytes(b"")
                    return result
                with patch.object(FakeRunner, "run", empty):
                    self.assert_failed_backup(fake_postgres(), "nonempty_file")

    def test_metadata_commit_failure_is_detectable_even_after_rename(self):
        original = write_json
        def failed(path, value):
            if value.get("status") == "complete":
                raise OSError("disk full")
            return original(path, value)
        with fake_postgres():
            self.assert_failed_backup(patch("dbkeeper.backup.write_json", side_effect=failed), "final_metadata")
        row = list_backups(self.profile.backup_dir)[0]
        self.assertEqual(row["status"], "incomplete")

    def test_metadata_failure_marker_survives_when_no_error_report_can_be_written(self):
        with fake_postgres(), patch("dbkeeper.backup.write_json", side_effect=OSError), \
                patch("dbkeeper.storage.write_json", side_effect=OSError):
            with self.assertRaisesRegex(DbkeeperError, "No se pudieron guardar"):
                backup(self.profile)
        directory = next(self.profile.backup_dir.iterdir())
        self.assertTrue((directory / MARKER).exists())

    def test_directory_creation_failure(self):
        with patch("dbkeeper.storage.Path.mkdir", side_effect=PermissionError):
            with self.assertRaises(OSError):
                backup(self.profile)

    def test_interrupt_and_log_creation_failure_leave_incomplete_state(self):
        with fake_postgres():
            self.assert_failed_backup(patch("dbkeeper.backup.SafeLog", side_effect=PermissionError), "initial_metadata")
        self.profile = replace(self.profile, backup_dir=self.root / "interrupted")
        with fake_postgres():
            self.assert_failed_backup(patch("dbkeeper.backup.preflight", side_effect=KeyboardInterrupt), "preflight")

    def test_final_marker_removal_failure(self):
        with fake_postgres():
            self.assert_failed_backup(patch("dbkeeper.backup.finish_run", side_effect=OSError), "final_metadata")

    def test_name_collisions_retry_and_never_overwrite(self):
        with patch("dbkeeper.storage.new_id", side_effect=["fixed", "fixed", "other"]):
            first = allocate_run(self.profile.backup_dir)
            (first / "preserved").write_text("old")
            second = allocate_run(self.profile.backup_dir)
        self.assertNotEqual(first, second)
        self.assertEqual((first / "preserved").read_text(), "old")
        with patch("dbkeeper.storage.new_id", return_value="fixed"):
            with self.assertRaisesRegex(DbkeeperError, "Colisiones"):
                allocate_run(self.profile.backup_dir)

    def test_no_overwrite_on_rename_and_os_replace_failure(self):
        source, target = self.root / "x.partial", self.root / "x.dump"
        source.write_bytes(b"new")
        target.write_bytes(b"old")
        with self.assertRaises(FileExistsError):
            rename_dump(source, target)
        self.assertEqual(target.read_bytes(), b"old")
        target.unlink()
        with patch("dbkeeper.storage.os.replace", side_effect=OSError):
            with self.assertRaises(OSError):
                rename_dump(source, target)
        self.assertTrue(source.exists())


class VerifyCopyRestoreTests(BaseTest):
    def test_basic_and_full_verification_are_distinct_and_offline(self):
        directory = self.generated()
        with fake_postgres():
            basic = verify(directory)
            self.assertEqual(basic["full_read"], "not_run")
            full = verify(directory / DUMP, full=True)
            self.assertEqual(full["full_read"], "ok")
            self.assertFalse(any(any(a.startswith("--dbname") for a in args) for args, _ in FakeRunner.calls))
        self.assertEqual(list_backups(self.profile.backup_dir)[0]["last_verification"]["full_read"], "ok")

    def test_corruption_incomplete_and_invalid_manifest(self):
        directory = self.generated()
        data = (directory / DUMP).read_bytes()
        (directory / DUMP).write_bytes(b"x" * len(data))
        with self.assertRaisesRegex(DbkeeperError, "SHA-256"):
            verify(directory)
        (directory / DUMP).write_bytes(data)
        (directory / MARKER).touch()
        with self.assertRaises(DbkeeperError):
            verify(directory)
        (directory / MARKER).unlink()
        (directory / MANIFEST).write_text("broken")
        with self.assertRaises(DbkeeperError):
            verify(directory)

    def test_full_read_failure_is_recorded(self):
        directory = self.generated()
        with fake_postgres("full"):
            with self.assertRaisesRegex(DbkeeperError, "full_read"):
                verify(directory, full=True)
        report_dir = next((directory / "verifications").iterdir())
        report = json.loads((report_dir / MANIFEST).read_text())
        self.assertEqual(report["checks"]["catalog"], "ok")
        self.assertEqual(report["checks"]["full_read"], "failed")
        self.assertTrue((report_dir / MARKER).exists())

    def test_partial_path_and_symlink_rejected(self):
        directory = self.generated()
        with self.assertRaises(DbkeeperError):
            verify(directory / "backup.dump.partial")
        (directory / DUMP).rename(directory / "real")
        (directory / DUMP).symlink_to(directory / "real")
        with self.assertRaises(DbkeeperError):
            verify(directory)

    def test_fifo_manifest_is_rejected_without_blocking(self):
        directory = self.generated()
        (directory / MANIFEST).unlink()
        os.mkfifo(directory / MANIFEST, 0o600)
        with self.assertRaises(DbkeeperError):
            verify(directory)

    def test_nonexistent_backup_does_not_create_directories(self):
        missing = self.root / "missing"
        with self.assertRaises(DbkeeperError):
            verify(missing / DUMP)
        self.assertFalse(missing.exists())

    def test_copy_compares_hashes_and_preserves_destination(self):
        directory = self.generated()
        destination = self.root / "Windows folder"
        destination.mkdir(mode=0o755)
        copied = copy_backup(directory, destination)
        self.assertEqual(sha256(copied / DUMP), sha256(directory / DUMP))
        self.assertEqual(completed_manifest(copied)["copy"]["sha256_comparison"], "ok")
        self.assertEqual(destination.stat().st_mode & 0o777, 0o755)
        self.assertNotEqual(copy_backup(directory, destination), copied)

    def test_copy_hash_failure(self):
        directory = self.generated()
        with patch("dbkeeper.copy.sha256", return_value="invalid"):
            with self.assertRaisesRegex(DbkeeperError, "copy_hash"):
                copy_backup(directory, self.root / "copies")
        copied = next((self.root / "copies").iterdir())
        self.assertTrue((copied / MARKER).exists())
        self.assertTrue((copied / (DUMP + ".partial")).exists())

    def test_copy_write_and_rename_errors(self):
        directory = self.generated()
        for symbol in ("shutil.copyfileobj", "rename_dump"):
            with self.subTest(symbol=symbol), patch("dbkeeper.copy." + symbol, side_effect=OSError):
                with self.assertRaises(DbkeeperError):
                    copy_backup(directory, self.root / "copies")
        for copied in (self.root / "copies").iterdir():
            self.assertTrue((copied / MARKER).exists())

    def test_target_protection_and_safe_quoted_sql(self):
        target = replace(self.profile, name="test", purpose="restore", host="VPN.EXAMPLE.")
        with self.assertRaisesRegex(DbkeeperError, "coincide"):
            protect_target(self.profile.source(), target, "app")
        with self.assertRaises(DbkeeperError):
            protect_target(self.profile.source(), self.profile, "test_db")
        for database in ("host=other dbname=app", "postgresql://user:SECRET@host/app", "x" * 64):
            with self.assertRaises(DbkeeperError):
                protect_target(self.profile.source(), target, database)
        self.assertEqual(create_database_sql('test"; --'), 'CREATE DATABASE "test""; --" TEMPLATE template0')
        protect_target(self.profile.source(), target, "new_test")

    def test_restore_new_database_only_and_distinct_status(self):
        directory = self.generated()
        target = replace(self.profile, name="test", purpose="restore", host="127.0.0.1")
        with fake_postgres():
            report = restore(directory, target, "test_db")
            self.assertEqual(report["test_restore"], "ok")
            args = [a for call, _ in FakeRunner.calls for a in call]
            self.assertIn('--command=CREATE DATABASE "test_db" TEMPLATE template0', args)
            self.assertNotIn("--clean", args)
            self.assertNotIn("--create", args)
            self.assertFalse(any("DROP " in a for a in args))
        self.assertEqual(list_backups(self.profile.backup_dir)[0]["last_test_restore"]["status"], "complete")

    def test_existing_database_and_failed_restore_never_drop(self):
        directory = self.generated()
        target = replace(self.profile, name="test", purpose="restore", host="127.0.0.1")
        for failure in ("create", "restore"):
            with fake_postgres(failure):
                with self.assertRaisesRegex(DbkeeperError, "No se borró"):
                    restore(directory, target, "test_db")
                self.assertFalse(any("DROP " in a for args, _ in FakeRunner.calls for a in args))
        records = [json.loads(p.read_text()) for p in (directory / "restores").glob("*/manifest.json")]
        self.assertEqual({r["database_creation"] for r in records}, {"attempted", "created"})
        self.assertTrue(all(r["status"] == "failed" for r in records))

    def test_older_target_rejected_before_create(self):
        directory = self.generated()
        target = replace(self.profile, name="test", purpose="restore")
        with fake_postgres(), patch("dbkeeper.restore.preflight", return_value=(
                {"psql": "psql", "pg_restore": "pg_restore"},
                {"server": "15.8", "pg_restore": "16.4"}, {}, [])):
            with self.assertRaisesRegex(DbkeeperError, "anterior"):
                restore(directory, target, "test_db")
            self.assertFalse(FakeRunner.calls)

    def test_doctor_and_cli_failure_never_report_success(self):
        with fake_postgres():
            self.assertEqual(doctor(self.profile)["checks"]["connectivity"], "ok")
        output, error = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            code = main(["--config", str(self.root / "absent"), "backup", "--profile", "source"])
        self.assertEqual(code, 1)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("Error:", error.getvalue())

    def test_cli_does_not_echo_accidental_secret_arguments(self):
        output = io.StringIO()
        with redirect_stderr(output), self.assertRaises(SystemExit) as caught:
            main(["backup", "--profile", "source", "--password", "SECRET"])
        self.assertEqual(caught.exception.code, 2)
        self.assertNotIn("SECRET", output.getvalue())


if __name__ == "__main__":
    unittest.main()
