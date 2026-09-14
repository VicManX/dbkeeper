"""Solo crea un cluster desechable propio en /tmp; nunca acepta DSN externos."""

from dataclasses import replace
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dbkeeper.backup import backup
from dbkeeper.config import Profile
from dbkeeper.copy import copy_backup
from dbkeeper.errors import DbkeeperError
from dbkeeper.manifest import completed_manifest
from dbkeeper.postgres import clean_env
from dbkeeper.restore import restore
from dbkeeper.storage import DUMP, MANIFEST, sha256, write_json
from dbkeeper.verify import verify


@unittest.skipUnless(os.environ.get("DBKEEPER_INTEGRATION") == "1",
                     "Integración opcional: establezca DBKEEPER_INTEGRATION=1; requiere servidor PostgreSQL local desechable.")
class DisposablePostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.geteuid() == 0:
            raise unittest.SkipTest("initdb no se ejecuta como root; use un usuario WSL normal.")
        configured = os.environ.get("DBKEEPER_PG_BIN")
        cls.tools = {}
        for name in ("initdb", "pg_ctl", "pg_dump", "pg_restore", "psql"):
            path = shutil.which(str(Path(configured) / name) if configured else name)
            if not path:
                raise unittest.SkipTest(f"No disponible: {name}; configure DBKEEPER_PG_BIN con binarios de una misma versión.")
            cls.tools[name] = path
        cls.temporary = tempfile.TemporaryDirectory(prefix="dbkeeper-integration-", dir="/tmp")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.data = cls.root / "cluster"
        cls.env = clean_env()
        # Env de libpq explícito, sin servicios, contraseñas ni rutas del usuario.
        cls.env.update(PGSSLMODE="disable", PGPASSFILE=str(cls.root / "unused-pgpass"), PGCONNECT_TIMEOUT="5")
        cls.run_tool([cls.tools["initdb"], "-D", str(cls.data), "--username=dbkeeper_test",
                      "--auth=trust", "--encoding=UTF8", "--locale=C"])
        # El directorio 0700 y listen_addresses=127.0.0.1 aíslan este cluster de red externa.
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            cls.port = probe.getsockname()[1]
        cls.addClassCleanup(cls.stop_cluster)
        cls.run_tool([cls.tools["pg_ctl"], "-D", str(cls.data), "-l", str(cls.root / "server.log"),
                      "-o", f"-h 127.0.0.1 -p {cls.port} -k {cls.root}", "-w", "start"])
        cls.env_patch = patch.dict(os.environ, {"PGPASSFILE": "", "PGPASSWORD": ""})
        cls.env_patch.start()
        # Un archivo vacío permite el método trust del cluster sintético.
        pgpass = cls.root / "empty-pgpass"
        pgpass.touch(mode=0o600)
        os.environ["PGPASSFILE"] = str(pgpass)
        cls.addClassCleanup(cls.env_patch.stop)
        cls.source = Profile("synthetic", "backup", "127.0.0.1", cls.port, "synthetic", "dbkeeper_test",
                             cls.root / "backups", sslmode="disable",
                             tools={name: cls.tools[name] for name in ("pg_dump", "pg_restore", "psql")})
        cls.target = replace(cls.source, name="isolated_test", purpose="restore", database="postgres")
        cls.sql("postgres", "CREATE DATABASE synthetic TEMPLATE template0")
        cls.sql("synthetic", """
            CREATE TABLE accounts (id integer PRIMARY KEY, balance bigint NOT NULL);
            INSERT INTO accounts VALUES (1, 1000000), (2, 1000000);
            CREATE TABLE transaction_state (singleton boolean PRIMARY KEY, completed bigint NOT NULL);
            INSERT INTO transaction_state VALUES (true, 0);
            CREATE SEQUENCE transaction_ids;
            CREATE TABLE movements (tx bigint NOT NULL, account integer REFERENCES accounts(id),
                                    amount bigint NOT NULL, PRIMARY KEY(tx, account));
            CREATE TABLE ballast AS
              SELECT i, (SELECT string_agg(md5((i::bigint * 20 + n)::text), '')
                         FROM generate_series(1, 20) AS n) AS payload
              FROM generate_series(1, 100000) AS i;
        """)

    @classmethod
    def run_tool(cls, args):
        return subprocess.run(args, env=cls.env, stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, check=True, timeout=120, shell=False)

    @classmethod
    def stop_cluster(cls):
        if (cls.data / "postmaster.pid").exists():
            cls.run_tool([cls.tools["pg_ctl"], "-D", str(cls.data), "-m", "immediate", "-w", "stop"])

    @classmethod
    def sql(cls, database, sql):
        args = [cls.tools["psql"], "-X", "-w", "-h", "127.0.0.1", "-p", str(cls.port),
                "-U", "dbkeeper_test", "-d", database, "-At", "-v", "ON_ERROR_STOP=1", "-c", sql]
        return cls.run_tool(args).stdout.strip()

    def test_concurrent_snapshot_restore_and_truncated_archive(self):
        stopped, ready = threading.Event(), threading.Event()
        commits, errors = [], []
        transaction = """
            BEGIN;
            UPDATE accounts SET balance = balance - 1 WHERE id = 1;
            SELECT pg_sleep(0.01);
            UPDATE accounts SET balance = balance + 1 WHERE id = 2;
            UPDATE transaction_state SET completed = completed + 1;
            WITH t AS (SELECT nextval('transaction_ids') AS id)
            INSERT INTO movements SELECT t.id, a.id, CASE WHEN a.id = 1 THEN -1 ELSE 1 END
                                  FROM t CROSS JOIN accounts a;
            DELETE FROM movements WHERE tx < currval('transaction_ids') - 50;
            COMMIT;
        """

        def writer():
            try:
                while not stopped.is_set():
                    self.sql("synthetic", transaction)
                    commits.append(time.monotonic())
                    ready.set()
            except Exception as exc:
                errors.append(exc)
                ready.set()

        thread = threading.Thread(target=writer)
        thread.start()
        try:
            self.assertTrue(ready.wait(10), "No inició el escritor transaccional")
            start = time.monotonic()
            directory = backup(self.source)
            end = time.monotonic()
        finally:
            stopped.set()
            thread.join(timeout=130)
        self.assertFalse(thread.is_alive())
        self.assertFalse(errors, errors)
        self.assertTrue(any(start < stamp < end for stamp in commits), "No se observaron commits durante el backup")
        self.assertEqual(verify(directory, full=True, tools=self.source.tools)["full_read"], "ok")
        report = restore(directory, self.target, "restored_test")
        self.assertEqual(report["test_restore"], "ok")
        # Invariantes internas de la instantánea restaurada; no conteos del origen cambiante.
        self.assertEqual(self.sql("restored_test", "SELECT sum(balance) FROM accounts"), "2000000")
        self.assertEqual(self.sql("restored_test", "SELECT count(*) FROM accounts"), "2")
        self.assertEqual(self.sql("restored_test", """
            SELECT count(*) FROM accounts CROSS JOIN transaction_state
            WHERE balance <> CASE WHEN id = 1 THEN 1000000 - completed
                                             ELSE 1000000 + completed END
        """), "0")
        self.assertEqual(self.sql("restored_test", """
            SELECT completed = (SELECT max(tx) FROM movements) FROM transaction_state
        """), "t")
        self.assertEqual(self.sql("restored_test", """
            SELECT count(*) FROM (SELECT tx FROM movements GROUP BY tx
                                  HAVING count(*) <> 2 OR sum(amount) <> 0) AS invalid
        """), "0")
        self.assertEqual(self.sql("restored_test", "SELECT count(*) > 0 FROM movements"), "t")
        self.assertEqual(self.sql("restored_test", "SELECT count(*) FROM ballast"), "100000")
        # CREATE debe fallar y preservar la base ya restaurada.
        with self.assertRaises(DbkeeperError):
            restore(directory, self.target, "restored_test")
        self.assertEqual(self.sql("restored_test", "SELECT sum(balance) FROM accounts"), "2000000")
        truncated = copy_backup(directory, self.root / "copies")
        metadata = completed_manifest(truncated)
        with (truncated / DUMP).open("r+b") as stream:
            stream.truncate(metadata["size_bytes"] // 2)
        # Recalcular intencionalmente el manifiesto SOLO en esta prueba sintética:
        # demuestra que el catálogo y un hash coherente no validan todos los bloques.
        metadata.update(size_bytes=(truncated / DUMP).stat().st_size, sha256=sha256(truncated / DUMP))
        write_json(truncated / MANIFEST, metadata)
        self.assertEqual(verify(truncated, tools=self.source.tools)["checks"]["catalog"], "ok")
        with self.assertRaisesRegex(DbkeeperError, "full_read"):
            verify(truncated, full=True, tools=self.source.tools)


if __name__ == "__main__":
    unittest.main()
