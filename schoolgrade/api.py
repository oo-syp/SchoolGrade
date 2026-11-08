# -*- coding: utf-8 -*-
"""REST API SchoolGrade."""
import base64
import csv
import io
import json
import os
import time

from . import APP_NAME, APP_VERSION, APP_ORG, APP_AUTHOR, APP_CITY_YEAR
from . import auth, grading as G, reports as R, xlsx, crypto, plans
from .server import ApiError, FileResponse, Router

router = Router()
XLSX_CT = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

STAFF_FIELDS = ["last_name", "first_name", "middle_name", "position", "education", "category", "degree", "title",
                "gender", "birth_date", "hire_date", "phone", "email", "pc_skill", "subjects_text", "note", "active"]
STUDENT_FIELDS = ["last_name", "first_name", "middle_name", "gender", "birth_date", "personal_no", "address", "phone",
                  "arrived_from", "study_form", "health_group", "pe_group", "language1", "foreign1", "foreign2",
                  "family_type", "parents", "parents_phone", "social", "hobbies", "note", "status", "left_date", "left_reason"]

SCHOOL_FIELDS = [
    ("Общие данные", ["Наименование", "Полное наименование", "Номер", "Подчинённость (вышестоящий орган)",
                      "Организационно-правовая форма", "Тип", "Вид", "Язык обучения и воспитания", "Специализация",
                      "Попечительский совет", "УНП"]),
    ("Контакты", ["Почтовый адрес", "Директор", "Телефон директора", "Телефон приёмной", "Факс", "Электронная почта",
                  "Сайт", "Председатель профкома", "Председатель попечительского совета"]),
    ("Оснащённость", ["Библиотечный фонд", "Количество учебных пособий", "Музей", "Пункт коррекционно-педагогической помощи",
                      "Кабинетов информатики", "Рабочих мест в кабинетах информатики", "Всего компьютеров",
                      "Мультимедийных проекторов / интерактивных панелей", "Доступ в Интернет (скорость)",
                      "Автобусов на балансе", "Автомобилей для учебных целей"]),
    ("Здания и сооружения", ["Зданий", "Учебных помещений", "Ученических мест", "Мастерских", "Общая площадь зданий, м²",
                             "Площадь спортивного зала, м²", "Столовая (мест)", "Учащихся, получающих горячее питание",
                             "Требуется капитальный ремонт", "Площадь территории, га", "Учебно-опытный участок, га",
                             "Спортивная площадка", "Стадион", "Бассейн", "Площадка по изучению ПДД", "Тир"]),
    ("Выпускники прошлого года", ["Окончили II ступень (свидетельство об общем базовом)", "Окончили III ступень (аттестат)",
                                   "С золотой медалью", "С серебряной медалью", "Поступили в учреждения ПТО",
                                   "Поступили в учреждения ССО", "Поступили в учреждения ВО"]),
]

DICTS = {
    "education": ["высшее", "среднее специальное", "профессионально-техническое", "общее среднее", "обучается заочно"],
    "category": ["без категории", "вторая", "первая", "высшая", "учитель-методист"],
    "degree": ["нет", "кандидат наук", "доктор наук"],
    "position": ["директор", "заместитель директора по учебной работе", "заместитель директора по воспитательной работе",
                 "учитель", "педагог-психолог", "педагог социальный", "педагог-организатор", "библиотекарь",
                 "учитель-дефектолог", "воспитатель ГПД", "руководитель по военно-патриотическому воспитанию"],
    "arrived_from": ["из семьи", "из учреждения дошкольного образования", "из другого учреждения образования",
                     "из другого района/области", "из-за рубежа"],
    "study_form": ["в учреждении образования", "на дому", "семейное обучение", "экстернат", "специальная интегрированная группа/класс"],
    "health_group": ["I", "II", "III", "IV", "V"],
    "pe_group": ["основная", "подготовительная", "специальная медицинская", "ЛФК", "освобождён"],
    "family_type": ["полная", "неполная", "многодетная", "опекунская", "приёмная", "детский дом семейного типа"],
    "year_result": ["переведён в следующий класс", "переведён условно", "оставлен на повторное обучение",
                    "получил свидетельство об общем базовом образовании", "получил свидетельство с отличием",
                    "получил аттестат об общем среднем образовании", "получил аттестат с золотой медалью",
                    "получил аттестат с серебряной медалью", "выбыл", "получил справку об обучении"],
}


class App(object):
    """Контейнер состояния: БД, пути, роутер."""
    def __init__(self, db, data_dir, static_dir, verbose=False):
        self.db, self.data_dir, self.static_dir, self.verbose = db, data_dir, static_dir, verbose
        self.router = router
        self.backup_dir = os.path.join(data_dir, "backup")


APP = {"app": None}


def db():
    return APP["app"].db


# ================================================================ helpers
def need(cond, msg="Недостаточно прав", status=403):
    if not cond:
        raise ApiError(status, msg)


def need_manage(req):
    need(auth.can_manage(req.user))


def need_admin(req):
    need(auth.can_admin(req.user))


def pick(d, fields):
    out = {}
    for f in fields:
        if f in d:
            v = d[f]
            if isinstance(v, str):
                v = v.strip()
            out[f] = v
    return out


def insert(table, data):
    keys = list(data.keys())
    return db().ex("INSERT INTO {}({}) VALUES({})".format(table, ",".join(keys), ",".join("?" * len(keys))),
                   [data[k] for k in keys])


def update(table, id_, data):
    if not data:
        return
    keys = list(data.keys())
    db().ex("UPDATE {} SET {} WHERE id=?".format(table, ",".join(k + "=?" for k in keys)),
            [data[k] for k in keys] + [id_])


def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def current_year():
    y = db().one("SELECT * FROM years WHERE is_current=1 LIMIT 1")
    if not y:
        y = db().one("SELECT * FROM years ORDER BY start_date DESC LIMIT 1")
    return y


def year_arg(req):
    y = req.arg("year_id", cast=int)
    if y:
        return y
    cy = current_year()
    if not cy:
        raise ApiError(400, "Не создан учебный год")
    return cy["id"]


def locked_kind(class_id, kind):
    """Закрыт ли период для редактирования (для итоговых: если закрыт год)."""
    y = R.year_of_class(db(), class_id)
    if not y:
        return False
    p = db().one("SELECT locked FROM periods WHERE year_id=? AND kind=?", (y["id"], kind))
    return bool(p and p["locked"])


def locked_date(class_id, date):
    y = R.year_of_class(db(), class_id)
    if not y:
        return False
    return db().val("SELECT 1 FROM periods WHERE year_id=? AND locked=1 AND start_date<=? AND end_date>=?",
                    (y["id"], date, date)) is not None


DEFAULT_FEATURES = {"journal": True}


def features():
    f = dict(DEFAULT_FEATURES)
    f.update(db().get_setting("features", {}) or {})
    return f


def need_journal():
    need(features()["journal"], "Электронный журнал отключён в настройках (режим «только ведомости»)", 400)


# ----- тип организации и скрытие полей -----
# Поля, которые администратор может скрыть (кроме фамилии — она обязательна всегда)
HIDEABLE = {
    "students": [f for f in STUDENT_FIELDS if f not in ("last_name", "status", "left_date", "left_reason")],
    "staff": [f for f in STAFF_FIELDS if f not in ("last_name", "active")],
}


def org_type():
    return db().get_setting("org_type", "") or ""


def hidden_fields():
    h = db().get_setting("hidden_fields", {}) or {}
    return {"students": h.get("students") or [], "staff": h.get("staff") or []}


def strip_hidden(table, data):
    """Убрать скрытые поля из входных данных (их нельзя заполнять)."""
    for f in hidden_fields().get(table, []):
        data.pop(f, None)
    return data


def out_entity(table, row):
    """Расшифровать и убрать скрытые поля для выдачи наружу."""
    if row is None:
        return row
    crypto.dec_row(table, row)
    for f in hidden_fields().get(table, []):
        row.pop(f, None)
    return row


def is_department_mode():
    return plans.is_department(org_type())


def need_school(req):
    """Операции учреждения недоступны в режиме «Отдел образования»."""
    need(not is_department_mode(), "Недоступно в режиме «Отдел (управление) образования»", 400)


def table_response(req, rep):
    if req.arg("format") == "xlsx":
        data = xlsx.build([rep])
        fname = "{}_{}.xlsx".format(rep.get("name") or "report", time.strftime("%Y%m%d"))
        return FileResponse(data, fname.replace(" ", "_"), XLSX_CT)
    return rep


def get_assignment(aid):
    a = db().one("SELECT a.*, sb.name AS subject, sb.category, c.grade, c.letter, c.year_id, st.last_name AS t_last, "
                 "st.first_name AS t_first, st.middle_name AS t_middle FROM assignments a "
                 "JOIN subjects sb ON sb.id=a.subject_id JOIN classes c ON c.id=a.class_id "
                 "LEFT JOIN staff st ON st.id=a.teacher_id WHERE a.id=?", (aid,))
    if not a:
        raise ApiError(404, "Предмет в классе не найден")
    a["teacher"] = R.teacher_of(a)
    a["class_name"] = R.class_name(a)
    a["is_attend"] = a["category"] in G.ATTEND_CATEGORIES
    return a


def need_journal_for(a):
    """Факультативам/объединениям журнал посещения доступен всегда,
    остальным — только если электронный журнал включён."""
    if not a.get("is_attend"):
        need_journal()


# ================================================================ auth
@router.route("POST", "/api/login", public=True)
def api_login(req):
    d = req.json()
    token = auth.login(db(), d.get("login", ""), d.get("password", ""), req.ip)
    if not token:
        db().audit({"login": d.get("login")}, req.ip, "login_failed")
        time.sleep(0.7)
        raise ApiError(401, "Неверное имя пользователя или пароль")
    u = auth.session_user(db(), token)
    db().audit(u, req.ip, "login")
    cookie = "sg_session={}; Path=/; HttpOnly; SameSite=Strict; Max-Age={}".format(token, auth.SESSION_TTL)
    return {"ok": True}, [("Set-Cookie", cookie)]


@router.route("POST", "/api/logout", public=True)
def api_logout(req):
    if req.token:
        auth.logout(db(), req.token)
    return {"ok": True}, [("Set-Cookie", "sg_session=; Path=/; Max-Age=0")]


@router.route("GET", "/api/about", public=True)
def api_about(req):
    return {"name": APP_NAME, "version": APP_VERSION, "org": APP_ORG, "author": APP_AUTHOR, "city_year": APP_CITY_YEAR,
            "school": (db().get_setting("school", {}) or {}).get("Наименование", "")}


@router.route("GET", "/api/me")
def api_me(req):
    u = dict(req.user)
    u["role_name"] = auth.ROLES.get(u["role"], u["role"])
    u["can_manage"] = auth.can_manage(u)
    u["can_admin"] = auth.can_admin(u)
    u["can_enter_marks_any"] = auth.can_enter_marks_any(u)
    u["own_classes"] = sorted(auth.own_class_ids(db(), u))
    years = db().q("SELECT * FROM years ORDER BY start_date DESC")
    cy = current_year()
    return {"user": u, "years": years, "current_year": cy, "rules": R.rules(db()),
            "roles": auth.ROLES, "dicts": DICTS, "school_fields": SCHOOL_FIELDS,
            "school": db().get_setting("school", {}) or {}, "features": features(),
            "org_type": org_type(), "org_types": [{"code": k, "name": n} for k, n, _ in plans.ORG_TYPES],
            "department_mode": is_department_mode(),
            "hidden_fields": hidden_fields(), "hideable": HIDEABLE,
            "categories": G.CATEGORIES,
            "marks": {"current": [str(i) for i in range(1, 11)] + G.SPECIAL_CURRENT,
                      "final": [str(i) for i in range(1, 11)] + G.SPECIAL_FINAL,
                      "behavior": G.BEHAVIOR, "attend": G.ATTEND_VALUES},
            "levels": [{"code": c, "name": n, "range": r} for c, n, r in G.LEVELS],
            "about": api_about(req)}


