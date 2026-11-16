# secopsentinel

Plataforma local de apoyo a investigación judicial y contratación estatal.

## Estado
- Fase 0 completada: LUKS montado con clave de recuperación, firewall activo, Apache en loopback, FTP detenido, Avahi deshabilitado, ClamAV operativo, MariaDB con usuarios DML/migración, login de usuario único y auditoría real verificada.
- Pendiente: decidir si el datadir de MariaDB se mueve al volumen LUKS y definir respaldos con IT/cliente.
- Fuente de verdad operativa: `_fix/bitacora.html`.

## Estructura
- `app/`: aplicación (config, base de datos, autenticación, routers, servicios, plantillas y estáticos).
- `migrations/`: migraciones Alembic.
- `scripts/`: arranque, creación de usuario, línea base y verificación de seguridad.
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
