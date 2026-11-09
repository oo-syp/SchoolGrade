# -*- coding: utf-8 -*-
"""SchoolGrade 1.4: расписание, замены, причины пропусков, прогноз и группа риска,
мониторинг журнала, сравнительный анализ, кабинет классного руководителя."""
import base64
import datetime as DT
import io
import json
import re
import time
import zipfile

from . import auth, crypto, grading as G, reports as R, xls, xlsx
from .api import (router, db, need, need_admin, need_school, current_year, year_arg, get_assignment, insert, update,
                  now, ApiError, FileResponse, XLSX_CT, table_response, _scoped_assignments, REASONS, ktp_schedule_slots_hook)

WEEKDAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота"]
WD_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб"]


def _d(s):
    try:
        return DT.date.fromisoformat(str(s)[:10])
    except ValueError:
        raise ApiError(400, "Неверная дата")


def need_manage(req):
    need(auth.can_manage(req.user), "Недостаточно прав", 403)


def _year_of_date(date):
    return db().one("SELECT * FROM years WHERE start_date<=? AND end_date>=? ORDER BY start_date DESC LIMIT 1", (date, date)) \
        or current_year()


# =================================================================== РАСПИСАНИЕ
def schedule_for(year_id, date):
    """Действующая на дату версия расписания и номер недели (1 или 2)."""
    s = db().one("SELECT * FROM schedules WHERE year_id=? AND start_date<=? ORDER BY start_date DESC, id DESC LIMIT 1",
                 (year_id, date))
    if not s:
        return None, 1
    week = 1
    if s["weeks"] == 2:
        start = _d(s["start_date"])
        monday = start - DT.timedelta(days=start.weekday())
        week = ((_d(date) - monday).days // 7) % 2 + 1
    return s, week


def lessons_on(year_id, date, where="", args=()):
    """Уроки по расписанию на дату: [{lesson_no, class_id, assignment_id, room}]."""
    s, week = schedule_for(year_id, date)
    wd = _d(date).isoweekday()
    if not s or wd > 6:
        return []
    return db().q("SELECT si.* FROM schedule_items si WHERE si.schedule_id=? AND si.week=? AND si.weekday=?" + where +
                  " ORDER BY si.lesson_no", [s["id"], week, wd] + list(args))


def assignment_slots(a, date):
    """Сколько уроков по нагрузке в этот день по расписанию (для «Уроки в журнал»)."""
    return [r["lesson_no"] for r in lessons_on(a["year_id"], date, " AND si.assignment_id=?", (a["id"],))]


ktp_schedule_slots_hook["fn"] = assignment_slots


def _sched_out(s):
    s = dict(s)
    s["items"] = db().val("SELECT COUNT(*) FROM schedule_items WHERE schedule_id=?", (s["id"],))
    nxt = db().val("SELECT MIN(start_date) FROM schedules WHERE year_id=? AND start_date>?", (s["year_id"], s["start_date"]))
    s["end_date"] = (_d(nxt) - DT.timedelta(days=1)).isoformat() if nxt else ""
    return s


@router.route("GET", "/api/schedule")
def api_sched_list(req):
    need_school(req)
    y = year_arg(req)
    return {"versions": [_sched_out(s) for s in db().q("SELECT * FROM schedules WHERE year_id=? ORDER BY start_date", (y,))],
            "bells": db().get_setting("bells", {}) or {}, "can_edit": auth.can_manage(req.user)}


@router.route("POST", "/api/schedule")
def api_sched_create(req):
    need_manage(req)
    d = req.json()
    y = int(d.get("year_id") or year_arg(req))
    start = _d(d.get("start_date") or time.strftime("%Y-%m-%d")).isoformat()
    need(not db().val("SELECT 1 FROM schedules WHERE year_id=? AND start_date=?", (y, start)),
         "Расписание с этой даты уже есть", 400)
    weeks = 2 if int(d.get("weeks") or 1) == 2 else 1
    with db().tx():
        sid = insert("schedules", {"year_id": y, "name": (d.get("name") or "").strip()[:100] or "Расписание с " + R._ru_date(start),
                                   "start_date": start, "weeks": weeks})
        src = d.get("copy_from")
        if src:
            for it in db().q("SELECT * FROM schedule_items WHERE schedule_id=?", (int(src),)):
                if weeks == 1 and it["week"] == 2:
                    continue
                insert("schedule_items", {"schedule_id": sid, "class_id": it["class_id"], "week": it["week"], "weekday": it["weekday"],
                                          "lesson_no": it["lesson_no"], "assignment_id": it["assignment_id"], "room": it["room"]})
                if weeks == 2 and db().one("SELECT weeks FROM schedules WHERE id=?", (int(src),))["weeks"] == 1:
                    insert("schedule_items", {"schedule_id": sid, "class_id": it["class_id"], "week": 2, "weekday": it["weekday"],
                                              "lesson_no": it["lesson_no"], "assignment_id": it["assignment_id"], "room": it["room"]})
    db().audit(req.user, req.ip, "schedule_created", {"id": sid, "start": start})
    return {"id": sid}


@router.route("PUT", "/api/schedule/bells")
def api_bells(req):
    need_manage(req)
    b = req.json().get("bells") or {}
    clean = {}
    for k, v in b.items():
        if str(k).isdigit() and isinstance(v, list) and len(v) == 2:
            clean[str(int(k))] = [str(v[0])[:5], str(v[1])[:5]]
    db().set_setting("bells", clean)
    return clean


@router.route("PUT", "/api/schedule/:sid")
def api_sched_put(req, sid):
    need_manage(req)
    s = db().one("SELECT * FROM schedules WHERE id=?", (int(sid),))
    need(s, "Не найдено", 404)
    d = req.json()
    upd = {}
    if "name" in d:
        upd["name"] = (d["name"] or "").strip()[:100]
    if d.get("start_date"):
        upd["start_date"] = _d(d["start_date"]).isoformat()
    if "weeks" in d:
        upd["weeks"] = 2 if int(d["weeks"] or 1) == 2 else 1
        if upd["weeks"] == 1:
            db().ex("DELETE FROM schedule_items WHERE schedule_id=? AND week=2", (s["id"],))
    if upd:
        update("schedules", s["id"], upd)
    return {"ok": True}


@router.route("DELETE", "/api/schedule/:sid")
def api_sched_del(req, sid):
    need_manage(req)
    db().ex("DELETE FROM schedules WHERE id=?", (int(sid),))
    db().audit(req.user, req.ip, "schedule_deleted", sid)
    return {"ok": True}


def _class_asg(cid):
    out = []
    for a in db().q("SELECT a.id, a.group_name, a.hours, a.teacher_id, sb.name AS subject, sb.short, sb.category, "
                    "st.last_name AS t_last, st.first_name AS t_first, st.middle_name AS t_middle FROM assignments a "
                    "JOIN subjects sb ON sb.id=a.subject_id LEFT JOIN staff st ON st.id=a.teacher_id WHERE a.class_id=? "
                    "ORDER BY sb.sort, sb.name, a.group_name", (int(cid),)):
        a["teacher"] = R.teacher_of(a)
        out.append(a)
    return out


@router.route("GET", "/api/schedule/:sid/class/:cid")
def api_sched_class(req, sid, cid):
    need_school(req)
    s = db().one("SELECT * FROM schedules WHERE id=?", (int(sid),))
    need(s, "Не найдено", 404)
    return {"schedule": _sched_out(s), "assignments": _class_asg(cid),
            "items": db().q("SELECT week, weekday, lesson_no, assignment_id, room FROM schedule_items WHERE schedule_id=? AND class_id=?",
                            (s["id"], int(cid))),
            "conflicts": _conflicts(s["id"], int(cid))}


def _conflicts(sched_id, class_id=None):
    """Один учитель в одно время в разных классах (кроме групп одного класса)."""
    rows = db().q("SELECT si.week, si.weekday, si.lesson_no, si.class_id, a.teacher_id, si.assignment_id FROM schedule_items si "
                  "JOIN assignments a ON a.id=si.assignment_id WHERE si.schedule_id=? AND a.teacher_id IS NOT NULL", (sched_id,))
    by = {}
    for r in rows:
        by.setdefault((r["week"], r["weekday"], r["lesson_no"], r["teacher_id"]), set()).add(r["class_id"])
    out = []
    for (w, wd, ln, tid), cls in by.items():
        if len(cls) > 1 and (class_id is None or class_id in cls):
            st = db().one("SELECT last_name, first_name, middle_name FROM staff WHERE id=?", (tid,))
            names = [R.class_name(db().one("SELECT grade, letter FROM classes WHERE id=?", (c,))) for c in sorted(cls)]
            out.append({"week": w, "weekday": wd, "lesson_no": ln, "teacher": R.staff_short(st), "classes": names})
    return out


@router.route("PUT", "/api/schedule/:sid/class/:cid")
def api_sched_class_put(req, sid, cid):
    need_manage(req)
    s = db().one("SELECT * FROM schedules WHERE id=?", (int(sid),))
    need(s, "Не найдено", 404)
    valid = set(a["id"] for a in _class_asg(cid))
    items = req.json().get("items") or []
    with db().tx():
        db().ex("DELETE FROM schedule_items WHERE schedule_id=? AND class_id=?", (s["id"], int(cid)))
        for it in items:
            aid = int(it.get("assignment_id") or 0)
            wd, ln, wk = int(it.get("weekday") or 0), int(it.get("lesson_no") or 0), int(it.get("week") or 1)
            if aid not in valid or not (1 <= wd <= 6) or not (0 <= ln <= 12) or wk not in (1, 2) or (wk == 2 and s["weeks"] == 1):
                continue
            insert("schedule_items", {"schedule_id": s["id"], "class_id": int(cid), "week": wk, "weekday": wd, "lesson_no": ln,
                                      "assignment_id": aid, "room": (it.get("room") or "")[:20]})
    db().audit(req.user, req.ip, "schedule_class_saved", {"schedule": s["id"], "class": cid, "items": len(items)})
    return {"ok": True, "conflicts": _conflicts(s["id"], int(cid))}


@router.route("GET", "/api/schedule/day")
def api_sched_day(req):
    """Расписание на день: всей школы, класса или учителя (с заменами)."""
    need_school(req)
    date = _d(req.arg("date") or time.strftime("%Y-%m-%d")).isoformat()
    y = _year_of_date(date)
    need(y, "Не создан учебный год", 400)
    where, args = "", []
    if req.arg("class_id"):
        where, args = " AND si.class_id=?", [int(req.arg("class_id"))]
    rows = lessons_on(y["id"], date, where, args)
    subs = dict(((r["assignment_id"], r["lesson_no"]), r) for r in db().q("SELECT * FROM substitutions WHERE date=?", (date,)))
    staff = req.arg("staff_id", cast=int)
    out = []
    for r in rows:
        a = get_assignment(r["assignment_id"])
        sb = subs.get((a["id"], r["lesson_no"]))
        sub_t = db().one("SELECT last_name, first_name, middle_name FROM staff WHERE id=?", (sb["substitute_id"],)) if sb else None
        teacher_id = sb["substitute_id"] if sb else a["teacher_id"]
        if staff and teacher_id != staff and a["teacher_id"] != staff:
            continue
        out.append({"lesson_no": r["lesson_no"], "class_id": a["class_id"], "class_name": a["class_name"], "assignment_id": a["id"],
                    "subject": a["subject"] + (" (" + a["group_name"] + ")" if a["group_name"] else ""), "teacher": a["teacher"],
                    "teacher_id": a["teacher_id"], "room": r["room"], "sub_teacher": R.staff_short(sub_t) if sub_t else "",
                    "sub_id": sb["id"] if sb else None, "mine_sub": bool(sb and staff == sb["substitute_id"])})
    # замены вне расписания (если расписание не ведётся)
    seen = set((o["assignment_id"], o["lesson_no"]) for o in out)
    for (aid, ln), sb in subs.items():
        if (aid, ln) in seen or (staff and sb["substitute_id"] != staff):
            continue
        a = get_assignment(aid)
        if req.arg("class_id") and a["class_id"] != int(req.arg("class_id")):
            continue
        sub_t = db().one("SELECT last_name, first_name, middle_name FROM staff WHERE id=?", (sb["substitute_id"],))
        out.append({"lesson_no": ln, "class_id": a["class_id"], "class_name": a["class_name"], "assignment_id": aid,
                    "subject": a["subject"], "teacher": a["teacher"], "teacher_id": a["teacher_id"], "room": "",
                    "sub_teacher": R.staff_short(sub_t), "sub_id": sb["id"], "mine_sub": staff == sb["substitute_id"]})
    out.sort(key=lambda o: (o["lesson_no"], o["class_name"]))
    s, week = schedule_for(y["id"], date)
    return {"date": date, "weekday": _d(date).isoweekday(), "week": week if s and s["weeks"] == 2 else None,
            "schedule": _sched_out(s) if s else None, "lessons": out, "bells": db().get_setting("bells", {}) or {}}


@router.route("GET", "/api/schedule/:sid/teachers")
def api_sched_teachers(req, sid):
    """Сетка «учитель × день/урок» — для проверки нагрузки и окон."""
    need_school(req)
    s = db().one("SELECT * FROM schedules WHERE id=?", (int(sid),))
    need(s, "Не найдено", 404)
    rows = db().q("SELECT si.*, a.teacher_id, sb.short, sb.name AS subject, c.grade, c.letter FROM schedule_items si "
                  "JOIN assignments a ON a.id=si.assignment_id JOIN subjects sb ON sb.id=a.subject_id "
                  "JOIN classes c ON c.id=si.class_id WHERE si.schedule_id=?", (s["id"],))
    teachers = {}
    for r in rows:
        if not r["teacher_id"]:
            continue
        t = teachers.setdefault(r["teacher_id"], {"id": r["teacher_id"], "cells": {}, "hours": 0})
        key = "%d_%d_%d" % (r["week"], r["weekday"], r["lesson_no"])
        t["cells"].setdefault(key, []).append(R.class_name(r))
        t["hours"] += 1
    out = []
    for tid, t in teachers.items():
        st = db().one("SELECT last_name, first_name, middle_name FROM staff WHERE id=?", (tid,))
        t["name"] = R.staff_short(st) if st else "?"
        out.append(t)
    out.sort(key=lambda t: t["name"])
    return {"schedule": _sched_out(s), "teachers": out}


# ----- загрузка расписания из Excel: строки — день и номер урока, столбцы — классы
_CLS = re.compile(r"^\s*(\d{1,2})\s*[-–«\"']?\s*([А-ЯЁA-Zа-яёa-z]?)\s*[»\"']?\s*(класс|кл\.?)?\s*$")


def _norm(s):
    return re.sub(r"[^a-zа-я0-9]+", "", (s or "").lower().replace("ё", "е"))


def _match_subject(cell, asg):
    """Текст ячейки → нагрузки класса (несколько — для групп: «Англ/Англ», «Инф. (1)/Инф. (2)»)."""
    parts = [p.strip() for p in re.split(r"[/\n;|]+", cell or "") if p.strip()]
    found = []
    for p in parts:
        p0 = re.sub(r"\s*\(?\b\d{1,3}[а-я]?\)?\s*$", "", p) if re.search(r"каб|\d{2,3}$", p) else p
        key = _norm(p0.split(",")[0])
        if not key:
            continue
        best = None
        for a in asg:
            names = [_norm(a["subject"]), _norm(a.get("short") or "")]
            if any(n and (n == key or n.startswith(key) or key.startswith(n)) for n in names):
                if a not in found:
                    best = a
                    break
        if best:
            found.append(best)
    return found


@router.route("POST", "/api/schedule/:sid/import")
def api_sched_import(req, sid):
    need_manage(req)
    s = db().one("SELECT * FROM schedules WHERE id=?", (int(sid),))
    need(s, "Не найдено", 404)
    d = req.json()
    data = base64.b64decode(d.get("data") or "")
    name = (d.get("filename") or "").lower()
    try:
        if name.endswith(".xlsx"):
            rows = xlsx.read_first_sheet(data)
        elif name.endswith(".xls"):
            sh = xls.open_workbook(data).sheet(0)
            rows = [[sh.text(r, c) for c in range(sh.ncols)] for r in range(sh.nrows)]
        else:
            raise ApiError(400, "Загрузите таблицу Excel (.xlsx или .xls)")
    except ApiError:
        raise
    except Exception:
        raise ApiError(400, "Не удалось прочитать таблицу")
    rows = [[str(c if c is not None else "").strip() for c in r] for r in rows]
    classes = dict(((c["grade"], (c["letter"] or "").upper()), c) for c in db().q("SELECT * FROM classes WHERE year_id=?", (s["year_id"],)))
    # строка заголовка: ячейки вида «5А», «5 "А"»
    head, cols = None, {}
    for i, r in enumerate(rows[:15]):
        found = {}
        for j, v in enumerate(r):
            m = _CLS.match(v.replace("«", "").replace("»", "").replace('"', ""))
            if m and (int(m.group(1)), m.group(2).upper()) in classes:
                found[j] = classes[(int(m.group(1)), m.group(2).upper())]
        if len(found) >= 1 and len(found) >= len(cols):
            head, cols = i, found
            if len(found) >= 3:
                break
    need(head is not None and cols, "Не найдена строка с названиями классов (например, «5А», «5 «Б»»)", 400)
    first_col = min(cols)
    wd, week = 0, 1
    items, problems = [], []
    asg_cache = {}
    for r in rows[head + 1:]:
        lead = " ".join(r[:first_col]).lower()
        for k, nm in enumerate(WEEKDAYS):
            if nm.lower()[:3] in lead or re.search(r"\b" + WD_SHORT[k].lower() + r"\b", lead):
                wd = k + 1
                break
        if "2 недел" in lead or "четн" in lead and "неч" not in lead:
            week = 2 if s["weeks"] == 2 else 1
        m = re.search(r"(\d{1,2})", " ".join(x for x in r[:first_col] if not re.search(r"\d{1,2}[.:]\d{2}", x)))
        if not wd or not m:
            continue
        ln = int(m.group(1))
        if not (0 <= ln <= 12):
            continue
        for j, c in cols.items():
            cell = r[j] if j < len(r) else ""
            if not cell:
                continue
            asg = asg_cache.setdefault(c["id"], _class_asg(c["id"]))
            room = ""
            mr = re.search(r"(?:каб\.?\s*)?(\d{1,3}[а-я]?)\s*$", cell)
            if mr and not re.match(r"^\d", cell):
                room = mr.group(1)
            found = _match_subject(cell, asg)
            if not found:
                problems.append("%s, %s, %d урок: «%s» — предмет не найден в нагрузке класса" % (R.class_name(c), WD_SHORT[wd - 1], ln, cell))
                continue
            for a in found:
                items.append({"class_id": c["id"], "week": week, "weekday": wd, "lesson_no": ln, "assignment_id": a["id"], "room": room})
    if d.get("commit"):
        with db().tx():
            for cid in set(it["class_id"] for it in items):
                db().ex("DELETE FROM schedule_items WHERE schedule_id=? AND class_id=?", (s["id"], cid))
            for it in items:
                insert("schedule_items", dict(it, schedule_id=s["id"]))
        db().audit(req.user, req.ip, "schedule_imported", {"schedule": s["id"], "items": len(items)})
    return {"items": len(items), "classes": sorted(set(R.class_name(c) for c in cols.values())), "problems": problems[:200],
            "committed": bool(d.get("commit"))}


# =================================================================== ЗАМЕНЫ
def _sub_out(r):
    a = get_assignment(r["assignment_id"])
    st = lambda i: R.staff_short(db().one("SELECT last_name, first_name, middle_name FROM staff WHERE id=?", (i,))) if i else ""  # noqa: E731
    return {"id": r["id"], "date": r["date"], "lesson_no": r["lesson_no"], "assignment_id": a["id"], "class_id": a["class_id"],
            "class_name": a["class_name"], "subject": a["subject"] + (" (" + a["group_name"] + ")" if a["group_name"] else ""),
            "absent_id": r["absent_id"], "absent": st(r["absent_id"]), "substitute_id": r["substitute_id"],
            "substitute": st(r["substitute_id"]), "hours": r["hours"], "reason": r["reason"], "note": r["note"],
            "lessons": db().val("SELECT COUNT(*) FROM lessons WHERE sub_id=?", (r["id"],))}


@router.route("GET", "/api/subs")
def api_subs(req):
    need_school(req)
    f = req.arg("from") or time.strftime("%Y-%m-01")
    t = req.arg("to") or "9999-12-31"
    sql, args = "SELECT * FROM substitutions WHERE date>=? AND date<=?", [f, t]
    if not auth.can_view_all(req.user):
        sid = auth.staff_ids(db(), req.user) or [-1]
        ph = ",".join("?" * len(sid))
        sql += " AND (substitute_id IN (%s) OR absent_id IN (%s))" % (ph, ph)
        args += list(sid) * 2
    return {"items": [_sub_out(r) for r in db().q(sql + " ORDER BY date, lesson_no", args)], "can_edit": auth.can_manage(req.user),
            "reasons": ["болезнь", "курсы повышения квалификации", "командировка", "отпуск", "иная причина"]}


@router.route("POST", "/api/subs")
def api_subs_add(req):
    need_manage(req)
    d = req.json()
    date = _d(d.get("date")).isoformat()
    items = d.get("items") or [d]
    ids = []
    with db().tx():
        for it in items:
            a = get_assignment(int(it.get("assignment_id") or 0))
            sub = int(it.get("substitute_id") or 0)
            need(db().val("SELECT 1 FROM staff WHERE id=?", (sub,)), "Выберите заменяющего учителя", 400)
            ln = int(it.get("lesson_no") or 0)
            need(sub != a["teacher_id"], "Заменяющий совпадает с учителем предмета", 400)
            ex = db().one("SELECT id FROM substitutions WHERE date=? AND assignment_id=? AND lesson_no=?", (date, a["id"], ln))
            row = {"date": date, "lesson_no": ln, "assignment_id": a["id"], "absent_id": int(d.get("absent_id") or 0) or a["teacher_id"],
                   "substitute_id": sub, "hours": float(str(it.get("hours") or 1).replace(",", ".")),
                   "reason": (d.get("reason") or "")[:100], "note": (it.get("note") or d.get("note") or "")[:300],
                   "created_by": req.user["id"]}
            if ex:
                update("substitutions", ex["id"], row)
                ids.append(ex["id"])
            else:
                ids.append(insert("substitutions", row))
    db().audit(req.user, req.ip, "subs_saved", {"date": date, "ids": ids})
    return {"ids": ids}


@router.route("DELETE", "/api/subs/:sid")
def api_subs_del(req, sid):
    need_manage(req)
    db().ex("UPDATE lessons SET sub_id=NULL WHERE sub_id=?", (int(sid),))
    db().ex("DELETE FROM substitutions WHERE id=?", (int(sid),))
    db().audit(req.user, req.ip, "subs_deleted", sid)
    return {"ok": True}


@router.route("GET", "/api/subs/plan")
def api_subs_plan(req):
    """Уроки отсутствующего учителя на дату (по расписанию) и свободные учителя на каждый урок."""
    need_manage(req)
    date = _d(req.arg("date")).isoformat()
    absent = req.arg("staff_id", cast=int)
    y = _year_of_date(date)
    need(y, "Не создан учебный год", 400)
    rows = lessons_on(y["id"], date, " AND si.assignment_id IN (SELECT id FROM assignments WHERE teacher_id=?)", (absent,)) if absent else []
    busy = {}
    for r in lessons_on(y["id"], date):
        t = db().val("SELECT teacher_id FROM assignments WHERE id=?", (r["assignment_id"],))
        busy.setdefault(r["lesson_no"], set()).add(t)
    staff = db().q("SELECT id, last_name, first_name, middle_name FROM staff WHERE active=1 ORDER BY last_name, first_name")
    out = []
    for r in rows:
        a = get_assignment(r["assignment_id"])
        free = [{"id": s["id"], "name": R.staff_short(s)} for s in staff if s["id"] != absent and s["id"] not in busy.get(r["lesson_no"], set())]
        # тот же предмет — в начало списка
        same = set(x["teacher_id"] for x in db().q("SELECT a.teacher_id FROM assignments a JOIN classes c ON c.id=a.class_id "
                                                  "WHERE a.subject_id=? AND c.year_id=?", (a["subject_id"], y["id"])))
        free.sort(key=lambda x: (x["id"] not in same, x["name"]))
        ex = db().one("SELECT * FROM substitutions WHERE date=? AND assignment_id=? AND lesson_no=?", (date, a["id"], r["lesson_no"]))
        out.append({"lesson_no": r["lesson_no"], "assignment_id": a["id"], "class_name": a["class_name"],
                    "subject": a["subject"] + (" (" + a["group_name"] + ")" if a["group_name"] else ""),
                    "free": free, "same_subject": [x["id"] for x in free if x["id"] in same],
                    "current": ex["substitute_id"] if ex else None})
    return {"date": date, "lessons": out, "has_schedule": bool(schedule_for(y["id"], date)[0]),
            "staff": [{"id": s["id"], "name": R.staff_short(s)} for s in staff]}


@router.route("GET", "/api/reports/subs")
def rep_subs(req):
    need_school(req)
    f = req.arg("from") or time.strftime("%Y-%m-01")
    t = req.arg("to") or time.strftime("%Y-%m-%d")
    rows, tot = [], {}
    for r in db().q("SELECT * FROM substitutions WHERE date>=? AND date<=? ORDER BY date, lesson_no", (f, t)):
        o = _sub_out(r)
        rows.append([R._ru_date(o["date"]), o["lesson_no"] or "", o["class_name"], o["subject"], o["absent"], o["reason"],
                     o["substitute"], R.fmt(float(o["hours"]), 1).replace(",0", ""), "да" if o["lessons"] else "нет"])
        tot[o["substitute"]] = tot.get(o["substitute"], 0) + float(o["hours"])
    rows.append(["", "", "", "", "", "", "ИТОГО", R.fmt(float(sum(tot.values())), 1).replace(",0", ""), ""])
    for name in sorted(tot):
        rows.append(["", "", "", "", "", "", name, R.fmt(float(tot[name]), 1).replace(",0", ""), ""])
    rep = {"name": "Замещения", "title": "Учёт замещённых уроков",
           "subtitle": "%s\nПериод: %s — %s" % (R.school_name(db()), R._ru_date(f), R._ru_date(t)),
           "headers": ["Дата", "Урок", "Класс", "Предмет", "Отсутствовал", "Причина", "Заменял", "Часов", "Урок в журнале"],
           "widths": [11, 6, 8, 24, 20, 18, 20, 7, 9], "rows": rows}
    return table_response(req, rep)


# =================================================================== ПРИЧИНЫ ПРОПУСКОВ
def _can_class(req, cid):
    need(auth.can_edit_class(db(), req.user, cid) or auth.can_view_all(req.user), "Недостаточно прав", 403)


@router.route("GET", "/api/classroom/:cid/absences")
def api_absences(req, cid):
    """Пропуски класса за месяц по дням: сколько «н» и причина дня."""
    need_school(req)
    _can_class(req, cid)
    month = req.arg("month") or time.strftime("%Y-%m")
    need(re.match(r"^\d{4}-\d{2}$", month), "Неверный месяц", 400)
    studs = R.class_students(db(), int(cid))
    cnt = {}
    for r in db().q("SELECT m.student_id, l.date, COUNT(*) n, GROUP_CONCAT(m.reason, '') rs FROM marks m JOIN lessons l ON l.id=m.lesson_id "
                    "JOIN assignments a ON a.id=l.assignment_id JOIN subjects sb ON sb.id=a.subject_id "
                    "WHERE a.class_id=? AND sb.category='subject' AND m.value='н' AND substr(l.date,1,7)=? GROUP BY m.student_id, l.date",
                    (int(cid), month)):
        cnt["%d_%s" % (r["student_id"], r["date"])] = {"n": r["n"], "teacher_reasons": r["rs"] or ""}
    days = {}
    for r in db().q("SELECT * FROM absence_days WHERE substr(date,1,7)=?", (month,)):
        crypto.dec_row("absence_days", r)
        days["%d_%s" % (r["student_id"], r["date"])] = {"reason": r["reason"], "note": r["note"]}
    y, m = int(month[:4]), int(month[5:])
    nd = (DT.date(y + (m == 12), m % 12 + 1, 1) - DT.timedelta(days=1)).day
    dates = ["%s-%02d" % (month, i) for i in range(1, nd + 1)]
    return {"month": month, "dates": dates, "students": [{"id": s["id"], "name": R.fio(s)} for s in studs],
            "counts": cnt, "days": days, "reasons": REASONS, "can_edit": auth.can_edit_class(db(), req.user, int(cid))}


@router.route("PUT", "/api/classroom/:cid/absences")
def api_absences_put(req, cid):
    need_school(req)
    need(auth.can_edit_class(db(), req.user, int(cid)), "Причину за день указывает классный руководитель", 403)
    d = req.json()
    sid, date = int(d.get("student_id") or 0), _d(d.get("date")).isoformat()
    need(db().val("SELECT 1 FROM class_students WHERE class_id=? AND student_id=?", (int(cid), sid)), "Учащийся не из этого класса", 400)
    reason = d.get("reason") or ""
    need(reason in REASONS or reason == "", "Неизвестная причина", 400)
    if not reason:
        db().ex("DELETE FROM absence_days WHERE student_id=? AND date=?", (sid, date))
    else:
        row = crypto.enc_row("absence_days", {"note": (d.get("note") or "")[:200]})
        db().ex("INSERT INTO absence_days(student_id,date,reason,note,set_by,set_at) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(student_id,date) DO UPDATE SET reason=excluded.reason, note=excluded.note, set_by=excluded.set_by, "
                "set_at=excluded.set_at", (sid, date, reason, row["note"], req.user["id"], now()))
    db().audit(req.user, req.ip, "absence_reason", {"student": sid, "date": date, "reason": reason})
    return {"ok": True}


def absences_report(class_id, date_from, date_to):
    c = R.get_class(db(), class_id)
    studs = R.class_students(db(), class_id)
    args = [class_id]
    cond = ""
    if date_from:
        cond += " AND l.date>=?"
        args.append(date_from)
    if date_to:
        cond += " AND l.date<=?"
        args.append(date_to)
    day = {}
    for r in db().q("SELECT student_id, date, reason FROM absence_days"):
        day[(r["student_id"], r["date"])] = r["reason"]
    agg = {}
    for r in db().q("SELECT m.student_id, l.date, m.reason FROM marks m JOIN lessons l ON l.id=m.lesson_id "
                    "JOIN assignments a ON a.id=l.assignment_id JOIN subjects sb ON sb.id=a.subject_id "
                    "WHERE a.class_id=? AND sb.category='subject' AND m.value='н'" + cond, args):
        rs = day.get((r["student_id"], r["date"])) or r["reason"] or ""
        a = agg.setdefault(r["student_id"], {"all": 0, "б": 0, "у": 0, "н": 0, "": 0, "days": set()})
        a["all"] += 1
        a[rs if rs in REASONS else ""] += 1
        a["days"].add(r["date"])
    rows = []
    tot = {"all": 0, "б": 0, "у": 0, "н": 0, "": 0, "days": 0}
    for i, s in enumerate(studs, 1):
        a = agg.get(s["id"], {"all": 0, "б": 0, "у": 0, "н": 0, "": 0, "days": set()})
        rows.append([i, R.fio(s), a["all"] or "", len(a["days"]) or "", a["б"] or "", a["у"] or "", a["н"] or "", a[""] or ""])
        for k in ("all", "б", "у", "н", ""):
            tot[k] += a[k]
        tot["days"] += len(a["days"])
    rows.append(["", "ИТОГО", tot["all"], tot["days"], tot["б"], tot["у"], tot["н"], tot[""]])
    return {"name": "Пропуски " + R.class_name(c), "title": "Пропуски учебных занятий по причинам: %s класс" % R.class_name(c),
            "subtitle": "%s\nПериод: %s — %s" % (R.school_name(db()), R._ru_date(date_from) or "начало года", R._ru_date(date_to) or "сегодня"),
            "headers": ["№", "Фамилия, имя", "Пропущено уроков", "Дней с пропусками", "По болезни", "По уважительной", "Без уважительной", "Причина не указана"],
            "widths": [4, 28, 10, 10, 10, 11, 11, 11], "rows": rows}


@router.route("GET", "/api/reports/absences")
def rep_absences(req):
    need_school(req)
    cid = req.arg("class_id", cast=int)
    need(cid, "Выберите класс", 400)
    need(auth.can_view_class(db(), req.user, cid), "Недостаточно прав", 403)
    return table_response(req, absences_report(cid, req.arg("from") or req.arg("date_from"), req.arg("to") or req.arg("date_to")))


# =================================================================== ПРОГНОЗ И ГРУППА РИСКА
def _period(year_id, kind):
    return db().one("SELECT * FROM periods WHERE year_id=? AND kind=?", (year_id, kind))


@router.route("GET", "/api/reports/risk")
def rep_risk(req):
    """Прогноз отметки за период по текущим отметкам и учащиеся группы риска."""
    need_school(req)
    y = year_arg(req)
    kind = req.arg("kind") or R.current_kind(db(), y)
    p = _period(y, kind)
    need(p and p["start_date"] and p["end_date"], "У периода не заданы даты", 400)
    rl = R.rules(db())
    only_risk = req.arg("all") != "1"
    asg = _scoped_assignments(req, y, req.arg("class_id", cast=int))
    rows = []
    stats = {"low": 0, "few": 0, "abs": 0}
    for a in sorted(asg, key=lambda x: (x["grade"], x["letter"], x["sort"], x["subject"])):
        if a["eval_type"] != "points":
            continue
        lessons = db().q("SELECT id FROM lessons WHERE assignment_id=? AND date>=? AND date<=?", (a["id"], p["start_date"], p["end_date"]))
        if not lessons:
            continue
        lids = [l["id"] for l in lessons]
        marks = {}
        for m in db().q("SELECT student_id, value FROM marks WHERE lesson_id IN (%s)" % ",".join("?" * len(lids)), lids):
            marks.setdefault(m["student_id"], []).append(m["value"])
        need_n = rl["min_marks_quarter_1h"] if float(a["hours"] or 1) <= 1 else rl["min_marks_quarter"]
        for s in R.assignment_students(db(), a):
            vals = marks.get(s["id"], [])
            pts = [int(v) for v in vals if G.is_point(v)]
            absn = sum(1 for v in vals if v == G.ABSENT)
            avg = sum(pts) / float(len(pts)) if pts else None
            prog = int(avg + 0.5 + 1e-9) if avg is not None else None
            flags = []
            if prog is not None and prog <= 2:
                flags.append("низкий уровень (1–2)")
                stats["low"] += 1
            if len(pts) < need_n:
                flags.append("мало отметок: %d из %d" % (len(pts), need_n))
                stats["few"] += 1
            if lessons and absn * 2 > len(lessons):
                flags.append("пропущено больше половины уроков")
                stats["abs"] += 1
            if only_risk and not flags:
                continue
            rows.append([R.class_name(a), a["subject"] + (" (" + a["group_name"] + ")" if a["group_name"] else ""), R.teacher_of(a),
                         R.fio(s), len(pts), R.fmt(avg, 2) if avg is not None else "", prog if prog is not None else "",
                         "%d из %d" % (absn, len(lessons)), "; ".join(flags)])
    rep = {"name": "Группа риска", "title": "Прогноз отметок за %s и группа риска" % R.kind_title(db(), kind).lower(),
           "subtitle": "%s\nНа %s. Прогноз — среднее арифметическое текущих отметок периода с математическим округлением." % (
               R.school_name(db()), R._ru_date(time.strftime("%Y-%m-%d"))),
           "headers": ["Класс", "Предмет", "Учитель", "Учащийся", "Отметок", "Средний балл", "Прогноз", "Пропуски", "Риск"],
           "widths": [8, 22, 18, 24, 8, 9, 8, 10, 40], "rows": rows,
           "footer": ["Низкий уровень: %d · мало отметок (риск н/а): %d · много пропусков: %d" % (stats["low"], stats["few"], stats["abs"])]}
    return table_response(req, rep)


# =================================================================== МОНИТОРИНГ ЖУРНАЛА
@router.route("GET", "/api/reports/monitor")
def rep_monitor(req):
    need_school(req)
    need_manage(req)
    t = req.arg("to") or time.strftime("%Y-%m-%d")
    f = req.arg("from") or (_d(t) - DT.timedelta(days=6)).isoformat()
    y = year_arg(req)
    asg = _scoped_assignments(req, y, req.arg("class_id", cast=int))
    days = []
    d = _d(f)
    while d <= _d(t):
        days.append(d.isoformat())
        d += DT.timedelta(days=1)
    sched_count = {}
    has_sched = False
    for day in days:
        for r in lessons_on(y, day):
            has_sched = True
            sched_count[r["assignment_id"]] = sched_count.get(r["assignment_id"], 0) + 1
    rows = []
    problems = 0
    for a in sorted(asg, key=lambda x: (R.teacher_of(x), x["grade"], x["letter"], x["subject"])):
        ls = db().q("SELECT * FROM lessons WHERE assignment_id=? AND date>=? AND date<=?", (a["id"], f, t))
        n = len(ls)
        no_topic = sum(1 for l in ls if not (l["topic"] or "").strip())
        no_hw = sum(1 for l in ls if not (l["homework"] or "").strip())
        no_marks = 0
        for l in ls:
            if not db().val("SELECT 1 FROM marks WHERE lesson_id=? AND value<>'н' LIMIT 1", (l["id"],)):
                no_marks += 1
        plan = sched_count.get(a["id"]) if has_sched else None
        lag = (plan - n) if plan is not None and plan > n else 0
        last = db().val("SELECT MAX(date) FROM lessons WHERE assignment_id=?", (a["id"],))
        issues = []
        if lag:
            issues.append("не заполнено уроков: %d" % lag)
        if no_topic:
            issues.append("без темы: %d" % no_topic)
        if no_hw:
            issues.append("без д/з: %d" % no_hw)
        if n and no_marks == n:
            issues.append("нет отметок")
        if plan is None and not n:
            issues.append("нет уроков за период")
        if issues:
            problems += 1
        if req.arg("all") != "1" and not issues:
            continue
        rows.append([R.teacher_of(a), R.class_name(a), a["subject"] + (" (" + a["group_name"] + ")" if a["group_name"] else ""),
                     "" if plan is None else plan, n, no_topic or "", no_hw or "", no_marks or "", R._ru_date(last) if last else "—",
                     "; ".join(issues) or "в порядке"])
    rep = {"name": "Мониторинг журнала", "title": "Мониторинг заполнения электронного журнала",
           "subtitle": "%s\nПериод: %s — %s. %s" % (R.school_name(db()), R._ru_date(f), R._ru_date(t),
                                                   "«По расписанию» — число уроков в расписании за период." if has_sched else
                                                   "Расписание не введено — сравнение с расписанием не выполняется."),
           "headers": ["Учитель", "Класс", "Предмет", "По расписанию", "В журнале", "Без темы", "Без д/з", "Без отметок",
                       "Последний урок", "Замечания"],
           "widths": [18, 8, 22, 9, 8, 7, 7, 8, 11, 40], "rows": rows,
           "footer": ["Нагрузок с замечаниями: %d из %d" % (problems, len(asg))]}
    return table_response(req, rep)


# =================================================================== СРАВНИТЕЛЬНЫЙ АНАЛИЗ
@router.route("GET", "/api/reports/analysis")
def rep_analysis(req):
    """Средний балл и качество знаний по годам и периодам в разрезе учителей, классов, параллелей или предметов."""
    need_school(req)
    group = req.arg("group") or "parallel"
    need(group in ("teacher", "class", "parallel", "subject", "school"), "Неверная группировка", 400)
    subject_id = req.arg("subject_id", cast=int)
    kinds = (req.arg("kinds") or "q1,q2,q3,q4,h1,h2,year").split(",")
    rl = R.rules(db())
    years = db().q("SELECT * FROM years ORDER BY start_date")
    if req.arg("years"):
        want = set(int(x) for x in req.arg("years").split(",") if x.isdigit())
        years = [y for y in years if y["id"] in want]
    cols = []
    for y in years:
        for k in kinds:
            if db().val("SELECT 1 FROM final_marks fm JOIN assignments a ON a.id=fm.assignment_id JOIN classes c ON c.id=a.class_id "
                        "WHERE c.year_id=? AND fm.kind=? LIMIT 1", (y["id"], k)):
                cols.append({"year_id": y["id"], "year": y["name"], "kind": k, "label": "%s %s" % (y["name"], R.kind_title(db(), k))})
    series = {}
    view_all = auth.can_view_all(req.user)
    mine = set(auth.teacher_assignment_ids(db(), req.user)) if not view_all else None
    own = set(auth.own_class_ids(db(), req.user)) if not view_all else None
    for ci, col in enumerate(cols):
        sql = ("SELECT fm.value, a.id AS aid, a.class_id, a.teacher_id, c.grade, c.letter, sb.name AS subject, st.last_name, st.first_name, "
               "st.middle_name FROM final_marks fm JOIN assignments a ON a.id=fm.assignment_id JOIN classes c ON c.id=a.class_id "
               "JOIN subjects sb ON sb.id=a.subject_id LEFT JOIN staff st ON st.id=a.teacher_id "
               "WHERE c.year_id=? AND fm.kind=? AND sb.category='subject'")
        args = [col["year_id"], col["kind"]]
        if subject_id:
            sql += " AND a.subject_id=?"
            args.append(subject_id)
        for r in db().q(sql, args):
            if not view_all and r["aid"] not in mine and r["class_id"] not in own:
                continue
            if group == "teacher":
                key = R.staff_short(r) if r["teacher_id"] else "без учителя"
            elif group == "class":
                key = R.class_name(r)          # между годами сравнивается одноимённый класс
            elif group == "parallel":
                key = "%d классы" % r["grade"]
            elif group == "subject":
                key = r["subject"]
            else:
                key = "Учреждение"
            series.setdefault(key, [[] for _ in cols])[ci].append(r["value"])
    rows = []
    for key in sorted(series, key=lambda k: (int(re.match(r"\d+", k).group()) if group == "parallel" and re.match(r"\d+", k) else 0, k)):
        vals = []
        for v in series[key]:
            st = G.stats(v, rl)
            vals.append({"avg": round(st["avg"], 2) if st["avg"] is not None else None,
                         "kz": round(st["kz"], 1) if st["kz"] is not None else None, "n": st["count"]})
        rows.append({"name": key, "values": vals})
    if req.arg("format") == "xlsx":
        metric = req.arg("metric") or "avg"
        rep = {"name": "Анализ", "title": "Сравнительный анализ: %s" % ("средний балл" if metric == "avg" else "качество знаний, %"),
               "subtitle": R.school_name(db()), "headers": [""] + [c["label"] for c in cols],
               "widths": [24] + [11] * len(cols),
               "rows": [[r["name"]] + [R.fmt(float(v[metric]), 2 if metric == "avg" else 1) if v[metric] is not None else "" for v in r["values"]]
                        for r in rows]}
        return table_response(req, rep)
    subjects = db().q("SELECT id, name FROM subjects WHERE category='subject' AND active=1 ORDER BY sort, name")
    return {"columns": cols, "rows": rows, "subjects": subjects, "kz_min": rl.get("kz_min_point")}


# =================================================================== КАБИНЕТ КЛАССНОГО РУКОВОДИТЕЛЯ
SOCIAL = [
    ("many", "Многодетная семья"), ("single", "Неполная семья"), ("orphan", "Сирота / без попечения родителей (опека, приёмная семья, ДДСТ)"),
    ("disabled", "Ребёнок-инвалид"), ("par_disabled", "Родитель(и) — инвалид(ы)"), ("opfr", "Ребёнок с ОПФР"),
    ("sop", "Семья в социально опасном положении (СОП)"), ("ngz", "Признан нуждающимся в государственной защите"),
    ("idn", "Состоит на учёте в ИДН"), ("school_reg", "На внутришкольном учёте"), ("lowincome", "Малообеспеченная семья (гос. адресная помощь)"),
    ("chernobyl", "Пострадавшие от катастрофы на ЧАЭС / проживающие на загрязнённой территории"),
    ("military", "Семья военнослужащего"), ("migrant", "Семья беженцев / вынужденных мигрантов"),
]


def _cr_need(req, cid, edit=False):
    need_school(req)
    if edit:
        need(auth.can_edit_class(db(), req.user, int(cid)), "Только классный руководитель или администрация", 403)
    else:
        need(auth.can_edit_class(db(), req.user, int(cid)) or auth.can_view_all(req.user), "Недостаточно прав", 403)


def _activities_auto(cid, year_id, sid):
    """Факультативы и объединения в учреждении, где занимается учащийся."""
    out = []
    for a in db().q("SELECT a.id, a.class_id, a.group_name, sb.name AS subject, sb.category, st.last_name AS t_last, st.first_name AS t_first, "
                    "st.middle_name AS t_middle FROM assignments a JOIN subjects sb ON sb.id=a.subject_id JOIN classes c ON c.id=a.class_id "
                    "LEFT JOIN staff st ON st.id=a.teacher_id WHERE c.year_id=? AND sb.category<>'subject'", (year_id,)):
        enrolled = db().val("SELECT 1 FROM assignment_students WHERE assignment_id=? AND student_id=?", (a["id"], sid))
        has_list = db().val("SELECT 1 FROM assignment_students WHERE assignment_id=? LIMIT 1", (a["id"],))
        if enrolled or (not has_list and a["class_id"] == int(cid)):
            out.append({"name": a["subject"] + (" (" + a["group_name"] + ")" if a["group_name"] else ""),
                        "kind": "ФЗ" if a["category"] == "fz" else "ОпИ", "leader": R.teacher_of(a)})
    return out


@router.route("GET", "/api/classroom/:cid")
def api_classroom(req, cid):
    _cr_need(req, cid)
    c = R.get_class(db(), int(cid))
    need(c, "Класс не найден", 404)
    studs = R.class_students(db(), int(cid))
    out = []
    for s in studs:
        par = [crypto.dec_row("parents", r) for r in db().q("SELECT * FROM parents WHERE student_id=? ORDER BY id", (s["id"],))]
        soc = db().one("SELECT * FROM student_social WHERE student_id=? AND year_id=?", (s["id"], c["year_id"]))
        crypto.dec_row("student_social", soc)
        ch = db().one("SELECT * FROM characteristics WHERE student_id=? AND year_id=?", (s["id"], c["year_id"]))
        crypto.dec_row("characteristics", ch)
        acts = db().q("SELECT * FROM activities WHERE student_id=? AND year_id=? ORDER BY id", (s["id"], c["year_id"]))
        out.append({"id": s["id"], "name": R.fio(s, True), "birth_date": s.get("birth_date") or "", "address": s.get("address") or "",
                    "phone": s.get("phone") or "", "old_parents": s.get("parents") or "", "old_parents_phone": s.get("parents_phone") or "",
                    "parents": par, "social": [x for x in ((soc or {}).get("categories") or "").split(",") if x],
                    "social_note": (soc or {}).get("note") or "", "characteristic": (ch or {}).get("text") or "",
                    "char_updated": (ch or {}).get("updated_at") or "", "activities": acts,
                    "clubs": _activities_auto(cid, c["year_id"], s["id"])})
    return {"class": {"id": c["id"], "name": R.class_name(c), "teacher": c.get("teacher") or "", "year_id": c["year_id"]},
            "students": out, "social_list": [{"key": k, "name": n} for k, n in SOCIAL],
            "can_edit": auth.can_edit_class(db(), req.user, int(cid))}


def _student_in_class(cid, sid):
    need(db().val("SELECT 1 FROM class_students WHERE class_id=? AND student_id=?", (int(cid), int(sid))), "Учащийся не из этого класса", 400)


@router.route("PUT", "/api/classroom/:cid/student/:sid")
def api_classroom_student(req, cid, sid):
    """Сохранить данные учащегося в кабинете классного руководителя (передаются только изменяемые разделы)."""
    _cr_need(req, cid, edit=True)
    _student_in_class(cid, sid)
    c = R.get_class(db(), int(cid))
    d = req.json()
    sid = int(sid)
    with db().tx():
        if "parents" in d:
            db().ex("DELETE FROM parents WHERE student_id=?", (sid,))
            for p in d["parents"][:6]:
                row = {"student_id": sid, "relation": (p.get("relation") or "")[:40], "fio": (p.get("fio") or "")[:120],
                       "phone": (p.get("phone") or "")[:60], "work": (p.get("work") or "")[:200], "note": (p.get("note") or "")[:300]}
                if row["fio"] or row["phone"]:
                    insert("parents", crypto.enc_row("parents", row))
        if "social" in d or "social_note" in d:
            keys = [k for k in (d.get("social") or []) if k in dict(SOCIAL)]
            row = crypto.enc_row("student_social", {"categories": ",".join(keys), "note": (d.get("social_note") or "")[:500]})
            db().ex("INSERT INTO student_social(student_id,year_id,categories,note) VALUES(?,?,?,?) ON CONFLICT(student_id,year_id) "
                    "DO UPDATE SET categories=excluded.categories, note=excluded.note", (sid, c["year_id"], row["categories"], row["note"]))
        if "characteristic" in d:
            txt = crypto.encrypt((d.get("characteristic") or "")[:20000])
            db().ex("INSERT INTO characteristics(student_id,year_id,text,updated_by,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(student_id,year_id) "
                    "DO UPDATE SET text=excluded.text, updated_by=excluded.updated_by, updated_at=excluded.updated_at",
                    (sid, c["year_id"], txt, req.user["id"], now()))
        if "activities" in d:
            db().ex("DELETE FROM activities WHERE student_id=? AND year_id=?", (sid, c["year_id"]))
            for a in d["activities"][:20]:
                if (a.get("name") or "").strip():
                    insert("activities", {"student_id": sid, "year_id": c["year_id"], "name": a["name"].strip()[:150],
                                          "place": (a.get("place") or "")[:150], "leader": (a.get("leader") or "")[:120],
                                          "hours": (a.get("hours") or "")[:60], "note": (a.get("note") or "")[:200]})
    db().audit(req.user, req.ip, "classroom_saved", {"class": int(cid), "student": sid, "parts": sorted(d.keys())})
    return {"ok": True}


@router.route("GET", "/api/classroom/:cid/social")
def api_classroom_social(req, cid):
    """Социальный паспорт класса: сводная таблица по категориям."""
    _cr_need(req, cid)
    c = R.get_class(db(), int(cid))
    studs = R.class_students(db(), int(cid))
    by = dict((k, []) for k, _n in SOCIAL)
    for s in studs:
        soc = crypto.dec_row("student_social", db().one("SELECT * FROM student_social WHERE student_id=? AND year_id=?", (s["id"], c["year_id"])))
        for k in ((soc or {}).get("categories") or "").split(","):
            if k in by:
                by[k].append(R.fio(s))
    boys = sum(1 for s in studs if (s.get("gender") or "").lower().startswith("м"))
    girls = sum(1 for s in studs if (s.get("gender") or "").lower().startswith("ж"))
    rows = [["Всего учащихся", len(studs), ""], ["из них мальчиков / девочек", "%d / %d" % (boys, girls), ""]]
    for k, n in SOCIAL:
        rows.append([n, len(by[k]) or "", ", ".join(by[k])])
    rep = {"name": "Соцпаспорт " + R.class_name(c), "title": "Социальный паспорт %s класса" % R.class_name(c),
           "subtitle": "%s\nКлассный руководитель: %s" % (R.school_name(db()), c.get("teacher") or "—"),
           "headers": ["Категория", "Количество", "Учащиеся"], "widths": [50, 11, 60], "rows": rows}
    return table_response(req, rep)


@router.route("GET", "/api/classroom/:cid/contacts")
def api_classroom_contacts(req, cid):
    _cr_need(req, cid)
    c = R.get_class(db(), int(cid))
    rows = []
    for i, s in enumerate(R.class_students(db(), int(cid)), 1):
        par = [crypto.dec_row("parents", r) for r in db().q("SELECT * FROM parents WHERE student_id=? ORDER BY id", (s["id"],))]
        if not par and (s.get("parents") or s.get("parents_phone")):
            par = [{"relation": "", "fio": s.get("parents") or "", "phone": s.get("parents_phone") or "", "work": ""}]
        rows.append([i, R.fio(s, True), s.get("address") or "",
                     "\n".join(("%s: " % p["relation"] if p.get("relation") else "") + (p.get("fio") or "") for p in par),
                     "\n".join(p.get("phone") or "" for p in par), "\n".join(p.get("work") or "" for p in par)])
    rep = {"name": "Контакты " + R.class_name(c), "title": "Сведения о родителях (законных представителях): %s класс" % R.class_name(c),
           "subtitle": R.school_name(db()), "headers": ["№", "Учащийся", "Адрес", "Родители (законные представители)", "Телефон", "Место работы"],
           "widths": [4, 26, 30, 34, 18, 30], "rows": rows}
    return table_response(req, rep)


@router.route("GET", "/api/classroom/:cid/activities")
def api_classroom_activities(req, cid):
    _cr_need(req, cid)
    c = R.get_class(db(), int(cid))
    rows, busy = [], 0
    studs = R.class_students(db(), int(cid))
    for i, s in enumerate(studs, 1):
        inside = _activities_auto(cid, c["year_id"], s["id"])
        outside = db().q("SELECT * FROM activities WHERE student_id=? AND year_id=?", (s["id"], c["year_id"]))
        if inside or outside:
            busy += 1
        rows.append([i, R.fio(s), "; ".join("%s (%s)" % (x["name"], x["kind"]) for x in inside),
                     "; ".join(x["name"] + (", " + x["place"] if x["place"] else "") for x in outside)])
    rows.append(["", "Занято во внеурочное время", "%d из %d (%s %%)" % (busy, len(studs), R.fmt(busy * 100.0 / len(studs), 0) if studs else "0"), ""])
    rep = {"name": "Занятость " + R.class_name(c), "title": "Внеурочная занятость учащихся %s класса" % R.class_name(c),
           "subtitle": R.school_name(db()), "headers": ["№", "Учащийся", "В учреждении (ФЗ, ОпИ)", "Вне учреждения"],
           "widths": [4, 26, 50, 50], "rows": rows}
    return table_response(req, rep)


# ----- характеристика в Word (.docx) без сторонних библиотек
def _docx(title, paragraphs, sign_lines):
    def esc(t):
        return (t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def para(text, bold=False, center=False, size=28, indent=True, after=120):
        ppr = '<w:pPr><w:spacing w:after="%d" w:line="276" w:lineRule="auto"/>%s%s</w:pPr>' % (
            after, '<w:jc w:val="center"/>' if center else '<w:jc w:val="both"/>', '<w:ind w:firstLine="709"/>' if indent and not center else "")
        rpr = '<w:rPr><w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:cs="Times New Roman"/>%s<w:sz w:val="%d"/></w:rPr>' % (
            "<w:b/>" if bold else "", size)
        return '<w:p>%s<w:r>%s<w:t xml:space="preserve">%s</w:t></w:r></w:p>' % (ppr, rpr, esc(text))
    body = para(title, bold=True, center=True, after=240)
    for ptxt in paragraphs:
        body += para(ptxt)
    body += para("", indent=False)
    for ln in sign_lines:
        body += para(ln, indent=False, after=60)
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>%s'
           '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1134" w:right="850" w:bottom="1134" w:left="1701" '
           'w:header="708" w:footer="708" w:gutter="0"/></w:sectPr></w:body></w:document>') % body
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                   '</Types>')
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                   '</Relationships>')
        z.writestr("word/document.xml", doc)
    return buf.getvalue()


