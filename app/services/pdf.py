import subprocess
from pathlib import Path

from pypdf import PdfReader


def extract_text_with_pdftotext(path: Path, timeout: int = 60) -> str:
    result = subprocess.run(
        ["pdftotext", "-layout", str(path), "-"],
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.stdout


def extract_text_with_pypdf(path: Path) -> str:
    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def extract_text(path: Path) -> str:
    try:
        text = extract_text_with_pdftotext(path)
    except (OSError, subprocess.SubprocessError):
        text = ""
    if text.strip():
        return text
    return extract_text_with_pypdf(path)
