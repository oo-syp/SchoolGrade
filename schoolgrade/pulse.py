# -*- coding: utf-8 -*-
"""SchoolPulse («Активность», SchoolGrade 1.6): события на уроке и вне урока,
отсутствия учащихся, рекомендации по поведению, «красные флаги», публичный экран класса."""
import datetime as DT
import json
import queue
import re
import threading
import uuid

from . import auth, reports as R
from .api import (router, db, need, need_admin, need_school, current_year, ApiError, APP, table_response)
from .v15 import bells, _mins, kiosk_enabled

ARCHIVE_YEARS = 3
FMT = "%Y-%m-%d %H:%M:%S"
CLOCK = {"fn": DT.datetime.now}          # тесты подменяют часы; время события берётся ТОЛЬКО отсюда (серверное)

DEFAULT_CFG = {"base": "уд.", "pos_step": 5, "neg_step": 3, "flag_negatives": 3, "flag_absence_min": 20}

ICON_EXIT = '<svg viewBox="0 0 24 24"><path d="M10 4H5v16h5M14 8l4 4-4 4M8 12h10" fill="none" stroke="currentColor" stroke-width="2"/></svg>'
ICON_BACK = '<svg viewBox="0 0 24 24"><path d="M14 4h5v16h-5M10 8l-4 4 4 4M6 12h10" fill="none" stroke="currentColor" stroke-width="2"/></svg>'
ICON_CLOCK = '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8" fill="none" stroke="currentColor" stroke-width="2"/><path d="M12 7v5l3 2" fill="none" stroke="currentColor" stroke-width="2"/></svg>'
ICON_PLUS = '<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="3" fill="none"/></svg>'
ICON_MINUS = '<svg viewBox="0 0 24 24"><path d="M5 12h14" stroke="currentColor" stroke-width="3" fill="none"/></svg>'
ICON_BOOK = '<svg viewBox="0 0 24 24"><path d="M5 5h6a2 2 0 012 2v12a2 2 0 00-2-2H5zM19 5h-6" fill="none" stroke="currentColor" stroke-width="2"/></svg>'
ICON_FLAG = '<svg viewBox="0 0 24 24"><path d="M6 21V4h11l-2 4 2 4H6" fill="none" stroke="currentColor" stroke-width="2"/></svg>'

# code, name, category, role, polarity, color, icon, is_active, extracurricular, affects, weight, max_per_lesson, sort
DEFAULT_TYPES = [
    ("~ушёл", "Вышел из класса", "presence", "exit", "none", "#6e6e73", ICON_EXIT, 1, 0, 0, 0.0, 0, 10),
    ("~вернулся", "Вернулся в класс", "presence", "return", "none", "#1a7f37", ICON_BACK, 1, 0, 0, 0.0, 0, 20),
    ("-опоздание", "Опоздание на урок", "presence", "late", "neg", "#9a6700", ICON_CLOCK, 1, 0, 1, 1.0, 1, 30),
    ("+ответил", "Активный устный ответ", "positive", "", "pos", "#1a7f37", ICON_PLUS, 1, 0, 1, 1.0, 0, 40),
    ("+опорный конспект", "Ведёт конспект, работает", "positive", "", "pos", "#1a7f37", ICON_BOOK, 1, 0, 1, 1.0, 0, 50),
    ("-поведение", "Замечание по дисциплине", "negative", "", "neg", "#cf222e", ICON_MINUS, 1, 0, 1, 1.0, 0, 60),
    ("-нет ДЗ", "Не готов к уроку", "negative", "", "neg", "#9a6700", ICON_MINUS, 1, 0, 1, 1.0, 1, 70),
    ("!дежурство", "Нарушение дежурства", "extra", "", "neg", "#0969da", ICON_FLAG, 0, 1, 1, 0.5, 0, 80),
    ("!столовая", "Нарушение в столовой", "extra", "", "neg", "#0969da", ICON_FLAG, 0, 1, 1, 0.5, 0, 90),
]
TYPE_FIELDS = ["code", "name", "category", "role", "polarity", "color", "icon", "is_active", "is_extracurricular",
               "affects_behavior_recommendation", "behavior_weight", "max_per_lesson", "sort_order"]
CATEGORIES = {"presence": "Присутствие", "positive": "Позитив", "negative": "Негатив", "extra": "Внеурочные"}


def now_dt():
    return CLOCK["fn"]().replace(microsecond=0)


def _s(dt):
    return dt.strftime(FMT)


def _p(s):
    return DT.datetime.strptime(s[:19], FMT)


def today():
    return now_dt().date().isoformat()


def app():
    return APP["app"]


# ------------------------------------------------------------------ справочник типов
def ensure_types():
    d = db()
    if getattr(d, "_pulse_ready", False):
        return
    if d.val("SELECT COUNT(*) FROM pulse_types") == 0 and not d.get_setting("pulse_types_seeded", False):
        for t in DEFAULT_TYPES:
            d.ex("INSERT INTO pulse_types(%s) VALUES(%s)" % (",".join(TYPE_FIELDS), ",".join("?" * len(TYPE_FIELDS))), t)
        d.set_setting("pulse_types_seeded", True)
    d._pulse_ready = True


def cfg():
    c = dict(DEFAULT_CFG)
    c.update(db().get_setting("pulse_cfg", {}) or {})
    return c


_SVG_BAD = re.compile(r"<\s*(script|foreignobject|iframe|object|embed|style|image|a)\b|on\w+\s*=|javascript:|data:|<!|&#", re.I)
_SVG_TAGS = re.compile(r"</?\s*([a-zA-Z]+)")


def clean_icon(s):
    s = (s or "").strip()
    if not s:
        return ""
    if len(s) > 3000 or not s.lower().startswith("<svg") or _SVG_BAD.search(s):
        raise ApiError(400, "Иконка должна быть простым SVG без скриптов и ссылок")
    for t in _SVG_TAGS.findall(s):
        if t.lower() not in ("svg", "path", "circle", "rect", "line", "polyline", "polygon", "ellipse", "g"):
            raise ApiError(400, "В SVG-иконке допустимы только path, circle, rect, line, polyline, polygon, ellipse, g")
    return s


def _color(s):
    s = (s or "").strip()
    if not re.match(r"^#[0-9a-fA-F]{6}$", s):
        raise ApiError(400, "Цвет задаётся в формате #RRGGBB")
    return s


def _type_data(d, partial=False):
    out = {}
    if "code" in d or not partial:
        c = (d.get("code") or "").strip()
        need(1 <= len(c) <= 40, "Укажите код типа", 400)
        out["code"] = c
    if "name" in d or not partial:
        n = (d.get("name") or "").strip()
        need(n, "Укажите название типа", 400)
        out["name"] = n[:80]
    if "category" in d or not partial:
        cat = d.get("category") or "positive"
        need(cat in CATEGORIES, "Неизвестная категория", 400)
        out["category"] = cat
    if "role" in d:
        need(d["role"] in ("", "exit", "return", "late"), "Неизвестная роль типа", 400)
        out["role"] = d["role"]
    if "polarity" in d:
        need(d["polarity"] in ("pos", "neg", "none"), "Неизвестная полярность", 400)
        out["polarity"] = d["polarity"]
    if "color" in d or not partial:
        out["color"] = _color(d.get("color") or "#6e6e73")
    if "icon" in d:
        out["icon"] = clean_icon(d.get("icon"))
    for k in ("is_active", "is_extracurricular", "affects_behavior_recommendation"):
        if k in d:
            out[k] = 1 if d[k] else 0
    if "behavior_weight" in d:
        try:
            w = float(d["behavior_weight"])
        except (TypeError, ValueError):
            raise ApiError(400, "Вес должен быть числом")
        need(0 <= w <= 10, "Вес от 0 до 10", 400)
        out["behavior_weight"] = w
    for k in ("max_per_lesson", "sort_order"):
        if k in d:
            try:
                out[k] = max(0, int(d[k]))
            except (TypeError, ValueError):
                raise ApiError(400, "Неверное число: " + k)
    return out


def _visible_codes(u):
    m = db().get_setting("pulse_visible_types", {}) or {}
    return m.get(str(u["id"]))


def can_input_role(u):
    return u["role"] in ("admin", "workspace", "teacher", "classteacher")


def is_admin_like(u):
    return u["role"] in ("admin", "workspace")


