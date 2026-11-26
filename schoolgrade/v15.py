# -*- coding: utf-8 -*-
"""SchoolGrade 1.5: справочник кабинетов, мастер импорта aSc Timetables, дежурство классов,
«свободные кабинеты», публичные экраны (киоски), экстренное оповещение (SSE), HTTPS-сертификат."""
import base64
import datetime as DT
import json
import os
import queue
import re
import threading
import time

from . import asc, auth, cert, reports as R
from .api import (router, db, need, need_admin, need_school, current_year, year_arg, insert, update, ApiError, FileResponse, APP)
from .v14 import (_d, need_manage, _year_of_date, schedule_for, lessons_on, MAX_LESSON)
from .server import local_addresses

KINDS = {"regular": "Обычный", "gym": "Спортзал", "computer": "Компьютерный класс", "hall": "Актовый зал"}
FEATURE_NOTE = "расписание"


def app():
    return APP["app"]


def cn(c):
    """Короткое название класса: «8А»."""
    return "%s%s" % (c["grade"], (c.get("letter") or "").strip()) if c else "?"


def _staff_short(sid):
    st = db().one("SELECT last_name, first_name, middle_name FROM staff WHERE id=?", (sid,)) if sid else None
    return R.staff_short(st) if st else ""


# =================================================================== КАБИНЕТЫ
def _room_out(r):
    r = dict(r)
    r["kind_name"] = KINDS.get(r["kind"], r["kind"])
    return r


def _room_by_name(name):
    n = (name or "").strip().lower()
    return next((r for r in db().q("SELECT id, name FROM classrooms") if r["name"].lower() == n), None)


def _room_row(d, cur=None):
    name = (d.get("name") or "").strip()[:60]
    need(name, "Укажите название кабинета", 400)
    kind = d.get("kind") or "regular"
    need(kind in KINDS, "Неизвестный тип кабинета", 400)
    try:
        cap = max(0, min(5000, int(d.get("capacity") or 0)))
    except (TypeError, ValueError):
        raise ApiError(400, "Вместимость — целое число")
    dup = _room_by_name(name)
    need(not dup or (cur and dup["id"] == cur), "Кабинет с таким названием уже есть", 400)
    return {"name": name, "short": (d.get("short") or "").strip()[:20] or name[:20], "capacity": cap, "kind": kind,
            "active": 0 if d.get("active") in (0, False, "0") else 1}


@router.route("GET", "/api/classrooms")
def api_rooms(req):
    need_school(req)
    return {"items": [_room_out(r) for r in db().q("SELECT * FROM classrooms ORDER BY sort, name")], "kinds": KINDS,
            "can_edit": auth.can_manage(req.user)}


@router.route("POST", "/api/classrooms")
def api_room_add(req):
    need_manage(req)
    row = _room_row(req.json())
    row["sort"] = (db().val("SELECT MAX(sort) FROM classrooms") or 0) + 10
    rid = insert("classrooms", row)
    return {"id": rid}


@router.route("PUT", "/api/classrooms/:rid")
def api_room_put(req, rid):
    need_manage(req)
    cur = db().one("SELECT * FROM classrooms WHERE id=?", (int(rid),))
    need(cur, "Не найдено", 404)
    row = _room_row(dict(cur, **req.json()), cur["id"])
    update("classrooms", cur["id"], row)
    db().ex("UPDATE schedule_items SET room=? WHERE room_id=?", (row["short"][:20], cur["id"]))
    return {"ok": True}


@router.route("DELETE", "/api/classrooms/:rid")
def api_room_del(req, rid):
    need_manage(req)
    db().ex("UPDATE schedule_items SET room_id=NULL WHERE room_id=?", (int(rid),))
    db().ex("DELETE FROM classrooms WHERE id=?", (int(rid),))
    db().audit(req.user, req.ip, "classroom_deleted", rid)
    return {"ok": True}


# =================================================================== ЗВОНКИ
def bells():
    return db().get_setting("bells", {}) or {}


def _mins(hm):
    m = re.match(r"^(\d{1,2}):(\d{2})", hm or "")
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def _slots():
    out = []
    for k, v in bells().items():
        a, b = _mins(v[0]), _mins(v[1])
        if a is not None and b is not None:
            out.append((int(k), a, b))
    return sorted(out)


# =================================================================== СВОБОДНЫЕ КАБИНЕТЫ
def _busy_rooms(date, lesson_no):
    y = _year_of_date(date)
    busy = {}
    if not y:
        return busy
    rooms = db().q("SELECT * FROM classrooms WHERE active=1")
    by_txt = {}
    for r in rooms:
        by_txt[r["name"].lower()] = r["id"]
        by_txt[(r["short"] or "").lower()] = r["id"]
    for it in lessons_on(y["id"], date, " AND si.lesson_no=?", (lesson_no,)):
        rid = it.get("room_id") or by_txt.get((it.get("room") or "").strip().lower())
        if rid:
            c = db().one("SELECT grade, letter FROM classes WHERE id=?", (it["class_id"],))
            busy.setdefault(rid, []).append(cn(c))
    return busy


def _free_for(date, lesson_no):
    busy = _busy_rooms(date, lesson_no)
    free, occ = [], []
    for r in db().q("SELECT * FROM classrooms WHERE active=1 ORDER BY sort, name"):
        r = _room_out(r)
        if r["id"] in busy:
            occ.append(dict(r, by=", ".join(sorted(set(busy[r["id"]])))))
        else:
            free.append(r)
    return {"lesson_no": lesson_no, "free": free, "busy": occ}


