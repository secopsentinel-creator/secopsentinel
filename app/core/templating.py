from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates
from starlette.responses import HTMLResponse, Response

from app.auth import get_or_create_csrf_token

APP_DIR = Path(__file__).resolve().parent.parent

templates = Jinja2Templates(directory=str(APP_DIR / "templates"))


def render_page(
    request: Request,
    name: str,
    context: dict | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    cookie_response = Response()
    csrf_token = get_or_create_csrf_token(request, cookie_response)
    template = templates.get_template(name)
    values = {"request": request, "csrf_token": csrf_token}
    if context:
        values.update(context)
    content = template.render(**values)
    response = HTMLResponse(content=content, status_code=status_code)
    for header in cookie_response.headers.getlist("set-cookie"):
        response.headers.append("set-cookie", header)
    return response
