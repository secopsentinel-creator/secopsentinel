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
- phpMyAdmin (Apache, usuario `daemon`) accede al socket con ACL de tránsito y login confirmado; no tiene lectura ni listado del datadir. El endurecimiento del datadir a `711` queda pendiente de `sudo`.
- XAMPP sin arranque automático; tras reiniciar, desbloquear LUKS y ejecutar `sudo /opt/lampp/lampp start`.

## Fase 1 · Documentos
- NUNC validado con patrón seguro y rutas resueltas dentro de `CASES_DIR`; se rechaza path traversal.
- Binarios fuera de `htdocs`, directorios `700` y archivos `600`; la BD guarda ruta relativa, SHA-256, MIME, tamaño y metadatos.
- Deduplicación por SHA-256: el binario se guarda una vez y se enlaza al expediente con enlace duro.
- Validación de extensión y firma de contenido (PDF, DOCX, XLSX), límite de tamaño y escaneo con ClamAV.
- Cuarentena para malware y rechazo cerrado si el antivirus falla; eventos en `security_events` y `audit_log`.
- Descarga solo autenticada, con auditoría; nunca se expone el almacén por `/static` ni por Apache.

## Pendientes
- Endurecer el datadir a `711` con `sudo` (opcional, no bloquea el acceso).
- Validar manualmente la interfaz de casos y la carga de documentos con datos sintéticos desde el navegador.
- Respaldo externo diferido, sin fecha. No se define proceso, herramienta ni destino hasta acordarlo con IT/cliente; restic es solo una recomendación.
