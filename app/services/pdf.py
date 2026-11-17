import json
import subprocess
import sys
from pathlib import Path

MAX_PDF_PAGES = 200


class PdfPageLimitError(ValueError):
    pass


class PdfReadError(ValueError):
    pass


def extract_text_with_pdftotext(path: Path, timeout: int = 60) -> str:
    result = subprocess.run(
        ["pdftotext", "-layout", str(path), "-"],
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.stdout


def extract_text_pages(path: Path, timeout: int = 60) -> list[str]:
    try:
        text = extract_text_with_pdftotext(path, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        text = ""

    if text.strip():
        pages = text.split("\f")
        while pages and not pages[-1].strip():
            pages.pop()
        if len(pages) > MAX_PDF_PAGES:
            raise PdfPageLimitError(f"El PDF supera el límite de {MAX_PDF_PAGES} páginas.")
        return [page.strip() for page in pages]

    try:
        pages = _extract_with_pypdf_subprocess(path, timeout)
    except PdfPageLimitError:
        raise
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as exc:
        raise PdfReadError("No se pudo leer el contenido del PDF.") from exc
    if len(pages) > MAX_PDF_PAGES:
        raise PdfPageLimitError(f"El PDF supera el límite de {MAX_PDF_PAGES} páginas.")
    return pages


def extract_text_with_pypdf(path: Path) -> str:
    return "\n".join(_extract_with_pypdf_subprocess(path, timeout=60))


def _extract_with_pypdf_subprocess(path: Path, timeout: int) -> list[str]:
    result = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("pdf_worker.py")), str(path)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
    )
    if result.returncode == 2:
        raise PdfPageLimitError(f"El PDF supera el límite de {MAX_PDF_PAGES} páginas.")
    if result.returncode != 0:
        raise PdfReadError("No se pudo leer el contenido del PDF.")
    payload = json.loads(result.stdout)
    pages = payload["pages"]
    if not isinstance(pages, list) or any(not isinstance(page, str) for page in pages):
        raise ValueError("El resultado de lectura del PDF no es válido.")
    return pages


def extract_text(path: Path) -> str:
    return "\n".join(extract_text_pages(path))