@router.route("GET", "/api/pulse/event-types")
def api_types(req):
    need_school(req)
    ensure_types()
    u = req.user
    rows = db().q("SELECT * FROM pulse_types ORDER BY sort_order, id")
    if req.arg("all") and auth.can_admin(u):
        return {"types": rows, "categories": CATEGORIES, "config": cfg()}
    vis = _visible_codes(u)
    rows = [t for t in rows if t["is_active"]]
    allowed = list(rows)
    if vis is not None:
        rows = [t for t in rows if t["code"] in vis]
    return {"types": rows, "all_active": [{"code": t["code"], "name": t["name"]} for t in allowed],
            "visible": vis, "categories": CATEGORIES}


@router.route("POST", "/api/pulse/event-types")
def api_type_add(req):
    need_admin(req)
    ensure_types()
    d = _type_data(req.json())
    need(not db().val("SELECT 1 FROM pulse_types WHERE code=?", (d["code"],)), "Тип с таким кодом уже есть", 400)
    d.setdefault("sort_order", (db().val("SELECT MAX(sort_order) FROM pulse_types") or 0) + 10)
    tid = db().ex("INSERT INTO pulse_types(%s) VALUES(%s)" % (",".join(d), ",".join("?" * len(d))), list(d.values()))
    db().audit(req.user, req.ip, "pulse_type_add", d["code"])
    return {"id": tid}


@router.route("PUT", "/api/pulse/event-types/:tid")
def api_type_put(req, tid):
    need_admin(req)
    t = db().one("SELECT * FROM pulse_types WHERE id=?", (int(tid),))
    need(t, "Тип не найден", 404)
    d = _type_data(req.json(), partial=True)
    if "code" in d and d["code"] != t["code"]:
        need(not db().val("SELECT 1 FROM pulse_types WHERE code=? AND id<>?", (d["code"], t["id"])), "Тип с таким кодом уже есть", 400)
    if d:
        db().ex("UPDATE pulse_types SET %s WHERE id=?" % ",".join(k + "=?" for k in d), list(d.values()) + [t["id"]])
    db().audit(req.user, req.ip, "pulse_type_edit", dict(d, id=t["id"]))
    return {"ok": True}


@router.route("DELETE", "/api/pulse/event-types/:tid")
def api_type_del(req, tid):
    need_admin(req)
    t = db().one("SELECT * FROM pulse_types WHERE id=?", (int(tid),))
    need(t, "Тип не найден", 404)
    used = db().val("SELECT 1 FROM pulse_events WHERE type_id=? LIMIT 1", (t["id"],)) or \
        db().val("SELECT 1 FROM pulse_events_archive WHERE type_id=? LIMIT 1", (t["id"],))
    if used:
        db().ex("UPDATE pulse_types SET is_active=0 WHERE id=?", (t["id"],))
        db().audit(req.user, req.ip, "pulse_type_off", t["code"])
        return {"ok": True, "deactivated": True}
    db().ex("DELETE FROM pulse_types WHERE id=?", (t["id"],))
    db().audit(req.user, req.ip, "pulse_type_del", t["code"])
    return {"ok": True}


@router.route("PUT", "/api/pulse/visible-types")
def api_visible_put(req):
    """Какие типы события видит пользователь на экране ввода. Себе — любой, другим — администратор."""
    d = req.json()
    uid = int(d.get("user_id") or req.user["id"])
    need(uid == req.user["id"] or auth.can_admin(req.user), "Недостаточно прав")
    codes = d.get("codes")
    m = db().get_setting("pulse_visible_types", {}) or {}
    if codes is None:
        m.pop(str(uid), None)
    else:
        known = set(r["code"] for r in db().q("SELECT code FROM pulse_types"))
        m[str(uid)] = [c for c in codes if c in known]
    db().set_setting("pulse_visible_types", m)
    return {"ok": True}


@router.route("GET", "/api/pulse/config")
def api_cfg_get(req):
    need_school(req)
    return cfg()


@router.route("PUT", "/api/pulse/config")
def api_cfg_put(req):
    need_admin(req)
    d = req.json()
    c = cfg()
    for k in ("pos_step", "neg_step", "flag_negatives", "flag_absence_min"):
        if k in d:
            try:
                v = float(d[k])
            except (TypeError, ValueError):
                raise ApiError(400, "Неверное число: " + k)
            need(v > 0, "Значение должно быть больше нуля", 400)
            c[k] = int(v) if v == int(v) else v
    if "base" in d:
        need(d["base"] in ("прим.", "уд.", "неуд."), "Базовая отметка: прим., уд. или неуд.", 400)
        c["base"] = d["base"]
    db().set_setting("pulse_cfg", c)
    db().audit(req.user, req.ip, "pulse_cfg", c)
    return c


# ------------------------------------------------------------------ доступ
def _is_sub(u, a_id, date, num=None):
    sids = auth.staff_ids(db(), u)
    if not sids:
        return False
    ph = ",".join("?" * len(sids))
    sql = "SELECT 1 FROM substitutions WHERE assignment_id=? AND date=? AND substitute_id IN (%s)" % ph
    args = [a_id, date] + list(sids)
    if num:
        sql += " AND (lesson_no=? OR lesson_no=0)"
        args.append(num)
    return db().val(sql, args) is not None


def can_input_lesson(u, lesson):
    if not can_input_role(u):
        return False
    if is_admin_like(u):
        return True
    return lesson["assignment_id"] in auth.teacher_assignment_ids(db(), u) or _is_sub(u, lesson["assignment_id"], lesson["date"], lesson["num"])


def can_input_class_extra(u, class_id):
    if not can_input_role(u):
        return False
    return is_admin_like(u) or int(class_id) in auth.own_class_ids(db(), u)


def scope_sql(u, alias="e"):
    """Условие видимости событий для пользователя: (sql, args)."""
    if auth.can_view_all(u):
        return "", []
    asg = sorted(auth.teacher_assignment_ids(db(), u))
    cls = sorted(auth.own_class_ids(db(), u))
    parts, args = [], []
    if asg:
        parts.append("%s.assignment_id IN (%s)" % (alias, ",".join("?" * len(asg))))
        args += asg
    if cls:
        parts.append("%s.class_id IN (%s)" % (alias, ",".join("?" * len(cls))))
        args += cls
    if not parts:
        return " AND 0", []
    return " AND (" + " OR ".join(parts) + ")", args


def can_see_class(u, class_id):
    if auth.can_view_all(u):
        return True
    return int(class_id) in auth.own_class_ids(db(), u) or auth.can_view_class(db(), u, class_id)


def _lesson(lid):
    l = db().one("SELECT l.*, a.class_id, a.subject_id, a.teacher_id, a.group_name, sb.name AS subject, c.grade, c.letter "
                 "FROM lessons l JOIN assignments a ON a.id=l.assignment_id JOIN subjects sb ON sb.id=a.subject_id "
                 "JOIN classes c ON c.id=a.class_id WHERE l.id=?", (int(lid),))
    need(l, "Урок не найден", 404)
    return l


def _lesson_bounds(l):
    b = bells().get(str(l["num"])) or bells().get(l["num"])
    if not b:
        return None, None
    a, e = _mins(b[0]), _mins(b[1])
    if a is None or e is None:
        return None, None
    d0 = DT.datetime.strptime(l["date"], "%Y-%m-%d")
    return d0 + DT.timedelta(minutes=a), d0 + DT.timedelta(minutes=e)


def lesson_students(l):
    a = db().one("SELECT * FROM assignments WHERE id=?", (l["assignment_id"],))
    return R.assignment_students(db(), a)


# ------------------------------------------------------------------ расчёт отсутствий
def compute_absence(events, end_dt):
    """events: [(datetime, role)] одного учащегося на одном уроке (по возрастанию времени).
    Возвращает интервалы [{start, end, seconds, minutes, closed}]. Незакрытый выход длится до end_dt."""
    out, start = [], None
    for t, role in sorted(events, key=lambda x: x[0]):
        if role == "exit" and start is None:
            start = t
        elif role == "return" and start is not None:
            out.append((start, t, True))
            start = None
    if start is not None:
        e = end_dt if end_dt and end_dt > start else start
        out.append((start, e, False))
    res = []
    for a, b, closed in out:
        sec = int((b - a).total_seconds())
        res.append({"start": _s(a), "end": _s(b), "seconds": sec, "minutes": int(sec / 60.0 + 0.5), "closed": closed})
    return res


def lesson_end_time(l, end_row=None, last_event=None):
    if end_row is None:
        end_row = db().one("SELECT * FROM pulse_lesson_end WHERE lesson_id=?", (l["id"],))
    if end_row:
        return _p(end_row["ended_at"])
    _s0, e = _lesson_bounds(l)
    if e:
        return e
    return last_event


