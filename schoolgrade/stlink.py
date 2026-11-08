# -*- coding: utf-8 -*-
"""Связь SchoolGrade с программой тестирования SchoolTest.

По сети (ключ доступа, заголовок X-SchoolGrade-Key):
  GET  /api/ext/ping                     — проверка связи
  GET  /api/ext/classes                  — классы текущего года с учащимися
  GET  /api/ext/assignments?class_id=N   — предметы класса
  POST /api/ext/marks                    — отметки за тест в журнал (урок «тест» на дату)
Файлами:
  GET  /api/schooltest/classes_file      — выгрузка классов для SchoolTest (.sgclasses.json)
  POST /api/schooltest/marks_preview     — разбор файла отметок SchoolTest (.sgmarks.json)
  POST /api/schooltest/marks_import      — запись отметок из файла в журнал
"""
import hmac
import json
import re
import secrets
import time

from . import APP_VERSION, auth, grading as G, reports as R
from .api import router, db, need, need_admin, current_year, get_assignment, locked_date, now, insert, ApiError, FileResponse

WORK_TYPE = "тест"


def _settings():
    return db().get_setting("schooltest", {}) or {}


def _need_key(req):
    st = _settings()
    key = req.headers.get("X-SchoolGrade-Key") or ""
    if not st.get("enabled") or not st.get("key") or not hmac.compare_digest(key, st["key"]):
        db().audit(None, req.ip, "schooltest_denied", "")
        raise ApiError(403, "Неверный ключ доступа SchoolTest")


def _norm(s):
    return re.sub(r"\s+", " ", (s or "").replace("ё", "е").replace("Ё", "Е")).strip().lower()


def classes_payload():
    y = current_year()
    need(y, "Не создан учебный год", 400)
    out = []
    for c in db().q("SELECT * FROM classes WHERE year_id=? ORDER BY grade, letter", (y["id"],)):
        studs = R.class_students(db(), c["id"])
        out.append({"id": c["id"], "name": R.class_name(c), "grade": c["grade"],
                    "students": [{"id": s["id"], "last_name": s["last_name"], "first_name": s["first_name"],
                                  "middle_name": s.get("middle_name") or ""} for s in studs]})
    return {"format": "schoolgrade-classes", "version": 1, "app": "SchoolGrade " + APP_VERSION,
            "school": R.school_name(db()), "year": y["name"] if y.get("name") else "", "exported": time.strftime("%Y-%m-%d %H:%M"),
            "classes": out}


def class_assignments(class_id, user=None):
    rows = db().q("SELECT a.id FROM assignments a JOIN subjects sb ON sb.id=a.subject_id WHERE a.class_id=? "
                  "ORDER BY sb.name, a.group_name", (int(class_id),))
    out = []
    for r in rows:
        a = get_assignment(r["id"])
        if a["is_attend"] or a["eval_type"] != "points":
            continue
        if user is not None and not auth.can_edit_assignment(db(), user, a["id"]):
            continue
        out.append({"id": a["id"], "subject": a["subject"], "group": a["group_name"] or "", "teacher": a["teacher"] or ""})
    return out


def write_marks(a, date, title, marks, overwrite=False, user=None, ip=None):
    """Отметки за тест → урок с видом работы «тест» на дату. Учащиеся ищутся по номеру, затем по ФИО."""
    need(re.match(r"^\d{4}-\d{2}-\d{2}$", date or ""), "Укажите дату", 400)
    need(a["eval_type"] == "points", "По этому предмету не выставляются отметки 1–10", 400)
    need(not locked_date(a["class_id"], date) or (user and auth.can_admin(user)), "Период закрыт для редактирования", 400)
    studs = R.assignment_students(db(), a)
    by_id = dict((s["id"], s) for s in studs)
    by_name = {}
    for s in studs:
        by_name.setdefault(_norm(s["last_name"] + " " + s["first_name"]), []).append(s)
        by_name.setdefault(_norm(" ".join([s["last_name"], s["first_name"], s.get("middle_name") or ""])), []).append(s)
    problems, saved = [], 0
    title = (title or "").strip()[:200]
    with db().tx():
        lesson = db().one("SELECT * FROM lessons WHERE assignment_id=? AND date=? AND work_type=? AND topic=?",
                          (a["id"], date, WORK_TYPE, title))
        if lesson:
            lid = lesson["id"]
        else:
            num = (db().val("SELECT COUNT(*) FROM lessons WHERE assignment_id=? AND date=?", (a["id"], date)) or 0) + 1
            lid = insert("lessons", {"assignment_id": a["id"], "date": date, "num": num, "topic": title, "homework": "",
                                     "work_type": WORK_TYPE})
        for m in marks:
            name = m.get("name") or " ".join(x for x in [m.get("last_name"), m.get("first_name")] if x)
            s = by_id.get(int(m["student_id"])) if str(m.get("student_id") or "").isdigit() else None
            if not s:
                cand = by_name.get(_norm(" ".join(x for x in [m.get("last_name"), m.get("first_name"), m.get("middle_name")] if x))) or \
                    by_name.get(_norm(" ".join(x for x in [m.get("last_name"), m.get("first_name")] if x))) or []
                if len(cand) == 1:
                    s = cand[0]
                else:
                    problems.append({"name": name, "reason": "не найден(а) в классе" if not cand else "несколько учащихся с таким именем"})
                    continue
            v = G.normalize(str(m.get("mark") or ""), "current")
            if not v or not G.is_point(v):
                problems.append({"name": name, "reason": "не отметка 1–10"})
                continue
            old = db().val("SELECT value FROM marks WHERE lesson_id=? AND student_id=?", (lid, s["id"]))
            if old and old != v and not overwrite:
                problems.append({"name": name, "reason": "уже стоит отметка %s" % old})
                continue
            db().ex("INSERT INTO marks(lesson_id,student_id,value,updated_by,updated_at) VALUES(?,?,?,?,?) "
                    "ON CONFLICT(lesson_id,student_id) DO UPDATE SET value=excluded.value, updated_by=excluded.updated_by, "
                    "updated_at=excluded.updated_at", (lid, s["id"], v, user["id"] if user else None, now()))
            saved += 1
    db().audit(user, ip, "schooltest_marks", {"assignment": a["id"], "date": date, "title": title, "saved": saved,
                                               "problems": len(problems)})
    return {"saved": saved, "problems": problems, "lesson_id": lid}