@router.route("POST", "/api/me/password")
def api_password(req):
    d = req.json()
    u = db().one("SELECT * FROM users WHERE id=?", (req.user["id"],))
    need(auth.check_password(d.get("old", ""), u["pw_hash"], u["pw_salt"]), "Текущий пароль указан неверно", 400)
    p = auth.password_problem(d.get("new", ""))
    need(not p, p, 400)
    need(d.get("new") != d.get("old"), "Новый пароль должен отличаться от текущего", 400)
    h, s = auth.hash_password(d["new"])
    db().ex("UPDATE users SET pw_hash=?, pw_salt=?, must_change=0 WHERE id=?", (h, s, u["id"]))
    db().ex("DELETE FROM sessions WHERE user_id=? AND token<>?", (u["id"], req.token))
    db().audit(req.user, req.ip, "password_changed")
    return {"ok": True}


# ================================================================ школа, правила
@router.route("GET", "/api/school")
def api_school(req):
    return {"school": db().get_setting("school", {}) or {}, "fields": SCHOOL_FIELDS}


@router.route("PUT", "/api/school")
def api_school_put(req):
    need_manage(req)
    d = req.json()
    db().set_setting("school", {k: (str(v).strip() if v is not None else "") for k, v in d.items()})
    db().audit(req.user, req.ip, "school_updated")
    return {"ok": True}


@router.route("PUT", "/api/rules")
def api_rules_put(req):
    need_admin(req)
    d = req.json()
    r = R.rules(db())
    for k in ("min_marks_quarter", "min_marks_quarter_1h", "kz_min_point"):
        if k in d:
            v = int(d[k])
            need(0 <= v <= 10, "Недопустимое значение " + k, 400)
            r[k] = v
    if "unexcused_na_as_zero" in d:
        r["unexcused_na_as_zero"] = bool(d["unexcused_na_as_zero"])
    db().set_setting("rules", r)
    db().audit(req.user, req.ip, "rules_updated", r)
    return r


@router.route("PUT", "/api/features")
def api_features_put(req):
    need_admin(req)
    d = req.json()
    f = features()
    for k in DEFAULT_FEATURES:
        if k in d:
            f[k] = bool(d[k])
    db().set_setting("features", f)
    db().audit(req.user, req.ip, "features_updated", f)
    return f


@router.route("PUT", "/api/org_type")
def api_org_type_put(req):
    need_manage(req)
    d = req.json()
    t = d.get("org_type")
    need(t in plans.ORG_NAMES, "Неверный тип организации", 400)
    prev = org_type()
    db().set_setting("org_type", t)
    # записать «Тип» и «Вид» в паспорт
    school = db().get_setting("school", {}) or {}
    if plans.is_department(t):
        school["Тип"] = "Отдел (управление) образования"
    else:
        school["Тип"] = plans.ORG_NAMES[t]
    db().set_setting("school", school)
    # при первой установке типа — завести предметы типового плана
    if not prev and not plans.is_department(t) and db().val("SELECT COUNT(*) FROM subjects") == 0:
        _seed_subjects_for_org(t)
    db().audit(req.user, req.ip, "org_type_set", t)
    return {"ok": True, "org_type": t}


def _seed_subjects_for_org(t):
    lo, hi = plans.org_range(t)
    names = []
    for g in range(lo, hi + 1):
        for nm in plans.subjects_for_grade(g):
            if nm not in names:
                names.append(nm)
    from .db import SUBJECT_SHORT
    for i, nm in enumerate(names):
        db().ex("INSERT INTO subjects(name,short,sort,eval_type,category) VALUES(?,?,?,?,'subject')",
                (nm, SUBJECT_SHORT.get(nm, ""), (i + 1) * 10, plans.eval_for(nm)))


@router.route("PUT", "/api/hidden_fields")
def api_hidden_fields_put(req):
    need_admin(req)
    d = req.json()
    cur = hidden_fields()
    for table in ("students", "staff"):
        if table in d:
            cur[table] = [f for f in d[table] if f in HIDEABLE[table]]
    db().set_setting("hidden_fields", cur)
    db().audit(req.user, req.ip, "hidden_fields_set", cur)
    return cur


# ================================================================ учебные годы
@router.route("GET", "/api/years")
def api_years(req):
    ys = db().q("SELECT * FROM years ORDER BY start_date DESC")
    for y in ys:
        y["periods"] = db().q("SELECT * FROM periods WHERE year_id=? ORDER BY id", (y["id"],))
    return ys


def _default_periods(start_year, scheme):
    y = int(start_year)
    if scheme == "halves":
        return [("h1", "1 полугодие", "%d-09-01" % y, "%d-12-31" % y),
                ("h2", "2 полугодие", "%d-01-09" % (y + 1), "%d-05-31" % (y + 1))]
    return [("q1", "1 четверть", "%d-09-01" % y, "%d-10-26" % y),
            ("q2", "2 четверть", "%d-11-04" % y, "%d-12-24" % y),
            ("q3", "3 четверть", "%d-01-09" % (y + 1), "%d-03-21" % (y + 1)),
            ("q4", "4 четверть", "%d-03-31" % (y + 1), "%d-05-31" % (y + 1))]


@router.route("POST", "/api/years")
def api_year_create(req):
    need_manage(req)
    d = req.json()
    start = int(d.get("start_year") or time.strftime("%Y"))
    scheme = d.get("scheme") if d.get("scheme") in ("quarters", "halves") else "quarters"
    name = "{}/{}".format(start, start + 1)
    need(not db().one("SELECT 1 FROM years WHERE name=?", (name,)), "Учебный год уже существует", 400)
    with db().tx():
        yid = db().ex("INSERT INTO years(name,start_date,end_date,is_current,scheme) VALUES(?,?,?,0,?)",
                      (name, "%d-09-01" % start, "%d-08-31" % (start + 1), scheme))
        for kind, nm, a, b in _default_periods(start, scheme):
            db().ex("INSERT INTO periods(year_id,kind,name,start_date,end_date) VALUES(?,?,?,?,?)", (yid, kind, nm, a, b))
        for kind, nm in G.FINAL_KINDS:
            db().ex("INSERT INTO periods(year_id,kind,name,start_date,end_date) VALUES(?,?,?,?,?)",
                    (yid, kind, nm, "", ""))
        if not db().val("SELECT COUNT(*) FROM years WHERE is_current=1"):
            db().ex("UPDATE years SET is_current=1 WHERE id=?", (yid,))
    db().audit(req.user, req.ip, "year_created", name)
    return {"id": yid}


@router.route("POST", "/api/years/:yid/current")
def api_year_current(req, yid):
    need_manage(req)
    with db().tx():
        db().ex("UPDATE years SET is_current=0")
        db().ex("UPDATE years SET is_current=1 WHERE id=?", (int(yid),))
    return {"ok": True}


@router.route("PUT", "/api/periods/:pid")
def api_period_put(req, pid):
    need_manage(req)
    d = req.json()
    data = pick(d, ["start_date", "end_date", "name"])
    if "locked" in d:
        need(auth.can_admin(req.user) or req.user["role"] == "director")
        data["locked"] = 1 if d["locked"] else 0
    update("periods", int(pid), data)
    db().audit(req.user, req.ip, "period_updated", dict(data, id=pid))
    return {"ok": True}


@router.route("POST", "/api/years/:yid/promote")
def api_promote(req, yid):
    """Перевод: создать классы нового учебного года (параллель +1) и перенести учащихся
    (кроме оставленных на повторное обучение и выбывших). Нагрузка копируется."""
    need_manage(req)
    d = req.json()
    src = int(yid)
    dst = int(d.get("target_year_id") or 0)
    need(dst and dst != src, "Укажите учебный год, в который переводятся учащиеся", 400)
    max_grade = int(d.get("max_grade") or 11)
    created = moved = 0
    with db().tx():
        for c in db().q("SELECT * FROM classes WHERE year_id=? ORDER BY grade, letter", (src,)):
            studs = R.class_students(db(), c["id"])
            # оставленные на повторное обучение — в класс той же параллели
            stay = [s for s in studs if (s.get("year_result") or "").startswith("оставлен")]
            go = [s for s in studs if s not in stay and not (s.get("year_result") or "").startswith(("выбыл", "получил аттестат", "получил справку"))]
            plan = []
            if c["grade"] < max_grade and go:
                plan.append((c["grade"] + 1, go))
            if stay:
                plan.append((c["grade"], stay))
            for grade, group in plan:
                nc = db().one("SELECT id FROM classes WHERE year_id=? AND grade=? AND letter=?", (dst, grade, c["letter"]))
                if nc:
                    ncid = nc["id"]
                else:
                    ncid = db().ex("INSERT INTO classes(year_id,grade,letter,teacher_id,profile) VALUES(?,?,?,?,?)",
                                   (dst, grade, c["letter"], c["teacher_id"], c["profile"]))
                    created += 1
                    if grade == c["grade"] + 1:
                        for a in db().q("SELECT * FROM assignments WHERE class_id=?", (c["id"],)):
                            db().ex("INSERT OR IGNORE INTO assignments(class_id,subject_id,teacher_id,group_name,hours,eval_type) "
                                    "VALUES(?,?,?,?,?,?)", (ncid, a["subject_id"], a["teacher_id"], a["group_name"], a["hours"], a["eval_type"]))
                for s in group:
                    db().ex("INSERT OR IGNORE INTO class_students(class_id,student_id,date_in) VALUES(?,?,?)",
                            (ncid, s["id"], "%s-09-01" % db().val("SELECT substr(start_date,1,4) FROM years WHERE id=?", (dst,))))
                    moved += 1
    db().audit(req.user, req.ip, "promote", {"from": src, "to": dst, "classes": created, "students": moved})
    return {"classes": created, "students": moved}


# ================================================================ предметы
SUBJECT_FIELDS = ["name", "short", "sort", "eval_type", "category", "active"]
PENDING_DELETE = {}   # token -> {"sid":int, "code":str, "ts":float}


@router.route("GET", "/api/subjects")
def api_subjects(req):
    return db().q("SELECT * FROM subjects ORDER BY sort, name")


@router.route("POST", "/api/subjects")
def api_subject_create(req):
    need_manage(req)
    d = pick(req.json(), SUBJECT_FIELDS)
    need(d.get("name"), "Укажите название предмета", 400)
    if d.get("category") in G.ATTEND_CATEGORIES:
        d["eval_type"] = "attend"
    return {"id": insert("subjects", d)}


@router.route("PUT", "/api/subjects/:sid")
def api_subject_put(req, sid):
    need_manage(req)
    d = pick(req.json(), SUBJECT_FIELDS)
    if d.get("category") in G.ATTEND_CATEGORIES:
        d["eval_type"] = "attend"
    update("subjects", int(sid), d)
    return {"ok": True}


def _subject_has_marks(sid):
    n = db().val("SELECT COUNT(*) FROM final_marks f JOIN assignments a ON a.id=f.assignment_id WHERE a.subject_id=?", (sid,))
    n += db().val("SELECT COUNT(*) FROM marks m JOIN lessons l ON l.id=m.lesson_id JOIN assignments a ON a.id=l.assignment_id WHERE a.subject_id=?", (sid,))
    return n


