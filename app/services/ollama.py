import httpx

from app.core.config import get_settings


def check_ollama(timeout: float = 5.0) -> bool:
    settings = get_settings()
    try:
        response = httpx.get(f"{settings.ollama_base_url}/api/tags", timeout=timeout)
        response.raise_for_status()
        return True
    except httpx.HTTPError:
        return False
