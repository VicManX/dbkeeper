# Diagnóstico de problemas

Use el directorio de reporte indicado en el error. `doctor` guarda sus reportes
en `backup_dir/diagnostics/ID`; los backups incluyen `backup.log`; verificaciones
y restauraciones tienen subdirectorios independientes. Los errores de clientes
se capturan localmente, con redacción de contraseñas conocidas del archivo pgpass,
URI PostgreSQL y campos de contraseña. Se omiten fragmentos de SQL/datos de los
diagnósticos y líneas excesivas. No se registran argumentos ni entornos completos.
Revise cualquier log antes de compartirlo: todavía puede contener nombres de
objetos, rutas, hosts u otros datos operativos.

| Síntoma | Revisión |
| --- | --- |
| `Dependencia ausente` | Instale clientes y corrija `[profiles.nombre.tools]`. El wrapper de Ubuntu puede elegir otra versión: use rutas `/usr/lib/postgresql/MAJOR/bin/...`. |
| `pg_dump es más antiguo` | Instale la versión mayor del origen. No fuerce un dump con cliente anterior. |
| `pg_dump y pg_restore ... misma versión mayor` | Seleccione las rutas de ambos desde el mismo directorio de versión. |
| `no password supplied` o autenticación fallida | Revise `.pgpass`/`PGPASSFILE`, modo `0600`, host, base, usuario y orden de entradas. No pase la contraseña por CLI. |
| Timeout/conexión rechazada | Confirme VPN, resolución DNS desde WSL, ruta al host, puerto, firewall y configuración PostgreSQL del administrador. La aplicación no establece la VPN. |
| Error de certificado | Revise CA, vigencia, hostname y claves configuradas. El ejemplo `verify-full` necesita el certificado real. |
| Permiso denegado en tablas o secuencias | El administrador debe conceder acceso suficiente a todos los objetos y tener en cuenta RLS. `doctor` solo comprueba conexión y versión. |
| Timeout de locks | Revise cambios DDL concurrentes. Ajuste `lock_wait_timeout_ms` conscientemente y reintente; no detenga servicios automáticamente. |
| Disco lleno o error de hash/rename/fsync | Revise `df -h`, permisos y salud del almacenamiento. Conserve los parciales para diagnóstico; no los renombre manualmente como completos. |
| No permite permisos `0700` | Configure `backup_dir` bajo `~/dbkeeper-backups`, en el filesystem Linux de WSL. Use `copy` para la transferencia posterior a Windows. |
| `INCOMPLETE`, temporal o manifiesto inconsistente | Trate la ejecución como incompleta aunque exista `backup.dump`. Revise `failed_stage`, logs y espacio; genere una ejecución nueva. |
| SHA-256 distinto | Conserve evidencia y use otro backup. No reescriba el hash esperado para hacer pasar la verificación. |
| Catálogo legible pero `--full` falla | Puede haber truncamiento, corrupción de bloques o compresión no compatible. Conserve el archivo y use otro respaldo/cliente compatible. |
| Base de pruebas ya existe | Elija un nombre nuevo; dbkeeper no reutiliza ni borra la base existente. |
| Falla por extensión/rol/tablespace | Prepare dependencias y permisos del destino; revise las opciones de restauración. Una base parcialmente creada permanece para inspección. |

El timeout de conexión no limita la duración de la transferencia ni la de toda
la restauración. Ante una VPN interrumpida durante una operación, los tiempos de
fallo posteriores dependen también de TCP y PostgreSQL. Puede interrumpir con
Ctrl+C: la aplicación termina el cliente y conserva la ejecución incompleta. Un
cierre abrupto de WSL o SIGKILL no permite guardar un error nuevo, pero deja el
marcador o metadatos de ejecución sin finalizar. El hardware y el filesystem
determinan las garantías últimas de durabilidad pese a `fsync`.

Para comprobar el proyecto sin conectar a ningún servidor:

```bash
python -m unittest discover -s tests -v
```

Para la integración desechable:

```bash
DBKEEPER_INTEGRATION=1 DBKEEPER_PG_BIN=/usr/lib/postgresql/16/bin \
  python -m unittest tests.test_integration -v
```

Ejecute como usuario Linux normal. El test crea un cluster propio en `/tmp`,
escucha solo en `127.0.0.1` en un puerto efímero y utiliza datos sintéticos con
autenticación `trust` limitada a ese cluster de pruebas. No reutiliza perfiles,
servicios ni contraseñas reales y no acepta destinos externos. Requiere espacio
temporal para aproximadamente 100 MB de datos, dumps y restauración (reserve al
menos 500 MB). Se detiene y elimina únicamente ese cluster al terminar; esta
limpieza del fixture no se aplica a bases restauradas por la CLI.

La integración mantiene transacciones con inserciones, actualizaciones y borrados
mientras corre el dump. Comprueba commits durante el intervalo y las invariantes
internas del destino: suma de balances, pares de movimientos y correspondencia
entre balances, contador y última transacción en tres tablas distintas.
Finalmente trunca una copia y modifica su manifiesto **solo en el fixture** para
demostrar que una lectura de catálogo exitosa y un hash coherente no bastan:
la verificación completa debe fallar. No haga esto con respaldos reales.

`.gitignore` excluye dumps, parciales, logs, pgpass, claves/certificados y los
perfiles reales bajo `config/`. Solo se versiona `profiles.example.toml`. Guarde
otras configuraciones reales fuera del repositorio; nunca fuerce `git add -f`
para introducir datos o secretos. El dump puede contener secretos almacenados
por la propia aplicación de origen, aunque dbkeeper nunca agregue credenciales.