@router.route("DELETE", "/api/subjects/:sid")
def api_subject_del(req, sid):
    need_manage(req)
    sid = int(sid)
    has_marks = _subject_has_marks(sid)
    if has_marks:
        import random
        code = req.arg("code")
        pend = PENDING_DELETE.get(req.token)
        if not code or not pend or pend.get("sid") != sid or pend.get("code") != code \
                or time.time() - pend.get("ts", 0) > 600:
            newcode = "".join(random.choice("0123456789") for _ in range(6))
            PENDING_DELETE[req.token] = {"sid": sid, "code": newcode, "ts": time.time()}
            raise ApiError(409, "confirm_code:" + newcode)
        PENDING_DELETE.pop(req.token, None)
    db().ex("DELETE FROM subjects WHERE id=?", (sid,))
    db().audit(req.user, req.ip, "subject_deleted", {"id": sid, "had_marks": has_marks})
    return {"ok": True}


# ================================================================ сотрудники
@router.route("GET", "/api/staff")
def api_staff(req):
    rows = db().q("SELECT * FROM staff ORDER BY active DESC, last_name COLLATE NOCASE, first_name")
    if not auth.can_manage(req.user):
        rows = [{k: r[k] for k in ("id", "last_name", "first_name", "middle_name", "position", "active")} for r in rows]
    else:
        rows = [out_entity("staff", r) for r in rows]
    for r in rows:
        r["short"] = R.staff_short(r)
    return rows


@router.route("POST", "/api/staff")
def api_staff_create(req):
    need_manage(req)
    d = strip_hidden("staff", pick(req.json(), STAFF_FIELDS))
    need(d.get("last_name"), "Укажите фамилию", 400)
    crypto.enc_row("staff", d)
    sid = insert("staff", d)
    db().audit(req.user, req.ip, "staff_created", sid)
    return {"id": sid}


@router.route("PUT", "/api/staff/:sid")
def api_staff_put(req, sid):
    need_manage(req)
    d = strip_hidden("staff", pick(req.json(), STAFF_FIELDS))
    crypto.enc_row("staff", d)
    update("staff", int(sid), d)
    db().audit(req.user, req.ip, "staff_updated", sid)
    return {"ok": True}


@router.route("DELETE", "/api/staff/:sid")
def api_staff_del(req, sid):
    need_manage(req)
    db().ex("DELETE FROM staff WHERE id=?", (int(sid),))
    db().audit(req.user, req.ip, "staff_deleted", sid)
    return {"ok": True}


# ================================================================ пользователи
def _user_staff_ids(uid):
    ids = [r["staff_id"] for r in db().q("SELECT staff_id FROM user_staff WHERE user_id=?", (uid,))]
    sid = db().val("SELECT staff_id FROM users WHERE id=?", (uid,))
    if sid and sid not in ids:
        ids.append(sid)
    return ids


def _set_user_staff(uid, staff_list):
    with db().tx():
        db().ex("DELETE FROM user_staff WHERE user_id=?", (uid,))
        primary = None
        for sid in staff_list or []:
            if not sid:
                continue
            db().ex("INSERT OR IGNORE INTO user_staff(user_id,staff_id) VALUES(?,?)", (uid, int(sid)))
            if primary is None:
                primary = int(sid)
        db().ex("UPDATE users SET staff_id=? WHERE id=?", (primary, uid))


@router.route("GET", "/api/users")
def api_users(req):
    need_admin(req)
    rows = db().q("SELECT id,login,full_name,role,staff_id,must_change,active,created_at FROM users "
                  "WHERE login<>? ORDER BY login", (auth.DEV_LOGIN,))
    for r in rows:
        r["staff_ids"] = _user_staff_ids(r["id"])
    return rows


@router.route("POST", "/api/users")
def api_user_create(req):
    need_admin(req)
    d = req.json()
    need(d.get("login") and d.get("password"), "Укажите логин и пароль", 400)
    need(not auth.is_dev_login(d["login"]), "Этот логин зарезервирован", 400)
    need(d.get("role") in auth.ROLES, "Неверная роль", 400)
    p = auth.password_problem(d["password"])
    need(not p, p, 400)
    need(not db().one("SELECT 1 FROM users WHERE login=?", (d["login"].strip(),)), "Такой логин уже есть", 400)
    h, s = auth.hash_password(d["password"])
    staff_list = d.get("staff_ids") or ([d["staff_id"]] if d.get("staff_id") else [])
    uid = db().ex("INSERT INTO users(login,pw_hash,pw_salt,full_name,role,must_change) VALUES(?,?,?,?,?,1)",
                  (d["login"].strip(), h, s, d.get("full_name", ""), d["role"]))
    _set_user_staff(uid, staff_list)
    db().audit(req.user, req.ip, "user_created", d["login"])
    return {"id": uid}


def _not_dev(uid):
    login = db().val("SELECT login FROM users WHERE id=?", (int(uid),))
    need(login != auth.DEV_LOGIN, "Системную учётную запись изменять нельзя", 400)


@router.route("PUT", "/api/users/:uid")
def api_user_put(req, uid):
    need_admin(req)
    _not_dev(uid)
    d = req.json()
    data = pick(d, ["full_name", "role", "active"])
    if "role" in data:
        need(data["role"] in auth.ROLES, "Неверная роль", 400)
    if int(uid) == req.user["id"]:
        need(data.get("role", "admin") == "admin" and data.get("active", 1), "Нельзя лишить себя прав администратора", 400)
    update("users", int(uid), data)
    if "staff_ids" in d or "staff_id" in d:
        _set_user_staff(int(uid), d.get("staff_ids") or ([d["staff_id"]] if d.get("staff_id") else []))
    if d.get("password"):
        p = auth.password_problem(d["password"])
        need(not p, p, 400)
        h, s = auth.hash_password(d["password"])
        db().ex("UPDATE users SET pw_hash=?, pw_salt=?, must_change=1 WHERE id=?", (h, s, int(uid)))
        db().ex("DELETE FROM sessions WHERE user_id=?", (int(uid),))
    db().audit(req.user, req.ip, "user_updated", uid)
    return {"ok": True}


@router.route("DELETE", "/api/users/:uid")
def api_user_del(req, uid):
    need_admin(req)
    _not_dev(uid)
    need(int(uid) != req.user["id"], "Нельзя удалить самого себя", 400)
    db().ex("DELETE FROM users WHERE id=?", (int(uid),))
    db().audit(req.user, req.ip, "user_deleted", uid)
    return {"ok": True}


# ================================================================ классы
@router.route("GET", "/api/classes")
def api_classes(req):
    yid = year_arg(req)
    rows = db().q("SELECT c.*, st.last_name, st.first_name, st.middle_name, "
                  "(SELECT COUNT(*) FROM class_students cs WHERE cs.class_id=c.id AND (cs.date_out='' OR cs.date_out IS NULL)) AS n "
                  "FROM classes c LEFT JOIN staff st ON st.id=c.teacher_id WHERE c.year_id=? ORDER BY c.grade, c.letter", (yid,))
    out = []
    for r in rows:
        r["name"] = R.class_name(r)
        r["teacher"] = R.staff_short(r)
        if auth.can_view_class(db(), req.user, r["id"]):
            out.append(r)
    return out


@router.route("POST", "/api/classes")
def api_class_create(req):
    need_manage(req)
    d = req.json()
    yid = int(d.get("year_id") or year_arg(req))
    grade = int(d.get("grade") or 0)
    need(1 <= grade <= 12, "Параллель должна быть от 1 до 12", 400)
    letter = (d.get("letter") or "").strip().upper()
    need(not db().one("SELECT 1 FROM classes WHERE year_id=? AND grade=? AND letter=?", (yid, grade, letter)), "Такой класс уже есть", 400)
    cid = insert("classes", {"year_id": yid, "grade": grade, "letter": letter, "teacher_id": d.get("teacher_id") or None,
                             "profile": d.get("profile", ""), "shift": d.get("shift") or 1, "room": d.get("room", "")})
    if d.get("add_subjects"):
        for sb in db().q("SELECT * FROM subjects WHERE active=1 ORDER BY sort"):
            pass  # нагрузку задают явно на вкладке «Нагрузка»
    db().audit(req.user, req.ip, "class_created", "{}{}".format(grade, letter))
    return {"id": cid}


@router.route("PUT", "/api/classes/:cid")
def api_class_put(req, cid):
    need_manage(req)
    d = req.json()
    data = pick(d, ["grade", "letter", "profile", "shift", "room"])
    if "teacher_id" in d:
        data["teacher_id"] = d["teacher_id"] or None
    if "letter" in data:
        data["letter"] = data["letter"].upper()
    update("classes", int(cid), data)
    return {"ok": True}


@router.route("DELETE", "/api/classes/:cid")
def api_class_del(req, cid):
    need_admin(req)
    db().ex("DELETE FROM classes WHERE id=?", (int(cid),))
    db().audit(req.user, req.ip, "class_deleted", cid)
    return {"ok": True}


# ================================================================ учащиеся
def _student_out(s, full):
    if full:
        return out_entity("students", s)
    return {k: s.get(k) for k in ("id", "last_name", "first_name", "middle_name", "gender", "status",
                                  "date_in", "date_out", "year_result", "pe_group", "class_id", "class_name")}


@router.route("GET", "/api/classes/:cid/students")
def api_class_students(req, cid):
    cid = int(cid)
    need(auth.can_view_class(db(), req.user, cid))
    full = auth.can_edit_class(db(), req.user, cid)
    rows = R.class_students(db(), cid, include_left=bool(req.arg("all")))
    return [_student_out(s, full) for s in rows]


@router.route("GET", "/api/students")
def api_students(req):
    need(auth.can_view_all(req.user) or auth.own_class_ids(db(), req.user))
    q = (req.arg("q") or "").strip()
    yid = year_arg(req)
    sql = ("SELECT s.*, c.id AS class_id, c.grade, c.letter FROM students s "
           "LEFT JOIN class_students cs ON cs.student_id=s.id AND (cs.date_out='' OR cs.date_out IS NULL) "
           "AND cs.class_id IN (SELECT id FROM classes WHERE year_id=?) "
           "LEFT JOIN classes c ON c.id=cs.class_id WHERE 1=1 ")
    args = [yid]
    if q:
        sql += "AND (s.last_name LIKE ? OR s.first_name LIKE ?) "
        args += ["%" + q + "%", "%" + q[:1].upper() + q[1:] + "%"]
    if req.arg("unassigned"):
        sql += "AND c.id IS NULL AND s.status='active' "
    sql += "ORDER BY s.last_name COLLATE NOCASE, s.first_name LIMIT 500"
    rows = db().q(sql, args)
    full = auth.can_manage(req.user)
    own = auth.own_class_ids(db(), req.user)
    out = []
    for r in rows:
        r["class_name"] = R.class_name(r) if r.get("class_id") else ""
        if full or r.get("class_id") in own:
            out.append(r)
        elif auth.can_view_all(req.user):
            out.append(_student_out(r, False))
    return out


@router.route("GET", "/api/students/:sid")
def api_student(req, sid):
    s = db().one("SELECT * FROM students WHERE id=?", (int(sid),))
    need(s, "Учащийся не найден", 404)
    hist = db().q("SELECT cs.*, c.grade, c.letter, y.name AS year FROM class_students cs JOIN classes c ON c.id=cs.class_id "
                  "JOIN years y ON y.id=c.year_id WHERE cs.student_id=? ORDER BY y.start_date", (int(sid),))
    allowed = auth.can_manage(req.user) or any(auth.can_edit_class(db(), req.user, h["class_id"]) for h in hist)
    need(allowed or auth.can_view_all(req.user))
    for h in hist:
        h["class_name"] = R.class_name(h)
    s = out_entity("students", s) if allowed else _student_out(s, False)
    db().audit(req.user, req.ip, "student_viewed", sid)
    return {"student": s, "history": hist, "can_edit": allowed}


