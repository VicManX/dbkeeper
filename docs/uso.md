# Operaciones y garantías

Ejecute los comandos desde el repositorio con el entorno virtual activo. Las
salidas correctas son JSON; los errores van a stderr con código distinto de cero.
Una opción de CLI inválida devuelve `2`; fallos de operaciones devuelven `1`.
Una interrupción puede devolver `130` antes de iniciar una operación o `1` si se
registró como fallo de una ejecución. No interprete texto de un log como resultado
de éxito: revise código de salida y manifiesto.

## Generar y localizar

```bash
dbkeeper --config config/profiles.toml doctor --profile origen
dbkeeper --config config/profiles.toml backup --profile origen
dbkeeper --config config/profiles.toml list --profile origen
```

El campo `directory` del backup apunta a un directorio nuevo con fecha UTC,
microsegundos y UUID. Se reintentan colisiones de directorio; nunca se reutiliza
una ejecución previa. `backup_dir` es un directorio dedicado administrado con
permisos `0700`; los archivos usan `0600` y la CLI aplica `umask 077`.

El archivo se escribe como `backup.dump.partial` directamente desde el cliente
local. Su publicación como `backup.dump` requiere salida `0` de `pg_dump`, archivo
regular no vacío, catálogo legible con `pg_restore --list`, SHA-256, renombrado y
metadatos escritos correctamente. Los hashes y las copias se procesan en bloques
de 1 MiB; el dump completo nunca se carga en RAM.

La publicación reserva el nombre final de forma exclusiva y después renombra
el parcial. Un marcador `INCOMPLETE` permanece durante toda la ejecución,
incluida la escritura atómica y sincronización de metadatos. Se retira al final.
Si hay un fallo posterior al renombrado puede existir `backup.dump` junto a ese
marcador: **no está finalizado**. Ante un corte abrupto, un manifiesto ausente,
estado distinto de `complete`, marcador o temporal `.partial` también indican
finalización incompleta. Si el disco impide registrar el fallo, el comando falla
y la estructura restante permite detectarlo. No se borran estos archivos.

El manifiesto `manifest.json` contiene identificador, perfil, host/puerto/base de
origen, inicio y fin UTC, versiones de servidor/`psql`/`pg_dump`/`pg_restore`, formato,
tamaño, SHA-256, estado y comprobaciones. `started_at_utc` y `ended_at_utc` son
**tiempos de ejecución**, no la hora exacta del snapshot. No almacena credenciales.

`list` comprueba estructura, manifiesto y tamaño, pero no recalcula todos los hashes.
Muestra `hash: not_rechecked`, el estado registrado y el último reporte de
verificación/restauración. `incomplete` exige revisión incluso si un manifiesto
antiguo dice `complete`. Varios perfiles pueden compartir `backup_dir`: se listan
todos sus backups y cada entrada identifica su origen. No hay retención automática.

## Consistencia y alcance PostgreSQL

