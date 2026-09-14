# dbkeeper

CLI en Python 3.11+ para generar y administrar backups **locales** de una base
PostgreSQL remota desde Ubuntu en WSL, con la VPN ya conectada. Orquesta `pg_dump`,
`pg_restore` y `psql`; no necesita instalar un agente ni crear archivos en el
servidor. No tiene dependencias Python de ejecución.

Cada comando realiza una operación independiente:

| Comando | Resultado |
| --- | --- |
| `doctor` | Valida perfiles, clientes, versiones, almacenamiento y conexión autenticada. |
| `backup` | Genera un archivo Custom, lee su catálogo, calcula SHA-256 y finaliza metadatos. |
| `verify` | Comprueba archivo, manifiesto, SHA-256 y catálogo; `--full` lee todo hacia `/dev/null`. |
| `list` | Muestra los estados locales y los últimos reportes, sin conexión remota. |
| `restore` | Verifica completamente y restaura en una base **nueva** de pruebas. |
| `copy` | Copia dump y manifiesto a otro directorio local y compara los hashes. |

`backup` no ejecuta `restore` ni `copy`. Tampoco realiza automáticamente la
lectura completa. Un resultado `complete` de backup significa **respaldo generado
y catálogo legible**; los otros niveles tienen reportes independientes.

Para empezar, con Python 3.11+ y los clientes PostgreSQL ya instalados:

```bash
cd /mnt/g/dev/dbkeeper
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
cp -n config/profiles.example.toml config/profiles.toml
nano config/profiles.toml
```

Complete los valores de ejemplo, instale los certificados necesarios y configure
`.pgpass` antes de conectar. Siga la [instalación paso a paso en WSL](docs/instalacion_wsl.md).
Después de conectar la VPN:

```bash
dbkeeper --config config/profiles.toml doctor --profile origen
dbkeeper --config config/profiles.toml backup --profile origen
dbkeeper --config config/profiles.toml list --profile origen
```

Use el campo `directory` devuelto por `backup` en las siguientes operaciones:

```bash
BACKUP_DIR="$HOME/dbkeeper-backups/ID_DEVUELTO_POR_BACKUP"
dbkeeper --config config/profiles.toml verify "$BACKUP_DIR" --profile origen --full
dbkeeper --config config/profiles.toml restore "$BACKUP_DIR" --target-profile prueba --database dbkeeper_prueba_001
dbkeeper copy "$BACKUP_DIR" --destination /mnt/c/Users/TU_USUARIO/Backups/dbkeeper
```

Consulte [uso y garantías](docs/uso.md), [restauración de prueba](docs/restauracion_prueba.md)
y [solución de problemas](docs/solucion_problemas.md). `python -m dbkeeper` equivale
al ejecutable `dbkeeper`. La opción global `--config` va **antes** del subcomando.

La instantánea consistente de `pg_dump` permite mantener las escrituras. El dump
contiene los datos visibles en esa instantánea; no incluye todos los commits que
ocurran hasta terminar el comando. No ofrece recuperación a un instante arbitrario
(PITR). Véase la [documentación oficial sobre dumps](https://www.postgresql.org/docs/18/backup-dump.html).
Hay consumo de CPU, disco, red y espacio local; cambios DDL pueden entrar en
conflicto con los locks compartidos del dump. No se promete impacto nulo.

La versión inicial cubre una base PostgreSQL por ejecución, sin web, planificación,
retención automática ni nube. No usa `pg_dumpall`, exportaciones propias con
`SELECT`, copias físicas, `shell=True` ni `--no-sync`.

Las pruebas usan `unittest` de la biblioteca estándar:

```bash
python -m unittest discover -s tests -v
# Opcional, únicamente contra un cluster desechable creado por el test en /tmp:
DBKEEPER_INTEGRATION=1 DBKEEPER_PG_BIN=/usr/lib/postgresql/16/bin \
  python -m unittest tests.test_integration -v
```

La integración requiere también `initdb` y `pg_ctl` y un usuario que no sea root.
Crea datos sintéticos, mantiene transacciones concurrentes, restaura en otra base,
comprueba invariantes y detecta un archivo truncado mediante lectura completa.
No acepta un host o DSN externo ni utiliza perfiles reales. Si faltan herramientas,
se informa la omisión. Las pruebas unitarias simulan PostgreSQL, pero ejecutan
procesos Python reales para comprobar captura, redacción de errores y timeout.

La implementación está separada en configuración, clientes PostgreSQL,
almacenamiento, manifiestos, logs y módulos de operaciones bajo `src/dbkeeper/`.
Los manifiestos JSON tienen versión de esquema `1`; dump y reportes se conservan
localmente para su revisión.
