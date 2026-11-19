# -*- coding: utf-8 -*-
"""Разбор экспорта «aSc Timetables (Расписание школы)» в XML (формат 2024.24.1 и совместимые)
и сопоставление его данных со справочниками SchoolGrade. Модуль не обращается к базе данных:
всё, что нужно из SchoolGrade, передаётся в функции обычными списками словарей."""
import re
import xml.etree.ElementTree as ET

MAX_LESSON = 20
MAX_XML = 40 * 1024 * 1024
FFFD = "�"

_LATIN = {"a": "а", "b": "в", "c": "с", "e": "е", "h": "н", "k": "к", "m": "м", "o": "о", "p": "р", "t": "т", "x": "х"}


class AscError(Exception):
    pass


def norm(s):
    s = (s or "").lower().replace("ё", "е")
    return re.sub(r"[^a-zа-я0-9]+", "", s)


def _letter(ch):
    ch = (ch or "").lower().replace("ё", "е")
    return _LATIN.get(ch, ch).upper()


def _ids(v):
    return [x.strip() for x in (v or "").split(",") if x.strip()]


def _hhmm(v):
    m = re.match(r"^\s*(\d{1,2}):(\d{2})", v or "")
    return "%02d:%s" % (int(m.group(1)), m.group(2)) if m else ""


def decode(data):
    """Байты -> (текст XML без объявления, число утраченных символов)."""
    if len(data) > MAX_XML:
        raise AscError("Файл слишком большой (больше 40 МБ)")
    if data[:3] == b"\xef\xbb\xbf":
        data = data[3:]
    head = data[:300].decode("ascii", "replace")
    m = re.search(r"encoding\s*=\s*[\"']([\w\-]+)[\"']", head)
    enc = (m.group(1) if m else "utf-8").lower()
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        enc = "utf-16"
    text = None
    if enc not in ("utf-8", "utf8", "utf-16") and any(b >= 0x80 for b in data[:200000]):
        try:                                  # файл в UTF-8, но в заголовке указана windows-1251 — частая ошибка выгрузки
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = None
    if text is None:
        try:
            text = data.decode(enc, "replace")
        except LookupError:
            raise AscError("Неизвестная кодировка файла: %s" % enc)
    lost = text.count(FFFD)
    text = re.sub(r"^\s*<\?xml[^>]*\?>", "", text.lstrip("﻿"), count=1)
    if re.search(r"<!(DOCTYPE|ENTITY)", text, re.I):
        raise AscError("В файле есть DOCTYPE/ENTITY — такой XML не принимается из соображений безопасности")
    return text, lost


def _rows(root, section, tag):
    sec = root.find(section)
    return [dict(e.attrib) for e in sec.findall(tag)] if sec is not None else []


def _days(mask):
    out = set()
    for part in (mask or "").split(","):
        for i, ch in enumerate(part.strip()):
            if ch == "1":
                out.add(i + 1)
    return sorted(out)


def _weeks(v):
    """'10' -> [True, False], '01' -> [False, True], '11' или '1' -> оба. Возвращает (список, длина цикла)."""
    v = (v or "1").strip().split(",")[0] or "1"
    if len(v) == 1:
        return [v == "1", v == "1"], 1
    return [v[0] == "1", v[1] == "1"], len(v)