Se utiliza `pg_dump --format=custom`, sin filtros de esquemas o tablas. La
estructura y los datos exportables de la base seleccionada se incluyen por
defecto. Las escrituras pueden continuar gracias a la instantánea consistente:
no se detienen servicios ni se añaden bloqueos exclusivos. Una instantánea larga
puede mantener versiones antiguas de filas y aumentar el trabajo de mantenimiento.
Véase [SQL Dump](https://www.postgresql.org/docs/18/backup-dump.html).

`lock_wait_timeout_ms` limita la espera inicial por locks compartidos; si se supera,
el dump falla. No es un límite de duración del backup. `connect_timeout` limita la
conexión; las consultas de diagnóstico tienen además un límite corto. Considere
la carga de disco/CPU del origen, compresión en el cliente, ancho de banda VPN,
espacio local y conflictos con cambios DDL. No hay porcentaje real de progreso:
dbkeeper no usa `--verbose` como una medida porcentual.

No se exportan roles ni definiciones globales de tablespaces: requieren un
procedimiento adicional del administrador. Los archivos externos referenciados
por la base, por ejemplo imágenes en el filesystem, no se copian automáticamente.
Los large objects internos sí forman parte del dump completo. Los datos de tablas
foráneas externas no están incluidos por defecto. Consulte el alcance de
[`pg_dump`](https://www.postgresql.org/docs/18/app-pgdump.html).

## Compatibilidad

Se prefiere la misma versión mayor de clientes y servidor de origen. dbkeeper
rechaza `pg_dump` anterior al servidor y exige `pg_restore` de la misma versión
mayor que `pg_dump` durante el backup. Un cliente más nuevo para un origen anterior
produce una advertencia; el propio cliente determina el límite inferior de
servidores admitidos. Se usan clientes estables, con sus actualizaciones menores.

La verificación admite `pg_restore` igual o posterior al productor. Para restaurar,
dbkeeper exige que el servidor de pruebas sea al menos tan nuevo como el origen,
el productor y el `pg_restore` utilizado. Es una política conservadora: PostgreSQL
no garantiza restaurar el SQL de un cliente en una versión mayor anterior, aunque
el origen fuese de esa versión. El manual explica estos límites en las
[notas de compatibilidad de pg_dump](https://www.postgresql.org/docs/16/app-pgdump.html).
La compatibilidad de extensiones y objetos debe comprobarse por separado.

## Verificar un backup existente

Sustituya el identificador por el devuelto al generar el backup:

```bash
BACKUP_DIR="$HOME/dbkeeper-backups/ID_DEVUELTO_POR_BACKUP"
dbkeeper --config config/profiles.toml verify "$BACKUP_DIR" --profile origen
dbkeeper --config config/profiles.toml verify "$BACKUP_DIR" --profile origen --full
```

Ambos modos comprueban archivo y manifiesto, comparan SHA-256 y leen el catálogo.
`--full` añade generación de todo el SQL hacia `/dev/null`, sin `--dbname` ni
conexión a una base destino. Puede usar únicamente una ruta de cliente sin TOML:

```bash
dbkeeper verify "$BACKUP_DIR" --full --pg-restore /usr/lib/postgresql/16/bin/pg_restore
```

| Nivel | Qué demuestra | Qué no demuestra |
| --- | --- | --- |
| Respaldo generado | El dump y la finalización terminaron correctamente. | Restaurabilidad en un entorno concreto. |
| Catálogo legible | `pg_restore --list` entiende el índice del archivo. | Integridad de todos los bloques de datos. |
| SHA-256 coincidente | Los bytes coinciden con el manifiesto. | Consistencia lógica, autenticidad o ausencia de una alteración del manifiesto. |
| Lectura completa verificada | `pg_restore` pudo recorrer y decodificar el contenido. | Ejecución correcta del SQL, extensiones o invariantes del negocio. |
| Restauración de prueba completada | El cliente completó la restauración sin errores en ese destino. | Validación funcional de toda la aplicación. |

La diferencia entre listar y emitir un script se basa en las opciones de
[`pg_restore`](https://www.postgresql.org/docs/17/app-pgrestore.html).
La lectura completa no sustituye [restaurar en pruebas](restauracion_prueba.md).
Cada verificación crea `verifications/ID/manifest.json` y un log propios; nunca
eleva un backup incompleto a completo ni modifica sus datos.

## Copiar a Windows y comprobar hashes

```bash
dbkeeper copy "$BACKUP_DIR" --destination /mnt/c/Users/TU_USUARIO/Backups/dbkeeper
```

Use una carpeta Windows existente con acceso limitado a su usuario. La copia
crea un subdirectorio único, copia dump y manifiesto, sincroniza, compara SHA-256
del original antes y después con el archivo copiado, y finaliza de forma marcada.
No cambia permisos del directorio padre preexistente. En DrvFS, la protección
efectiva depende de los permisos Windows y de las opciones de montaje; el origen
y las credenciales permanecen en el filesystem Linux. Si el destino no admite
sincronización o renombrado, la copia falla y queda marcada como incompleta.

La copia conserva el identificador y tiempos del backup original y agrega un
campo `copy` con sus propios tiempos y comparación. No transfiere logs ni el
historial de verificaciones/restauraciones. También funciona sin TOML o clientes
PostgreSQL; exige un backup finalizado cuyo hash coincida.

Compare manualmente usando el directorio devuelto por `copy`:

```bash
COPIA_DIR="/mnt/c/Users/TU_USUARIO/Backups/dbkeeper/ID_DEVUELTO_POR_COPY"
sha256sum "$BACKUP_DIR/backup.dump" "$COPIA_DIR/backup.dump"
```

En PowerShell, el valor `Hash` debe coincidir ignorando mayúsculas/minúsculas:

```powershell
Get-FileHash -Algorithm SHA256 'C:\Users\TU_USUARIO\Backups\dbkeeper\ID_DEVUELTO_POR_COPY\backup.dump'
```

No edite dumps o manifiestos durante operaciones. Los hashes detectan cambios,
pero no constituyen una firma digital. Los backups pueden contener datos sensibles
de la base y deben recibir la misma protección que el origen.

