# -*- coding: utf-8 -*-
"""Обфускация чувствительных полей в базе данных (уровень «обфускация»).

ВНИМАНИЕ. Это НЕ стойкое шифрование. Ключ встроен в программу, поэтому
данные защищены только от простого открытия файла базы сторонним человеком,
но не от специалиста, разобравшего программу. Для стойкой защиты нужен
мастер-пароль при запуске (в этой версии не включён по выбору заказчика).

Обфускация применяется к постоянной базе. В файлах экспорта/импорта
персональные данные хранятся в открытом виде (зашифрованы только пароли —
они всегда необратимый хэш). Имена и фамилии НЕ шифруются, иначе перестанут
работать поиск и сортировка по спискам.
"""
import hashlib
import hmac

# Встроенный ключ обфускации. Один и тот же во всех копиях программы.
_KEY = b"SchoolGrade-1.1-obfuscation-key-2026-SUP"
_MARK = "\x01sg1:"   # префикс зашифрованного значения

# Чувствительные поля, которые обфусцируются в постоянной базе
SENSITIVE = {
    "students": ["personal_no", "address", "phone", "parents", "parents_phone", "social", "note", "extra"],
    "staff": ["phone", "email", "note", "extra", "birth_date", "hire_date"],
    "parents": ["phone", "work", "note"],
    "student_social": ["categories", "note"],
    "characteristics": ["text"],
    "absence_days": ["note"],
}


def _keystream(n, salt):
    out = bytearray()
    counter = 0
    while len(out) < n:
        block = hmac.new(_KEY, salt + counter.to_bytes(4, "big"), hashlib.sha256).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:n])


def is_encrypted(value):
    return isinstance(value, str) and value.startswith(_MARK)


def encrypt(value):
    """Зашифровать строковое значение. Пустое и уже зашифрованное не трогаем."""
    if value is None or value == "" or is_encrypted(value):
        return value
    data = str(value).encode("utf-8")
    salt = hashlib.sha256(data + _KEY).digest()[:8]
    ks = _keystream(len(data), salt)
    enc = bytes(a ^ b for a, b in zip(data, ks))
    import binascii
    return _MARK + binascii.hexlify(salt).decode() + ":" + binascii.hexlify(enc).decode()


def decrypt(value):
    """Расшифровать значение. Открытый текст возвращаем как есть."""
    if not is_encrypted(value):
        return value
    import binascii
    try:
        body = value[len(_MARK):]
        shex, ehex = body.split(":", 1)
        salt = binascii.unhexlify(shex)
        enc = binascii.unhexlify(ehex)
        ks = _keystream(len(enc), salt)
        return bytes(a ^ b for a, b in zip(enc, ks)).decode("utf-8")
    except Exception:
        return value


def enc_row(table, data):
    """Зашифровать чувствительные поля словаря перед записью (на месте)."""
    for f in SENSITIVE.get(table, []):
        if f in data and data[f] not in (None, ""):
            data[f] = encrypt(data[f])
    return data


def dec_row(table, row):
    """Расшифровать чувствительные поля прочитанной строки (на месте)."""
    if row is None:
        return row
    for f in SENSITIVE.get(table, []):
        if f in row and is_encrypted(row[f]):
            row[f] = decrypt(row[f])
    return row