def _check_student_edit(req, sid):
    if auth.can_manage(req.user):
        return
    cls = [r["class_id"] for r in db().q("SELECT class_id FROM class_students WHERE student_id=?", (sid,))]
    need(any(auth.can_edit_class(db(), req.user, c) for c in cls))


@router.route("POST", "/api/students")
def api_student_create(req):
    d = req.json()
    cid = d.get("class_id")
    need(auth.can_manage(req.user) or (cid and auth.can_edit_class(db(), req.user, int(cid))))
    data = strip_hidden("students", pick(d, STUDENT_FIELDS))
    need(data.get("last_name"), "Укажите фамилию", 400)
    crypto.enc_row("students", data)
    with db().tx():
        sid = insert("students", data)
        if cid:
            db().ex("INSERT INTO class_students(class_id,student_id,date_in) VALUES(?,?,?)",
                    (int(cid), sid, d.get("date_in") or time.strftime("%Y-%m-%d")))
    db().audit(req.user, req.ip, "student_created", sid)
    return {"id": sid}


@router.route("PUT", "/api/students/:sid")
def api_student_put(req, sid):
    sid = int(sid)
    _check_student_edit(req, sid)
    data = strip_hidden("students", pick(req.json(), STUDENT_FIELDS))
    crypto.enc_row("students", data)
    update("students", sid, data)
    db().audit(req.user, req.ip, "student_updated", sid)
    return {"ok": True}


@router.route("DELETE", "/api/students/:sid")
def api_student_del(req, sid):
    need_admin(req)
    db().ex("DELETE FROM students WHERE id=?", (int(sid),))
    db().audit(req.user, req.ip, "student_deleted", sid)
    return {"ok": True}


@router.route("POST", "/api/classes/:cid/enroll")
def api_enroll(req, cid):
    cid = int(cid)
    need(auth.can_edit_class(db(), req.user, cid))
    d = req.json()
    for sid in d.get("student_ids") or []:
        db().ex("INSERT INTO class_students(class_id,student_id,date_in) VALUES(?,?,?) "
                "ON CONFLICT(class_id,student_id) DO UPDATE SET date_out=''",
                (cid, int(sid), d.get("date_in") or time.strftime("%Y-%m-%d")))
    db().audit(req.user, req.ip, "enrolled", d)
    return {"ok": True}


@router.route("POST", "/api/classes/:cid/leave")
def api_leave(req, cid):
    """Выбытие учащегося из класса (с датой и причиной) или перевод в другой класс."""
    cid = int(cid)
    need(auth.can_edit_class(db(), req.user, cid))
    d = req.json()
    sid = int(d["student_id"])
    date = d.get("date") or time.strftime("%Y-%m-%d")
    with db().tx():
        db().ex("UPDATE class_students SET date_out=?, note=? WHERE class_id=? AND student_id=?",
                (date, d.get("reason", ""), cid, sid))
        if d.get("to_class_id"):
            db().ex("INSERT OR IGNORE INTO class_students(class_id,student_id,date_in) VALUES(?,?,?)",
                    (int(d["to_class_id"]), sid, date))
        elif d.get("left_school"):
            db().ex("UPDATE students SET status='left', left_date=?, left_reason=? WHERE id=?", (date, d.get("reason", ""), sid))
    db().audit(req.user, req.ip, "student_left", d)
    return {"ok": True}


@router.route("POST", "/api/classes/:cid/result")
def api_year_result(req, cid):
    cid = int(cid)
    need(auth.can_edit_class(db(), req.user, cid))
    d = req.json()
    db().ex("UPDATE class_students SET year_result=? WHERE class_id=? AND student_id=?",
            (d.get("value", ""), cid, int(d["student_id"])))
    return {"ok": True}


@router.route("POST", "/api/classes/:cid/import")
def api_import_students(req, cid):
    """Импорт списка учащихся из .xlsx или .csv: Фамилия; Имя; Отчество; Дата рождения; Пол; ..."""
    cid = int(cid)
    need(auth.can_edit_class(db(), req.user, cid))
    d = req.json()
    raw = base64.b64decode(d.get("data", ""))
    name = (d.get("filename") or "").lower()
    if name.endswith(".xlsx"):
        rows = xlsx.read_first_sheet(raw)
    else:
        txt = None
        for enc in ("utf-8-sig", "cp1251"):
            try:
                txt = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        need(txt is not None, "Не удалось прочитать файл", 400)
        dialect = csv.Sniffer().sniff(txt[:2000], delimiters=";,\t") if txt.strip() else csv.excel
        rows = list(csv.reader(io.StringIO(txt), dialect))
    colmap = {"фамилия": "last_name", "имя": "first_name", "отчество": "middle_name", "дата рождения": "birth_date",
              "пол": "gender", "адрес": "address", "личный номер": "personal_no", "законные представители": "parents",
              "родители": "parents", "телефон": "parents_phone", "группа здоровья": "health_group",
              "физкультурная группа": "pe_group", "группа по физкультуре": "pe_group"}
    order = ["last_name", "first_name", "middle_name", "birth_date", "gender"]
    if rows and any((c or "").strip().lower() in colmap for c in rows[0]):
        order = [colmap.get((c or "").strip().lower()) for c in rows[0]]
        rows = rows[1:]
    added = 0
    with db().tx():
        for r in rows:
            rec = {}
            for i, f in enumerate(order):
                if f and i < len(r) and (r[i] or "").strip():
                    rec[f] = r[i].strip()
            if not rec.get("last_name"):
                continue
            if " " in rec["last_name"] and not rec.get("first_name"):
                parts = rec["last_name"].split()
                rec["last_name"], rec["first_name"] = parts[0], parts[1] if len(parts) > 1 else ""
                if len(parts) > 2:
                    rec["middle_name"] = " ".join(parts[2:])
            bd = rec.get("birth_date", "")
            if bd and "." in bd:
                p = bd.split(".")
                if len(p) == 3:
                    rec["birth_date"] = "{}-{:0>2}-{:0>2}".format(p[2], p[1], p[0])
            sid = insert("students", rec)
            db().ex("INSERT INTO class_students(class_id,student_id,date_in) VALUES(?,?,?)",
                    (cid, sid, time.strftime("%Y-%m-%d")))
            added += 1
    db().audit(req.user, req.ip, "students_imported", {"class": cid, "n": added})
    return {"added": added}


# ================================================================ нагрузка
@router.route("GET", "/api/assignments")
def api_assignments(req):
    if req.arg("class_id"):
        cid = req.arg("class_id", cast=int)
        need(auth.can_view_class(db(), req.user, cid))
        rows = R.class_assignments(db(), cid)
    else:
        yid = year_arg(req)
        sql = ("SELECT a.*, sb.name AS subject, sb.sort, sb.short, sb.category, c.grade, c.letter, st.last_name AS t_last, "
               "st.first_name AS t_first, st.middle_name AS t_middle FROM assignments a JOIN subjects sb ON sb.id=a.subject_id "
               "JOIN classes c ON c.id=a.class_id LEFT JOIN staff st ON st.id=a.teacher_id WHERE c.year_id=? ")
        args = [yid]
        staff_id = req.arg("staff_id", cast=int)
        if staff_id:
            sql += "AND a.teacher_id=? "
            args.append(staff_id)
        elif req.arg("mine") or not auth.can_view_all(req.user):
            sids = auth.staff_ids(db(), req.user) or {-1}
            sql += "AND a.teacher_id IN (%s) " % ",".join("?" * len(sids))
            args += list(sids)
        rows = db().q(sql + "ORDER BY c.grade, c.letter, sb.sort, sb.name", args)
    for r in rows:
        r["teacher"] = R.teacher_of(r)
        if "grade" in r:
            r["class_name"] = R.class_name(r)
        r["n_sub"] = db().val("SELECT COUNT(*) FROM assignment_students WHERE assignment_id=?", (r["id"],))
    return rows


@router.route("POST", "/api/assignments")
def api_assignment_create(req):
    need_manage(req)
    d = req.json()
    cid = int(d["class_id"])
    ids = d.get("subject_ids") or [d.get("subject_id")]
    n = 0
    for sbid in ids:
        if not sbid:
            continue
        sb = db().one("SELECT * FROM subjects WHERE id=?", (int(sbid),))
        ev = d.get("eval_type") or (sb and sb["eval_type"]) or "points"
        if sb and sb["category"] in G.ATTEND_CATEGORIES:
            ev = "attend"
        try:
            insert("assignments", {"class_id": cid, "subject_id": int(sbid), "teacher_id": d.get("teacher_id") or None,
                                   "group_name": (d.get("group_name") or "").strip(), "hours": d.get("hours") or 1,
                                   "eval_type": ev})
            n += 1
        except Exception:
            pass
    return {"added": n}


@router.route("POST", "/api/classes/:cid/autoload")
def api_class_autoload(req, cid):
    """Добавить в класс предметы типового учебного плана по его параллели."""
    need_manage(req)
    cid = int(cid)
    c = db().one("SELECT * FROM classes WHERE id=?", (cid,))
    need(c, "Класс не найден", 404)
    from .db import SUBJECT_SHORT
    names = plans.subjects_for_grade(c["grade"])
    added = 0
    for nm in names:
        sb = db().one("SELECT * FROM subjects WHERE name=?", (nm,))
        if not sb:
            sid = db().ex("INSERT INTO subjects(name,short,sort,eval_type,category) VALUES(?,?,?,?,'subject')",
                          (nm, SUBJECT_SHORT.get(nm, ""), 100, plans.eval_for(nm)))
            sb = {"id": sid, "eval_type": plans.eval_for(nm)}
        if db().one("SELECT 1 FROM assignments WHERE class_id=? AND subject_id=? AND group_name=''", (cid, sb["id"])):
            continue
        db().ex("INSERT INTO assignments(class_id,subject_id,teacher_id,group_name,hours,eval_type) VALUES(?,?,?,?,?,?)",
                (cid, sb["id"], c["teacher_id"], "", 1, sb["eval_type"]))
        added += 1
    db().audit(req.user, req.ip, "class_autoload", {"class": cid, "added": added})
    return {"added": added}


@router.route("PUT", "/api/assignments/:aid")
def api_assignment_put(req, aid):
    need_manage(req)
    d = req.json()
    data = pick(d, ["group_name", "hours", "eval_type"])
    if "teacher_id" in d:
        data["teacher_id"] = d["teacher_id"] or None
    update("assignments", int(aid), data)
    if "student_ids" in d:
        with db().tx():
            db().ex("DELETE FROM assignment_students WHERE assignment_id=?", (int(aid),))
            for sid in d["student_ids"] or []:
                db().ex("INSERT INTO assignment_students(assignment_id,student_id) VALUES(?,?)", (int(aid), int(sid)))
    return {"ok": True}


@router.route("DELETE", "/api/assignments/:aid")
def api_assignment_del(req, aid):
    need_manage(req)
    n = db().val("SELECT COUNT(*) FROM lessons WHERE assignment_id=?", (int(aid),))
    need(n == 0 or req.arg("force"), "По предмету уже есть записи в журнале. Удаление сотрёт все отметки.", 409)
    db().ex("DELETE FROM assignments WHERE id=?", (int(aid),))
    db().audit(req.user, req.ip, "assignment_deleted", aid)
    return {"ok": True}