def _events(where, args):
    return db().q("SELECT e.*, t.code, t.name AS type_name, t.category, t.role, t.polarity, t.color, t.icon, "
                  "t.behavior_weight, t.affects_behavior_recommendation AS affects, t.is_extracurricular "
                  "FROM pulse_events e JOIN pulse_types t ON t.id=e.type_id WHERE " + where + " ORDER BY e.event_time, e.id", args)


def absences_for(events, lessons):
    """events — события (dict с role/student_id/lesson_id/event_time). lessons: {lesson_id: lesson}.
    Возвращает {(student_id, lesson_id): [интервалы]}."""
    groups, lastev = {}, {}
    for e in events:
        if e["lesson_id"] and e["role"] in ("exit", "return"):
            groups.setdefault((e["student_id"], e["lesson_id"]), []).append((_p(e["event_time"]), e["role"]))
        if e["lesson_id"]:
            t = _p(e["event_time"])
            if t > lastev.get(e["lesson_id"], DT.datetime.min):
                lastev[e["lesson_id"]] = t
    ends = dict((r["lesson_id"], r) for r in (db().q("SELECT * FROM pulse_lesson_end WHERE lesson_id IN (%s)" % ",".join(
        str(int(x)) for x in set(k[1] for k in groups)) ) if groups else []))
    out = {}
    for (sid, lid), evs in groups.items():
        l = lessons.get(lid)
        end = lesson_end_time(l, ends.get(lid), lastev.get(lid)) if l else lastev.get(lid)
        out[(sid, lid)] = compute_absence(evs, end)
    return out


def _lessons_map(events):
    ids = sorted(set(e["lesson_id"] for e in events if e["lesson_id"]))
    if not ids:
        return {}
    rows = db().q("SELECT l.*, a.class_id, a.teacher_id, sb.name AS subject, sb.short AS subject_short FROM lessons l "
                  "JOIN assignments a ON a.id=l.assignment_id JOIN subjects sb ON sb.id=a.subject_id WHERE l.id IN (%s)" % ",".join(map(str, ids)))
    return dict((r["id"], r) for r in rows)


def late_minutes(e, l):
    s, _e = _lesson_bounds(l) if l else (None, None)
    if not s:
        return None
    m = int((_p(e["event_time"]) - s).total_seconds() / 60.0 + 0.5)
    return m if m > 0 else None


# ------------------------------------------------------------------ периоды
def resolve_period(period, year, class_id=None, d1=None, d2=None):
    t = DT.date.fromisoformat(today())
    if d1 and d2:
        return d1, d2, "с %s по %s" % (d1, d2)
    period = period or "quarter"
    if period == "week":
        return (t - DT.timedelta(days=6)).isoformat(), t.isoformat(), "последние 7 дней"
    if period == "month":
        return (t - DT.timedelta(days=29)).isoformat(), t.isoformat(), "последние 30 дней"
    if period == "today":
        return t.isoformat(), t.isoformat(), "сегодня"
    if year and period == "year":
        return year["start_date"], year["end_date"], "учебный год"
    if year:
        ps = db().q("SELECT * FROM periods WHERE year_id=? AND start_date<>'' AND end_date<>'' ORDER BY start_date", (year["id"],))
        if period in ("quarter", "current"):
            cur = [p for p in ps if p["start_date"] <= t.isoformat() <= p["end_date"]]
            if not cur:
                past = [p for p in ps if p["end_date"] < t.isoformat()]
                cur = past[-1:] or ps[:1]
            if cur:
                return cur[0]["start_date"], cur[0]["end_date"], cur[0]["name"]
        else:
            for p in ps:
                if p["kind"] == period:
                    return p["start_date"], p["end_date"], p["name"]
    return (t - DT.timedelta(days=89)).isoformat(), t.isoformat(), "последние 90 дней"


def _year_for(class_id=None):
    if class_id:
        y = R.year_of_class(db(), class_id)
        if y:
            return y
    return current_year()


def _period_from(req, class_id=None):
    return resolve_period(req.arg("period"), _year_for(class_id), class_id, req.arg("from"), req.arg("to"))


# ------------------------------------------------------------------ учебные списки
def _fio_short(s):
    return "%s %s." % (s["last_name"], s["first_name"][:1]) if s.get("first_name") else s["last_name"]


@router.route("GET", "/api/pulse/lessons/today")
def api_lessons_today(req):
    need_school(req)
    ensure_types()
    u = req.user
    need(can_input_role(u), "Ввод событий доступен учителям и учительской")
    date = today()
    y = current_year()
    out = []
    if not y:
        return {"date": date, "lessons": [], "now": _s(now_dt())}
    from .v14 import lessons_on
    items = lessons_on(y["id"], date)
    sids = auth.staff_ids(db(), u)
    subs = db().q("SELECT * FROM substitutions WHERE date=?", (date,))
    mine = auth.teacher_assignment_ids(db(), u)
    for it in items:
        a = db().one("SELECT a.*, sb.name AS subject, c.grade, c.letter FROM assignments a JOIN subjects sb ON sb.id=a.subject_id "
                     "JOIN classes c ON c.id=a.class_id WHERE a.id=?", (it["assignment_id"],))
        if not a:
            continue
        sub = [s for s in subs if s["assignment_id"] == a["id"] and s["lesson_no"] in (0, it["lesson_no"])]
        ok = False
        if is_admin_like(u):
            ok = True
        elif a["id"] in mine:
            ok = not sub or any(s["substitute_id"] in sids for s in sub)     # заменённый урок учитель не ведёт
        elif sub and any(s["substitute_id"] in sids for s in sub):
            ok = True
        if not ok:
            continue
        les = db().one("SELECT id FROM lessons WHERE assignment_id=? AND date=? AND num=?", (a["id"], date, it["lesson_no"]))
        b = bells().get(str(it["lesson_no"]))
        out.append({"assignment_id": a["id"], "lesson_id": les["id"] if les else None, "num": it["lesson_no"],
                    "subject": a["subject"], "group": a.get("group_name") or "", "class_id": a["class_id"],
                    "class": R.class_name(a), "room": it.get("room") or "", "time": "–".join(b) if b else ""})
    return {"date": date, "lessons": out, "now": _s(now_dt()), "bells": bool(bells())}


@router.route("GET", "/api/pulse/choices")
def api_choices(req):
    """«Другой урок» / «Вне урока»: классы и нагрузки, по которым пользователь может вводить события."""
    need_school(req)
    u = req.user
    need(can_input_role(u), "Ввод событий доступен учителям и учительской")
    y = current_year()
    if not y:
        return {"classes": []}
    classes = db().q("SELECT * FROM classes WHERE year_id=? ORDER BY grade, letter", (y["id"],))
    mine = auth.teacher_assignment_ids(db(), u)
    own = auth.own_class_ids(db(), u)
    out = []
    for c in classes:
        asg = db().q("SELECT a.id, a.group_name, sb.name AS subject, a.teacher_id FROM assignments a JOIN subjects sb ON sb.id=a.subject_id "
                     "WHERE a.class_id=? ORDER BY sb.sort, sb.name", (c["id"],))
        if not is_admin_like(u):
            asg = [a for a in asg if a["id"] in mine]
        extra = is_admin_like(u) or c["id"] in own
        if asg or extra:
            out.append({"class_id": c["id"], "class": R.class_name(c), "extra": extra,
                        "assignments": [{"id": a["id"], "subject": a["subject"], "group": a["group_name"] or ""} for a in asg]})
    return {"classes": out}


@router.route("POST", "/api/pulse/lessons/open")
def api_lesson_open(req):
    """Найти запись урока в журнале (или создать пустую) для ввода событий."""
    need_school(req)
    d = req.json()
    a = db().one("SELECT * FROM assignments WHERE id=?", (int(d.get("assignment_id") or 0),))
    need(a, "Нагрузка не найдена", 404)
    date = today()
    num = int(d.get("num") or 0)
    if not num:
        num = _guess_num(a, date)
    probe = {"assignment_id": a["id"], "date": date, "num": num}
    need(can_input_lesson(req.user, probe), "Это не ваш предмет и не ваш класс")
    les = db().one("SELECT id FROM lessons WHERE assignment_id=? AND date=? AND num=?", (a["id"], date, num))
    if les:
        return {"lesson_id": les["id"], "created": False}
    lid = db().ex("INSERT INTO lessons(assignment_id,date,num,topic,homework,work_type) VALUES(?,?,?,?,?,?)",
                  (a["id"], date, num, "", "", ""))
    return {"lesson_id": lid, "created": True}


