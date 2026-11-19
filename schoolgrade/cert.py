# -*- coding: utf-8 -*-
"""Собственный сертификат безопасности школы (HTTPS) — на чистой стандартной библиотеке Python.

Модуль самодостаточен (не импортирует остальные модули SchoolGrade) и одинаков в SchoolGrade и
SchoolTest: файл можно скопировать в другую программу без изменений. Формат хранения общий:

  <папка>/schoolca.crt  — корневой сертификат школы (PEM) — его устанавливают на ПК в «Доверенные корневые»
  <папка>/schoolca.key  — закрытый ключ корневого сертификата (PEM, PKCS#1) — нужен, чтобы выпускать
                          сертификаты для других серверов школы; хранить в тайне
  <папка>/server.crt    — сертификат сервера (PEM): имя компьютера, IP-адреса, localhost
  <папка>/server.key    — закрытый ключ сервера (PEM, PKCS#1)
  <папка>/meta.json     — служебные сведения (срок действия, дата выпуска)

Схема: один корневой сертификат школы (устанавливается на ПК один раз) + сертификат сервера,
который можно перевыпускать при смене имени/адреса компьютера, не трогая ПК пользователей.
Один и тот же комплект (ZIP) можно импортировать в SchoolGrade и SchoolTest на одном или
разных серверах школы. Порт в сертификат не входит, поэтому он подходит для :8080 и :8081.
"""
import base64
import datetime as DT
import hashlib
import io
import json
import math
import os
import re
import socket
import ssl
import time
import zipfile

CA_CRT, CA_KEY, SRV_CRT, SRV_KEY, META = "schoolca.crt", "schoolca.key", "server.crt", "server.key", "meta.json"
RSA_BITS = 2048
UNLIMITED = DT.datetime(9999, 12, 31, 23, 59, 59)   # RFC 5280: «срок действия не ограничен»

OID_RSA = "1.2.840.113549.1.1.1"
OID_SHA256_RSA = "1.2.840.113549.1.1.11"
OID_CN, OID_ORG = "2.5.4.3", "2.5.4.10"
OID_SKI, OID_KU, OID_SAN, OID_BC, OID_AKI, OID_EKU = "2.5.29.14", "2.5.29.15", "2.5.29.17", "2.5.29.19", "2.5.29.35", "2.5.29.37"


class CertError(Exception):
    pass


# ------------------------------------------------------------------ DER
def _len(n):
    if n < 0x80:
        return bytes([n])
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(b)]) + b


def _tlv(tag, content):
    return bytes([tag]) + _len(len(content)) + content


