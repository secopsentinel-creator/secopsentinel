from __future__ import annotations

import json
import resource
import sys
from pathlib import Path

MAX_PAGES = 200
MAX_TEXT_CHARS = 1_000_000
MAX_ADDRESS_SPACE = 512 * 1024 * 1024


def _set_resource_limit(resource_id: int, requested: int) -> None:
    _, hard_limit = resource.getrlimit(resource_id)
    limit = requested if hard_limit == resource.RLIM_INFINITY else min(requested, hard_limit)
    resource.setrlimit(resource_id, (limit, limit))


def main() -> int:
    _set_resource_limit(resource.RLIMIT_CPU, 45)
    _set_resource_limit(resource.RLIMIT_AS, MAX_ADDRESS_SPACE)

    from pypdf import PdfReader

    path = Path(sys.argv[1])
    reader = PdfReader(str(path), strict=False)
    if reader.is_encrypted:
        raise ValueError("encrypted PDF")
    if len(reader.pages) > MAX_PAGES:
        return 2

    pages: list[str] = []
    total_chars = 0
    for page in reader.pages:
        text = page.extract_text() or ""
        remaining = MAX_TEXT_CHARS - total_chars
        pages.append(text[:remaining])
        total_chars += min(len(text), remaining)
        if total_chars >= MAX_TEXT_CHARS:
            break

    print(json.dumps({"pages": pages}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        exit_code = main()
    except Exception:
        print("PDF text extraction failed", file=sys.stderr)
        exit_code = 1
    raise SystemExit(exit_code)