def _guess_num(a, date):
    """Номер урока: по расписанию, иначе по звонкам, иначе следующий свободный."""
    from .v14 import lessons_on
    y = R.year_of_class(db(), a["class_id"])
    if y:
        sl = [r["lesson_no"] for r in lessons_on(y["id"], date, " AND si.assignment_id=?", (a["id"],))]
        if sl:
            return sl[0]
    t = now_dt()
    m = t.hour * 60 + t.minute
    for k, v in sorted(bells().items(), key=lambda kv: int(kv[0])):
        s, e = _mins(v[0]), _mins(v[1])
        if s is not None and e is not None and s <= m < e:
            return int(k)
    mx = db().val("SELECT MAX(num) FROM lessons WHERE assignment_id=? AND date=?", (a["id"], date)) or 0
    return mx + 1


# ------------------------------------------------------------------ ввод событий
def _student_state(lid, sid):
    r = db().one("SELECT t.role FROM pulse_events e JOIN pulse_types t ON t.id=e.type_id WHERE e.lesson_id=? AND e.student_id=? "
                 "AND t.role IN ('exit','return') ORDER BY e.event_time DESC, e.id DESC LIMIT 1", (lid, sid))
    return "out" if r and r["role"] == "exit" else "in"


def _publish(class_id):
    try:
        PULSE_HUB.publish(class_id)
    except Exception:
        pass


@router.route("GET", "/api/pulse/class/:cid/roster")
def api_roster(req, cid):
    """Состав класса для ввода внеурочных событий."""
    need_school(req)
    need(can_input_class_extra(req.user, int(cid)), "Внеурочные события вводит классный руководитель")
    return {"students": [{"id": s["id"], "name": R.fio(s), "short": _fio_short(s), "state": "in", "pos": 0, "neg": 0, "absence_min": 0}
                         for s in R.class_students(db(), int(cid))]}


@router.route("POST", "/api/pulse/events")
def api_events_add(req):
    need_school(req)
    ensure_types()
    u = req.user
    need(can_input_role(u), "Ввод событий доступен учителям и учительской")
    d = req.json()
    items = d.get("events")
    if items is None:
        items = [d]
    need(isinstance(items, list) and 0 < len(items) <= 500, "Нет событий для записи", 400)
    batch = uuid.uuid4().hex[:12]
    types = dict((t["code"], t) for t in db().q("SELECT * FROM pulse_types"))
    types.update(dict((t["id"], t) for t in types.values()))
    ts = _s(now_dt())
    date_today = ts[:10]
    created, skipped, classes = [], [], set()
    lcache, scache = {}, {}
    with db().tx():
        for it in items:
            t = types.get(it.get("type_code") or it.get("type_id"))
            need(t and t["is_active"], "Тип события не найден или отключён", 400)
            sids = it.get("student_ids")
            if sids is None:
                sids = [it.get("student_id")]
            lid = it.get("lesson_id")
            if lid:
                l = lcache.get(lid) or _lesson(lid)
                lcache[lid] = l
                need(can_input_lesson(u, l), "Нет права вводить события на этом уроке")
                need(l["date"] == date_today, "События вводятся только в день урока", 400)
                need(not t["is_extracurricular"], "Внеурочное событие вводится вне урока", 400)
                class_id, asg_id = l["class_id"], l["assignment_id"]
                if lid not in scache:
                    scache[lid] = set(s["id"] for s in lesson_students(l))
                allowed = scache[lid]
            else:
                class_id = int(it.get("class_id") or 0)
                need(class_id and db().val("SELECT 1 FROM classes WHERE id=?", (class_id,)), "Укажите урок или класс", 400)
                need(can_input_class_extra(u, class_id), "Внеурочные события вводит классный руководитель")
                need(t["is_extracurricular"], "Для события вне урока выберите внеурочный тип", 400)
                asg_id = None
                key = ("c", class_id)
                if key not in scache:
                    scache[key] = set(s["id"] for s in R.class_students(db(), class_id))
                allowed = scache[key]
            for sid in sids:
                sid = int(sid or 0)
                if sid not in allowed:
                    skipped.append({"student_id": sid, "reason": "Учащийся не входит в состав"})
                    continue
                if lid:
                    if t["role"] == "exit" and _student_state(lid, sid) == "out":
                        skipped.append({"student_id": sid, "reason": "Уже вышел из класса"})
                        continue
                    if t["role"] == "return" and _student_state(lid, sid) != "out":
                        skipped.append({"student_id": sid, "reason": "Не отмечен выход"})
                        continue
                    if t["max_per_lesson"]:
                        n = db().val("SELECT COUNT(*) FROM pulse_events WHERE lesson_id=? AND student_id=? AND type_id=?", (lid, sid, t["id"]))
                        if n >= t["max_per_lesson"]:
                            skipped.append({"student_id": sid, "reason": "Лимит «%s» на урок: %d" % (t["name"], t["max_per_lesson"])})
                            continue
                eid = db().ex("INSERT INTO pulse_events(student_id,class_id,type_id,lesson_id,assignment_id,date,event_time,created_by,batch_id,note) "
                              "VALUES(?,?,?,?,?,?,?,?,?,?)",
                              (sid, class_id, t["id"], lid, asg_id, l["date"] if lid else date_today, ts, u["id"], batch,
                               (it.get("note") or "")[:200]))
                created.append(eid)
                classes.add(class_id)
    for c in classes:
        _publish(c)
    if not created and skipped and len(skipped) == sum(1 for _ in skipped) and not d.get("allow_empty"):
        # ничего не записано: сообщаем причину как предупреждение, не как ошибку сервера
        pass
    return {"created": created, "batch_id": batch, "skipped": skipped, "event_time": ts}


def _can_delete(u, e):
    if auth.can_admin(u):
        return True
    if e["created_by"] != u["id"]:
        return False
    return (now_dt() - _p(e["event_time"])) <= DT.timedelta(hours=24)


def _delete_events(req, rows):
    cl = set()
    for e in rows:
        need(_can_delete(req.user, e), "Удалять событие может только его автор в течение 24 часов; позднее — администратор")
    for e in rows:
        db().ex("DELETE FROM pulse_events WHERE id=?", (e["id"],))
        cl.add(e["class_id"])
        late = (now_dt() - _p(e["event_time"])) > DT.timedelta(hours=24)
        if late or e["created_by"] != req.user["id"]:
            db().audit(req.user, req.ip, "pulse_event_delete", {"id": e["id"], "student_id": e["student_id"], "type_id": e["type_id"],
                                                                 "event_time": e["event_time"], "created_by": e["created_by"]})
    for c in cl:
        _publish(c)


@router.route("DELETE", "/api/pulse/events/:eid")
def api_event_del(req, eid):
    need_school(req)
    e = db().one("SELECT * FROM pulse_events WHERE id=?", (int(eid),))
    need(e, "Событие не найдено", 404)
    _delete_events(req, [e])
    return {"ok": True}


@router.route("DELETE", "/api/pulse/batches/:bid")
def api_batch_del(req, bid):
    """Кнопка «↶»: отмена последнего действия (одно нажатие = одна пачка событий)."""
    need_school(req)
    rows = db().q("SELECT * FROM pulse_events WHERE batch_id=?", (bid,))
    need(rows, "Событие не найдено", 404)
    _delete_events(req, rows)
    return {"ok": True, "deleted": len(rows)}


