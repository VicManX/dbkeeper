# Graph Report - dbkeeper  (2026-09-14)

## Corpus Check
- 26 files · ~10,469 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 2 file(s) not represented in the graph (top: (none) 1, .toml 1)

## Summary
- 188 nodes · 657 edges · 12 communities (9 shown, 2 thin omitted)
- Extraction: 95% EXTRACTED · 5% INFERRED · 0% AMBIGUOUS · INFERRED: 33 edges (avg confidence: 0.93)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `f627df5c`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- DbkeeperError
- verify
- restore.py
- postgres.py
- backup
- Operaciones y garantías
- .test_concurrent_snapshot_restore_and_truncated_archive
- cli.py
- completed_manifest
- AGENTS.md
- dbkeeper

## God Nodes (most connected - your core abstractions)
1. `DbkeeperError` - 54 edges
2. `verify()` - 27 edges
3. `restore()` - 26 edges
4. `backup()` - 25 edges
5. `SafeLog` - 23 edges
6. `Profile` - 22 edges
7. `Runner` - 21 edges
8. `copy_backup()` - 20 edges
9. `allocate_run()` - 18 edges
10. `VerifyCopyRestoreTests` - 18 edges

## Surprising Connections (you probably didn't know these)
- `DisposablePostgresTests` --uses--> `Profile`  [INFERRED]
  tests/test_integration.py → src/dbkeeper/config.py
- `BaseTest` --uses--> `Profile`  [INFERRED]
  tests/test_unit.py → src/dbkeeper/config.py
- `DisposablePostgresTests` --uses--> `DbkeeperError`  [INFERRED]
  tests/test_integration.py → src/dbkeeper/errors.py
- `BackupTests` --uses--> `DbkeeperError`  [INFERRED]
  tests/test_unit.py → src/dbkeeper/errors.py
- `PostgresTests` --uses--> `DbkeeperError`  [INFERRED]
  tests/test_unit.py → src/dbkeeper/errors.py

## Import Cycles
- None detected.

## Communities (12 total, 2 thin omitted)

### Community 0 - "DbkeeperError"
Cohesion: 0.16
Nodes (15): Exception, host_name(), identifier(), integer(), load_profiles(), path_value(), Path, Perfiles estrictos: no se aceptan DSN, contraseñas ni opciones arbitrarias. (+7 more)

### Community 1 - "verify"
Cohesion: 0.15
Nodes (8): copy_backup(), Path, sha256(), integrity(), Path, verify(), BaseTest, VerifyCopyRestoreTests

### Community 2 - "restore.py"
Cohesion: 0.29
Nodes (17): Diagnósticos redactados antes de tocar disco; nunca se registran argv/env., backup_directory(), allocate_run(), exclusive_file(), finish_run(), new_id(), nonempty_file(), private_dir() (+9 more)

### Community 3 - "postgres.py"
Cohesion: 0.11
Nodes (25): Profile, password_file(), pgpass_secrets(), Path, SafeLog, catalog_args(), check_backup_versions(), clean_env() (+17 more)

### Community 4 - "backup"
Cohesion: 0.23
Nodes (4): backup(), Path, BackupTests, fake_postgres()

### Community 5 - "Operaciones y garantías"
Cohesion: 0.15
Nodes (10): Preparación de Ubuntu en WSL, Restauración explícita de prueba, Diagnóstico de problemas, Compatibilidad, Consistencia y alcance PostgreSQL, Copiar a Windows y comprobar hashes, Generar y localizar, Operaciones y garantías (+2 more)

### Community 6 - ".test_concurrent_snapshot_restore_and_truncated_archive"
Cohesion: 0.46
Nodes (3): skipUnless, DisposablePostgresTests, writer()

### Community 7 - "cli.py"
Cohesion: 0.23
Nodes (7): ArgumentParser, main(), parser(), SafeArgumentParser, select_profile(), doctor(), Orquestación local de las herramientas oficiales de PostgreSQL.

### Community 8 - "completed_manifest"
Cohesion: 0.62
Nodes (6): latest_report(), list_backups(), Path, completed_manifest(), Path, read_manifest()

## Knowledge Gaps
- **11 isolated node(s):** `dbkeeper`, `graphify`, `dbkeeper`, `Preparación de Ubuntu en WSL`, `Restauración explícita de prueba` (+6 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 34 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **2 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `DbkeeperError` connect `DbkeeperError` to `verify`, `restore.py`, `postgres.py`, `backup`, `.test_concurrent_snapshot_restore_and_truncated_archive`, `cli.py`, `completed_manifest`?**
  _High betweenness centrality (0.265) - this node is a cross-community bridge._
- **Why does `backup()` connect `backup` to `DbkeeperError`, `verify`, `restore.py`, `postgres.py`, `.test_concurrent_snapshot_restore_and_truncated_archive`, `cli.py`?**
  _High betweenness centrality (0.063) - this node is a cross-community bridge._
- **Why does `BackupTests` connect `backup` to `DbkeeperError`, `verify`, `restore.py`?**
  _High betweenness centrality (0.059) - this node is a cross-community bridge._
- **Are the 11 inferred relationships involving `DbkeeperError` (e.g. with `main()` and `latest_report()`) actually correct?**
  _`DbkeeperError` has 11 INFERRED edges - model-reasoned connections that need verification._
- **Are the 3 inferred relationships involving `SafeLog` (e.g. with `DbkeeperError` and `Runner`) actually correct?**
  _`SafeLog` has 3 INFERRED edges - model-reasoned connections that need verification._
- **What connects `dbkeeper`, `graphify`, `dbkeeper` to the rest of the system?**
  _11 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `verify` be split into smaller, more focused modules?**
  _Cohesion score 0.1476923076923077 - nodes in this community are weakly interconnected._