@router.route("GET", "/api/rooms/free")
def api_rooms_free(req):
    """«Свободный кабинет прямо сейчас»: время сервера × звонки × действующая версия расписания."""
    need_manage(req)
    now = DT.datetime.now()
    date = _d(req.arg("date") or now.strftime("%Y-%m-%d")).isoformat()
    hm = req.arg("time") or now.strftime("%H:%M")
    t = _mins(hm)
    need(t is not None, "Неверное время", 400)
    slots = _slots()
    y = _year_of_date(date)
    s, _w = schedule_for(y["id"], date) if y else (None, 1)
    res = {"date": date, "time": hm, "has_bells": bool(slots), "has_schedule": bool(s), "now": None, "next": None,
           "weekday_ok": _d(date).isoweekday() <= 6}
    cur = next((x for x in slots if x[1] <= t < x[2]), None)
    nxt = next((x for x in slots if x[1] > t and (not cur or x[0] > cur[0])), None)
    for key, sl in (("now", cur), ("next", nxt)):
        if sl:
            res[key] = dict(_free_for(date, sl[0]), start="%02d:%02d" % divmod(sl[1], 60), end="%02d:%02d" % divmod(sl[2], 60))
    if not cur and slots:
        res["note"] = "Сейчас перемена или занятия не идут."
    return res


# =================================================================== ДЕЖУРСТВО
def _duty_out(r):
    c = db().one("SELECT grade, letter FROM classes WHERE id=?", (r["class_id"],))
    today = time.strftime("%Y-%m-%d")
    return dict(r, class_name=cn(c), active=r["start_date"] <= today <= r["end_date"])


def duty_text(date=None):
    date = date or time.strftime("%Y-%m-%d")
    rows = db().q("SELECT * FROM duty WHERE start_date<=? AND end_date>=? ORDER BY id", (date, date))
    parts = []
    for r in rows:
        o = _duty_out(r)
        parts.append(o["class_name"] + (" (%s)" % r["place"] if r["place"] else ""))
    if not parts:
        return ""
    return ("Сегодня дежурит: " if len(parts) == 1 else "Сегодня дежурят: ") + ", ".join(parts)


@router.route("GET", "/api/duty")
def api_duty(req):
    need_school(req)
    y = year_arg(req)
    rows = db().q("SELECT d.* FROM duty d JOIN classes c ON c.id=d.class_id WHERE c.year_id=? ORDER BY d.start_date DESC, d.id DESC", (y,))
    return {"items": [_duty_out(r) for r in rows], "today": duty_text(), "can_edit": auth.can_manage(req.user)}


@router.route("GET", "/api/duty/today")
def api_duty_today(req):
    need_school(req)
    return {"text": duty_text(req.arg("date"))}


def _duty_row(d):
    c = db().one("SELECT id FROM classes WHERE id=?", (int(d.get("class_id") or 0),))
    need(c, "Выберите класс", 400)
    a = _d(d.get("start_date")).isoformat()
    b = _d(d.get("end_date") or a).isoformat()
    need(b >= a, "Конец периода раньше начала", 400)
    return {"class_id": c["id"], "start_date": a, "end_date": b, "place": (d.get("place") or "").strip()[:80]}


@router.route("POST", "/api/duty")
def api_duty_add(req):
    need_manage(req)
    row = _duty_row(req.json())
    row["created_by"] = req.user["id"]
    return {"id": insert("duty", row)}


@router.route("PUT", "/api/duty/:did")
def api_duty_put(req, did):
    need_manage(req)
    need(db().val("SELECT 1 FROM duty WHERE id=?", (int(did),)), "Не найдено", 404)
    update("duty", int(did), _duty_row(req.json()))
    return {"ok": True}


@router.route("DELETE", "/api/duty/:did")
def api_duty_del(req, did):
    need_manage(req)
    db().ex("DELETE FROM duty WHERE id=?", (int(did),))
    return {"ok": True}


# =================================================================== ЭКСТРЕННОЕ ОПОВЕЩЕНИЕ (SSE)
class Hub(object):
    def __init__(self):
        self.clients, self.lock = set(), threading.Lock()

    def add(self, q):
        with self.lock:
            if len(self.clients) >= 400:
                return False
            self.clients.add(q)
            return True

    def remove(self, q):
        with self.lock:
            self.clients.discard(q)

    def publish(self, event, data):
        msg = "event: %s\ndata: %s\n\n" % (event, json.dumps(data, ensure_ascii=False))
        with self.lock:
            for q in list(self.clients):
                try:
                    q.put_nowait(msg)
                except queue.Full:
                    pass

    def count(self):
        return len(self.clients)


HUB = Hub()


def _fg_for(bg):
    m = re.match(r"^#([0-9a-fA-F]{6})$", bg)
    r, g, b = [int(m.group(1)[i:i + 2], 16) for i in (0, 2, 4)]
    return "#000000" if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else "#ffffff"


def active_alert():
    r = db().one("SELECT * FROM alerts WHERE cancelled=0 AND until>? ORDER BY id DESC LIMIT 1", (time.time(),))
    if not r:
        return None
    return {"id": r["id"], "text": r["text"], "bg": r["bg"], "fg": r["fg"], "until": r["until"], "now": time.time()}


@router.route("GET", "/api/alert")
def api_alert_get(req):
    need_manage(req)
    return {"alert": active_alert(), "screens": HUB.count()}