# ------------------------------------------------------------------ сетевой доступ для SchoolTest
@router.route("GET", "/api/ext/ping", public=True)
def ext_ping(req):
    _need_key(req)
    return {"app": "SchoolGrade", "version": APP_VERSION, "school": R.school_name(db())}


@router.route("GET", "/api/ext/classes", public=True)
def ext_classes(req):
    _need_key(req)
    return classes_payload()


@router.route("GET", "/api/ext/assignments", public=True)
def ext_assignments(req):
    _need_key(req)
    return class_assignments(req.arg("class_id", cast=int) or 0)


@router.route("POST", "/api/ext/marks", public=True)
def ext_marks(req):
    _need_key(req)
    d = req.json()
    a = get_assignment(int(d.get("assignment_id") or 0))
    need(not d.get("class_id") or int(d["class_id"]) == a["class_id"], "Предмет не относится к этому классу", 400)
    return write_marks(a, d.get("date"), d.get("title") or "Тест", d.get("marks") or [], bool(d.get("overwrite")),
                       None, req.ip)


# ------------------------------------------------------------------ настройки и файлы (в интерфейсе SchoolGrade)
@router.route("GET", "/api/schooltest/settings")
def st_settings(req):
    need_admin(req)
    st = _settings()
    if not st.get("key"):                 # ключ создаётся сразу, связь включается отдельно
        st["key"] = secrets.token_urlsafe(18)
        db().set_setting("schooltest", st)
    return {"enabled": bool(st.get("enabled")), "key": st["key"]}


@router.route("PUT", "/api/schooltest/settings")
def st_settings_put(req):
    need_admin(req)
    st = _settings()
    d = req.json()
    if "enabled" in d:
        st["enabled"] = bool(d["enabled"])
    if d.get("new_key") or not st.get("key"):
        st["key"] = secrets.token_urlsafe(18)
    db().set_setting("schooltest", st)
    db().audit(req.user, req.ip, "schooltest_settings", {"enabled": st["enabled"] if "enabled" in st else False})
    return {"enabled": bool(st.get("enabled")), "key": st["key"]}


@router.route("GET", "/api/schooltest/classes_file")
def st_classes_file(req):
    need(auth.can_manage(req.user) or auth.can_admin(req.user))
    data = json.dumps(classes_payload(), ensure_ascii=False, indent=1).encode("utf-8")
    return FileResponse(data, "Классы_SchoolGrade_%s.sgclasses.json" % time.strftime("%Y%m%d"), "application/json")


def _marks_file(req):
    import base64
    try:
        doc = json.loads(base64.b64decode(req.json().get("data") or "").decode("utf-8-sig"))
    except Exception:
        raise ApiError(400, "Файл не является файлом отметок SchoolTest")
    need(isinstance(doc, dict) and doc.get("format") == "schooltest-marks", "Файл не является файлом отметок SchoolTest", 400)
    return doc


def _find_class(doc):
    y = current_year()
    need(y, "Не создан учебный год", 400)
    c = doc.get("class") or {}
    row = None
    if str(c.get("id") or "").isdigit():
        row = db().one("SELECT * FROM classes WHERE id=? AND year_id=?", (int(c["id"]), y["id"]))
    if not row and c.get("name"):
        for x in db().q("SELECT * FROM classes WHERE year_id=?", (y["id"],)):
            if _norm(R.class_name(x)).replace("«", "").replace("»", "").replace(" ", "") == \
                    _norm(c["name"]).replace("«", "").replace("»", "").replace('"', "").replace(" ", ""):
                row = x
                break
    return row


@router.route("POST", "/api/schooltest/marks_preview")
def st_marks_preview(req):
    doc = _marks_file(req)
    c = _find_class(doc)
    out = {"title": doc.get("title") or "Тест", "date": doc.get("date") or time.strftime("%Y-%m-%d"),
           "subject": doc.get("subject") or "", "class_name": (doc.get("class") or {}).get("name") or "",
           "marks": doc.get("marks") or [], "class_found": bool(c), "assignments": []}
    if c:
        out["class_id"] = c["id"]
        out["class_name"] = R.class_name(c)
        out["assignments"] = class_assignments(c["id"], req.user)
        subj = _norm(out["subject"])
        for a in out["assignments"]:
            a["suggest"] = bool(subj) and _norm(a["subject"]) == subj
    return out


@router.route("POST", "/api/schooltest/marks_import")
def st_marks_import(req):
    doc = _marks_file(req)
    d = req.json()
    a = get_assignment(int(d.get("assignment_id") or 0))
    need(auth.can_edit_assignment(db(), req.user, a["id"]))
    c = _find_class(doc)
    need(c and c["id"] == a["class_id"], "Предмет не относится к классу из файла", 400)
    return write_marks(a, d.get("date") or doc.get("date"), d.get("title") or doc.get("title"), doc.get("marks") or [],
                       bool(d.get("overwrite")), req.user, req.ip)
