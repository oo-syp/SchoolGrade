# -*- coding: utf-8 -*-
"""Пользователи, роли и сессии."""
import binascii
import hashlib
import hmac
import os
import time

ROLES = {
    "admin": "Администратор",
    "director": "Директор / заместитель директора",
    "classteacher": "Классный руководитель",
    "teacher": "Учитель",
    "viewer": "Только просмотр",
}

SESSION_TTL = 12 * 3600  # 12 часов бездействия
ITER = 120000


def hash_password(password, salt=None):
    if salt is None:
        salt = binascii.hexlify(os.urandom(16)).decode()
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode(), ITER)
    return binascii.hexlify(dk).decode(), salt


def check_password(password, pw_hash, salt):
    h, _ = hash_password(password, salt)
    return hmac.compare_digest(h, pw_hash)


def password_problem(pw):
    if len(pw) < 6:
        return "Пароль должен содержать не менее 6 символов"
    return None


def ensure_admin(db):
    if db.val("SELECT COUNT(*) FROM users") == 0:
        h, s = hash_password("admin")
        db.ex("INSERT INTO users(login,pw_hash,pw_salt,full_name,role,must_change) VALUES(?,?,?,?,?,1)",
              ("admin", h, s, "Администратор", "admin"))


def login(db, login_name, password, ip):
    u = db.one("SELECT * FROM users WHERE login=? AND active=1", (login_name.strip(),))
    if not u or not check_password(password, u["pw_hash"], u["pw_salt"]):
        return None
    token = binascii.hexlify(os.urandom(24)).decode()
    now = time.time()
    db.ex("INSERT INTO sessions(token,user_id,created,last_seen,ip) VALUES(?,?,?,?,?)",
          (token, u["id"], now, now, ip))
    db.ex("DELETE FROM sessions WHERE last_seen < ?", (now - SESSION_TTL,))
    return token


def session_user(db, token):
    if not token:
        return None
    s = db.one("SELECT * FROM sessions WHERE token=?", (token,))
    now = time.time()
    if not s or s["last_seen"] < now - SESSION_TTL:
        return None
    if now - s["last_seen"] > 60:
        db.ex("UPDATE sessions SET last_seen=? WHERE token=?", (now, token))
    u = db.one("SELECT id,login,full_name,role,staff_id,must_change FROM users WHERE id=? AND active=1",
               (s["user_id"],))
    return u


def logout(db, token):
    db.ex("DELETE FROM sessions WHERE token=?", (token,))


# ------------------------------------------------------------- права
def can_manage(u):
    """Справочники, классы, учащиеся, нагрузка, закрытие периодов."""
    return u["role"] in ("admin", "director")


def can_admin(u):
    return u["role"] == "admin"


def can_view_all(u):
    return u["role"] in ("admin", "director", "viewer")


def teacher_assignment_ids(db, u):
    if not u.get("staff_id"):
        return set()
    return set(r["id"] for r in db.q("SELECT id FROM assignments WHERE teacher_id=?", (u["staff_id"],)))


def own_class_ids(db, u):
    if not u.get("staff_id"):
        return set()
    return set(r["id"] for r in db.q("SELECT id FROM classes WHERE teacher_id=?", (u["staff_id"],)))


def can_edit_assignment(db, u, assignment_id):
    if can_manage(u):
        return True
    return int(assignment_id) in teacher_assignment_ids(db, u)


def can_edit_finals(db, u, assignment_id, class_id):
    """Отметки за период/год: учитель предмета, классный руководитель класса, администрация."""
    return can_edit_assignment(db, u, assignment_id) or int(class_id) in own_class_ids(db, u)


def can_view_class(db, u, class_id):
    if can_view_all(u):
        return True
    cid = int(class_id)
    if cid in own_class_ids(db, u):
        return True
    return db.val("SELECT 1 FROM assignments WHERE class_id=? AND teacher_id=?", (cid, u.get("staff_id"))) is not None


def can_edit_class(db, u, class_id):
    """Классный руководитель — поведение, состав и данные учащихся своего класса."""
    if can_manage(u):
        return True
    return int(class_id) in own_class_ids(db, u)
