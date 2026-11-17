# Seguridad

## Principios
- Datos judiciales sensibles: acceso local, mínimo privilegio y trazabilidad.
- Secretos solo en `.env` local (no versionar, permisos 600). Usuario de BD dedicado; rotar credenciales administrativas.
- Datos y expedientes fuera de `htdocs`; no exponer por Apache.

## Línea base Fase 0 (según bitácora)
- Cifrado en reposo (LUKS) para datos/secretos.
- Firewall auditado; Apache/FTP restringidos; Uvicorn/Ollama/MariaDB sin exposición de red.
- ClamAV para cargas/descargas y cuarentena.
- Login usuario único y auditoría (`login_attempts`, `security_events`).

## IA y documentos
- Documentos tratados como contenido no confiable (prompt injection).
- Motor local sin APIs externas/telemetría; salida validada y revisión humana antes de guardar.

## Estado Fase 0
- LUKS activo con frase, clave de recuperación y cabecera respaldada en medio externo con checksum.
- UFW con entrada denegada; Apache solo en `127.0.0.1`; FTP detenido; Avahi deshabilitado; ClamAV operativo.
- MariaDB: `secopsentinel_app` solo DML y `secopsentinel_migrate` solo para Alembic; `.env` 600 dentro del LUKS.
- Datadir de MariaDB trasladado a LUKS (`/opt/lampp/var/mysql` -> `~/.local/share/secopsentinel/mysql`); consulta de la aplicación y `@@datadir` verificados. Copia sin cifrar eliminada.
- Login de la aplicación verificado con el datadir trasladado y auditoría en `login_attempts` y `audit_log`.
- phpMyAdmin (Apache, usuario `daemon`) accede al socket con ACL de tránsito; no tiene lectura ni listado del datadir. El endurecimiento del datadir a `711` queda pendiente de `sudo`.
- XAMPP sin arranque automático; tras reiniciar, desbloquear LUKS y ejecutar `sudo /opt/lampp/lampp start`.

## Pendientes
- Confirmar el acceso a phpMyAdmin después de la ACL; si falla, revisar `mysqli.default_socket` y los logs de Apache.
- Endurecer el datadir a `711` con `sudo` (opcional, no bloquea el acceso).
- Respaldo externo diferido, sin fecha. No se define proceso, herramienta ni destino hasta acordarlo con IT/cliente; restic es solo una recomendación.