# ================================================================ журнал
@router.route("GET", "/api/journal/:aid")
def api_journal(req, aid):
    aid = int(aid)
    a = get_assignment(aid)
    need(auth.can_view_class(db(), req.user, a["class_id"]))
    kind = req.arg("period") or "q1"
    p = db().one("SELECT * FROM periods WHERE year_id=? AND kind=?", (a["year_id"], kind))
    args = [aid]
    sql = "SELECT * FROM lessons WHERE assignment_id=? "
    if p and p["start_date"] and p["end_date"]:
        sql += "AND date>=? AND date<=? "
        args += [p["start_date"], p["end_date"]]
    lessons = db().q(sql + "ORDER BY date, num, id", args)
    studs = R.assignment_students(db(), a)
    lids = [l["id"] for l in lessons]
    marks = {}
    if lids:
        for m in db().q("SELECT lesson_id, student_id, value, comment FROM marks WHERE lesson_id IN (%s)" % ",".join("?" * len(lids)), lids):
            marks["%d_%d" % (m["lesson_id"], m["student_id"])] = m["value"]
    finals = {}
    for f in db().q("SELECT student_id, kind, value, excused FROM final_marks WHERE assignment_id=?", (aid,)):
        finals["%d_%s" % (f["student_id"], f["kind"])] = {"value": f["value"], "excused": f["excused"]}
    rl = R.rules(db())
    sugg = {}
    for s in studs:
        vals = [marks.get("%d_%d" % (l["id"], s["id"])) for l in lessons]
        vals = [v for v in vals if v]
        sugg[s["id"]] = G.suggest_period_mark(vals, a["hours"], rl)
    can_edit = auth.can_edit_assignment(db(), req.user, aid)
    periods = db().q("SELECT * FROM periods WHERE year_id=? ORDER BY id", (a["year_id"],))
    return {"assignment": a, "period": p, "periods": periods, "lessons": lessons,
            "students": [{"id": s["id"], "name": R.fio(s), "pe_group": s.get("pe_group", "")} for s in studs],
            "marks": marks, "finals": finals, "suggest": sugg, "can_edit": can_edit,
            "locked": bool(p and p["locked"]) and not auth.can_admin(req.user)}


@router.route("POST", "/api/journal/:aid/lessons")
def api_lesson_create(req, aid):
    aid = int(aid)
    a = get_assignment(aid)
    need_journal_for(a)
    need(auth.can_edit_assignment(db(), req.user, aid))
    d = req.json()
    date = d.get("date") or time.strftime("%Y-%m-%d")
    need(not locked_date(a["class_id"], date) or auth.can_admin(req.user), "Период закрыт для редактирования", 400)
    num = db().val("SELECT COUNT(*) FROM lessons WHERE assignment_id=? AND date=?", (aid, date)) + 1
    topic = d.get("topic", "")
    if "ktp_id" in d:
        kid = _ktp_item_id(aid, d.get("ktp_id"))
    else:   # вызов без выбора темы: взять следующую тему из КТП
        nxt = _ktp_next(aid) if not topic else None
        kid = nxt["id"] if nxt else None
    if kid and not topic:
        topic = db().val("SELECT topic FROM ktp WHERE id=?", (kid,)) or ""
    lid = insert("lessons", {"assignment_id": aid, "date": date, "num": num, "topic": topic,
                             "homework": d.get("homework", ""), "work_type": d.get("work_type", ""), "ktp_id": kid})
    return {"id": lid}


@router.route("PUT", "/api/lessons/:lid")
def api_lesson_put(req, lid):
    l = db().one("SELECT * FROM lessons WHERE id=?", (int(lid),))
    need(l, "Урок не найден", 404)
    a = get_assignment(l["assignment_id"])
    need(auth.can_edit_assignment(db(), req.user, a["id"]))
    raw = req.json()
    d = pick(raw, ["date", "topic", "homework", "work_type"])
    if "ktp_id" in raw:
        d["ktp_id"] = _ktp_item_id(a["id"], raw.get("ktp_id"))
    for dt in [l["date"], d.get("date") or l["date"]]:
        need(not locked_date(a["class_id"], dt) or auth.can_admin(req.user), "Период закрыт для редактирования", 400)
    update("lessons", int(lid), d)
    return {"ok": True}


@router.route("DELETE", "/api/lessons/:lid")
def api_lesson_del(req, lid):
    l = db().one("SELECT * FROM lessons WHERE id=?", (int(lid),))
    need(l, "Урок не найден", 404)
    a = get_assignment(l["assignment_id"])
    need(auth.can_edit_assignment(db(), req.user, a["id"]))
    need(not locked_date(a["class_id"], l["date"]) or auth.can_admin(req.user), "Период закрыт для редактирования", 400)
    db().ex("DELETE FROM lessons WHERE id=?", (int(lid),))
    db().audit(req.user, req.ip, "lesson_deleted", l)
    return {"ok": True}


@router.route("POST", "/api/marks")
def api_marks(req):
    """Сохранить текущие отметки: {items:[{lesson_id, student_id, value}]}"""
    items = req.json().get("items") or []
    cache = {}
    saved = []
    with db().tx():
        for it in items:
            lid = int(it["lesson_id"])
            if lid not in cache:
                l = db().one("SELECT * FROM lessons WHERE id=?", (lid,))
                need(l, "Урок не найден", 404)
                a = get_assignment(l["assignment_id"])
                need_journal_for(a)
                need(auth.can_edit_assignment(db(), req.user, a["id"]))
                need(not locked_date(a["class_id"], l["date"]) or auth.can_admin(req.user), "Период закрыт для редактирования", 400)
                cache[lid] = a
            a = cache[lid]
            if a.get("is_attend"):
                v = G.normalize(it.get("value"), "attend")
            else:
                v = G.normalize(it.get("value"), "current")
            if v and a["eval_type"] == "pass" and G.is_point(v):
                raise ApiError(400, "По предмету «{}» ведётся зачётная система (зач./незач.)".format(a["subject"]))
            if v and a["eval_type"] == "none" and v != G.ABSENT:
                raise ApiError(400, "По предмету «{}» отметки не выставляются (только пропуски «н»)".format(a["subject"]))
            if v:
                db().ex("INSERT INTO marks(lesson_id,student_id,value,updated_by,updated_at) VALUES(?,?,?,?,?) "
                        "ON CONFLICT(lesson_id,student_id) DO UPDATE SET value=excluded.value, updated_by=excluded.updated_by, updated_at=excluded.updated_at",
                        (lid, int(it["student_id"]), v, req.user["id"], now()))
            else:
                db().ex("DELETE FROM marks WHERE lesson_id=? AND student_id=?", (lid, int(it["student_id"])))
            saved.append({"lesson_id": lid, "student_id": int(it["student_id"]), "value": v})
    if items:
        db().audit(req.user, req.ip, "marks_saved", saved[:50])
    return {"saved": saved}


# ================================================================ итоговые отметки
@router.route("GET", "/api/finals/:aid")
def api_finals(req, aid):
    aid = int(aid)
    a = get_assignment(aid)
    need(auth.can_view_class(db(), req.user, a["class_id"]))
    y = db().one("SELECT * FROM years WHERE id=?", (a["year_id"],))
    kinds = G.all_kinds(y["scheme"])
    pkinds = [k for k, _ in G.period_kinds(y["scheme"])]
    studs = R.assignment_students(db(), a)
    fm = {}
    for f in db().q("SELECT * FROM final_marks WHERE assignment_id=?", (aid,)):
        fm.setdefault(f["student_id"], {})[f["kind"]] = {"value": f["value"], "excused": f["excused"]}
    rl = R.rules(db())
    periods = {p["kind"]: p for p in db().q("SELECT * FROM periods WHERE year_id=?", (a["year_id"],))}
    rows = []
    for s in studs:
        sf = fm.get(s["id"], {})
        sugg = {}
        for k in pkinds:
            p = periods.get(k)
            if p and p["start_date"]:
                vals = [r["value"] for r in db().q(
                    "SELECT m.value FROM marks m JOIN lessons l ON l.id=m.lesson_id WHERE l.assignment_id=? AND m.student_id=? "
                    "AND l.date>=? AND l.date<=? ORDER BY l.date", (aid, s["id"], p["start_date"], p["end_date"]))]
                sugg[k] = G.suggest_period_mark(vals, a["hours"], rl)
        sugg["year"] = G.suggest_year_mark([sf.get(k, {}) for k in pkinds], rl)
        rows.append({"id": s["id"], "name": R.fio(s), "finals": sf, "suggest": sugg})
    return {"assignment": a, "kinds": [{"kind": k, "name": n, "locked": bool(periods.get(k, {}).get("locked"))} for k, n in kinds],
            "rows": rows, "can_edit": auth.can_edit_finals(db(), req.user, aid, a["class_id"]), "is_admin": auth.can_admin(req.user)}


@router.route("POST", "/api/finals")
def api_finals_save(req):
    """{items:[{assignment_id, student_id, kind, value, excused}]}"""
    items = req.json().get("items") or []
    valid = set(k for k, _ in G.all_kinds("quarters") + G.period_kinds("halves"))
    saved = []
    with db().tx():
        for it in items:
            a = get_assignment(int(it["assignment_id"]))
            need(auth.can_edit_finals(db(), req.user, a["id"], a["class_id"]),
                 "Отметки по предмету «{}» может выставлять учитель предмета или классный руководитель".format(a["subject"]))
            kind = it.get("kind")
            need(kind in valid, "Неверный вид отметки", 400)
            need(not locked_kind(a["class_id"], kind) or auth.can_admin(req.user), "Период «{}» закрыт".format(G.kind_name(kind)), 400)
            v = G.normalize(it.get("value"), "final")
            if v and a["eval_type"] == "pass" and G.is_point(v):
                raise ApiError(400, "По предмету «{}» ведётся зачётная система".format(a["subject"]))
            if v:
                db().ex("INSERT INTO final_marks(assignment_id,student_id,kind,value,excused,updated_by,updated_at) VALUES(?,?,?,?,?,?,?) "
                        "ON CONFLICT(assignment_id,student_id,kind) DO UPDATE SET value=excluded.value, excused=excluded.excused, "
                        "updated_by=excluded.updated_by, updated_at=excluded.updated_at",
                        (a["id"], int(it["student_id"]), kind, v, 1 if it.get("excused") else 0, req.user["id"], now()))
            else:
                db().ex("DELETE FROM final_marks WHERE assignment_id=? AND student_id=? AND kind=?", (a["id"], int(it["student_id"]), kind))
            saved.append({"assignment_id": a["id"], "student_id": int(it["student_id"]), "kind": kind, "value": v})
    db().audit(req.user, req.ip, "finals_saved", items[:50])
    return {"ok": True, "saved": saved}


