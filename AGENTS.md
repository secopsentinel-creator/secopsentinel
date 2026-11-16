# AGENTS.md

## Proyecto
secopsentinel (Fase 0 en preparación). Fuente de verdad operativa: `_fix/bitacora.html`.

## Reglas
- No registrar secretos, credenciales ni datos judiciales en el repositorio o documentación.
- `.env` solo local (no versionar). Usar usuario dedicado de MariaDB, no `root`.
- Datos/expedientes fuera de `htdocs`; rutas relativas/configurables en BD.
- Motor local sin Copilot externo, sin APIs, sin telemetría; búsqueda por caso (Módulo 17) y todo el Código Penal.
- No ejecutar instalaciones/comandos privilegiados sin autorización explícita.

## Desarrollo
- Backend: Python 3.12, FastAPI, Uvicorn, SQLAlchemy, Alembic, Jinja2, HTMX.
- BD: MariaDB `secopsentinel`.
- Pruebas/lint: pytest, ruff (cuando el entorno esté preparado).
