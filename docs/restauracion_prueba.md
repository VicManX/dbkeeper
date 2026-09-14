# Restauración explícita de prueba

Prepare un servidor de pruebas independiente y un usuario con permiso para crear
bases, conectarse a la base de mantenimiento y crear los objetos del dump. Instale
las extensiones necesarias en el servidor destino y revise sus versiones,
bibliotecas, locales, encoding y collations. `template0` hereda los valores de
encoding/locale del cluster destino; prepare un cluster compatible con el origen.
No se concede automáticamente el rol de superusuario ni se instalan extensiones
del sistema desde dbkeeper.

El perfil `[profiles.prueba]` debe usar `purpose = "restore"`. Su `database` es
la base de mantenimiento existente, habitualmente `postgres`. Configure
autenticación tanto para esa conexión como para la futura base de pruebas.

```bash
dbkeeper --config config/profiles.toml doctor --profile prueba
BACKUP_DIR="$HOME/dbkeeper-backups/ID_DEVUELTO_POR_BACKUP"
dbkeeper --config config/profiles.toml restore "$BACKUP_DIR" \
  --target-profile prueba --database dbkeeper_prueba_001
```

El comando exige perfil de destino y nombre literal de base. Rechaza usar el
perfil origen y rechaza coincidir en host, puerto y base del manifiesto. Normaliza
mayúsculas del hostname, punto final y representación de IP; **no detecta todos
los alias DNS**, túneles, proxies o balanceadores que lleven al mismo servidor.
Compruebe el destino real al configurar el perfil.

Antes de crear la base, se comprueban versiones y se ejecuta una verificación
completa del archivo. Después se emite `CREATE DATABASE` con identificador citado
y `TEMPLATE template0`; si el nombre ya existe, PostgreSQL lo rechaza. La CLI no
ofrece sobrescribirlo. `pg_restore` se conecta explícitamente al nombre nuevo con
`--exit-on-error`, sin `--create`, `--clean` ni `DROP`. Este procedimiento evita
que `--create` restaure en el nombre original almacenado en el archivo. Véase el
[ejemplo oficial de restauración en otra base](https://www.postgresql.org/docs/18/app-pgrestore.html).

Por defecto se aplican `--no-owner --no-privileges --no-tablespaces`. Los objetos
creados pertenecerán al usuario de restauración; no se reproducen los GRANT/REVOKE
originales ni las asignaciones originales de tablespaces. Esto facilita una prueba
local y **no valida** la equivalencia de propietarios, autorización o distribución
física respecto al origen. Puede conservarlos explícitamente:

```bash
dbkeeper --config config/profiles.toml restore "$BACKUP_DIR" \
  --target-profile prueba --database dbkeeper_prueba_002 \
  --keep-ownership --keep-tablespaces
```

En ese caso, prepare antes los roles, privilegios y tablespaces requeridos. Incluso
omitiendo propietarios, otros objetos como políticas RLS pueden referenciar roles
que deben existir. Los objetos globales no se crean desde este flujo. Las opciones
se describen en [`pg_restore`](https://www.postgresql.org/docs/16/app-pgrestore.html).

Una restauración ejecuta código almacenado en el dump: use respaldos de un origen
confiable en un entorno de pruebas con permisos y conexiones adecuados. La prueba
no es una evaluación funcional automática de su aplicación.

El reporte está en `restores/ID/manifest.json`; registra el destino, opciones,
versiones, inicio/fin UTC, verificación asociada y cada fase. `database_creation`
distingue `not_attempted`, `attempted` y `created`. Si una conexión se interrumpe
al crear la base, `attempted` requiere inspeccionar el servidor para saber qué
ocurrió. Si fallan extensiones, datos o metadatos finales, el reporte y marcador
indican fallo y **no se elimina automáticamente ninguna base**.

Revise el log y conecte explícitamente al destino para inspeccionar lo creado:

```bash
psql -X -w -h 127.0.0.1 -p 5432 -U usuario_pruebas -d dbkeeper_prueba_001
```

Dentro de `psql`, use `\dt`, `\dx` y consultas de invariantes de su aplicación.
La limpieza de una base fallida queda a cargo de su administrador, después de
confirmar host y nombre. Reintente con un nombre nuevo. No compare conteos con
un origen que sigue cambiando como si fueran una comprobación exacta del snapshot.
Valide relaciones, restricciones, balances y otras reglas internas de los datos
restaurados; los tests sintéticos del proyecto muestran ese enfoque.