@router.route("POST", "/api/alert")
def api_alert_set(req):
    need_manage(req)
    d = req.json()
    text = (d.get("text") or "").strip()[:400]
    need(text, "Введите текст оповещения", 400)
    bg = d.get("bg") or "#c62828"
    need(re.match(r"^#[0-9a-fA-F]{6}$", bg), "Неверный цвет фона", 400)
    try:
        minutes = max(1, min(720, int(d.get("minutes") or 5)))
    except (TypeError, ValueError):
        raise ApiError(400, "Длительность — число минут")
    db().ex("UPDATE alerts SET cancelled=1 WHERE cancelled=0")
    now = time.time()
    aid = insert("alerts", {"text": text, "bg": bg, "fg": _fg_for(bg), "started": now, "until": now + minutes * 60, "created_by": req.user["id"]})
    db().audit(req.user, req.ip, "alert_sent", {"id": aid, "minutes": minutes})
    HUB.publish("alert", active_alert())
    return {"alert": active_alert(), "screens": HUB.count()}


@router.route("DELETE", "/api/alert")
def api_alert_clear(req):
    need_manage(req)
    db().ex("UPDATE alerts SET cancelled=1 WHERE cancelled=0")
    db().audit(req.user, req.ip, "alert_cleared", "")
    HUB.publish("clear", {"now": time.time()})
    return {"ok": True}


# =================================================================== КИОСКИ
KIOSK_TTL = 45          # секунд (требование: 30–60)
_cache = {}
_cache_lock = threading.Lock()


def kiosk_enabled():
    return db().get_setting("kiosk_enabled", True) is not False


@router.route("GET", "/api/kiosk")
def api_kiosk_get(req):
    need_manage(req)
    return {"enabled": kiosk_enabled(), "ttl": KIOSK_TTL, "screens": HUB.count()}


@router.route("PUT", "/api/kiosk")
def api_kiosk_put(req):
    need_admin(req)
    db().set_setting("kiosk_enabled", bool(req.json().get("enabled")))
    return {"ok": True}


def _day_rows(date):
    y = _year_of_date(date)
    if not y:
        return [], None
    s, week = schedule_for(y["id"], date)
    if not s or _d(date).isoweekday() > 6:
        return [], s
    rows = db().q(
        "SELECT si.lesson_no, si.class_id, si.assignment_id, si.room, si.room_id, a.teacher_id, a.group_name, sb.name AS subject, sb.short, "
        "c.grade, c.letter FROM schedule_items si JOIN assignments a ON a.id=si.assignment_id JOIN subjects sb ON sb.id=a.subject_id "
        "JOIN classes c ON c.id=si.class_id WHERE si.schedule_id=? AND si.week=? AND si.weekday=? ORDER BY si.lesson_no, c.grade, c.letter",
        (s["id"], week, _d(date).isoweekday()))
    subs = dict(((r["assignment_id"], r["lesson_no"]), r["substitute_id"]) for r in db().q("SELECT * FROM substitutions WHERE date=?", (date,)))
    names = {}

    def nm(i):
        if i not in names:
            names[i] = _staff_short(i)
        return names[i]
    out = []
    for r in rows:
        sub = subs.get((r["assignment_id"], r["lesson_no"]))
        eff = sub or r["teacher_id"]
        out.append({"n": r["lesson_no"], "class_id": r["class_id"], "class": cn(r), "subject": r["subject"], "short": r["short"] or r["subject"],
                    "group": r["group_name"], "teacher_id": eff, "orig_id": r["teacher_id"], "teacher": nm(eff), "orig": nm(r["teacher_id"]),
                    "sub": bool(sub), "room": r["room"] or "", "room_id": r["room_id"]})
    return out, s


def _kiosk_payload(kind, ident):
    date = time.strftime("%Y-%m-%d")
    rows, s = _day_rows(date)
    title, sub_title = "", ""
    if kind == "class":
        c = db().one("SELECT * FROM classes WHERE id=?", (ident,))
        if not c:
            return None
        title = cn(c) + " класс"
        rows = [r for r in rows if r["class_id"] == ident]
    elif kind == "teacher":
        st = db().one("SELECT last_name, first_name, middle_name FROM staff WHERE id=?", (ident,))
        if not st:
            return None
        title = " ".join(x for x in (st["last_name"], st["first_name"], st["middle_name"]) if x)
        rows = [dict(r, replaced=(r["orig_id"] == ident and r["teacher_id"] != ident)) for r in rows if ident in (r["teacher_id"], r["orig_id"])]
    elif kind == "room":
        rm = db().one("SELECT * FROM classrooms WHERE id=?", (ident,))
        if not rm:
            return None
        title = "Кабинет " + rm["name"]
        sub_title = KINDS.get(rm["kind"], "")
        keys = set([rm["name"].lower(), (rm["short"] or "").lower()])
        rows = [r for r in rows if r["room_id"] == ident or (not r["room_id"] and r["room"].lower() in keys)]
    else:
        title = "Расписание уроков"
    return {"kind": kind, "id": ident, "title": title, "subtitle": sub_title, "date": date, "lessons": rows,
            "bells": bells(), "has_schedule": bool(s), "duty": duty_text(date),
            "school": R.school_name(db()), "week": None}


def kiosk_data(kind, ident):
    key = (kind, ident, time.strftime("%Y-%m-%d"))
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < KIOSK_TTL:
            return hit[1]
        for k in [k for k, v in _cache.items() if now - v[0] > KIOSK_TTL * 4]:
            _cache.pop(k, None)
    p = _kiosk_payload(kind, ident)
    with _cache_lock:
        _cache[key] = (now, p)
    return p


