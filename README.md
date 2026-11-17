# secopsentinel

Plataforma local de apoyo a investigación judicial y contratación estatal.

## Estado
- Fase 0: línea base completada (LUKS, firewall, Apache en loopback, FTP detenido, Avahi deshabilitado, ClamAV, MariaDB con usuarios DML/migración, login y auditoría verificados).
- Respaldo externo: diferido, sin fecha; no se define proceso hasta acordarlo con IT/cliente.
- Herramienta de respaldo recomendada: restic; no se instala hasta definir el proceso y el destino.
- Fuente de verdad operativa: `_fix/bitacora.html`.

## Estructura
- `app/`: aplicación (config, base de datos, autenticación, routers, servicios, plantillas y estáticos).
- `migrations/`: migraciones Alembic.
- `scripts/`: arranque, creación de usuario, línea base, verificación de seguridad y traslado de MariaDB a LUKS.
- `tests/`: pruebas con SQLite aislado.
- `_fix/`: bitácora y notas de trabajo.

## Entorno
- Python 3.12 con `.venv` local.
- Dependencias fijadas en `requirements.txt`.

```bash
.venv/bin/python -m pip install -r requirements.txt
```

## Configuración
1. Ejecutar `sudo scripts/security_baseline.sh` para crear el contenedor LUKS y restringir servicios.
2. Para una instalación nueva, crear `~/.config/secopsentinel/.env` a partir de `.env.example` y ajustar valores.
3. Guardar secretos solo en `.env` con permisos 600; no versionarlo.
4. Mantener `DATA_DIR` dentro de `~/.local/share/secopsentinel` (volumen LUKS).
5. Usar `secopsentinel_app` para la aplicación y `secopsentinel_migrate` solo para Alembic; no usar `root`.
6. Verificar que la cabecera LUKS tenga checksum y que existan al menos dos ranuras de clave.

Desbloqueo manual tras reiniciar:

```bash
sudo cryptsetup open ~/.local/share/secopsentinel.luks secopsentinel-data
sudo mount /dev/mapper/secopsentinel-data ~/.local/share/secopsentinel
```

Si al crear el contenedor se omitió la recuperación:

```bash
sudo cryptsetup luksAddKey ~/.local/share/secopsentinel.luks
sudo cryptsetup luksHeaderBackup ~/.local/share/secopsentinel.luks \
  --header-backup-file /medio/externo/secopsentinel-luks-header.img
sha256sum /medio/externo/secopsentinel-luks-header.img > \
  /medio/externo/secopsentinel-luks-header.sha256
```
Guardar la clave de recuperación y la cabecera fuera del equipo. Verificar con `sha256sum -c`.

## Base de datos
Alembic usa `DB_MIGRATION_USER` y `DB_MIGRATION_PASSWORD`; la aplicación usa `DB_USER` y `DB_PASSWORD`.

```bash
.venv/bin/alembic upgrade head
.venv/bin/python scripts/create_user.py --username maudaemon
```
La contraseña debe tener al menos 12 caracteres. El script no imprime ni almacena la contraseña en el repositorio.

## MariaDB dentro del volumen LUKS
El datadir está trasladado y verificado: `/opt/lampp/var/mysql` es un enlace a `~/.local/share/secopsentinel/mysql`, dentro del volumen LUKS. La aplicación consulta MariaDB con ese datadir y `@@datadir` responde correctamente. La copia sin cifrar se eliminó.

El procedimiento queda documentado en `scripts/move_mariadb_to_luks.sh`; detiene aplicación y MariaDB, copia y verifica, reemplaza el datadir por el enlace y restaura el original si falla.

phpMyAdmin (Apache, usuario `daemon`) accede al socket mediante ACL de tránsito; no tiene lectura ni listado del datadir. El endurecimiento del datadir a `711` queda pendiente de `sudo` y no bloquea el acceso.

XAMPP ya no arranca solo (`xampp.service` deshabilitado). Tras reiniciar, desbloquear y montar LUKS antes de iniciar XAMPP:

```bash
sudo /opt/lampp/lampp start
```

## Respaldos
Respaldo externo diferido, sin fecha. Herramienta recomendada: restic, pero no se instala ni se define proceso/destino hasta acordarlo con IT/cliente. El USB actual de 3.8 GB solo contiene la cabecera LUKS y no alcanza para el volumen de 50 GB.

## Ejecución local
```bash
scripts/run.sh
```
La aplicación escucha solo en `http://127.0.0.1:8000`.

## Pruebas y lint
```bash
.venv/bin/ruff check .
.venv/bin/python -m pytest -q
```

## Seguridad
- Sin APIs externas para el motor local; los documentos no salen del equipo.
- Ver `SECURITY.md` y `_fix/bitacora.html`.
- Línea base interactiva: `sudo scripts/security_baseline.sh`.
- Auditoría de servicios: `sudo scripts/security_check.sh`.