def _int(n):
    return _tlv(0x02, n.to_bytes((n.bit_length() + 8) // 8, "big"))


def _oid(s):
    p = [int(x) for x in s.split(".")]
    out = bytearray([40 * p[0] + p[1]])
    for v in p[2:]:
        chunk = [v & 0x7F]
        v >>= 7
        while v:
            chunk.append(0x80 | (v & 0x7F))
            v >>= 7
        out += bytes(reversed(chunk))
    return _tlv(0x06, bytes(out))


def _seq(*parts):
    return _tlv(0x30, b"".join(parts))


def _set(*parts):
    return _tlv(0x31, b"".join(parts))


def _utf8(s):
    return _tlv(0x0C, s.encode("utf-8"))


def _bitstr(b, unused=0):
    return _tlv(0x03, bytes([unused]) + b)


def _octets(b):
    return _tlv(0x04, b)


def _time(dt):
    if 1950 <= dt.year < 2050:
        return _tlv(0x17, dt.strftime("%y%m%d%H%M%SZ").encode())
    return _tlv(0x18, ("%04d" % dt.year + dt.strftime("%m%d%H%M%SZ")).encode())


def _read(b, i):
    """(тег, начало содержимого, конец содержимого)."""
    tag = b[i]
    ln = b[i + 1]
    i += 2
    if ln & 0x80:
        k = ln & 0x7F
        ln = int.from_bytes(b[i:i + k], "big")
        i += k
    if i + ln > len(b):
        raise CertError("Повреждённая структура сертификата")
    return tag, i, i + ln


def _children(b):
    out, i = [], 0
    while i < len(b):
        tag, s, e = _read(b, i)
        out.append((tag, b[s:e]))
        i = e
    return out


def _decode_oid(v):
    parts = [str(v[0] // 40), str(v[0] % 40)]
    n = 0
    for c in v[1:]:
        n = (n << 7) | (c & 0x7F)
        if not c & 0x80:
            parts.append(str(n))
            n = 0
    return ".".join(parts)


# ------------------------------------------------------------------ PEM
def pem_encode(label, der):
    b = base64.b64encode(der).decode()
    return "-----BEGIN %s-----\n%s\n-----END %s-----\n" % (label, "\n".join(b[i:i + 64] for i in range(0, len(b), 64)), label)


_PEM = re.compile(r"-----BEGIN ([A-Z0-9 ]+)-----(.*?)-----END \1-----", re.S)


def pem_decode_all(text):
    out = []
    for m in _PEM.finditer(text):
        try:
            out.append((m.group(1), base64.b64decode("".join(m.group(2).split()))))
        except ValueError:
            raise CertError("Файл сертификата повреждён")
    return out


# ------------------------------------------------------------------ RSA
_SMALL = [p for p in range(3, 3000, 2) if all(p % q for q in range(3, int(p ** 0.5) + 1, 2))]
_SMALL_PROD = 1
for _p in _SMALL:
    _SMALL_PROD *= _p


def _rand_bits(bits):
    return int.from_bytes(os.urandom((bits + 7) // 8), "big") >> ((-bits) % 8)


def _is_prime(n, rounds=24):
    if n < 4:
        return n in (2, 3)
    if n % 2 == 0:
        return False
    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for i in range(rounds):
        a = 2 if i == 0 else 2 + _rand_bits(n.bit_length() - 2) % (n - 3)
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _gen_prime(bits):
    while True:
        n = _rand_bits(bits) | (1 << (bits - 1)) | (1 << (bits - 2)) | 1
        if math.gcd(n, _SMALL_PROD) != 1:
            continue
        if pow(2, n - 1, n) != 1:
            continue
        if _is_prime(n):
            return n


def gen_rsa(bits=RSA_BITS, e=65537):
    while True:
        p, q = _gen_prime(bits // 2), _gen_prime(bits // 2)
        if p == q or math.gcd(e, p - 1) != 1 or math.gcd(e, q - 1) != 1:
            continue
        n = p * q
        if n.bit_length() != bits:
            continue
        lam = (p - 1) * (q - 1) // math.gcd(p - 1, q - 1)
        d = pow(e, -1, lam)
        return {"n": n, "e": e, "d": d, "p": p, "q": q}


def key_der(k):
    p, q, d = k["p"], k["q"], k["d"]
    return _seq(_int(0), _int(k["n"]), _int(k["e"]), _int(d), _int(p), _int(q), _int(d % (p - 1)), _int(d % (q - 1)), _int(pow(q, -1, p)))


def key_pem(k):
    return pem_encode("RSA PRIVATE KEY", key_der(k))


def parse_key(der_or_pem):
    """Закрытый ключ RSA (PKCS#1 или PKCS#8, без пароля) -> словарь n,e,d,p,q."""
    if isinstance(der_or_pem, str):
        blocks = pem_decode_all(der_or_pem)
        for label, der in blocks:
            if "ENCRYPTED" in label:
                raise CertError("Ключ защищён паролем — экспортируйте его без пароля")
            if label in ("RSA PRIVATE KEY", "PRIVATE KEY"):
                return parse_key(der)
        raise CertError("В файле нет закрытого ключа RSA")
    outer = _read(der_or_pem, 0)
    top = _children(der_or_pem[outer[1]:outer[2]])
    if len(top) >= 3 and top[2][0] == 0x04:            # PKCS#8
        if _decode_oid(_children(top[1][1])[0][1]) != OID_RSA:
            raise CertError("Поддерживаются только ключи RSA")
        return parse_key(top[2][1])
    if len(top) < 9:
        raise CertError("Неподдерживаемый формат ключа (нужен RSA)")
    v = [int.from_bytes(x[1], "big") for x in top[1:9]]
    return {"n": v[0], "e": v[1], "d": v[2], "p": v[3], "q": v[4]}


def _sign(key, data):
    h = hashlib.sha256(data).digest()
    t = bytes.fromhex("3031300d060960864801650304020105000420") + h
    k = (key["n"].bit_length() + 7) // 8
    em = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    # ускорение через китайскую теорему об остатках
    p, q, d = key["p"], key["q"], key["d"]
    m = int.from_bytes(em, "big")
    sp, sq = pow(m % p, d % (p - 1), p), pow(m % q, d % (q - 1), q)
    s = sq + q * (((sp - sq) * pow(q, -1, p)) % p)
    return s.to_bytes(k, "big")


# ------------------------------------------------------------------ X.509
def _name(cn, org):
    return _seq(_set(_seq(_oid(OID_ORG), _utf8(org))), _set(_seq(_oid(OID_CN), _utf8(cn))))


def _ext(oid, value, critical=False):
    return _seq(_oid(oid), *( [_tlv(0x01, b"\xff")] if critical else [] ), _octets(value))


def _ip_bytes(ip):
    try:
        return socket.inet_pton(socket.AF_INET, ip)
    except (OSError, ValueError):
        return socket.inet_pton(socket.AF_INET6, ip)


def make_cert(subject_cn, org, pub, signer_key, issuer_cn, issuer_ski, not_before, not_after, is_ca, dns=(), ips=()):
    spki_key = _seq(_int(pub["n"]), _int(pub["e"]))
    spki = _seq(_seq(_oid(OID_RSA), _tlv(0x05, b"")), _bitstr(spki_key))
    ski = hashlib.sha1(spki_key).digest()
    exts = [_ext(OID_BC, _seq(_tlv(0x01, b"\xff")) if is_ca else _seq(), True),
            _ext(OID_KU, _bitstr(b"\x06", 1) if is_ca else _bitstr(b"\xa0", 5), True)]
    if not is_ca:
        exts.append(_ext(OID_EKU, _seq(_oid("1.3.6.1.5.5.7.3.1"), _oid("1.3.6.1.5.5.7.3.2"))))
        gn = [_tlv(0x82, d.encode("idna") if not d.isascii() else d.encode("ascii")) for d in dns]
        gn += [_tlv(0x87, _ip_bytes(i)) for i in ips]
        exts.append(_ext(OID_SAN, _seq(*gn)))
    exts.append(_ext(OID_SKI, _octets(ski)))
    exts.append(_ext(OID_AKI, _seq(_tlv(0x80, issuer_ski or ski))))
    serial = int.from_bytes(os.urandom(16), "big") >> 1 | (1 << 120)
    sigalg = _seq(_oid(OID_SHA256_RSA), _tlv(0x05, b""))
    issuer = _name(issuer_cn if not is_ca else subject_cn, org)
    tbs = _seq(_tlv(0xA0, _int(2)), _int(serial), sigalg, issuer, _seq(_time(not_before), _time(not_after)),
               _name(subject_cn, org), spki, _tlv(0xA3, _seq(*exts)))
    return _seq(tbs, sigalg, _bitstr(_sign(signer_key, tbs))), ski


def cert_info(der):
    """Сведения о сертификате: срок, имена, CA, модуль ключа, отпечаток."""
    try:
        top = _read(der, 0)
        tbs = _children(der[top[1]:top[2]])[0][1]
        items = _children(tbs)
        if items[0][0] == 0xA0:
            items = items[1:]
        serial, _alg, issuer, validity, subject, spki = items[:6]
        exts = [x for x in items[6:] if x[0] == 0xA3]

        def cn_of(name):
            res = {}
            for rdn in _children(name):
                for atv in _children(rdn[1]):
                    oid, val = _children(atv[1])[:2]
                    res[_decode_oid(oid[1])] = val[1].decode("utf-8", "replace")
            return res

        def when(t):
            s = t[1].decode()
            if t[0] == 0x17:
                y = int(s[:2])
                s = ("20" if y < 50 else "19") + s
            return DT.datetime.strptime(s[:14], "%Y%m%d%H%M%S")

        nb, na = _children(validity[1])
        spk = _children(_children(_children(spki[1])[1][1][1:])[0][1])
        info = {"subject": cn_of(subject[1]).get(OID_CN, ""), "issuer": cn_of(issuer[1]).get(OID_CN, ""),
                "org": cn_of(subject[1]).get(OID_ORG, ""), "not_before": when(nb), "not_after": when(na),
                "n": int.from_bytes(spk[0][1], "big"), "ca": False, "dns": [], "ips": [],
                "sha256": ":".join("%02X" % c for c in hashlib.sha256(der).digest())}
        if exts:
            for e in _children(_children(exts[0][1])[0][1]):
                parts = _children(e[1])
                oid = _decode_oid(parts[0][1])
                value = parts[-1][1]
                if oid == OID_BC:
                    info["ca"] = any(t == 0x01 and v == b"\xff" for t, v in _children(_children(value)[0][1]))
                elif oid == OID_SAN:
                    for tag, v in _children(_children(value)[0][1]):
                        if tag == 0x82:
                            info["dns"].append(v.decode("ascii", "replace"))
                        elif tag == 0x87:
                            try:
                                info["ips"].append(socket.inet_ntop(socket.AF_INET if len(v) == 4 else socket.AF_INET6, v))
                            except (OSError, ValueError):
                                pass
        return info
    except (IndexError, ValueError, CertError):
        raise CertError("Не удалось прочитать сертификат (ожидается PEM или DER в формате X.509)")


def host_covered(info, host):
    host = host.lower()
    for d in info["dns"]:
        d = d.lower()
        if d == host or (d.startswith("*.") and host.split(".", 1)[-1:] == [d[2:]] and "." in host):
            return True
    return host in info["ips"]


# ------------------------------------------------------------------ сеть
def host_names():
    names = []
    try:
        names.append(socket.gethostname())
    except OSError:
        pass
    try:
        fq = socket.getfqdn()
        if fq and fq.lower() not in [n.lower() for n in names] and "." in fq:
            names.append(fq)
    except OSError:
        pass
    return [n for n in names if n]


def host_ips():
    ips = set(["127.0.0.1"])
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return sorted(ips)


def _clean_names(names):
    out = []
    for n in names or []:
        n = str(n).strip().lower()
        if n and re.match(r"^[a-z0-9а-яё]([a-z0-9а-яё.\-]*[a-z0-9а-яё])?$|^\*\.[a-z0-9.\-]+$", n) and n not in out:
            out.append(n)
    return out


def _until(years):
    years = int(years or 0)
    if years <= 0:
        return UNLIMITED
    now = DT.datetime.utcnow().replace(microsecond=0)
    try:
        return now.replace(year=now.year + years)
    except ValueError:
        return now.replace(year=now.year + years, day=28)


# ------------------------------------------------------------------ хранилище
class CertStore(object):
    def __init__(self, folder):
        self.dir = folder

    def p(self, name):
        return os.path.join(self.dir, name)

    def _read(self, name, binary=False):
        try:
            with open(self.p(name), "rb" if binary else "r", **({} if binary else {"encoding": "utf-8"})) as f:
                return f.read()
        except OSError:
            return None

    def _write(self, name, text, secret=False):
        if not os.path.isdir(self.dir):
            os.makedirs(self.dir)
        path = self.p(name)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        if secret:
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass

    def has_ca(self):
        return bool(self._read(CA_CRT)) and bool(self._read(CA_KEY))

    def has_server(self):
        return bool(self._read(SRV_CRT)) and bool(self._read(SRV_KEY))

    def meta(self):
        try:
            return json.loads(self._read(META) or "{}")
        except ValueError:
            return {}

    def ca_der(self):
        t = self._read(CA_CRT)
        if not t:
            return None
        for label, der in pem_decode_all(t):
            if label == "CERTIFICATE":
                return der
        return None

    def ca_pem(self):
        return self._read(CA_CRT)

    def _info(self, name):
        t = self._read(name)
        if not t:
            return None
        for label, der in pem_decode_all(t):
            if label == "CERTIFICATE":
                return cert_info(der)
        return None

    def status(self):
        ca, srv = self._info(CA_CRT), self._info(SRV_CRT)
        host = (host_names() or ["localhost"])[0]
        fmt = lambda dt: None if dt is None else ("unlimited" if dt.year >= 9999 else dt.strftime("%Y-%m-%d"))  # noqa: E731
        return {"has_ca": self.has_ca(), "has_server": self.has_server(), "host": host, "names": host_names(), "ips": host_ips(),
                "ca": None if not ca else {"subject": ca["subject"], "not_after": fmt(ca["not_after"]), "sha256": ca["sha256"],
                                           "has_key": bool(self._read(CA_KEY))},
                "server": None if not srv else {"subject": srv["subject"], "not_after": fmt(srv["not_after"]), "dns": srv["dns"],
                                                "ips": srv["ips"], "covers_host": host_covered(srv, host),
                                                "expired": srv["not_after"] < DT.datetime.utcnow()},
                "meta": self.meta()}

    # --- выпуск
    def _new_ca(self, org, years):
        key = gen_rsa()
        nb = DT.datetime.utcnow().replace(microsecond=0) - DT.timedelta(days=1)
        cn = "%s — корневой сертификат" % org
        der, ski = make_cert(cn, org, key, key, cn, None, nb, _until(years), True)
        self._write(CA_CRT, pem_encode("CERTIFICATE", der))
        self._write(CA_KEY, key_pem(key), True)

    def issue_server(self, names=None, ips=None, years=0, org=None):
        """Выпустить (перевыпустить) сертификат сервера. Корневой сертификат должен существовать."""
        ca_t, ca_k = self._read(CA_CRT), self._read(CA_KEY)
        if not (ca_t and ca_k):
            raise CertError("Нет корневого сертификата школы с закрытым ключом")
        ca_der = [d for l, d in pem_decode_all(ca_t) if l == "CERTIFICATE"][0]
        ca = cert_info(ca_der)
        ca_key = parse_key(ca_k)
        if ca_key["n"] != ca["n"]:
            raise CertError("Закрытый ключ не соответствует корневому сертификату")
        dns = _clean_names(list(names or []) + host_names() + ["localhost"])
        ipl = list(dict.fromkeys(list(ips or []) + host_ips()))
        key = gen_rsa()
        nb = DT.datetime.utcnow().replace(microsecond=0) - DT.timedelta(days=1)
        na = _until(years)
        if ca["not_after"] < na:
            na = ca["not_after"]
        spki_key = _seq(_int(ca["n"]), _int(65537))
        der, _ski = make_cert(dns[0], org or ca["org"] or "SchoolGrade", key, ca_key, ca["subject"], hashlib.sha1(spki_key).digest(),
                              nb, na, False, dns, ipl)
        self._write(SRV_CRT, pem_encode("CERTIFICATE", der))
        self._write(SRV_KEY, key_pem(key), True)
        return cert_info(der)

    def generate(self, years=0, names=None, org=None, new_ca=False):
        """Создать комплект: корневой сертификат школы (если нет) + сертификат сервера."""
        org = (org or "SchoolGrade").strip()[:60] or "SchoolGrade"
        if new_ca or not self.has_ca():
            self._new_ca(org, years)
        self.issue_server(names, None, years, org)
        m = {"created": time.strftime("%Y-%m-%d %H:%M:%S"), "years": int(years or 0), "org": org,
             "host": (host_names() or [""])[0]}
        self._write(META, json.dumps(m, ensure_ascii=False, indent=1))
        return self.status()

    def remove(self):
        if os.path.isdir(self.dir):
            base = os.path.join(self.dir, "old_" + time.strftime("%Y%m%d_%H%M%S"))
            old, k = base, 1
            while os.path.exists(old):
                k += 1
                old = "%s_%d" % (base, k)
            os.makedirs(old)
            for n in (CA_CRT, CA_KEY, SRV_CRT, SRV_KEY, META):
                if os.path.exists(self.p(n)):
                    os.replace(self.p(n), os.path.join(old, n))

    # --- обмен
    def export_zip(self, with_keys=True):
        """Комплект для переноса на другой сервер / в SchoolTest. С ключами — хранить в тайне."""
        if not self.has_server() and not self.has_ca():
            raise CertError("Сертификат ещё не создан")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            ca_der = self.ca_der()
            if ca_der:
                z.writestr(CA_CRT, self._read(CA_CRT))
                z.writestr("schoolca.cer", ca_der)
                z.writestr("install_ca.bat", INSTALL_BAT)
                if with_keys and self._read(CA_KEY):
                    z.writestr(CA_KEY, self._read(CA_KEY))
            if self._read(SRV_CRT):
                z.writestr(SRV_CRT, self._read(SRV_CRT))
                if with_keys and self._read(SRV_KEY):
                    z.writestr(SRV_KEY, self._read(SRV_KEY))
            z.writestr(META, self._read(META) or "{}")
            z.writestr("README.txt", EXPORT_README if with_keys else EXPORT_README_PUBLIC)
        return buf.getvalue()

    def import_files(self, files, reissue=True, org=None, years=0):
        """files — список (имя, байты): ZIP-комплект или отдельные .crt/.pem/.cer/.key.
        Возвращает список предупреждений."""
        blobs = []
        for name, data in files:
            if name.lower().endswith(".zip") or data[:2] == b"PK":
                try:
                    with zipfile.ZipFile(io.BytesIO(data)) as z:
                        if sum(i.file_size for i in z.infolist()) > 5 * 1024 * 1024 or len(z.infolist()) > 40:
                            raise CertError("Слишком большой архив")
                        for i in z.infolist():
                            if not i.filename.endswith("/") and not i.filename.lower().endswith((".bat", ".txt", ".json")):
                                blobs.append((os.path.basename(i.filename), z.read(i)))
                except zipfile.BadZipFile:
                    raise CertError("Архив повреждён")
            else:
                blobs.append((name, data))
        certs, keys = [], []
        for name, data in blobs:
            if data.lstrip()[:5] == b"-----":
                text = data.decode("utf-8", "replace")
                for label, der in pem_decode_all(text):
                    if label in ("CERTIFICATE", "TRUSTED CERTIFICATE"):
                        certs.append(der)
                    elif "PRIVATE KEY" in label:
                        keys.append(parse_key(pem_encode(label, der)))
            elif data[:1] == b"\x30":       # DER-сертификат (.cer)
                certs.append(data)
        if not certs:
            raise CertError("В загруженных файлах нет сертификата")
        infos = [(der, cert_info(der)) for der in certs]
        ca = next(((d, i) for d, i in infos if i["ca"]), None)
        leaf = next(((d, i) for d, i in infos if not i["ca"]), None)
        keyof = lambda info: next((k for k in keys if k["n"] == info["n"]), None)  # noqa: E731
        warns = []
        if leaf and not keyof(leaf[1]):
            raise CertError("К сертификату сервера не приложен подходящий закрытый ключ (.key)")
        if ca and not leaf and not keyof(ca[1]):
            raise CertError("Загружен только корневой сертификат без закрытого ключа — выпустить сертификат сервера нельзя. "
                            "Нужен комплект с ключом или пара server.crt + server.key")
        # проверка пары «сертификат + ключ» самой библиотекой ssl
        stage = CertStore(self.dir + "_stage")
        try:
            if ca:
                stage._write(CA_CRT, pem_encode("CERTIFICATE", ca[0]))
                if keyof(ca[1]):
                    stage._write(CA_KEY, key_pem(keyof(ca[1])), True)
            if leaf:
                stage._write(SRV_CRT, pem_encode("CERTIFICATE", leaf[0]))
                stage._write(SRV_KEY, key_pem(keyof(leaf[1])), True)
                try:
                    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                    ctx.load_cert_chain(stage.p(SRV_CRT), stage.p(SRV_KEY))
                except (ssl.SSLError, OSError) as e:
                    raise CertError("Сертификат и ключ не подходят друг к другу или повреждены (%s)" % e)
                if leaf[1]["not_after"] < DT.datetime.utcnow():
                    warns.append("Срок действия загруженного сертификата сервера истёк.")
            self.remove()
            if not os.path.isdir(self.dir):
                os.makedirs(self.dir)
            for n in (CA_CRT, CA_KEY, SRV_CRT, SRV_KEY):
                if os.path.exists(stage.p(n)):
                    os.replace(stage.p(n), self.p(n))
        finally:
            for n in os.listdir(stage.dir) if os.path.isdir(stage.dir) else []:
                try:
                    os.remove(stage.p(n))
                except OSError:
                    pass
            try:
                os.rmdir(stage.dir)
            except OSError:
                pass
        host = (host_names() or ["localhost"])[0]
        need_new = (not leaf) or not host_covered(leaf[1], host)
        if self.has_ca() and need_new and reissue:
            self.issue_server(None, None, years, org)
            warns.append("Сертификат сервера выпущен заново для этого компьютера (%s) на основе корневого сертификата школы." % host)
        elif leaf and not host_covered(leaf[1], host):
            warns.append("В сертификате нет имени этого компьютера (%s): браузеры будут предупреждать об ошибке." % host)
        return warns

    # --- TLS
    def ssl_context(self):
        if not self.has_server():
            return None
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        try:
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        except (AttributeError, ValueError):
            ctx.options |= getattr(ssl, "OP_NO_TLSv1", 0) | getattr(ssl, "OP_NO_TLSv1_1", 0)
        ctx.load_cert_chain(self.p(SRV_CRT), self.p(SRV_KEY))
        return ctx


INSTALL_BAT = ("@echo off\r\nrem Установка корневого сертификата школы в «Доверенные корневые центры сертификации».\r\n"
               "rem Запускать от имени администратора. Подходит для групповой политики и ручной установки.\r\n"
               "chcp 65001 >nul\r\ncd /d \"%~dp0\"\r\ncertutil -addstore -f Root schoolca.cer\r\n"
               "if errorlevel 1 (echo Не удалось. Запустите файл от имени администратора.) else (echo Готово. Перезапустите браузер.)\r\npause\r\n")
EXPORT_README = (
    "Комплект сертификата безопасности школы (SchoolGrade / SchoolTest).\r\n\r\n"
    "ВНИМАНИЕ: в архиве закрытые ключи (*.key). Храните его в тайне, не пересылайте посторонним.\r\n\r\n"
    "Установка на компьютеры пользователей: schoolca.cer -> правая кнопка -> «Установить сертификат» -> \r\n"
    "«Локальный компьютер» -> «Поместить в следующее хранилище» -> «Доверенные корневые центры сертификации»\r\n"
    "(или запустите install_ca.bat от имени администратора).\r\n\r\n"
    "Импорт на другой сервер: SchoolGrade/SchoolTest -> Администрирование -> HTTPS -> «Импорт». Если имя компьютера\r\n"
    "отличается, сертификат сервера будет выпущен заново из этого же корневого сертификата — на ПК пользователей\r\n"
    "ничего переустанавливать не нужно.\r\n")
EXPORT_README_PUBLIC = (
    "Открытая часть сертификата школы (без закрытых ключей). Установите schoolca.cer в «Доверенные корневые центры\r\n"
    "сертификации» на компьютерах пользователей (install_ca.bat — от имени администратора).\r\n")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Сертификат школы (HTTPS)")
    ap.add_argument("dir")
    ap.add_argument("--years", type=int, default=0, help="срок в годах, 0 — бессрочный")
    ap.add_argument("--name", action="append", help="дополнительное DNS-имя")
    ap.add_argument("--new-ca", action="store_true")
    a = ap.parse_args()
    print(json.dumps(CertStore(a.dir).generate(a.years, a.name, new_ca=a.new_ca), ensure_ascii=False, indent=1))