def kiosk_get(h, path, query):
    """Публичные страницы: /kiosk/..., /kiosk/data/..., /kiosk/events, /ca.cer"""
    if path in ("/ca.cer", "/ca.crt"):
        store = cert_store()
        data = store.ca_der() if path.endswith("cer") else (store.ca_pem() or "").encode()
        if not data:
            return h._send(404, "Сертификат ещё не создан", "text/plain; charset=utf-8")
        return h._send(200, data, "application/x-x509-ca-cert", [("Content-Disposition", 'attachment; filename="schoolca%s"' % path[-4:])])
    if not kiosk_enabled():
        return h._send(404, "Публичные экраны отключены администратором", "text/plain; charset=utf-8")
    if path.startswith("/kiosk/pulse/"):
        from . import pulse
        return pulse.kiosk_get(h, path, query)
    if path == "/kiosk/events":
        return _sse(h)
    m = re.match(r"^/kiosk/data/(class|teacher|room)/(\d+)$", path) or re.match(r"^/kiosk/data/(school)()$", path)
    if m:
        p = kiosk_data(m.group(1), int(m.group(2) or 0))
        if p is None:
            return h._send(404, json.dumps({"error": "Не найдено"}), extra=[("Cache-Control", "no-store")])
        p = dict(p, alert=active_alert(), now=time.time(), tz=-time.timezone // 60)
        return h._send(200, json.dumps(p, ensure_ascii=False), extra=[("Cache-Control", "no-store")])
    m = re.match(r"^/kiosk/(class|teacher|room)/(\d+)/?$", path) or re.match(r"^/kiosk/(school)/?()$", path)
    if m:
        html = KIOSK_HTML.replace("__KIND__", m.group(1)).replace("__ID__", m.group(2) or "0")
        return h._send(200, html, "text/html; charset=utf-8", [("Cache-Control", "no-store")])
    return h._send(404, "Not found", "text/plain")


def _sse(h):
    q = queue.Queue(maxsize=50)
    if not HUB.add(q):
        return h._send(503, "Слишком много экранов", "text/plain")
    try:
        h.send_response(200)
        h.send_header("Content-Type", "text/event-stream; charset=utf-8")
        h.send_header("Cache-Control", "no-cache")
        h.send_header("Connection", "close")
        h.send_header("X-Accel-Buffering", "no")
        h.end_headers()
        h.close_connection = True
        a = active_alert()
        h.wfile.write(("retry: 3000\n\n" + ("event: alert\ndata: %s\n\n" % json.dumps(a, ensure_ascii=False) if a else "")).encode("utf-8"))
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
        HUB.remove(q)


KIOSK_HTML = r"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Расписание</title>
<style>
*{box-sizing:border-box}html,body{margin:0;height:100%;background:#0b1220;color:#fff;font-family:"Segoe UI",Arial,sans-serif;overflow:hidden}
#top{display:flex;justify-content:space-between;align-items:center;padding:1.2vh 2vw;background:#13213d;border-bottom:.5vh solid #ffd54a;height:13vh}
#ttl{font-size:5.2vh;font-weight:700;line-height:1.1}#sub{font-size:2.6vh;color:#b8c7e6}
#clock{font-size:8vh;font-weight:700;color:#ffd54a;font-variant-numeric:tabular-nums}#date{font-size:2.6vh;color:#b8c7e6;text-align:right}
#duty{display:none;background:#ffd54a;color:#0b1220;font-size:3.4vh;font-weight:700;padding:1vh 2vw;height:6.5vh}
#wrap{position:absolute;left:0;right:0;top:13.5vh;bottom:0;overflow:hidden}#wrap.d{top:20vh}
#scroll{position:absolute;left:0;right:0;top:0}
table{border-collapse:collapse;width:100%}td,th{padding:1.1vh 1.2vw;border-bottom:1px solid #2a3b60;font-size:4vh;vertical-align:middle}
th{font-size:2.6vh;color:#b8c7e6;text-align:left;background:#0f1a30}
td.n{width:6vw;font-weight:700;color:#ffd54a;text-align:center;font-size:5vh}td.t{width:14vw;color:#b8c7e6;font-size:3vh}
td.r{width:12vw;text-align:right;font-weight:700}
tr.cur td{background:#1b5e20;color:#fff}tr.cur td.n,tr.cur td.t{color:#fff}tr.nxt td{background:#17325e}
tr.rep td{opacity:.5;text-decoration:line-through}
.sub{color:#ffab40;font-size:3vh;display:block}tr.cur .sub{color:#ffe082}.s2{font-size:2.8vh;color:#b8c7e6;display:block}tr.cur .s2{color:#c8e6c9}
#school td{font-size:2.6vh;padding:.8vh .6vw;border:1px solid #2a3b60;text-align:center}#school td.c{font-weight:700;font-size:3.2vh;background:#0f1a30;width:7vw;text-align:left}
#school td.cur{background:#1b5e20}#school td.sb{background:#4e2a00}#school small{display:block;color:#b8c7e6;font-size:2vh}
#school th{text-align:center;font-size:2.6vh}#school th.cur{background:#1b5e20}
#empty{text-align:center;font-size:5vh;margin-top:20vh;color:#b8c7e6}
#off{position:fixed;right:1vw;bottom:.6vh;font-size:2vh;color:#ff8a80;display:none}
#alert{display:none;position:fixed;left:0;top:0;right:0;bottom:0;z-index:9;align-items:center;justify-content:center;text-align:center;padding:4vw;
font-size:9vh;font-weight:800;line-height:1.15;white-space:pre-wrap}
#alert.on{display:flex;opacity:1;animation:bl 1.2s infinite alternate}@keyframes bl{from{filter:brightness(1)}to{filter:brightness(.78)}}
</style></head><body>
<div id="top"><div><div id="ttl">…</div><div id="sub"></div></div><div><div id="clock">--:--</div><div id="date"></div></div></div>
<div id="duty"></div>
<div id="wrap"><div id="scroll"></div></div><div id="off">нет связи с сервером</div><div id="alert"></div>
<script>
(function(){
var KIND="__KIND__",ID="__ID__",D=null,off=0,alertUntil=0;
var WD=["воскресенье","понедельник","вторник","среда","четверг","пятница","суббота"],MN=["января","февраля","марта","апреля","мая","июня","июля","августа","сентября","октября","ноября","декабря"];
function $(i){return document.getElementById(i)}
function esc(s){return String(s==null?"":s).replace(/[&<>"]/g,function(c){return{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]})}
function srv(){return new Date(Date.now()+off)}
function mins(hm){var m=/^(\d+):(\d+)/.exec(hm||"");return m?+m[1]*60+ +m[2]:null}
function p2(n){return(n<10?"0":"")+n}
function curLesson(){ if(!D)return{cur:null,nxt:null};var t=srv().getHours()*60+srv().getMinutes(),b=D.bells||{},cur=null,nxt=null,ks=Object.keys(b).map(Number).sort(function(a,c){return a-c});
 for(var i=0;i<ks.length;i++){var a=mins(b[ks[i]][0]),e=mins(b[ks[i]][1]);if(a==null||e==null)continue;if(t>=a&&t<e){cur=ks[i];break}if(a>t&&nxt==null)nxt=ks[i]}
 if(cur!=null){nxt=null;for(var j=0;j<ks.length;j++)if(ks[j]>cur){nxt=ks[j];break}}return{cur:cur,nxt:nxt}}
function bell(n){var b=D&&D.bells&&D.bells[n];return b?b[0]+"–"+b[1]:""}
function tick(){var d=srv();$("clock").textContent=p2(d.getHours())+":"+p2(d.getMinutes());$("date").textContent=WD[d.getDay()]+", "+d.getDate()+" "+MN[d.getMonth()]+" "+d.getFullYear();
 if(alertUntil&&Date.now()+off>alertUntil*1000){hideAlert()} paintCur()}
function paintCur(){if(!D)return;var c=curLesson(),rows=document.querySelectorAll("[data-n]");
 for(var i=0;i<rows.length;i++){var n=+rows[i].getAttribute("data-n"),el=rows[i];
  var isr=el.tagName=="TR";if(isr){el.className=(el.className.replace(/\b(cur|nxt)\b/g,"")+(n===c.cur?" cur":(n===c.nxt&&c.cur==null?" nxt":""))).replace(/^\s+/,"")}
  else{el.className=el.className.replace(/\bcur\b/g,"")+(n===c.cur?" cur":"")}}}
function render(){
 $("ttl").textContent=D.title;$("sub").textContent=[D.subtitle,D.school].filter(Boolean).join(" · ");
 var du=$("duty");if(KIND=="school"&&D.duty){du.style.display="block";du.textContent=D.duty;$("wrap").className="d"}else{du.style.display="none";$("wrap").className=""}
 var h="";
 if(!D.lessons.length){h='<div id="empty">'+(D.has_schedule?"Сегодня уроков нет":"Расписание не введено")+"</div>"}
 else if(KIND=="school"){h=renderSchool()}
 else{h="<table><thead><tr><th>№</th><th>Время</th><th>Предмет</th><th>"+(KIND=="class"?"Учитель":"Класс")+'</th><th style="text-align:right">Каб.</th></tr></thead><tbody>';
  var L=D.lessons.slice().sort(function(a,b){return a.n-b.n});
  for(var i=0;i<L.length;i++){var l=L[i],who=KIND=="class"?l.teacher:l.class;
   h+='<tr data-n="'+l.n+'"'+(l.replaced?' class="rep"':'')+'><td class="n">'+l.n+'</td><td class="t">'+(bell(l.n)||(l.n+" урок"))+"</td><td>"+esc(l.subject)+(l.group?' <span class="s2">'+esc(l.group)+"</span>":"")+
   (l.sub&&KIND!="teacher"?'<span class="sub">замена: '+esc(l.teacher)+(l.orig?" (вместо "+esc(l.orig)+")":"")+"</span>":"")+(l.replaced?'<span class="sub">заменяет '+esc(l.teacher)+"</span>":"")+
   "</td><td>"+esc(who)+'</td><td class="r">'+esc(l.room)+"</td></tr>"}
  h+="</tbody></table>"}
 $("scroll").innerHTML=h;paintCur();sc=0;$("scroll").style.top="0px"}
function renderSchool(){var by={},ns={},i,L=D.lessons;
 for(i=0;i<L.length;i++){var l=L[i];(by[l.class]=by[l.class]||{cid:l.class_id,r:{}});(by[l.class].r[l.n]=by[l.class].r[l.n]||[]).push(l);ns[l.n]=1}
 var nums=Object.keys(ns).map(Number).sort(function(a,b){return a-b}),cl=Object.keys(by).sort(function(a,b){var x=parseInt(a),y=parseInt(b);return x-y||(a<b?-1:1)});
 var h='<table id="school"><thead><tr><th>Класс</th>';
 for(i=0;i<nums.length;i++)h+='<th data-n="'+nums[i]+'">'+nums[i]+(bell(nums[i])?"<small>"+bell(nums[i]).split("–")[0]+"</small>":"")+"</th>";
 h+="</tr></thead><tbody>";
 for(var k=0;k<cl.length;k++){h+='<tr><td class="c">'+esc(cl[k])+"</td>";
  for(i=0;i<nums.length;i++){var c=by[cl[k]].r[nums[i]]||[],t="",sb=false;
   for(var j=0;j<c.length;j++){t+=(j?"<hr>":"")+esc(c[j].short)+"<small>"+esc(c[j].teacher)+(c[j].room?" · "+esc(c[j].room):"")+"</small>";if(c[j].sub)sb=true}
   h+='<td data-n="'+nums[i]+'"'+(sb?' class="sb"':'')+">"+t+"</td>"}
  h+="</tr>"}
 return h+"</tbody></table>"}
var sc=0,dir=1,pause=0;
function auto(){var w=$("wrap"),s=$("scroll"),over=s.offsetHeight-w.offsetHeight;if(over<=4){s.style.top="0px";sc=0;pause=0;return}
 if(pause>0){pause--;return}sc+=dir*2;if(sc>=over+10){dir=-1;pause=60}else if(sc<=0){sc=0;dir=1;pause=60}s.style.top=(-sc)+"px"}
function load(){var x=new XMLHttpRequest();x.open("GET","/kiosk/data/"+KIND+(KIND=="school"?"":"/"+ID),true);
 x.onreadystatechange=function(){if(x.readyState!=4)return;
  if(x.status==200){try{D=JSON.parse(x.responseText);off=D.now*1000-Date.now();$("off").style.display="none";render();if(D.alert)showAlert(D.alert);else if(alertUntil&&!D.alert)hideAlert()}catch(e){}}
  else if(x.status==404){$("ttl").textContent="Экран не найден или отключён"}else{$("off").style.display="block"}};
 x.onerror=function(){$("off").style.display="block"};x.send()}
function showAlert(a){var e=$("alert");e.style.background=a.bg;e.style.color=a.fg;e.textContent=a.text;e.className="on";alertUntil=a.until}
function hideAlert(){$("alert").className="";alertUntil=0}
function sse(){if(!window.EventSource)return;var es=new EventSource("/kiosk/events");
 es.addEventListener("alert",function(e){try{var a=JSON.parse(e.data);if(a){off=a.now*1000-Date.now();showAlert(a)}}catch(x){}});
 es.addEventListener("clear",function(){hideAlert()});es.onerror=function(){}}
load();setInterval(load,30000);setInterval(tick,1000);setInterval(auto,50);sse();tick();
})();
</script></body></html>"""


# =================================================================== ИМПОРТ aSc (мастер)
_ASC = {}
_ASC_LOCK = threading.Lock()


def _sg_lists(year_id):
    classes = [{"id": c["id"], "name": cn(c), "grade": c["grade"], "letter": c["letter"]}
               for c in db().q("SELECT * FROM classes WHERE year_id=? ORDER BY grade, letter", (year_id,))]
    staff = [{"id": s["id"], "name": R.staff_short(s), "last": s["last_name"], "first": s["first_name"], "middle": s["middle_name"] or ""}
             for s in db().q("SELECT id, last_name, first_name, middle_name FROM staff WHERE active=1 ORDER BY last_name, first_name")]
    subjects = [{"id": s["id"], "name": s["name"], "short": s["short"] or ""} for s in db().q("SELECT * FROM subjects WHERE active=1 ORDER BY sort, name")]
    rooms = [{"id": r["id"], "name": r["name"], "short": r["short"] or ""} for r in db().q("SELECT * FROM classrooms ORDER BY sort, name")]
    return {"classes": classes, "staff": staff, "subjects": subjects, "rooms": rooms}


def _used(p):
    u = {"classes": set(), "teachers": set(), "subjects": set(), "rooms": set()}
    cards = {}
    for c in p["cards"]:
        cards.setdefault(c["lesson"], []).append(c)
    for lid, les in p["lessons"].items():
        if lid not in cards:
            continue
        u["classes"].update(les["classes"])
        u["teachers"].update(les["teachers"])
        u["subjects"].add(les["subject"])
        u["rooms"].update(les["rooms"])
        for c in cards[lid]:
            u["rooms"].update(c["rooms"])
    return u


@router.route("POST", "/api/schedule/asc/upload")
def api_asc_upload(req):
    need_manage(req)
    d = req.json()
    try:
        data = base64.b64decode(d.get("data") or "")
    except ValueError:
        raise ApiError(400, "Файл не прочитан")
    need(data, "Файл пуст", 400)
    try:
        p = asc.parse(data)
    except asc.AscError as e:
        raise ApiError(400, str(e))
    y = current_year()
    need(y, "Не создан учебный год", 400)
    sg = _sg_lists(y["id"])
    auto = asc.auto_map(p, sg)
    used = _used(p)
    token = base64.urlsafe_b64encode(os.urandom(18)).decode()
    with _ASC_LOCK:
        for k in [k for k, v in _ASC.items() if time.time() - v[0] > 3600]:
            _ASC.pop(k)
        while len(_ASC) >= 4:
            _ASC.pop(min(_ASC, key=lambda k: _ASC[k][0]))
        _ASC[token] = (time.time(), p, auto, y["id"])

    def lst(key, extra=None):
        out = []
        for k, v in p[key].items():
            if k in used[key]:
                o = {"asc_id": k, "name": v["name"], "short": v.get("short", ""), "sg_id": auto[key].get(k)}
                if extra:
                    o.update(extra(v))
                out.append(o)
        return out
    over = sorted(set(c["period"] for c in p["cards"] if c["period"] > MAX_LESSON))
    warns = list(p["warnings"])
    if over:
        warns.append("В файле есть уроки с номерами %s — за пределами сетки (максимум %d уроков). Такие уроки загружены не будут: "
                     "уменьшите число уроков в aSc или объедините звонки." % (", ".join(map(str, over[:10])), MAX_LESSON))
    return {"token": token, "version": p["version"], "year": y["name"] if "name" in y else "", "cycle": p["cycle"], "warnings": warns,
            "lost_chars": p["lost_chars"], "max_period": p["max_period"], "overflow": over,
            "counts": {"cards": len(p["cards"]), "lessons": len(p["lessons"]), "classes": len(used["classes"]), "teachers": len(used["teachers"]),
                       "subjects": len(used["subjects"]), "rooms": len(used["rooms"])},
            "classes": lst("classes"), "teachers": lst("teachers"), "subjects": lst("subjects"),
            "rooms": lst("rooms", lambda r: {"capacity": r["capacity"], "kind": asc.guess_kind(r["name"])}),
            "periods": p["periods"], "bells_exist": bool(bells()), "sg": sg,
            "start_date": time.strftime("%Y-%m-%d"), "name": "Импорт из aSc от " + time.strftime("%d.%m.%Y")}


def _pick(user, auto, key, none_ok=True):
    out = dict(auto[key])
    for k, v in (user.get(key) or {}).items():
        if v in (None, "", 0, "0"):
            out[k] = None
        elif v == "new":
            out[k] = "new"
        else:
            try:
                out[k] = int(v)
            except (TypeError, ValueError):
                out[k] = None
    return out


@router.route("POST", "/api/schedule/asc/apply")
def api_asc_apply(req):
    need_manage(req)
    d = req.json()
    with _ASC_LOCK:
        hit = _ASC.get(d.get("token") or "")
    need(hit, "Сеанс импорта устарел — загрузите файл заново", 400)
    _ts, p, auto, year_id = hit
    user = d.get("mapping") or {}
    m = {k: _pick(user, auto, k) for k in ("classes", "teachers", "subjects", "rooms")}
    add_rooms = d.get("add_rooms", True) is not False
    # кабинеты, не найденные в справочнике: «new» (если разрешено), иначе без кабинета
    new_rooms = {}
    used_rooms = _used(p)["rooms"]
    for k, v in list(m["rooms"].items()):
        if k not in used_rooms:
            continue
        if v is None and k in p["rooms"] and add_rooms and k not in (user.get("rooms") or {}):
            m["rooms"][k] = v = "new"
        if v == "new":
            if add_rooms:
                new_rooms[k] = p["rooms"][k]
            else:
                m["rooms"][k] = None
    start = _d(d.get("start_date") or time.strftime("%Y-%m-%d")).isoformat()
    need(not db().val("SELECT 1 FROM schedules WHERE year_id=? AND start_date=?", (year_id, start)),
         "Версия расписания с датой начала %s уже существует — выберите другую дату" % R._ru_date(start), 400)
    try:
        shift = max(-5, min(5, int(d.get("shift") or 0)))
    except (TypeError, ValueError):
        shift = 0
    assignments = db().q("SELECT a.id, a.class_id, a.subject_id, a.teacher_id, a.group_name FROM assignments a JOIN classes c ON c.id=a.class_id WHERE c.year_id=?", (year_id,))
    mm = dict(m)
    mm["rooms"] = {k: (("new:" + k) if v == "new" else v) for k, v in m["rooms"].items()}
    items, rep = asc.build_items(p, mm, assignments, shift, MAX_LESSON)
    cname = {c["id"]: cn(c) for c in db().q("SELECT grade, letter, id FROM classes WHERE year_id=?", (year_id,))}
    sname = {s["id"]: s["name"] for s in db().q("SELECT id, name FROM subjects")}
    problems = []
    if rep["overflow"]:
        nums = sorted(rep["overflow"])
        problems.append("ВНИМАНИЕ: %d уроков не поместились в сетку из %d слотов (номера %s) — они не загружены. Расширьте сетку или сократите число уроков в aSc."
                        % (sum(rep["overflow"].values()), MAX_LESSON, ", ".join(map(str, nums[:12]))))
    for k in sorted(rep["no_subject"]):
        problems.append("Предмет «%s» не сопоставлен — его уроки пропущены" % p["subjects"].get(k, {}).get("name", k))
    for k in sorted(rep["no_class"]):
        problems.append("Класс «%s» не сопоставлен — его уроки пропущены" % p["classes"].get(k, {}).get("name", k))
    for (cid, sid), n in sorted(rep["no_load"].items(), key=lambda x: (cname.get(x[0][0], ""), x[0][1])):
        problems.append("%s: нет нагрузки по предмету «%s» (карточек: %d). Добавьте её в «Классы и нагрузка»" % (cname.get(cid, cid), sname.get(sid, sid), n))
    for (cid, sid), n in sorted(rep["ambiguous"].items(), key=lambda x: (cname.get(x[0][0], ""), x[0][1])):
        problems.append("%s, «%s»: не удалось определить подгруппу/учителя (карточек: %d) — урок пропущен" % (cname.get(cid, cid), sname.get(sid, sid), n))
    if rep["bad_day"]:
        problems.append("Уроки в воскресенье пропущены: %d" % rep["bad_day"])
    res = {"items": len(items), "classes": len(set(i["class_id"] for i in items)), "problems": problems[:300],
           "rooms_new": [dict(name=r["name"], short=r["short"], capacity=r["capacity"]) for r in new_rooms.values()],
           "overflow": bool(rep["overflow"]), "weeks": p["cycle"], "dry_run": bool(d.get("dry_run"))}
    if d.get("dry_run"):
        return res
    need(items, "Не получилось ни одного урока — проверьте сопоставление", 400)
    with db().tx():
        room_ids = {}
        for k, r in new_rooms.items():
            ex = _room_by_name(r["name"][:60])
            room_ids["new:" + k] = ex["id"] if ex else insert("classrooms", {
                "name": r["name"][:60], "short": (r["short"] or r["name"])[:20], "capacity": r["capacity"], "kind": asc.guess_kind(r["name"]),
                "sort": (db().val("SELECT MAX(sort) FROM classrooms") or 0) + 10})
        sid = insert("schedules", {"year_id": year_id, "name": (d.get("name") or "").strip()[:100] or "Импорт из aSc", "start_date": start, "weeks": p["cycle"]})
        rname = {r["id"]: (r["short"] or r["name"])[:20] for r in db().q("SELECT * FROM classrooms")}
        for it in items:
            rid = room_ids.get(it["room_id"], it["room_id"]) if isinstance(it["room_id"], str) else it["room_id"]
            insert("schedule_items", {"schedule_id": sid, "class_id": it["class_id"], "week": it["week"], "weekday": it["weekday"],
                                      "lesson_no": it["lesson_no"], "assignment_id": it["assignment_id"], "room_id": rid, "room": rname.get(rid, "")})
        if d.get("import_bells"):
            b = {}
            for pr in p["periods"]:
                n = pr["period"] + shift
                if pr["start"] and pr["end"] and 0 <= n <= MAX_LESSON:
                    b[str(n)] = [pr["start"], pr["end"]]
            if b:
                db().set_setting("bells", b)
    db().audit(req.user, req.ip, "schedule_asc_imported", {"schedule": sid, "items": len(items)})
    with _ASC_LOCK:
        _ASC.pop(d.get("token"), None)
    res.update({"id": sid, "dry_run": False, "rooms_added": len(new_rooms)})
    return res


# =================================================================== HTTPS / адрес по имени ПК
def cert_store():
    return cert.CertStore(os.path.join(app().data_dir, "certs"))


def https_enabled():
    return bool(db().get_setting("https_enabled", False))


def apply_tls():
    """Применить настройки HTTPS к работающему серверу (вызывается при старте и после изменений)."""
    srv = getattr(app(), "server", None)
    if srv is None:
        return
    ctx = None
    try:
        ctx = cert_store().ssl_context() if https_enabled() else None
    except Exception:  # noqa — повреждённый сертификат не должен ронять сервер
        ctx = None
    srv.set_tls(ctx, https_enabled())


def addresses(port=None, https=None):
    port = port or getattr(app(), "port", 8080)
    https = https_enabled() if https is None else https
    names = cert.host_names() or ["localhost"]
    out = []
    for n in names:
        out.append("http://%s:%d" % (n.upper() if "." not in n else n, port))
        if https:
            out.append("https://%s:%d" % (n.upper() if "." not in n else n, port))
    out += ["http://%s:%d" % (ip, port) for ip in local_addresses()]
    return out


@router.route("GET", "/api/server_info")
def api_server_info(req):
    return {"host": (cert.host_names() or [""])[0], "port": getattr(app(), "port", 8080), "urls": addresses(), "https": https_enabled()}


@router.route("GET", "/api/https")
def api_https(req):
    need_admin(req)
    st = cert_store().status()
    st.update({"enabled": https_enabled(), "urls": addresses(), "port": getattr(app(), "port", 8080), "ca_url": "/ca.cer"})
    return st


@router.route("POST", "/api/https/generate")
def api_https_generate(req):
    need_admin(req)
    d = req.json()
    try:
        years = max(0, min(50, int(d.get("years") or 0)))
    except (TypeError, ValueError):
        raise ApiError(400, "Срок — число лет (0 — бессрочный)")
    names = [x for x in re.split(r"[\s,;]+", d.get("names") or "") if x] if isinstance(d.get("names"), str) else (d.get("names") or [])
    org = (R.school_name(db()) or "SchoolGrade")[:50]
    try:
        cert_store().generate(years, names, org, bool(d.get("new_ca")))
    except cert.CertError as e:
        raise ApiError(400, str(e))
    db().audit(req.user, req.ip, "https_generated", {"years": years})
    apply_tls()
    return api_https(req)


@router.route("POST", "/api/https/enable")
def api_https_enable(req):
    need_admin(req)
    on = bool(req.json().get("enabled"))
    if on:
        need(cert_store().has_server(), "Сначала создайте или импортируйте сертификат", 400)
        try:
            cert_store().ssl_context()
        except Exception as e:  # noqa
            raise ApiError(400, "Сертификат не удалось загрузить: %s" % e)
    db().set_setting("https_enabled", on)
    apply_tls()
    return api_https(req)


@router.route("GET", "/api/https/export")
def api_https_export(req):
    need_admin(req)
    try:
        data = cert_store().export_zip(req.arg("keys") != "0")
    except cert.CertError as e:
        raise ApiError(400, str(e))
    db().audit(req.user, req.ip, "https_exported", {"keys": req.arg("keys") != "0"})
    return FileResponse(data, "SchoolCert_%s.zip" % time.strftime("%Y%m%d"), "application/zip")


@router.route("POST", "/api/https/import")
def api_https_import(req):
    need_admin(req)
    d = req.json()
    files = []
    for f in (d.get("files") or [])[:10]:
        try:
            files.append((os.path.basename(f.get("name") or "file"), base64.b64decode(f.get("data") or "")))
        except ValueError:
            raise ApiError(400, "Файл не прочитан")
    need(files, "Выберите файлы сертификата", 400)
    try:
        warns = cert_store().import_files(files, d.get("reissue", True) is not False, (R.school_name(db()) or "SchoolGrade")[:50])
    except cert.CertError as e:
        raise ApiError(400, str(e))
    db().audit(req.user, req.ip, "https_imported", {})
    apply_tls()
    st = api_https(req)
    st["warnings"] = warns
    return st


@router.route("DELETE", "/api/https")
def api_https_del(req):
    need_admin(req)
    cert_store().remove()
    db().set_setting("https_enabled", False)
    apply_tls()
    return {"ok": True}


def install(application):
    application.public_get = kiosk_get
