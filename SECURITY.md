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
- Login real verificado con auditoría en `login_attempts` y `audit_log`.

## Pendientes
- Decidir si el datadir de MariaDB (`/opt/lampp/var/mysql`) se mueve al volumen LUKS antes de cargar expedientes.
- Definir herramienta de respaldo (restic/borg) y destino externo con IT y cliente (ver `_fix/bitacora.html`).