# ================================================================ ведомость класса (ввод)
@router.route("GET", "/api/sheet/:cid")
def api_sheet(req, cid):
    """Ведомость для ввода отметок за период: учащиеся × предметы + поведение."""
    cid = int(cid)
    need(auth.can_view_class(db(), req.user, cid))
    kind = req.arg("kind") or "q1"
    c = R.get_class(db(), cid)
    y = R.year_of_class(db(), cid)
    studs = R.class_students(db(), cid)
    asg = R.class_assignments(db(), cid, graded_only=True)
    groups = {}
    for a in asg:
        groups[a["id"]] = set(r["student_id"] for r in db().q("SELECT student_id FROM assignment_students WHERE assignment_id=?", (a["id"],)))
    subjects, order = {}, []
    for a in asg:
        if a["subject_id"] not in subjects:
            subjects[a["subject_id"]] = {"subject_id": a["subject_id"], "name": a["subject"], "short": a["short"] or a["subject"],
                                         "eval_type": a["eval_type"], "teachers": [], "editable": False}
            order.append(a["subject_id"])
        sb = subjects[a["subject_id"]]
        t = R.teacher_of(a)
        if t and t not in sb["teachers"]:
            sb["teachers"].append(t)
    cellaid = {}
    for s in studs:
        for a in asg:
            key = "%d_%d" % (s["id"], a["subject_id"])
            g = groups[a["id"]]
            if g and s["id"] in g:
                cellaid[key] = a["id"]
            elif not g and key not in cellaid:
                cellaid[key] = a["id"]
    editable = [a["id"] for a in asg if auth.can_edit_finals(db(), req.user, a["id"], cid)]
    for a in asg:
        if a["id"] in editable:
            subjects[a["subject_id"]]["editable"] = True
    cells = {}
    for f in db().q("SELECT f.student_id, a.subject_id, f.value, f.excused FROM final_marks f JOIN assignments a ON a.id=f.assignment_id "
                    "WHERE a.class_id=? AND f.kind=?", (cid, kind)):
        cells["%d_%d" % (f["student_id"], f["subject_id"])] = {"value": f["value"], "excused": f["excused"]}
    beh = {}
    has_beh = kind not in ("exam", "final")
    if has_beh:
        beh = {str(r["student_id"]): r["value"] for r in db().q("SELECT student_id,value FROM behavior WHERE class_id=? AND kind=?", (cid, kind))}
    p = db().one("SELECT * FROM periods WHERE year_id=? AND kind=?", (y["id"], kind)) if y else None
    return {"class": c, "kind": kind, "kind_name": G.kind_name(kind), "kinds": [{"kind": k, "name": n} for k, n in G.all_kinds(y["scheme"] if y else "quarters")],
            "students": [{"id": s["id"], "name": R.fio(s)} for s in studs],
            "subjects": [subjects[i] for i in order], "cellaid": cellaid, "editable": editable, "cells": cells,
            "behavior": beh, "has_behavior": has_beh, "can_behavior": has_beh and auth.can_edit_behavior(db(), req.user, cid),
            "locked": bool(p and p["locked"]) and not auth.can_admin(req.user),
            "school": R.school_name(db()), "year": y["name"] if y else ""}


def _split_fio_lines(text):
    import re
    out = []
    for line in (text or "").splitlines():
        line = re.sub(r"^\s*\d+[.)]?\s+", "", line.replace("\t", " ")).strip()
        line = re.sub(r"\s+", " ", line)
        if not line:
            continue
        parts = line.split(" ")
        rec = {"last_name": parts[0], "first_name": parts[1] if len(parts) > 1 else "",
               "middle_name": " ".join(parts[2:]) if len(parts) > 2 else ""}
        out.append(rec)
    return out


@router.route("POST", "/api/classes/:cid/import_text")
def api_import_text(req, cid):
    """Добавить учащихся из текста: по одному в строке «Фамилия Имя Отчество»."""
    cid = int(cid)
    need(auth.can_edit_class(db(), req.user, cid))
    d = req.json()
    recs = _split_fio_lines(d.get("text"))
    need(recs, "Список пуст", 400)
    if d.get("preview"):
        return {"items": recs}
    existing = set((s["last_name"].lower(), (s["first_name"] or "").lower(), (s["middle_name"] or "").lower())
                   for s in R.class_students(db(), cid))
    added, skipped = 0, []
    with db().tx():
        for r in recs:
            key = (r["last_name"].lower(), r["first_name"].lower(), r["middle_name"].lower())
            if key in existing:
                skipped.append(" ".join(x for x in [r["last_name"], r["first_name"], r["middle_name"]] if x))
                continue
            existing.add(key)
            sid = insert("students", r)
            db().ex("INSERT INTO class_students(class_id,student_id,date_in) VALUES(?,?,?)",
                    (cid, sid, d.get("date_in") or time.strftime("%Y-%m-%d")))
            added += 1
    db().audit(req.user, req.ip, "students_pasted", {"class": cid, "n": added})
    return {"added": added, "skipped": skipped}


# ================================================================ поведение
@router.route("GET", "/api/behavior/:cid")
def api_behavior(req, cid):
    cid = int(cid)
    need(auth.can_view_class(db(), req.user, cid))
    y = R.year_of_class(db(), cid)
    kinds = G.period_kinds(y["scheme"]) + [("year", "Годовая")]
    studs = R.class_students(db(), cid)
    beh = {}
    for b in db().q("SELECT * FROM behavior WHERE class_id=?", (cid,)):
        beh["%d_%s" % (b["student_id"], b["kind"])] = b["value"]
    return {"kinds": [{"kind": k, "name": n} for k, n in kinds], "students": [{"id": s["id"], "name": R.fio(s), "year_result": s.get("year_result", "")} for s in studs],
            "values": beh, "can_edit": auth.can_edit_behavior(db(), req.user, cid),
            "can_result": auth.can_edit_class(db(), req.user, cid), "class": R.get_class(db(), cid)}


@router.route("POST", "/api/behavior")
def api_behavior_save(req):
    d = req.json()
    cid = int(d["class_id"])
    need(auth.can_edit_behavior(db(), req.user, cid), "Отметку по поведению выставляет классный руководитель или учительская")
    need(not locked_kind(cid, d["kind"]) or auth.can_admin(req.user), "Период закрыт", 400)
    v = G.normalize(d.get("value"), "behavior")
    if v:
        db().ex("INSERT INTO behavior(class_id,student_id,kind,value,updated_by,updated_at) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(class_id,student_id,kind) DO UPDATE SET value=excluded.value, updated_by=excluded.updated_by, updated_at=excluded.updated_at",
                (cid, int(d["student_id"]), d["kind"], v, req.user["id"], now()))
    else:
        db().ex("DELETE FROM behavior WHERE class_id=? AND student_id=? AND kind=?", (cid, int(d["student_id"]), d["kind"]))
    db().audit(req.user, req.ip, "behavior_saved", d)
    return {"value": v}


# ================================================================ отчёты
def _kind(req):
    k = req.arg("kind") or "q1"
    return k


@router.route("GET", "/api/reports/class_sheet")
def rep_class_sheet(req):
    cid = req.arg("class_id", cast=int)
    need(cid and auth.can_view_class(db(), req.user, cid))
    return table_response(req, R.class_sheet(db(), cid, _kind(req)))


@router.route("GET", "/api/reports/class_subjects")
def rep_class_subjects(req):
    cid = req.arg("class_id", cast=int)
    need(cid and auth.can_view_class(db(), req.user, cid))
    return table_response(req, R.class_subjects(db(), cid, _kind(req)))


@router.route("GET", "/api/reports/class_students")
def rep_class_students(req):
    cid = req.arg("class_id", cast=int)
    need(cid and auth.can_view_class(db(), req.user, cid))
    return table_response(req, R.class_students_report(db(), cid, _kind(req)))


@router.route("GET", "/api/reports/students_list")
def rep_students_list(req):
    cid = req.arg("class_id", cast=int)
    need(cid and auth.can_edit_class(db(), req.user, cid))
    db().audit(req.user, req.ip, "personal_data_export", {"class": cid})
    return table_response(req, R.students_list(db(), cid))


@router.route("GET", "/api/reports/attendance")
def rep_attendance(req):
    cid = req.arg("class_id", cast=int)
    need(cid and auth.can_view_class(db(), req.user, cid))
    return table_response(req, R.attendance(db(), cid, req.arg("from"), req.arg("to")))


@router.route("GET", "/api/reports/school")
def rep_school(req):
    need(auth.can_view_all(req.user))
    return table_response(req, R.school_summary(db(), year_arg(req), _kind(req)))


@router.route("GET", "/api/reports/parallel")
def rep_parallel(req):
    need(auth.can_view_all(req.user))
    return table_response(req, R.parallel_subjects(db(), year_arg(req), req.arg("grade", cast=int) or 1, _kind(req)))


@router.route("GET", "/api/reports/lists")
def rep_lists(req):
    need(auth.can_view_all(req.user))
    return table_response(req, R.lists_report(db(), year_arg(req), _kind(req)))


@router.route("GET", "/api/reports/school_subjects")
def rep_school_subjects(req):
    need(auth.can_view_all(req.user))
    return table_response(req, R.school_subjects(db(), year_arg(req), _kind(req)))


@router.route("GET", "/api/reports/levels_parallel")
def rep_levels_parallel(req):
    need(auth.can_view_all(req.user))
    return table_response(req, R.levels_by_parallel(db(), year_arg(req), _kind(req)))


@router.route("GET", "/api/reports/clubs")
def rep_clubs(req):
    need(auth.can_view_all(req.user))
    cat = req.arg("category")
    need(cat in G.ATTEND_CATEGORIES, "Неверная категория", 400)
    return table_response(req, R.clubs_attendance(db(), year_arg(req), cat))


@router.route("GET", "/api/reports/teacher")
def rep_teacher(req):
    sid = req.arg("staff_id", cast=int) or req.user.get("staff_id")
    need(sid, "Пользователь не связан с сотрудником", 400)
    need(auth.can_view_all(req.user) or sid == req.user.get("staff_id"))
    return table_response(req, R.teacher_report(db(), sid, year_arg(req), _kind(req)))


@router.route("GET", "/api/reports/student")
def rep_student(req):
    sid = req.arg("student_id", cast=int)
    need(sid and db().one("SELECT 1 FROM students WHERE id=?", (sid,)), "Выберите учащегося", 400)
    yid = year_arg(req)
    cls = [r["class_id"] for r in db().q("SELECT cs.class_id FROM class_students cs JOIN classes c ON c.id=cs.class_id "
                                          "WHERE cs.student_id=? AND c.year_id=?", (sid, yid))]
    need(auth.can_view_all(req.user) or any(auth.can_view_class(db(), req.user, c) for c in cls))
    return table_response(req, R.student_card(db(), sid, yid))


# ------------------------------------------------ пакет для отдела образования
def _passport_sheet():
    school = db().get_setting("school", {}) or {}
    rows = []
    for group, fields in SCHOOL_FIELDS:
        for f in fields:
            rows.append([group, f, school.get(f, "")])
    return {"name": "Паспорт", "title": "Паспорт учреждения образования", "headers": ["Раздел", "Показатель", "Значение"],
            "rows": rows, "widths": [24, 52, 40]}


def _staff_sheet():
    rows = []
    for s in db().q("SELECT * FROM staff WHERE active=1 ORDER BY last_name"):
        rows.append([R.fio(s, True), s["position"], s["education"], s["category"], s["degree"], s["subjects_text"]])
    return {"name": "Педагоги", "title": "Педагогические работники",
            "headers": ["ФИО", "Должность", "Образование", "Категория", "Учёная степень", "Предметы"], "rows": rows,
            "widths": [34, 30, 20, 16, 14, 30]}


@router.route("GET", "/api/export/department")
def api_export_department(req):
    """Файл для передачи данных в отдел образования (без персональных данных учащихся)."""
    need(auth.can_view_all(req.user))
    yid = year_arg(req)
    y = db().one("SELECT * FROM years WHERE id=?", (yid,))
    kinds = [k for k, _ in G.all_kinds(y["scheme"])]
    fmt_ = req.arg("format") or "xlsx"
    if fmt_ == "json":
        pkg = {"format": "SchoolGrade-department", "version": 1, "app_version": APP_VERSION, "created": now(),
               "year": y["name"], "school": db().get_setting("school", {}) or {}, "kinds": {}}
        for k in kinds:
            pkg["kinds"][k] = {"school": R.school_summary(db(), yid, k),
                               "parallels": {str(g["grade"]): R.parallel_subjects(db(), yid, g["grade"], k)
                                             for g in db().q("SELECT DISTINCT grade FROM classes WHERE year_id=? ORDER BY grade", (yid,))},
                               "raw": _raw_counts(yid, k)}
        db().audit(req.user, req.ip, "department_export", "json")
        data = json.dumps(pkg, ensure_ascii=False, indent=1).encode("utf-8")
        name = (pkg["school"].get("Наименование") or "school").replace(" ", "_")
        return FileResponse(data, "{}_{}.sgdep.json".format(name, y["name"].replace("/", "-")), "application/json")
    sheets = [_passport_sheet(), _staff_sheet()]
    for k in kinds:
        rep = R.school_summary(db(), yid, k)
        rep["name"] = "Классы " + G.kind_name(k)
        sheets.append(rep)
    for k in kinds:
        for g in db().q("SELECT DISTINCT grade FROM classes WHERE year_id=? ORDER BY grade", (yid,)):
            rep = R.parallel_subjects(db(), yid, g["grade"], k)
            rep["name"] = "{} кл {}".format(g["grade"], G.kind_name(k))
            sheets.append(rep)
    db().audit(req.user, req.ip, "department_export", "xlsx")
    return FileResponse(xlsx.build(sheets), "Отдел_образования_{}.xlsx".format(y["name"].replace("/", "-")), XLSX_CT)