@router.route("GET", "/api/classroom/:cid/characteristic/:sid")
def api_characteristic_docx(req, cid, sid):
    _cr_need(req, cid)
    _student_in_class(cid, sid)
    c = R.get_class(db(), int(cid))
    s = db().one("SELECT * FROM students WHERE id=?", (int(sid),))
    ch = crypto.dec_row("characteristics", db().one("SELECT * FROM characteristics WHERE student_id=? AND year_id=?", (int(sid), c["year_id"])))
    text = (ch or {}).get("text") or ""
    school = db().get_setting("school", {}) or {}
    title = "ХАРАКТЕРИСТИКА\nучащегося %s класса %s\n%s" % (R.class_name(c), school.get("Наименование", ""), R.fio(s, True))
    paras = [p.strip() for p in re.split(r"\n\s*\n|\n", text) if p.strip()] or ["(текст характеристики не заполнен)"]
    docx = _docx(title.replace("\n", " "), paras, ["Классный руководитель ____________ " + (c.get("teacher") or ""),
                                                    "Директор ____________ " + (school.get("Директор") or ""), "", R._ru_date(time.strftime("%Y-%m-%d"))])
    return FileResponse(docx, "Характеристика_%s_%s.docx" % (s["last_name"], R.class_name(c).replace(" ", "")),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