def parse(data):
    text, lost = decode(data)
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        raise AscError("Файл не является корректным XML (%s)" % e)
    if root.tag != "timetable":
        raise AscError("Это не экспорт aSc Timetables: корневой элемент <%s>, ожидается <timetable>" % root.tag)
    warns = []
    out = {"version": root.get("ascttversion", ""), "lost_chars": lost, "warnings": warns}
    out["periods"] = []
    for r in _rows(root, "periods", "period"):
        try:
            out["periods"].append({"period": int(r.get("period", "0")), "name": r.get("name", ""),
                                   "start": _hhmm(r.get("starttime")), "end": _hhmm(r.get("endtime"))})
        except ValueError:
            continue
    out["subjects"] = dict((r["id"], {"id": r["id"], "name": r.get("name", ""), "short": r.get("short", "")}) for r in _rows(root, "subjects", "subject") if r.get("id"))
    out["teachers"] = dict((r["id"], {"id": r["id"], "name": r.get("name", ""), "short": r.get("short", ""),
                                      "first": r.get("firstname", ""), "last": r.get("lastname", "")})
                           for r in _rows(root, "teachers", "teacher") if r.get("id"))
    out["rooms"] = {}
    for r in _rows(root, "classrooms", "classroom"):
        if r.get("id"):
            try:
                cap = int(r.get("capacity", "0"))
            except ValueError:
                cap = 0           # «*» — без ограничения
            out["rooms"][r["id"]] = {"id": r["id"], "name": r.get("name", ""), "short": r.get("short", ""), "capacity": cap}
    out["classes"] = dict((r["id"], {"id": r["id"], "name": r.get("name", ""), "short": r.get("short", "")})
                          for r in _rows(root, "classes", "class") if r.get("id"))
    out["groups"] = dict((r["id"], {"id": r["id"], "classid": r.get("classid", ""), "name": r.get("name", ""),
                                    "entire": r.get("entireclass") == "1"}) for r in _rows(root, "groups", "group") if r.get("id"))
    out["lessons"] = {}
    for r in _rows(root, "lessons", "lesson"):
        if r.get("id"):
            out["lessons"][r["id"]] = {"id": r["id"], "subject": r.get("subjectid", ""), "classes": _ids(r.get("classids")),
                                       "groups": _ids(r.get("groupids")), "teachers": _ids(r.get("teacherids")),
                                       "rooms": _ids(r.get("classroomids"))}
    out["cards"] = []
    cycle = 1
    for r in _rows(root, "cards", "card"):
        try:
            period = int(r.get("period", ""))
        except ValueError:
            continue
        wk, n = _weeks(r.get("weeks"))
        cycle = max(cycle, n)
        out["cards"].append({"lesson": r.get("lessonid", ""), "period": period, "days": _days(r.get("days")),
                             "weeks": wk, "rooms": _ids(r.get("classroomids"))})
    for r in _rows(root, "weeksdefs", "weeksdef"):
        cycle = max(cycle, max([len(w) for w in (r.get("weeks") or "1").split(",")] or [1]))
    out["cycle"] = 2 if cycle >= 2 else 1
    if cycle > 2:
        warns.append("В расписании цикл из %d недель — SchoolGrade поддерживает две. Учтены только первые две недели цикла." % cycle)
    if not out["cards"]:
        raise AscError("В файле нет карточек уроков (<cards>). Экспортируйте расписание полностью: «Файл → Экспорт → XML».")
    if lost:
        warns.append("В файле утрачены буквы: найдено %d символов «�». Так бывает, если XML пересохранили в неверной "
                     "кодировке (русские буквы превратились в «�»). Автоматическое сопоставление по названиям будет "
                     "работать плохо — лучше заново экспортировать файл из aSc (кодировка windows-1251 или UTF-8) и не "
                     "открывать его в текстовых редакторах." % lost)
    out["max_period"] = max([c["period"] for c in out["cards"]] or [0])
    return out


# ------------------------------------------------------------------ сопоставление
def parse_class_name(s):
    s = (s or "").replace("«", "").replace("»", "").replace('"', "").strip()
    m = re.match(r"^(\d{1,2})\s*[-–.]?\s*([^\W\d_])?\s*(?:класс|кл\.?)?$", s, re.I)
    if not m:
        return None
    return int(m.group(1)), _letter(m.group(2)) if m.group(2) else ""


def match_class(c, sg_classes):
    for s in (c["name"], c["short"]):
        p = parse_class_name(s)
        if p:
            for k in sg_classes:
                if k["grade"] == p[0] and (k["letter"] or "").upper() == p[1]:
                    return k["id"]
            if not p[1]:
                same = [k for k in sg_classes if k["grade"] == p[0]]
                if len(same) == 1:
                    return same[0]["id"]
    for k in sg_classes:
        if norm(k["name"]) in (norm(c["name"]), norm(c["short"])) and norm(k["name"]):
            return k["id"]
    return None


def _fio(t):
    toks = [x for x in re.split(r"[\s.]+", (t["name"] or "").strip()) if x]
    last = t["last"] or (toks[0] if toks else "")
    rest = toks[1:] if not t["last"] else ([t["first"]] if t["first"] else toks[1:])
    ini = [x[0].lower().replace("ё", "е") for x in rest if x]
    return norm(last), ini


def match_teacher(t, sg_staff):
    last, ini = _fio(t)
    if not last:
        return None
    cand = [s for s in sg_staff if norm(s["last"]) == last]
    if len(cand) > 1 and ini:
        def ok(s):
            fi = (s["first"] or "")[:1].lower().replace("ё", "е")
            mi = (s["middle"] or "")[:1].lower().replace("ё", "е")
            return (not ini or ini[0] == fi) and (len(ini) < 2 or ini[1] == mi)
        cand = [s for s in cand if ok(s)] or []
    return cand[0]["id"] if len(cand) == 1 else None


def match_subject(sj, sg_subjects):
    keys = [norm(sj["name"]), norm(sj["short"])]
    keys = [k for k in keys if k]
    for s in sg_subjects:
        if any(norm(s["name"]) == k or (norm(s["short"]) and norm(s["short"]) == k) for k in keys):
            return s["id"]
    cand = []
    for s in sg_subjects:
        names = [norm(s["name"]), norm(s["short"])]
        if any(n and k and len(k) >= 4 and (n.startswith(k) or k.startswith(n)) for n in names for k in keys):
            cand.append(s["id"])
    return cand[0] if len(cand) == 1 else None