@router.route("GET", "/api/pulse/lesson/:lid/events")
def api_lesson_events(req, lid):
    need_school(req)
    ensure_types()
    u = req.user
    l = _lesson(lid)
    need(can_input_lesson(u, l) or can_see_class(u, l["class_id"]) and _may_see_lesson(u, l), "Недостаточно прав")
    studs = lesson_students(l)
    evs = _events("e.lesson_id=?", (l["id"],))
    ab = absences_for(evs, {l["id"]: l})
    end_row = db().one("SELECT * FROM pulse_lesson_end WHERE lesson_id=?", (l["id"],))
    state = {}
    for e in evs:
        if e["role"] == "exit":
            state[e["student_id"]] = "out"
        elif e["role"] == "return":
            state[e["student_id"]] = "in"
    counts = {}
    for e in evs:
        c = counts.setdefault(e["student_id"], {"pos": 0, "neg": 0})
        if e["polarity"] == "pos":
            c["pos"] += 1
        elif e["polarity"] == "neg":
            c["neg"] += 1
    names = dict((s["id"], R.fio(s)) for s in studs)
    return {
        "lesson": {"id": l["id"], "date": l["date"], "num": l["num"], "subject": l["subject"], "class_id": l["class_id"],
                   "class": R.class_name(l), "group": l.get("group_name") or "", "ended_at": end_row["ended_at"] if end_row else None},
        "students": [{"id": s["id"], "name": R.fio(s), "short": _fio_short(s), "state": state.get(s["id"], "in"),
                      "pos": counts.get(s["id"], {}).get("pos", 0), "neg": counts.get(s["id"], {}).get("neg", 0),
                      "absence_min": sum(i["minutes"] for i in ab.get((s["id"], l["id"]), []))} for s in studs],
        "events": [{"id": e["id"], "student_id": e["student_id"], "student": names.get(e["student_id"], ""), "time": e["event_time"],
                    "code": e["code"], "type": e["type_name"], "color": e["color"], "category": e["category"], "mine": e["created_by"] == u["id"],
                    "can_delete": _can_delete(u, dict(e))} for e in evs],
        "absences": [{"student_id": k[0], "intervals": v} for k, v in ab.items()],
        "can_input": can_input_lesson(u, l), "now": _s(now_dt()),
    }


def _may_see_lesson(u, l):
    if auth.can_view_all(u):
        return True
    return l["assignment_id"] in auth.teacher_assignment_ids(db(), u) or l["class_id"] in auth.own_class_ids(db(), u)


@router.route("POST", "/api/pulse/lesson/:lid/end")
def api_lesson_end(req, lid):
    """«Завершить урок»: закрыть незакрытые выходы. fix_open=true — зафиксировать отсутствие до конца урока;
    return_time (ЧЧ:ММ) — примерное время возвращения (будет записано событием «вернулся» с пометкой)."""
    need_school(req)
    l = _lesson(lid)
    need(can_input_lesson(req.user, l), "Нет права завершать этот урок")
    d = req.json()
    evs = _events("e.lesson_id=?", (l["id"],))
    open_ids = {}
    for e in evs:
        if e["role"] == "exit":
            open_ids[e["student_id"]] = e
        elif e["role"] == "return":
            open_ids.pop(e["student_id"], None)
    if open_ids and not d.get("fix_open"):
        names = dict((s["id"], R.fio(s)) for s in lesson_students(l))
        raise ApiError(409, "У %d учащихся не отмечено возвращение в класс" % len(open_ids))
    t = now_dt()
    ended = _s(t)
    rt = d.get("return_time")
    if open_ids and rt:
        m = re.match(r"^(\d{1,2}):(\d{2})$", str(rt))
        need(m, "Время возвращения укажите как ЧЧ:ММ", 400)
        rtdt = DT.datetime.combine(t.date(), DT.time(int(m.group(1)), int(m.group(2))))
        need(rtdt <= t, "Время возвращения не может быть в будущем", 400)
        rtype = db().one("SELECT id FROM pulse_types WHERE role='return' ORDER BY id LIMIT 1")
        for sid, e in open_ids.items():
            if rtdt > _p(e["event_time"]) and rtype:
                db().ex("INSERT INTO pulse_events(student_id,class_id,type_id,lesson_id,assignment_id,date,event_time,created_by,batch_id,note) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?)", (sid, l["class_id"], rtype["id"], l["id"], l["assignment_id"], l["date"],
                                                         _s(rtdt), req.user["id"], "end", "приблизительно"))
    db().ex("INSERT INTO pulse_lesson_end(lesson_id,ended_at,ended_by) VALUES(?,?,?) ON CONFLICT(lesson_id) DO UPDATE SET "
            "ended_at=excluded.ended_at, ended_by=excluded.ended_by", (l["id"], ended, req.user["id"]))
    _publish(l["class_id"])
    return {"ok": True, "ended_at": ended, "closed": len(open_ids)}


# ------------------------------------------------------------------ аналитика
def _class_events(u, class_id, d1, d2, mine_only=False):
    sc, sargs = scope_sql(u)
    where = "e.class_id=? AND e.date>=? AND e.date<=?" + sc
    args = [class_id, d1, d2] + sargs
    if mine_only and not is_admin_like(u):
        asg = sorted(auth.teacher_assignment_ids(db(), u))
        where += " AND e.assignment_id IN (%s)" % (",".join("?" * len(asg)) if asg else "NULL")
        args += asg
    return _events(where, args)


def _score(evs):
    pos = neg = 0.0
    for e in evs:
        if not e["affects"]:
            continue
        if e["polarity"] == "pos":
            pos += e["behavior_weight"]
        elif e["polarity"] == "neg":
            neg += e["behavior_weight"]
    return pos, neg


