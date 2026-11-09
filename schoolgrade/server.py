# -*- coding: utf-8 -*-
"""HTTP-сервер SchoolGrade на стандартной библиотеке Python (без зависимостей)."""
import json
import mimetypes
import os
import re
import socket
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs, quote

from . import auth


class ApiError(Exception):
    def __init__(self, status, message):
        Exception.__init__(self, message)
        self.status = status
        self.message = message


class FileResponse(object):
    def __init__(self, data, filename, ctype):
        self.data, self.filename, self.ctype = data, filename, ctype


class Request(object):
    def __init__(self, method, path, query, body, user, token, ip, headers):
        self.method, self.path, self.query, self.body = method, path, query, body
        self.user, self.token, self.ip, self.headers = user, token, ip, headers

    def arg(self, name, default=None, cast=None):
        v = self.query.get(name, [default])[0]
        if v in (None, "") and default is None:
            return None
        if cast and v not in (None, ""):
            try:
                return cast(v)
            except ValueError:
                raise ApiError(400, "Неверный параметр " + name)
        return v

    def json(self):
        if not self.body:
            return {}
        try:
            return json.loads(self.body.decode("utf-8"))
        except ValueError:
            raise ApiError(400, "Неверный формат данных")


class Router(object):
    def __init__(self):
        self.routes = []

    def add(self, method, pattern, func, public=False):
        rx = re.compile("^" + re.sub(r":(\w+)", r"(?P<\1>[^/]+)", pattern) + "$")
        self.routes.append((method, rx, func, public))

    def route(self, method, pattern, public=False):
        def deco(f):
            self.add(method, pattern, f, public)
            return f
        return deco

    def match(self, method, path):
        allowed = False
        for m, rx, f, public in self.routes:
            mt = rx.match(path)
            if mt:
                if m == method:
                    return f, mt.groupdict(), public
                allowed = True
        if allowed:
            raise ApiError(405, "Метод не поддерживается")
        return None, None, None


class ThreadingServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def make_handler(app):
    static_dir = app.static_dir

    class Handler(BaseHTTPRequestHandler):
        server_version = "SchoolGrade"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            if app.verbose:
                sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

        def _cookie(self, name):
            raw = self.headers.get("Cookie", "")
            for part in raw.split(";"):
                if "=" in part:
                    k, v = part.strip().split("=", 1)
                    if k == name:
                        return v
            return None

        def _send(self, status, body, ctype="application/json; charset=utf-8", extra=None):
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "SAMEORIGIN")
            self.send_header("Referrer-Policy", "same-origin")
            for k, v in (extra or []):
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status, obj, extra=None):
            self._send(status, json.dumps(obj, ensure_ascii=False), extra=extra)

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def do_PUT(self):
            self._dispatch("PUT")

        def do_DELETE(self):
            self._dispatch("DELETE")

        def _static(self, path):
            if path in ("/", ""):
                path = "/index.html"
            rel = os.path.normpath(path.lstrip("/")).replace("\\", "/")
            if rel.startswith(".."):
                return self._send(404, "Not found", "text/plain")
            full = os.path.join(static_dir, rel)
            if not os.path.isfile(full):
                full = os.path.join(static_dir, "index.html")
            ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript",):
                ctype += "; charset=utf-8"
            if full.endswith(".js"):
                ctype = "application/javascript; charset=utf-8"
            with open(full, "rb") as f:
                data = f.read()
            self._send(200, data, ctype, [("Cache-Control", "no-cache")])

        def _dispatch(self, method):
            url = urlparse(self.path)
            path = url.path
            if not path.startswith("/api/"):
                if method != "GET":
                    return self._send(405, "Method not allowed", "text/plain")
                return self._static(path)
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length > 60 * 1024 * 1024:
                    raise ApiError(413, "Слишком большой запрос")
                body = self.rfile.read(length) if length else b""
                func, params, public = app.router.match(method, path)
                if not func:
                    raise ApiError(404, "Не найдено")
                # защита от CSRF: изменяющие запросы только из нашего интерфейса
                if method != "GET" and self.headers.get("X-SchoolGrade") != "1":
                    raise ApiError(403, "Запрос отклонён")
                token = self._cookie("sg_session")
                ip = self.client_address[0]
                user = auth.session_user(app.db, token)
                if not public and not user:
                    raise ApiError(401, "Требуется вход в систему")
                if user and user.get("must_change") and not public and path not in ("/api/me", "/api/me/password", "/api/logout"):
                    raise ApiError(403, "Необходимо сменить пароль")
                guard = getattr(app, "guard", None)
                if guard and path not in ("/api/me", "/api/features"):
                    guard(path)
                req = Request(method, path, parse_qs(url.query), body, user, token, ip, self.headers)
                res = func(req, **params)
                if isinstance(res, FileResponse):
                    disp = "attachment; filename*=UTF-8''" + quote(res.filename)
                    return self._send(200, res.data, res.ctype, [("Content-Disposition", disp)])
                extra = None
                if isinstance(res, tuple):
                    res, extra = res
                self._json(200, res if res is not None else {"ok": True}, extra)
            except ApiError as e:
                self._json(e.status, {"error": e.message})
            except ValueError as e:
                self._json(400, {"error": str(e)})
            except Exception as e:  # noqa
                traceback.print_exc()
                self._json(500, {"error": "Внутренняя ошибка: " + str(e)})

    return Handler


def local_addresses():
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except socket.error:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except socket.error:
        pass
    ips.discard("127.0.0.1")
    return sorted(ips)


class ServerThread(threading.Thread):
    def __init__(self, app, host, port):
        threading.Thread.__init__(self)
        self.daemon = True
        self.httpd = ThreadingServer((host, port), make_handler(app))

    def run(self):
        self.httpd.serve_forever(poll_interval=0.5)

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
