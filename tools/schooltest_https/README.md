# HTTPS-сертификат школы в SchoolTest

Сертификат SchoolGrade 1.5 подходит и для SchoolTest: формат один (PEM, корневой сертификат школы + сертификат сервера),
порт в сертификат не входит, поэтому один и тот же сертификат работает для `:8080` и `:8081`.

## Вариант 1. SchoolTest на том же компьютере, что и SchoolGrade
Ничего устанавливать на ПК пользователей повторно не нужно: корневой сертификат уже в «Доверенных корневых».
Имя компьютера в сертификате сервера то же, значит `https://ИМЯ-КОМПЬЮТЕРА:8081` будет считаться доверенным,
как только SchoolTest начнёт отдавать этот сертификат (см. ниже).

## Вариант 2. SchoolTest на другом сервере школы
1. SchoolGrade → Администрирование → «HTTPS и адрес» → «Экспорт с ключами» → файл `SchoolCert_ГГГГММДД.zip`.
2. Перенесите ZIP на сервер SchoolTest, распакуйте `schoolca.crt`, `schoolca.key` (и при необходимости `server.crt`/`server.key`)
   в папку `<данные SchoolTest>\certs\`.
3. Выпустите сертификат сервера для этого компьютера (имя ПК берётся автоматически):
   `python cert.py "<данные SchoolTest>\certs" --years 0`
   (команда создаёт недостающий корневой сертификат; если `schoolca.crt/key` уже лежат в папке, они используются).
   Корневой сертификат тот же → на ПК пользователей ничего переустанавливать не нужно.

## Подключение к серверу SchoolTest (3 небольшие правки)
Скопируйте `cert.py` в пакет `schooltest/` (рядом с `server.py`) и внесите правки.

**schooltest/server.py** — в `ThreadingServer` добавьте приём TLS на том же порту (как в SchoolGrade 1.5, `schoolgrade/server.py`):
```python
import ssl                                   # в начало файла

class ThreadingServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128
    ssl_context = None
    https_enabled = False

    def finish_request(self, request, client_address):
        try:
            request.settimeout(15)
            first = request.recv(1, socket.MSG_PEEK)
        except OSError:
            return
        if not first:
            return
        wrapped = None
        if first == b"\x16":
            if not self.ssl_context or not self.https_enabled:
                return
            try:
                request = wrapped = self.ssl_context.wrap_socket(request, server_side=True)
            except (ssl.SSLError, OSError):
                return
        try:
            request.settimeout(None)
            self.RequestHandlerClass(request, client_address, self)
        finally:
            if wrapped is not None:
                try:
                    wrapped.close()
                except OSError:
                    pass
```
**schooltest/main.py** — после создания `srv = ServerThread(...)`:
```python
from . import cert
store = cert.CertStore(os.path.join(data_dir, "certs"))
if store.has_server():
    srv.httpd.ssl_context = store.ssl_context()
    srv.httpd.https_enabled = True
```
Адрес по имени компьютера: `http://ИМЯ:8081` и `https://ИМЯ:8081`.