def recommend(pos, neg, c=None):
    """Рекомендация по поведению: база «уд.»; +1 ступень за каждые pos_step позитивных, −1 за каждые neg_step негативных."""
    c = c or cfg()
    scale = ["прим.", "уд.", "неуд."]
    base = scale.index(c["base"])
    up = int(pos // c["pos_step"])
    down = int(neg // c["neg_step"])
    net = up - down
    idx = min(2, max(0, base - net))
    return {"value": scale[idx], "up": up, "down": down, "net": net}


def build_summary(u, class_id, d1, d2, mine_only=False):
    cl = R.get_class(db(), class_id)
    need(cl, "Класс не найден", 404)
    studs = R.class_students(db(), class_id)
    evs = _class_events(u, class_id, d1, d2, mine_only)
    lessons = _lessons_map(evs)
    ab = absences_for(evs, lessons)
    types = [t for t in db().q("SELECT * FROM pulse_types ORDER BY sort_order, id")]
    used = set(e["type_id"] for e in evs)
    types = [t for t in types if t["is_active"] or t["id"] in used]
    by = {}
    for e in evs:
        by.setdefault(e["student_id"], []).append(e)
    abs_by = {}
    for (sid, _l), ints in ab.items():
        abs_by[sid] = abs_by.get(sid, 0) + sum(i["minutes"] for i in ints)
    rows = []
    c = cfg()
    for s in studs:
        se = by.get(s["id"], [])
        cnt = {}
        for e in se:
            cnt[e["type_id"]] = cnt.get(e["type_id"], 0) + 1
        pos, neg = _score(se)
        rows.append({"id": s["id"], "name": R.fio(s), "counts": cnt, "pos": round(pos, 1), "neg": round(neg, 1),
                     "absence_min": abs_by.get(s["id"], 0), "recommendation": recommend(pos, neg, c)["value"]})
    subj = {}
    for e in evs:
        key = lessons[e["lesson_id"]]["subject"] if e["lesson_id"] and e["lesson_id"] in lessons else "Вне урока"
        r = subj.setdefault(key, {"subject": key, "pos": 0.0, "neg": 0.0, "events": 0})
        r["events"] += 1
        if e["affects"] and e["polarity"] == "pos":
            r["pos"] += e["behavior_weight"]
        elif e["affects"] and e["polarity"] == "neg":
            r["neg"] += e["behavior_weight"]
    return {"class": {"id": class_id, "name": R.class_name(cl), "teacher": cl.get("teacher") or ""}, "from": d1, "to": d2,
            "types": [{"id": t["id"], "code": t["code"], "name": t["name"], "color": t["color"], "category": t["category"]} for t in types],
            "students": rows, "subjects": sorted(subj.values(), key=lambda r: r["subject"]), "total_events": len(evs),
            "config": c}


@router.route("GET", "/api/pulse/class/:cid/summary")
def api_summary(req, cid):
    need_school(req)
    ensure_types()
    cid = int(cid)
    need(can_see_class(req.user, cid) or auth.teacher_assignment_ids(db(), req.user) & set(
        r["id"] for r in db().q("SELECT id FROM assignments WHERE class_id=?", (cid,))), "Недостаточно прав")
    d1, d2, label = _period_from(req, cid)
    mine = req.arg("scope") == "mine"
    s = build_summary(req.user, cid, d1, d2, mine)
    s["period_label"] = label
    if req.arg("format") == "xlsx":
        heads = ["№", "Учащийся"] + [t["code"] for t in s["types"]] + ["Позитив", "Негатив", "Отсутствие, мин", "Рекомендация"]
        rows = []
        for i, r in enumerate(s["students"]):
            rows.append([i + 1, r["name"]] + [r["counts"].get(t["id"], 0) for t in s["types"]] + [r["pos"], r["neg"], r["absence_min"], r["recommendation"]])
        return table_response(req, {"name": "Активность " + s["class"]["name"], "title": "Активность учащихся: %s класс, %s" % (s["class"]["name"], label),
                                    "subtitle": "%s — %s" % (d1, d2), "headers": heads, "rows": rows,
                                    "widths": [4, 28] + [11] * len(s["types"]) + [9, 9, 12, 14],
                                    "footer": ["Рекомендация носит справочный характер; отметку по поведению выставляет педагог."]})
    return s


def _student_class(u, sid, class_id=None):
    y = current_year()
    sql = ("SELECT c.* FROM class_students cs JOIN classes c ON c.id=cs.class_id WHERE cs.student_id=? "
           + ("AND c.id=? " if class_id else "AND c.year_id=? ") + "ORDER BY c.id DESC LIMIT 1")
    return db().one(sql, (sid, class_id if class_id else (y["id"] if y else 0)))


def timeline(u, sid, class_id, d1, d2):
    sc, sargs = scope_sql(u)
    evs = _events("e.student_id=? AND e.date>=? AND e.date<=?" + sc, [sid, d1, d2] + sargs)
    lessons = _lessons_map(evs)
    ab = absences_for(evs, lessons)
    out = []
    used_returns = set()
    # сопоставление выход→возвращение для подписи
    pairs = {}
    for e in evs:
        if e["role"] == "exit":
            pairs[e["id"]] = None
    open_exit = {}
    for e in evs:
        if e["role"] == "exit" and e["lesson_id"] not in open_exit:
            open_exit[e["lesson_id"]] = e
        elif e["role"] == "return" and e["lesson_id"] in open_exit:
            pairs[open_exit[e["lesson_id"]]["id"]] = e
            used_returns.add(e["id"])
            del open_exit[e["lesson_id"]]
    for e in evs:
        if e["id"] in used_returns:
            continue
        l = lessons.get(e["lesson_id"])
        subj = l["subject"] if l else "вне урока"
        text, mins = e["type_name"], None
        if e["role"] == "exit":
            iv = [i for i in ab.get((sid, e["lesson_id"]), []) if i["start"] == e["event_time"]]
            if iv:
                mins = iv[0]["minutes"]
                text = "Выход из класса (отсутствие: %d мин)" % mins if iv[0]["closed"] else "Выход из класса, возвращение не отмечено (до конца урока: %d мин)" % mins
        elif e["role"] == "late":
            lm = late_minutes(e, l)
            text = "Опоздание на урок" + (" (отсутствие: %d мин)" % lm if lm else "")
            mins = lm
        elif e["role"] == "return":
            text = "Вернулся в класс (выход не отмечен)"
        out.append({"id": e["id"], "time": e["event_time"], "date": e["date"], "subject": subj, "text": text, "code": e["code"],
                    "color": e["color"], "category": e["category"], "polarity": e["polarity"], "minutes": mins})
    total = sum(i["minutes"] for k, v in ab.items() for i in v)
    return out, total


@router.route("GET", "/api/pulse/student/:sid/timeline")
def api_timeline(req, sid):
    need_school(req)
    sid = int(sid)
    c = _student_class(req.user, sid, req.arg("class_id", cast=int))
    need(c, "Учащийся не найден", 404)
    need(can_see_class(req.user, c["id"]) or auth.teacher_assignment_ids(db(), req.user) & set(
        r["id"] for r in db().q("SELECT id FROM assignments WHERE class_id=?", (c["id"],))), "Недостаточно прав")
    d1, d2, label = _period_from(req, c["id"])
    items, total = timeline(req.user, sid, c["id"], d1, d2)
    st = db().one("SELECT * FROM students WHERE id=?", (sid,))
    from . import crypto
    st = crypto.dec_row("students", st)
    return {"student": {"id": sid, "name": R.fio(st), "class_id": c["id"], "class": R.class_name(c)}, "from": d1, "to": d2,
            "period_label": label, "items": items, "absence_total_min": total}


@router.route("GET", "/api/pulse/behavior-recommendation/:sid")
def api_recommendation(req, sid):
    need_school(req)
    sid = int(sid)
    c = _student_class(req.user, sid, req.arg("class_id", cast=int))
    need(c, "Учащийся не найден", 404)
    u = req.user
    need(c["id"] in auth.own_class_ids(db(), u) or auth.can_enter_marks_any(u), "Рекомендация доступна классному руководителю и учительской")
    d1, d2, label = _period_from(req, c["id"])
    sc, sargs = scope_sql(u)
    evs = _events("e.student_id=? AND e.class_id=? AND e.date>=? AND e.date<=?" + sc, [sid, c["id"], d1, d2] + sargs)
    pos, neg = _score(evs)
    cf = cfg()
    r = recommend(pos, neg, cf)
    kind = req.arg("period") if req.arg("period") in ("q1", "q2", "q3", "q4", "h1", "h2", "year") else _kind_for(c, d2)
    cur = db().val("SELECT value FROM behavior WHERE class_id=? AND student_id=? AND kind=?", (c["id"], sid, kind)) if kind else None
    lessons = _lessons_map(evs)
    ab = absences_for(evs, lessons)
    absm = sum(i["minutes"] for v in ab.values() for i in v)
    return {"student_id": sid, "class_id": c["id"], "from": d1, "to": d2, "period_label": label, "kind": kind,
            "positive": round(pos, 2), "negative": round(neg, 2), "absence_min": absm, "recommended": r["value"], "steps_up": r["up"],
            "steps_down": r["down"], "base": cf["base"], "current": cur or "",
            "explain": "Базовая отметка «%s»; +%d за %s позитивных (вес %.1f), −%d за %s негативных (вес %.1f)." % (
                cf["base"], r["up"], cf["pos_step"], pos, r["down"], cf["neg_step"], neg),
            "can_apply": auth.can_edit_behavior(db(), u, c["id"]),
            "note": "Рекомендация справочная: отметка в журнал записывается только после вашего подтверждения."}


def _kind_for(c, date):
    ps = db().q("SELECT kind, start_date, end_date FROM periods WHERE year_id=? AND kind IN ('q1','q2','q3','q4','h1','h2')", (c["year_id"],))
    for p in ps:
        if p["start_date"] and p["start_date"] <= date <= p["end_date"]:
            return p["kind"]
    return ps[-1]["kind"] if ps else None


@router.route("POST", "/api/pulse/behavior-recommendation/:sid/apply")
def api_recommendation_apply(req, sid):
    """Запись отметки по поведению в журнал — только по явному подтверждению педагога."""
    need_school(req)
    sid = int(sid)
    d = req.json()
    c = _student_class(req.user, sid, int(d.get("class_id") or 0) or None)
    need(c, "Учащийся не найден", 404)
    need(d.get("confirm") is True, "Требуется подтверждение педагога", 400)
    kind = d.get("kind")
    need(kind in ("q1", "q2", "q3", "q4", "h1", "h2", "year"), "Не указан период отметки", 400)
    need(d.get("value") in ("прим.", "уд.", "неуд."), "Неверная отметка", 400)
    need(auth.can_edit_behavior(db(), req.user, c["id"]), "Отметку по поведению выставляет классный руководитель или учительская")
    from .api import locked_kind
    need(not locked_kind(c["id"], kind) or auth.can_admin(req.user), "Период закрыт", 400)
    db().ex("INSERT INTO behavior(class_id,student_id,kind,value,updated_by,updated_at) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(class_id,student_id,kind) DO UPDATE SET value=excluded.value, updated_by=excluded.updated_by, updated_at=excluded.updated_at",
            (c["id"], sid, kind, d["value"], req.user["id"], _s(now_dt())))
    db().audit(req.user, req.ip, "pulse_behavior_applied", {"student_id": sid, "kind": kind, "value": d["value"]})
    return {"ok": True, "value": d["value"]}


def week_flags(u, class_ids=None):
    c = cfg()
    d2 = today()
    d1 = (DT.date.fromisoformat(d2) - DT.timedelta(days=6)).isoformat()
    y = current_year()
    if class_ids is None:
        if auth.can_view_all(u):
            class_ids = [r["id"] for r in db().q("SELECT id FROM classes WHERE year_id=?", (y["id"],))] if y else []
        else:
            class_ids = sorted(auth.own_class_ids(db(), u))
    out = []
    for cid in class_ids:
        cl = R.get_class(db(), cid)
        evs = _class_events(u, cid, d1, d2)
        lessons = _lessons_map(evs)
        ab = absences_for(evs, lessons)
        names = dict((s["id"], R.fio(s)) for s in R.class_students(db(), cid))
        neg, absm = {}, {}
        for e in evs:
            if e["polarity"] == "neg" and e["affects"]:
                neg[e["student_id"]] = neg.get(e["student_id"], 0) + 1
        for (sid, _l), ints in ab.items():
            absm[sid] = absm.get(sid, 0) + sum(i["minutes"] for i in ints)
        for sid in set(list(neg) + list(absm)):
            reasons = []
            if neg.get(sid, 0) >= c["flag_negatives"]:
                reasons.append("негативных событий за неделю: %d" % neg[sid])
            if absm.get(sid, 0) > c["flag_absence_min"]:
                reasons.append("отсутствие на уроках: %d мин" % absm[sid])
            if reasons and sid in names:
                out.append({"class_id": cid, "class": R.class_name(cl), "student_id": sid, "student": names[sid],
                            "negatives": neg.get(sid, 0), "absence_min": absm.get(sid, 0), "reasons": reasons})
    out.sort(key=lambda r: (-len(r["reasons"]), -r["negatives"], -r["absence_min"]))
    return {"from": d1, "to": d2, "flags": out, "config": c}


@router.route("GET", "/api/pulse/flags/week")
def api_flags(req):
    need_school(req)
    ensure_types()
    u = req.user
    cid = req.arg("class_id", cast=int)
    if cid:
        need(can_see_class(u, cid), "Недостаточно прав")
        return week_flags(u, [cid])
    return week_flags(u)


@router.route("GET", "/api/pulse/overview")
def api_overview(req):
    """Сводная аналитика для завуча/директора: по классам, параллелям, учителям, предметам."""
    need_school(req)
    ensure_types()
    u = req.user
    need(auth.can_view_all(u), "Сводная аналитика доступна администрации и учительской")
    y = current_year()
    d1, d2, label = resolve_period(req.arg("period"), y, None, req.arg("from"), req.arg("to"))
    evs = _events("e.date>=? AND e.date<=?", (d1, d2))
    lessons = _lessons_map(evs)
    ab = absences_for(evs, lessons)
    classes = dict((c["id"], c) for c in db().q("SELECT * FROM classes"))
    teachers = dict((s["id"], R.staff_short(s)) for s in db().q("SELECT * FROM staff"))
    def agg(key_fn, name_fn):
        m = {}
        for e in evs:
            k = key_fn(e)
            if k is None:
                continue
            r = m.setdefault(k, {"name": name_fn(k), "pos": 0.0, "neg": 0.0, "events": 0, "absence_min": 0})
            r["events"] += 1
            if e["affects"] and e["polarity"] == "pos":
                r["pos"] += e["behavior_weight"]
            elif e["affects"] and e["polarity"] == "neg":
                r["neg"] += e["behavior_weight"]
        return m
    by_class = agg(lambda e: e["class_id"], lambda k: R.class_name(classes[k]) if k in classes else "?")
    for (sid, lid), ints in ab.items():
        l = lessons.get(lid)
        if l and l["class_id"] in by_class:
            by_class[l["class_id"]]["absence_min"] += sum(i["minutes"] for i in ints)
    by_par = {}
    for cid, r in by_class.items():
        g = classes[cid]["grade"] if cid in classes else 0
        p = by_par.setdefault(g, {"name": "%s классы" % g, "pos": 0.0, "neg": 0.0, "events": 0, "absence_min": 0})
        for k in ("pos", "neg", "events", "absence_min"):
            p[k] += r[k]
    by_teacher = agg(lambda e: lessons[e["lesson_id"]]["teacher_id"] if e["lesson_id"] in lessons else None, lambda k: teachers.get(k, "—"))
    by_subj = agg(lambda e: lessons[e["lesson_id"]]["subject"] if e["lesson_id"] in lessons else None, lambda k: k)
    def lst(m):
        return sorted(({"id": k, "name": v["name"], "pos": round(v["pos"], 1), "neg": round(v["neg"], 1), "events": v["events"],
                        "absence_min": v["absence_min"]} for k, v in m.items()), key=lambda r: r["name"])
    return {"from": d1, "to": d2, "period_label": label, "classes": lst(by_class), "parallels": lst(by_par),
            "teachers": lst(by_teacher), "subjects": lst(by_subj), "total_events": len(evs)}


# ------------------------------------------------------------------ архив
def archive_old(years=ARCHIVE_YEARS):
    border = (DT.date.fromisoformat(today()) - DT.timedelta(days=365 * years + years // 4)).isoformat()
    d = db()
    with d.tx():
        n = d.val("SELECT COUNT(*) FROM pulse_events WHERE date<?", (border,))
        if n:
            cols = "id,student_id,class_id,type_id,lesson_id,assignment_id,date,event_time,created_by,batch_id,note"
            d.ex("INSERT OR IGNORE INTO pulse_events_archive(%s,archived_at) SELECT %s,? FROM pulse_events WHERE date<?" % (cols, cols),
                 (_s(now_dt()), border))
            d.ex("DELETE FROM pulse_events WHERE date<?", (border,))
    return n


def _auto_archive():
    t = today()
    if db().get_setting("pulse_archived_on") != t:
        db().set_setting("pulse_archived_on", t)
        try:
            archive_old()
        except Exception:
            pass


@router.route("GET", "/api/pulse/archive")
def api_archive_info(req):
    need_admin(req)
    d = db()
    return {"events": d.val("SELECT COUNT(*) FROM pulse_events"), "archived": d.val("SELECT COUNT(*) FROM pulse_events_archive"),
            "oldest": d.val("SELECT MIN(date) FROM pulse_events"), "years": ARCHIVE_YEARS, "archived_on": d.get_setting("pulse_archived_on")}


@router.route("POST", "/api/pulse/archive")
def api_archive_run(req):
    need_admin(req)
    y = req.json().get("years", ARCHIVE_YEARS)
    try:
        y = max(0, int(y))
    except (TypeError, ValueError):
        raise ApiError(400, "Неверное число лет")
    n = archive_old(y)
    db().audit(req.user, req.ip, "pulse_archive", {"years": y, "moved": n})
    return {"moved": n}


@router.route("DELETE", "/api/pulse/archive")
def api_archive_purge(req):
    """Окончательное удаление архива (только администратор, с записью в журнал действий)."""
    need_admin(req)
    n = db().val("SELECT COUNT(*) FROM pulse_events_archive")
    db().ex("DELETE FROM pulse_events_archive")
    db().audit(req.user, req.ip, "pulse_archive_purge", {"deleted": n})
    return {"deleted": n}


# ------------------------------------------------------------------ публичный экран класса
class PulseHub(object):
    def __init__(self):
        self.clients, self.lock = {}, threading.Lock()

    def add(self, class_id, q):
        with self.lock:
            if sum(len(v) for v in self.clients.values()) >= 200:
                return False
            self.clients.setdefault(class_id, set()).add(q)
            return True

    def remove(self, class_id, q):
        with self.lock:
            self.clients.get(class_id, set()).discard(q)

    def publish(self, class_id):
        with self.lock:
            qs = list(self.clients.get(class_id, ()))
        if not qs:
            return
        msg = "event: pulse\ndata: %s\n\n" % json.dumps(kiosk_snapshot(class_id), ensure_ascii=False)
        for q in qs:
            try:
                q.put_nowait(msg)
            except queue.Full:
                pass


PULSE_HUB = PulseHub()


def kiosk_snapshot(class_id):
    """Данные публичного экрана. Имена — только у позитивных событий; негатив и присутствие — только счётчики."""
    cl = R.get_class(db(), class_id)
    if not cl:
        return None
    d = today()
    evs = _events("e.class_id=? AND e.date=?", (class_id, d))
    lessons = _lessons_map(evs)
    names = dict((s["id"], _fio_short(s)) for s in R.class_students(db(), class_id))
    per, feed, top = {}, [], {}
    for e in evs:
        key = e["lesson_id"] or 0
        r = per.setdefault(key, {"lesson_id": key, "subject": lessons[key]["subject"] if key in lessons else "Вне урока",
                                 "num": lessons[key]["num"] if key in lessons else 0, "pos": 0, "neg": 0})
        if e["polarity"] == "pos":
            r["pos"] += 1
            top[e["student_id"]] = top.get(e["student_id"], 0) + 1
            feed.append({"time": e["event_time"][11:16], "student": names.get(e["student_id"], ""), "text": e["type_name"],
                         "color": e["color"], "subject": r["subject"]})
        elif e["polarity"] == "neg":
            r["neg"] += 1
    t3 = sorted(top.items(), key=lambda kv: (-kv[1], names.get(kv[0], "")))[:3]
    cur = None
    now = now_dt()
    m = now.hour * 60 + now.minute
    for k, v in bells().items():
        s, e2 = _mins(v[0]), _mins(v[1])
        if s is not None and e2 is not None and s <= m < e2:
            cur = int(k)
    cur_subject = ""
    for r in per.values():
        if r["num"] == cur and cur:
            cur_subject = r["subject"]
    return {"title": "%s · Активность сегодня" % R.class_name(cl), "date": d, "now": _s(now), "subject": cur_subject,
            "feed": feed[-12:][::-1], "lessons": sorted(per.values(), key=lambda r: r["num"]),
            "top": [{"student": names.get(sid, ""), "count": n} for sid, n in t3]}


def kiosk_get(h, path, query):
    """/kiosk/pulse/class/<id>  (страница),  .../data (снимок),  .../events (SSE). Публично, без входа."""
    from .api import features
    if not features().get("pulse", True):
        return h._send(404, "Модуль «Активность» отключён администратором", "text/plain; charset=utf-8")
    ensure_types()
    m = re.match(r"^/kiosk/pulse/class/(\d+)(/data|/events)?/?$", path)
    if not m:
        return h._send(404, "Not found", "text/plain")
    cid = int(m.group(1))
    snap = kiosk_snapshot(cid)
    if snap is None:
        return h._send(404, "Класс не найден", "text/plain; charset=utf-8")
    if m.group(2) == "/data":
        return h._send(200, json.dumps(snap, ensure_ascii=False), extra=[("Cache-Control", "no-store")])
    if m.group(2) == "/events":
        return _sse(h, cid, snap)
    return h._send(200, KIOSK_PULSE_HTML.replace("__ID__", str(cid)), "text/html; charset=utf-8", [("Cache-Control", "no-store")])


def _sse(h, cid, snap):
    q = queue.Queue(maxsize=50)
    if not PULSE_HUB.add(cid, q):
        return h._send(503, "Слишком много экранов", "text/plain")
    try:
        h.send_response(200)
        h.send_header("Content-Type", "text/event-stream; charset=utf-8")
        h.send_header("Cache-Control", "no-cache")
        h.send_header("Connection", "close")
        h.send_header("X-Accel-Buffering", "no")
        h.end_headers()
        h.close_connection = True
        h.wfile.write(("retry: 3000\n\nevent: pulse\ndata: %s\n\n" % json.dumps(snap, ensure_ascii=False)).encode("utf-8"))
        h.wfile.flush()
        while True:
            try:
                msg = q.get(timeout=15)
            except queue.Empty:
                msg = ": ping\n\n"
            h.wfile.write(msg.encode("utf-8"))
            h.wfile.flush()
    except (OSError, ValueError):
        pass
    finally:
        PULSE_HUB.remove(cid, q)


KIOSK_PULSE_HTML = r"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Активность класса</title>
<style>
*{box-sizing:border-box}html,body{margin:0;height:100%;background:#0b1220;color:#fff;font-family:"Segoe UI",Arial,sans-serif;overflow:hidden}
#top{display:flex;justify-content:space-between;align-items:center;padding:1.2vh 2vw;background:#13213d;border-bottom:.5vh solid #ffd54a;height:14vh}
#ttl{font-size:5vh;font-weight:700;line-height:1.1}#sub{font-size:2.8vh;color:#b8c7e6}
#clock{font-size:8vh;font-weight:700;color:#ffd54a;font-variant-numeric:tabular-nums}
#main{position:absolute;left:0;right:0;top:14.5vh;bottom:0;display:flex}
#left{flex:3;padding:1.5vh 1.5vw;overflow:hidden}#right{flex:2;padding:1.5vh 1.5vw;border-left:1px solid #2a3b60;overflow:hidden}
h2{margin:0 0 1vh;font-size:3vh;color:#b8c7e6;font-weight:600;text-transform:uppercase;letter-spacing:.1vw}
.ev{display:flex;align-items:center;gap:1.2vw;padding:1.1vh 1vw;margin-bottom:1vh;background:#13213d;border-radius:1vh;border-left:1vh solid #1a7f37;font-size:3.6vh}
.ev small{display:block;color:#b8c7e6;font-size:2.4vh}.ev .t{color:#ffd54a;font-variant-numeric:tabular-nums;min-width:7vw}
.ln{display:flex;justify-content:space-between;align-items:center;padding:1vh 1vw;margin-bottom:1vh;background:#0f1a30;border-radius:1vh;font-size:3.4vh}
.ln b{font-size:4vh}.p{color:#5be584}.n{color:#ff8a80;margin-left:1.2vw}
.top{display:flex;align-items:center;gap:1vw;padding:1vh 1vw;margin-bottom:1vh;font-size:4vh}
.top i{font-style:normal;width:7vh;height:7vh;border-radius:50%;background:#ffd54a;color:#0b1220;display:flex;align-items:center;justify-content:center;font-weight:800}
#empty{color:#b8c7e6;font-size:3.4vh;padding:2vh 0}#off{position:fixed;right:1vw;bottom:.6vh;font-size:2vh;color:#ff8a80;display:none}
</style></head><body>
<div id="top"><div><div id="ttl">…</div><div id="sub"></div></div><div id="clock">--:--</div></div>
<div id="main"><div id="left"><h2>Недавняя активность</h2><div id="feed"></div></div>
<div id="right"><h2>По урокам</h2><div id="les"></div><h2 style="margin-top:2vh">Самые активные</h2><div id="top3"></div></div></div>
<div id="off">нет связи с сервером</div>
<script>
(function(){
var ID="__ID__",D=null,off=0;
function $(i){return document.getElementById(i)}
function esc(s){return String(s==null?"":s).replace(/[&<>"]/g,function(c){return{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]})}
function p2(n){return(n<10?"0":"")+n}
function tick(){var d=new Date(Date.now()+off);$("clock").textContent=p2(d.getHours())+":"+p2(d.getMinutes())}
function render(d){D=d;
 var t=Date.parse(d.now.replace(" ","T"));if(t)off=t-Date.now();
 $("ttl").textContent=d.title;$("sub").textContent=d.subject?("Сейчас: "+d.subject):"";
 var h="";for(var i=0;i<d.feed.length;i++){var e=d.feed[i];h+='<div class="ev" style="border-left-color:'+esc(e.color)+'"><span class="t">'+esc(e.time)+'</span><span>'+esc(e.student)+'<small>'+esc(e.text)+(e.subject?" · "+esc(e.subject):"")+'</small></span></div>'}
 $("feed").innerHTML=h||'<div id="empty">Пока событий нет</div>';
 h="";for(i=0;i<d.lessons.length;i++){var l=d.lessons[i];h+='<div class="ln"><span>'+(l.num?l.num+". ":"")+esc(l.subject)+'</span><b><span class="p">+'+l.pos+'</span><span class="n">−'+l.neg+'</span></b></div>'}
 $("les").innerHTML=h||'<div id="empty">—</div>';
 h="";for(i=0;i<d.top.length;i++){h+='<div class="top"><i>'+(i+1)+'</i><span>'+esc(d.top[i].student)+' <span class="p">+'+d.top[i].count+'</span></span></div>'}
 $("top3").innerHTML=h||'<div id="empty">—</div>'}
function load(){var x=new XMLHttpRequest();x.open("GET","/kiosk/pulse/class/"+ID+"/data");x.onload=function(){if(x.status==200){render(JSON.parse(x.responseText));$("off").style.display="none"}};x.onerror=function(){$("off").style.display="block"};x.send()}
function connect(){if(!window.EventSource){setInterval(load,5000);load();return}
 var es=new EventSource("/kiosk/pulse/class/"+ID+"/events");
 es.addEventListener("pulse",function(e){render(JSON.parse(e.data));$("off").style.display="none"});
 es.onerror=function(){$("off").style.display="block"}}
setInterval(tick,1000);setInterval(load,60000);load();connect();
})();
</script></body></html>"""


# хук на открытие страницы: ежедневная архивация (раз в сутки, при первом запросе дня)
_orig_ensure = ensure_types


def ensure_types():  # noqa: F811
    _orig_ensure()
    _auto_archive()