def _raw_counts(yid, kind):
    """Агрегаты для свода по району: по параллелям и предметам — количество отметок каждого балла."""
    out = {}
    for c in db().q("SELECT * FROM classes WHERE year_id=?", (yid,)):
        studs = set(s["id"] for s in R.class_students(db(), c["id"]))
        g = out.setdefault(str(c["grade"]), {"students": 0, "subjects": {}})
        g["students"] += len(studs)
        subs = {sb["subject_id"]: sb["subject"] for sb in R.subjects_of_class(db(), c["id"])}
        for (sid, subj), v in R.finals_map(db(), c["id"], kind).items():
            if sid in studs:
                d = g["subjects"].setdefault(subs.get(subj, "?"), {})
                d[v] = d.get(v, 0) + 1
    return out


def _parse_pkg(raw_bytes, fname=""):
    pkg = json.loads(raw_bytes.decode("utf-8"))
    if pkg.get("format") != "SchoolGrade-department":
        raise ApiError(400, "Файл {} не является пакетом SchoolGrade".format(fname))
    return pkg


def _build_merge(pkgs, kind):
    """Свод по списку пакетов школ: по учреждениям и по предметам."""
    rl = R.rules(db())
    schools, agg, parallels = [], {}, {}
    for pkg in pkgs:
        raw = (pkg.get("kinds", {}).get(kind) or {}).get("raw") or {}
        all_vals = []
        for grade, g in raw.items():
            pv = parallels.setdefault(grade, [])
            for subj, cnt in g["subjects"].items():
                vals = []
                for v, n in cnt.items():
                    vals += [v] * n
                all_vals += vals; pv += vals
                agg.setdefault(subj, []).extend(vals)
        st = G.stats(all_vals, rl)
        schools.append([pkg.get("school", {}).get("Наименование") or "—", pkg.get("year"),
                        sum(g["students"] for g in raw.values()), st["count"]] + R._level_cells(st)
                       + [st["other"][G.NOT_ATT], R.fmt(st["avg"], 2), R.fmt(st["kz"]), R.fmt(st["sou"])])
    rep1 = {"name": "Свод по УО", "title": "Свод по учреждениям образования: " + G.kind_name(kind).lower(),
            "headers": ["Учреждение", "Уч. год", "Уч-ся", "Отметок"] + R._level_headers() + ["н/а", "Ср. балл", "КЗ %", "СОУ %"],
            "rows": schools, "widths": [36, 10, 7, 8] + [5, 6] * 5 + [5, 8, 7, 7]}
    subj_rows = []
    for subj in sorted(agg):
        st = G.stats(agg[subj], rl)
        subj_rows.append([subj, st["count"]] + R._level_cells(st) + [st["other"][G.NOT_ATT], R.fmt(st["avg"], 2), R.fmt(st["kz"]), R.fmt(st["sou"])])
    rep2 = {"name": "Свод по предметам", "title": "Свод по предметам: " + G.kind_name(kind).lower(),
            "headers": ["Предмет", "Отметок"] + R._level_headers() + ["н/а", "Ср. балл", "КЗ %", "СОУ %"],
            "rows": subj_rows, "widths": [36, 8] + [5, 6] * 5 + [5, 8, 7, 7]}
    par_rows = []
    for grade in sorted(parallels, key=lambda x: int(x) if str(x).isdigit() else 99):
        st = G.stats(parallels[grade], rl)
        par_rows.append([str(grade) + " классы", st["count"]] + R._level_cells(st)
                        + [st["other"][G.NOT_ATT], R.fmt(st["avg"], 2), R.fmt(st["kz"]), R.fmt(st["sou"])])
    rep3 = {"name": "Свод по параллелям", "title": "Свод по параллелям: " + G.kind_name(kind).lower(),
            "headers": ["Параллель", "Отметок"] + R._level_headers() + ["н/а", "Ср. балл", "КЗ %", "СОУ %"],
            "rows": par_rows, "widths": [16, 8] + [5, 6] * 5 + [5, 8, 7, 7]}
    return [rep1, rep3, rep2]


@router.route("POST", "/api/department/merge")
def api_department_merge(req):
    """Свод из пакетов, переданных прямо в запросе (разовый расчёт)."""
    need(auth.can_view_all(req.user))
    files = req.json().get("files") or []
    kind = req.json().get("kind") or "year"
    pkgs = [_parse_pkg(base64.b64decode(f["data"]), f.get("filename")) for f in files]
    reps = _build_merge(pkgs, kind)
    if req.arg("format") == "xlsx":
        return FileResponse(xlsx.build(reps), "Свод_отдел_образования.xlsx", XLSX_CT)
    return {"reports": reps}


# ----- хранимые пакеты школ (режим «Отдел образования») -----
@router.route("GET", "/api/department/packages")
def api_dep_packages(req):
    need(auth.can_view_all(req.user))
    return db().q("SELECT id, school, year, filename, uploaded_at FROM dep_packages ORDER BY school, year")


@router.route("POST", "/api/department/packages")
def api_dep_pkg_add(req):
    need_manage(req)
    items = req.json().get("files") or []
    added = 0
    for f in items:
        raw = base64.b64decode(f["data"])
        pkg = _parse_pkg(raw, f.get("filename"))
        school = (pkg.get("school") or {}).get("Наименование") or f.get("filename")
        # заменить прежний пакет этой школы за тот же год
        db().ex("DELETE FROM dep_packages WHERE school=? AND year=?", (school, pkg.get("year") or ""))
        db().ex("INSERT INTO dep_packages(school,year,filename,data) VALUES(?,?,?,?)",
                (school, pkg.get("year") or "", f.get("filename") or "", raw.decode("utf-8")))
        added += 1
    db().audit(req.user, req.ip, "dep_pkg_added", added)
    return {"added": added}


@router.route("DELETE", "/api/department/packages/:pid")
def api_dep_pkg_del(req, pid):
    need_manage(req)
    db().ex("DELETE FROM dep_packages WHERE id=?", (int(pid),))
    return {"ok": True}


@router.route("GET", "/api/department/package/:pid")
def api_dep_pkg_one(req, pid):
    need(auth.can_view_all(req.user))
    row = db().one("SELECT * FROM dep_packages WHERE id=?", (int(pid),))
    need(row, "Пакет не найден", 404)
    pkg = json.loads(row["data"])
    return {"school": pkg.get("school") or {}, "year": pkg.get("year"), "fields": SCHOOL_FIELDS,
            "kinds": [{"kind": k, "name": n} for k, n in G.all_kinds("quarters")]}


@router.route("GET", "/api/department/summary")
def api_dep_summary(req):
    need(auth.can_view_all(req.user))
    kind = req.arg("kind") or "year"
    rows = db().q("SELECT data FROM dep_packages")
    need(rows, "Пакеты школ не загружены", 400)
    pkgs = [json.loads(r["data"]) for r in rows]
    reps = _build_merge(pkgs, kind)
    if req.arg("format") == "xlsx":
        return FileResponse(xlsx.build(reps), "Свод_по_району.xlsx", XLSX_CT)
    return {"reports": reps}


