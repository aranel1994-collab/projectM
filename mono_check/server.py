"""Вебдемо одного запуску. Завантаження тримаються в пам’яті запиту."""

import argparse
import base64
import binascii
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import urlparse

from .analysis import analyze
from .extraction import ExtractionError, api_key
from .pdf import DocumentError, MAX_BYTES


ROOT = Path(__file__).resolve().parent.parent
WEB = Path(__file__).with_name("web")
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}
MAX_BODY = ((MAX_BYTES + 2) // 3) * 4 + 4096


def examples():
    return json.loads((ROOT / "output" / "examples.json").read_text(encoding="utf-8"))


class Handler(BaseHTTPRequestHandler):
    server_version = "mono-check"

    def send_content(self, status, content, media_type):
        self.send_response(status)
        self.send_header("Content-Type", media_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(content)

    def send_json(self, status, obj):
        self.send_content(status, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def log_message(self, format_string, *arguments):
        pass  # Документи, імена файлів і параметри кейсів не потрапляють у лог.

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ASSETS:
            file, media_type = ASSETS[path]
            self.send_content(HTTPStatus.OK, (WEB / file).read_bytes(), media_type)
        elif path == "/favicon.ico":
            self.send_content(HTTPStatus.NO_CONTENT, b"", "image/x-icon")
        elif path == "/api/config":
            self.send_json(HTTPStatus.OK, {"examples": examples(), "live_available": bool(api_key())})
        else:
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "Сторінку не знайдено."})

    def do_POST(self):
        if urlparse(self.path).path != "/api/analyze":
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "Сторінку не знайдено."})
            return
        if self.headers.get_content_type() != "application/json":
            self.send_json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "Потрібен запит із PDF у JSON."})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= MAX_BODY:
                raise DocumentError("Завантажте PDF розміром до 8 МБ.")
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise DocumentError("Некоректні параметри запиту.")
            mode = body.get("mode", "local")
            if not isinstance(mode, str):
                raise DocumentError("Виберіть доступний режим запуску.")
            if body.get("example"):
                example = next((item for item in examples() if item["id"] == body["example"]), None)
                if example is None:
                    raise DocumentError("Приклад не знайдено.")
                data = (ROOT / "output" / "pdf" / (example["pdf"] + ".pdf")).read_bytes()
            else:
                data = base64.b64decode(body.get("pdf", ""), validate=True)
            report = analyze(data, mode, body.get("claimed_code", ""), body.get("date"))
        except (DocumentError, ExtractionError) as error:
            self.send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(error)})
            return
        except (ValueError, TypeError, UnicodeError, binascii.Error):
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "Запит не містить коректного PDF або параметрів."})
            return
        except (RuntimeError, OSError):
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Не вдалося обробити PDF. Спробуйте інший файл."})
            return
        self.send_json(HTTPStatus.OK, report)


def main():
    parser = argparse.ArgumentParser(description="Демо попередньої перевірки виписки з ЄДР.")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    with ThreadingHTTPServer(("127.0.0.1", args.port), Handler) as server:
        print(f"Демо відкривається за адресою http://127.0.0.1:{args.port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
