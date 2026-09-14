# Preparación de Ubuntu en WSL

1. Si WSL aún no está instalado, abra PowerShell como administrador y ejecute:

   ```powershell
   wsl --install -d Ubuntu
   ```

   Reinicie si se solicita y cree su usuario Linux. Abra Ubuntu. Consulte la
   [instalación oficial de WSL](https://learn.microsoft.com/en-us/windows/wsl/install)
   si su versión de Windows necesita pasos adicionales.

2. Prepare Python y las utilidades básicas dentro de Ubuntu:

   ```bash
   sudo apt update
   sudo apt install python3 python3-venv python3-pip ca-certificates
   python3 --version
   ```

   Se requiere Python 3.11 o posterior. En Ubuntu 24.04, por ejemplo, Python 3.12
   cumple el requisito. Si `python3` es anterior, use una distribución Ubuntu más
   reciente o una instalación local de Python 3.11+; no sustituya el Python del
   sistema manualmente.

3. Solicite la versión mayor del PostgreSQL de origen a su administrador. `16`
   es solamente un ejemplo en esta guía. Instale esa versión de los **clientes**:

   ```bash
   sudo apt install postgresql-client-16
   /usr/lib/postgresql/16/bin/pg_dump --version
   /usr/lib/postgresql/16/bin/pg_restore --version
   /usr/lib/postgresql/16/bin/psql --version
   ```

   Si Ubuntu no ofrece la versión necesaria, habilite el repositorio oficial PGDG
   mediante el procedimiento publicado por PostgreSQL:

   ```bash
   sudo apt install postgresql-common
   sudo /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh
   sudo apt update
   sudo apt install postgresql-client-16
   ```

   El repositorio proporciona paquetes por versión; confirme que su Ubuntu esté
   admitido en la [guía oficial de paquetes](https://www.postgresql.org/download/linux/ubuntu/).
   Para respaldar el servidor remoto solo hacen falta clientes. Las pruebas de
   integración opcionales requieren además los binarios del servidor, por ejemplo
   el paquete `postgresql-16`, instalados en un ambiente de desarrollo desechable.

4. Instale el proyecto en un entorno virtual:

   ```bash
   cd /mnt/g/dev/dbkeeper
   python3 -m venv .venv
   source .venv/bin/activate
   python -m pip install .
   dbkeeper --help
   ```

   Para desarrollar puede usar `python -m pip install -e .`. El código puede estar
   en `/mnt/g`, pero configure los backups y credenciales bajo el filesystem Linux,
   por ejemplo `~/dbkeeper-backups`, no bajo `/mnt/c` o `/mnt/g`. Allí funcionan los
   permisos POSIX que verifica la aplicación. Revise espacio disponible con
   `df -h "$HOME"`; el tamaño final depende de datos y compresión.

5. Cree su configuración, conservando cualquier archivo real ya existente:

   ```bash
   cp -n config/profiles.example.toml config/profiles.toml
   chmod 600 config/profiles.toml
   nano config/profiles.toml
   ```

   Complete host VPN, puerto, base, usuario, directorio y rutas de los tres
   clientes. Configure `prueba` con un servidor destinado a pruebas; su campo
   `database` es la base de mantenimiento, normalmente `postgres`.
   No introduzca contraseñas ni URI de conexión. Las rutas admiten `~`; las rutas
   relativas se resuelven respecto al TOML, sin expansión de variables `$...`.
   Se admite un solo host DNS/IP por perfil. Los nombres de base y usuario son
   literales de hasta 63 bytes, sin caracteres de control, `=`, `:`, `/`, `\`
   ni guion inicial; no se aceptan cadenas de conexión reinterpretables por libpq.

   El ejemplo usa `sslmode = "verify-full"` y espera una CA en
   `~/.postgresql/root.crt`. Obtenga la CA del administrador y compruebe que el
   hostname coincida con el certificado. Si la autenticación exige certificado
   de cliente, configure juntos `sslcert` y `sslkey`; la clave debe tener modo
   `0600`. `disable` en el perfil de prueba es un ejemplo para loopback local.
   Los modos SSL aceptados son `disable`, `allow`, `prefer`, `require`,
   `verify-ca` y `verify-full`; seleccione el exigido por el servidor.

6. Configure autenticación sin poner la contraseña en comandos o historial:

   ```bash
   umask 077
   touch ~/.pgpass
   chmod 600 ~/.pgpass
   nano ~/.pgpass
   ```

   En el editor, use el formato `host:puerto:base:usuario:contraseña`. El host debe
   coincidir con el perfil. Para pruebas agregue entradas para `postgres` y la
   nueva base, o un comodín de base limitado al host y usuario de pruebas.
   Escape `:` y `\` con `\`; ponga entradas específicas antes de comodines.
   También se admite `export PGPASSFILE="$HOME/.config/dbkeeper/pgpass"` con un
   archivo existente de modo `0600`.
   Estas reglas siguen la [documentación oficial de .pgpass](https://www.postgresql.org/docs/18/libpq-pgpass.html).

   No use `PGPASSWORD`: dbkeeper lo rechaza al conectar. Se limpia el resto del
   entorno `PG*` para impedir que servicios, opciones o bases heredadas cambien
   el destino. `PGPASSFILE` se incorpora explícitamente. Todos los clientes usan
   `--no-password` y entrada estándar cerrada: si faltan credenciales, fallan
   sin esperar interacción. El ejemplo no instala ni modifica credenciales reales.

7. Conecte la VPN con su cliente habitual y ejecute desde Ubuntu:

   ```bash
   dbkeeper --config config/profiles.toml doctor --profile origen
   ```

   `doctor` comprueba una conexión autenticada usando `psql`, la versión del
   servidor, herramientas, SSL configurado y escritura local. No modifica datos
   de la base. Su éxito no garantiza privilegios para todos los objetos ni espacio
   suficiente para el dump completo. Continúe con [uso](uso.md).