def match_room(r, sg_rooms):
    keys = [norm(r["name"]), norm(r["short"])]
    for s in sg_rooms:
        if any(k and k in (norm(s["name"]), norm(s["short"])) for k in keys):
            return s["id"]
    return None


def guess_kind(name):
    n = (name or "").lower()
    if "актов" in n:
        return "hall"
    if "спорт" in n or "физкульт" in n or n.endswith("зал") or " зал" in n:
        return "gym"
    if "информат" in n or "компьют" in n:
        return "computer"
    return "regular"


def auto_map(p, sg):
    """Автоматическое сопоставление. Результат — словари asc_id -> id SchoolGrade (или None)."""
    m = {"classes": {}, "teachers": {}, "subjects": {}, "rooms": {}}
    for k, c in p["classes"].items():
        m["classes"][k] = match_class(c, sg["classes"])
    for k, t in p["teachers"].items():
        m["teachers"][k] = match_teacher(t, sg["staff"])
    for k, s in p["subjects"].items():
        m["subjects"][k] = match_subject(s, sg["subjects"])
    for k, r in p["rooms"].items():
        m["rooms"][k] = match_room(r, sg["rooms"])
    return m


# ------------------------------------------------------------------ построение расписания
def build_items(p, m, assignments, shift=0, max_lesson=MAX_LESSON):
    """p — результат parse(); m — {"classes","teachers","subjects","rooms"}: asc_id -> id SchoolGrade (None — пропуск;
    для кабинетов None — без кабинета); assignments — список нагрузок {id,class_id,subject_id,teacher_id,group_name}.
    Возвращает (items, report)."""
    by = {}
    for a in assignments:
        by.setdefault((a["class_id"], a["subject_id"]), []).append(a)
    items, seen = [], set()
    rep = {"overflow": {}, "no_class": set(), "no_subject": set(), "no_load": {}, "ambiguous": {}, "bad_day": 0, "cards": 0}
    for card in p["cards"]:
        les = p["lessons"].get(card["lesson"])
        if not les:
            continue
        rep["cards"] += 1
        sub = m["subjects"].get(les["subject"])
        if sub is None:
            rep["no_subject"].add(les["subject"])
            continue
        classes = [m["classes"].get(c) for c in les["classes"]]
        if not any(classes):
            rep["no_class"].update(les["classes"])
            continue
        teachers = [m["teachers"].get(t) for t in les["teachers"] if m["teachers"].get(t)]
        group_lesson = any(not (p["groups"].get(g) or {}).get("entire", True) for g in les["groups"])
        rooms = [m["rooms"].get(r) for r in (card["rooms"] or les["rooms"]) if m["rooms"].get(r)]
        lesson_no = card["period"] + shift
        if lesson_no < 0:
            continue
        if lesson_no > max_lesson:
            rep["overflow"][lesson_no] = rep["overflow"].get(lesson_no, 0) + 1
            continue
        for ascid, cid in zip(les["classes"], classes):
            if not cid:
                continue
            cand = by.get((cid, sub), [])
            if not cand:
                rep["no_load"][(cid, sub)] = rep["no_load"].get((cid, sub), 0) + 1
                continue
            chosen = [a for a in cand if a["teacher_id"] in teachers]
            if not chosen:
                if len(cand) == 1:
                    chosen = cand
                elif group_lesson or len(cand) > 1:
                    gnames = [norm((p["groups"].get(g) or {}).get("name", "")) for g in les["groups"]]
                    chosen = [a for a in cand if a["group_name"] and any(g and (g in norm(a["group_name"]) or norm(a["group_name"]) in g) for g in gnames)]
                    if not chosen:
                        rep["ambiguous"][(cid, sub)] = rep["ambiguous"].get((cid, sub), 0) + 1
                        continue
            for idx, a in enumerate(chosen):
                room = rooms[idx] if len(rooms) == len(chosen) and len(rooms) > 1 else (rooms[0] if rooms else None)
                for wd in card["days"]:
                    if not 1 <= wd <= 6:
                        rep["bad_day"] += 1
                        continue
                    for w in (1, 2):
                        if w == 2 and p["cycle"] == 1:
                            continue
                        if not card["weeks"][w - 1]:
                            continue
                        key = (cid, w, wd, lesson_no, a["id"])
                        if key in seen:
                            continue
                        seen.add(key)
                        items.append({"class_id": cid, "week": w, "weekday": wd, "lesson_no": lesson_no,
                                      "assignment_id": a["id"], "room_id": room})
    return items, rep