# ================================================================ администрирование
@router.route("GET", "/api/audit")
def api_audit(req):
    need_admin(req)
    lim = min(req.arg("limit", cast=int) or 300, 5000)
    rows = db().q("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (lim,))
    if req.arg("format") == "xlsx":
        rep = {"name": "Журнал действий", "title": "Журнал действий пользователей",
               "headers": ["Дата и время", "Пользователь", "IP", "Действие", "Подробности"],
               "rows": [[r["ts"], r["login"], r["ip"], r["action"], r["details"]] for r in rows], "widths": [20, 14, 14, 22, 80]}
        return FileResponse(xlsx.build([rep]), "audit.xlsx", XLSX_CT)
    return rows


@router.route("GET", "/api/backups")
def api_backups(req):
    need_admin(req)
    d = APP["app"].backup_dir
    if not os.path.isdir(d):
        return []
    out = []
    for f in sorted(os.listdir(d), reverse=True):
        if f.endswith(".db"):
            st = os.stat(os.path.join(d, f))
            out.append({"name": f, "size": st.st_size, "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime))})
    return out


@router.route("POST", "/api/backups")
def api_backup_make(req):
    need_admin(req)
    p = db().backup(APP["app"].backup_dir, "manual")
    db().audit(req.user, req.ip, "backup", os.path.basename(p))
    return {"name": os.path.basename(p)}


@router.route("GET", "/api/backups/download")
def api_backup_download(req):
    need_admin(req)
    name = os.path.basename(req.arg("name") or "")
    path = os.path.join(APP["app"].backup_dir, name)
    need(name.endswith(".db") and os.path.isfile(path), "Файл не найден", 404)
    with open(path, "rb") as f:
        data = f.read()
    return FileResponse(data, name, "application/octet-stream")


@router.route("POST", "/api/backups/restore")
def api_backup_restore(req):
    need_admin(req)
    d = req.json()
    bdir = APP["app"].backup_dir
    if d.get("data"):
        tmp = os.path.join(bdir, "upload_{}.db".format(int(time.time())))
        if not os.path.isdir(bdir):
            os.makedirs(bdir)
        with open(tmp, "wb") as f:
            f.write(base64.b64decode(d["data"]))
        src = tmp
    else:
        src = os.path.join(bdir, os.path.basename(d.get("name") or ""))
        need(os.path.isfile(src), "Файл не найден", 404)
    db().backup(bdir, "before_restore")
    db().restore(src)
    db().audit(req.user, req.ip, "restore", os.path.basename(src))
    return {"ok": True}


@router.route("GET", "/api/export/full")
def api_export_full(req):
    """Экспорт всех настроек и оценок для переноса на другой сервер.
    Это снимок базы: пароли — необратимый хэш, персональные данные обфусцированы
    тем же ключом, что и в программе, поэтому другой сервер SchoolGrade прочитает их."""
    need_admin(req)
    import tempfile
    import sqlite3 as _sq
    fd, tmp = tempfile.mkstemp(suffix=".db", dir=APP["app"].backup_dir)
    os.close(fd)
    try:
        with db().lock:
            tgt = _sq.connect(tmp)
            try:
                db().conn.backup(tgt)
            finally:
                tgt.close()
        with open(tmp, "rb") as f:
            data = f.read()
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    db().audit(req.user, req.ip, "export_full")
    school = (db().get_setting("school", {}) or {}).get("Наименование", "SchoolGrade")
    fname = "SchoolGrade_{}_{}.sgdb".format((school or "data").replace(" ", "_")[:40], time.strftime("%Y-%m-%d"))
    return FileResponse(data, fname, "application/octet-stream")


@router.route("POST", "/api/demo")
def api_demo(req):
    need_admin(req)
    need(db().val("SELECT COUNT(*) FROM students") == 0, "Демо-данные можно загрузить только в пустую базу", 400)
    from . import demo
    demo.fill(db())
    db().audit(req.user, req.ip, "demo_loaded")
    return {"ok": True}


# ------------------------------------------------------------------ импорт из «Старт ШКОЛА»
from . import startschool as SS   # noqa: E402

START_IMPORTS = {}   # user_id -> {имя файла: разобранная книга}


def _start_files(req):
    return START_IMPORTS.setdefault(req.user["id"], {})


def _start_opts(d):
    return {"kinds": [k for k in (d.get("kinds") or []) if k in SS.KIND_NAMES] or None,
            "overwrite": bool(d.get("overwrite")), "passport": d.get("passport", True) is not False,
            "staff": d.get("staff", True) is not False, "create_teachers": d.get("create_teachers", True) is not False}


@router.route("GET", "/api/import/startschool")
def api_start_list(req):
    need_admin(req)
    need_school(req)
    return {"files": [SS.file_summary(p) for p in _start_files(req).values()],
            "kinds": [{"kind": k, "name": n} for k, _s, n in SS.KINDS]}


@router.route("POST", "/api/import/startschool/file")
def api_start_file(req):
    need_admin(req)
    need_school(req)
    d = req.json()
    name = os.path.basename(d.get("filename") or "file.xls")
    try:
        data = base64.b64decode(d.get("data") or "")
    except Exception:
        raise ApiError(400, "Файл повреждён при загрузке")
    try:
        parsed = SS.parse_file(name, data)
    except SS.ImportError_ as e:
        raise ApiError(400, str(e))
    _start_files(req)[name] = parsed
    return api_start_list(req)


@router.route("DELETE", "/api/import/startschool")
def api_start_clear(req):
    need_admin(req)
    START_IMPORTS.pop(req.user["id"], None)
    return {"ok": True}


def _start_run(req, dry):
    files = list(_start_files(req).values())
    need(files, "Сначала добавьте файлы «Старт ШКОЛА»", 400)
    try:
        return SS.apply(db(), files, _start_opts(req.json()), req.user, dry_run=dry)
    except SS.ImportError_ as e:
        raise ApiError(400, str(e))


@router.route("POST", "/api/import/startschool/preview")
def api_start_preview(req):
    need_admin(req)
    need_school(req)
    return _start_run(req, True)


@router.route("POST", "/api/import/startschool/commit")
def api_start_commit(req):
    need_admin(req)
    need_school(req)
    need(_start_files(req), "Сначала добавьте файлы «Старт ШКОЛА»", 400)
    backup = db().backup(APP["app"].backup_dir, "before_import")
    rep = _start_run(req, False)
    rep["backup"] = os.path.basename(backup)
    db().audit(req.user, req.ip, "import_startschool",
               {"files": list(_start_files(req)), "summary": rep["summary"], "marks": rep["marks"]})
    START_IMPORTS.pop(req.user["id"], None)
    return rep


# ------------------------------------------------------------------ контроль накопляемости
def _scoped_assignments(req, year_id, class_id=None):
    """Нагрузки, которые пользователь вправе контролировать: администрация —
    все; учитель — свои предметы и все предметы своего класса."""
    u = req.user
    sql = ("SELECT a.*, sb.name AS subject, sb.sort, sb.category, c.grade, c.letter, st.last_name AS t_last, "
           "st.first_name AS t_first, st.middle_name AS t_middle FROM assignments a "
           "JOIN subjects sb ON sb.id=a.subject_id JOIN classes c ON c.id=a.class_id "
           "LEFT JOIN staff st ON st.id=a.teacher_id WHERE c.year_id=? AND sb.category='subject'")
    args = [year_id]
    if class_id:
        sql += " AND c.id=?"
        args.append(int(class_id))
    rows = db().q(sql, args)
    if auth.can_view_all(u):
        return rows
    mine = auth.teacher_assignment_ids(db(), u)
    own = auth.own_class_ids(db(), u)
    return [a for a in rows if a["id"] in mine or a["class_id"] in own]


@router.route("GET", "/api/reports/accumulation")
def rep_accumulation(req):
    need_school(req)
    y = year_arg(req)
    kind = req.arg("kind") or R.current_kind(db(), y)
    need(kind in ("q1", "q2", "q3", "q4", "h1", "h2"), "Контроль накопляемости выполняется для четвертей (полугодий)", 400)
    asg = _scoped_assignments(req, y, req.arg("class_id", cast=int))
    rep = R.accumulation(db(), y, kind, asg, bool(features().get("journal", True)))
    rep["kind"] = kind
    return table_response(req, rep)


@router.route("GET", "/api/classes/:cid/report_cards")
def api_report_cards(req, cid):
    need_school(req)
    need(auth.can_view_class(db(), req.user, cid))
    kinds = [k for k in (req.arg("kinds") or "").split(",") if k] or None
    ids = [x for x in (req.arg("students") or "").split(",") if x.strip().isdigit()] or None
    return R.report_cards(db(), int(cid), kinds, ids)


# ------------------------------------------------------------------ календарно-тематическое планирование
from . import ktp as KTP   # noqa: E402

KTP_FIELDS = ["num", "topic", "hours", "planned_date", "section", "note"]


def _ktp_item_id(aid, kid):
    """Проверить, что пункт КТП принадлежит этой нагрузке."""
    if not kid:
        return None
    return db().val("SELECT id FROM ktp WHERE id=? AND assignment_id=?", (int(kid), int(aid)))


def _ktp_items(aid):
    items = db().q("SELECT * FROM ktp WHERE assignment_id=? ORDER BY num, id", (int(aid),))
    done = {}
    for l in db().q("SELECT id, date, ktp_id FROM lessons WHERE assignment_id=? AND ktp_id IS NOT NULL ORDER BY date", (int(aid),)):
        done.setdefault(l["ktp_id"], []).append(l["date"])
    for it in items:
        it["done"] = done.get(it["id"], [])
    return items


def _ktp_next(aid):
    for it in _ktp_items(aid):
        if len(it["done"]) < max(1, int(round(float(it["hours"] or 1)))):
            return it
    return None


def _need_ktp_view(req, aid):
    a = get_assignment(int(aid))
    need(auth.can_view_class(db(), req.user, a["class_id"]))
    return a


def _can_edit_ktp(req, a):
    return auth.can_edit_assignment(db(), req.user, a["id"]) or auth.can_enter_marks_any(req.user)


@router.route("GET", "/api/assignments/:aid/ktp")
def api_ktp_get(req, aid):
    need_school(req)
    a = _need_ktp_view(req, aid)
    items = _ktp_items(aid)
    lessons_total = db().val("SELECT COUNT(*) FROM lessons WHERE assignment_id=?", (a["id"],)) or 0
    nxt = _ktp_next(aid)
    return {"assignment": a, "items": items, "can_edit": _can_edit_ktp(req, a),
            "next_id": nxt["id"] if nxt else None,
            "stats": {"items": len(items), "hours": sum(float(i["hours"] or 0) for i in items),
                      "done_items": sum(1 for i in items if i["done"]), "lessons_total": lessons_total,
                      "lessons_linked": sum(len(i["done"]) for i in items)}}


@router.route("PUT", "/api/assignments/:aid/ktp")
def api_ktp_put(req, aid):
    need_school(req)
    a = _need_ktp_view(req, aid)
    need(_can_edit_ktp(req, a))
    items = req.json().get("items") or []
    need(len(items) <= 1000, "Слишком много строк", 400)
    existing = set(r["id"] for r in db().q("SELECT id FROM ktp WHERE assignment_id=?", (a["id"],)))
    keep = set()
    with db().tx():
        for i, it in enumerate(items):
            row = pick(it, KTP_FIELDS)
            row["topic"] = (row.get("topic") or "").strip()[:500]
            if not row["topic"]:
                continue
            try:
                row["hours"] = float(str(row.get("hours") or 1).replace(",", ".")) or 1.0
            except ValueError:
                row["hours"] = 1.0
            row["num"] = i + 1
            row["planned_date"] = (row.get("planned_date") or "")[:10]
            iid = it.get("id")
            if iid and int(iid) in existing:
                update("ktp", int(iid), row)
                keep.add(int(iid))
            else:
                row["assignment_id"] = a["id"]
                keep.add(insert("ktp", row))
        for gone in existing - keep:
            db().ex("DELETE FROM ktp WHERE id=?", (gone,))
    db().audit(req.user, req.ip, "ktp_saved", {"assignment": a["id"], "items": len(keep)})
    return api_ktp_get(req, aid)


@router.route("POST", "/api/ktp/parse")
def api_ktp_parse(req):
    need_school(req)
    d = req.json()
    y = current_year()
    start = int((y or {}).get("name", "2026")[:4]) if y else 2026
    try:
        if d.get("text") is not None:
            items, notes = KTP.parse(text=d["text"], start_year=start)
        else:
            items, notes = KTP.parse(d.get("filename") or "", base64.b64decode(d.get("data") or ""), start_year=start)
    except KTP.KtpError as e:
        raise ApiError(400, str(e))
    return {"items": items, "notes": notes}


@router.route("GET", "/api/assignments/:aid/ktp/sources")
def api_ktp_sources(req, aid):
    need_school(req)
    a = _need_ktp_view(req, aid)
    rows = db().q("SELECT a.id, a.group_name, c.grade, c.letter, c.year_id, y.name AS year, st.last_name t_last, st.first_name t_first, "
                  "st.middle_name t_middle, (SELECT COUNT(*) FROM ktp k WHERE k.assignment_id=a.id) AS n "
                  "FROM assignments a JOIN classes c ON c.id=a.class_id JOIN years y ON y.id=c.year_id "
                  "LEFT JOIN staff st ON st.id=a.teacher_id WHERE a.subject_id=? AND a.id<>? "
                  "AND (SELECT COUNT(*) FROM ktp k WHERE k.assignment_id=a.id)>0 ORDER BY y.name DESC, c.grade, c.letter",
                  (a["subject_id"], a["id"]))
    my_grade = db().val("SELECT grade FROM classes WHERE id=?", (a["class_id"],))
    rows.sort(key=lambda r: (r["grade"] != my_grade, -int(r["year"][:4] or 0), r["grade"], r["letter"]))
    return [{"id": r["id"], "n": r["n"], "title": "%s, %s класс%s — %s, %s строк" % (
        r["year"], R.class_name(r), (" (" + r["group_name"] + ")") if r["group_name"] else "",
        R.teacher_of(r) or "учитель не указан", r["n"])} for r in rows]


@router.route("GET", "/api/assignments/:aid/ktp/xlsx")
def api_ktp_xlsx(req, aid):
    need_school(req)
    a = _need_ktp_view(req, aid)
    items = _ktp_items(aid)
    rep = {"name": "КТП", "title": "Календарно-тематическое планирование: {}, {} класс".format(a["subject"], a.get("class_name", "")),
           "subtitle": R.school_name(db()),
           "headers": ["№", "Раздел", "Тема урока", "Часов", "Дата по плану", "Проведено", "Примечание"],
           "widths": [5, 26, 60, 7, 12, 16, 20],
           "rows": [[i["num"], i["section"], i["topic"], R.fmt(float(i["hours"]), 1).replace(",0", ""),
                     R._ru_date(i["planned_date"]), ", ".join(R._ru_date(x) for x in i["done"]), i["note"]] for i in items]}
    data = xlsx.build([rep])
    return FileResponse(data, "KTP_{}_{}.xlsx".format(a["subject"], a.get("class_name", "")).replace(" ", "_"), XLSX_CT)


@router.route("GET", "/api/reports/ktp_progress")
def rep_ktp_progress(req):
    need_school(req)
    y = year_arg(req)
    asg = _scoped_assignments(req, y, req.arg("class_id", cast=int))
    return table_response(req, R.ktp_progress(db(), y, asg))


# ================================================================ связь с SchoolTest
from . import stlink  # noqa: E402,F401
