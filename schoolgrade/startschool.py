# -*- coding: utf-8 -*-
"""Импорт данных из книг Excel комплекса «Старт ШКОЛА».

Поддерживаются:
  * главная книга «Старт ШКОЛА.xls» — паспорт учреждения, сотрудники,
    список классов с классными руководителями;
  * книги параллелей «Параллель_N.xls» — до пяти классов в каждой:
    списки учащихся, отметки за 1–4 четверти и итоговые (годовые).

Импорт выполняется в два шага: разбор файлов (parse_file) и применение к базе
(apply). Предварительный просмотр — это то же применение внутри транзакции,
которая затем откатывается, поэтому просмотр точно совпадает с результатом.
"""
import datetime
import difflib
import re

from . import crypto, xls
from . import grading as G

KINDS = [("q1", "1ч", "1 четверть"), ("q2", "2ч", "2 четверть"), ("q3", "3ч", "3 четверть"),
         ("q4", "4ч", "4 четверть"), ("year", "Ит", "годовая (итоговая в «Старт ШКОЛА»)")]
KIND_NAMES = dict((k, n) for k, _s, n in KINDS)

# названия «Старт ШКОЛА» -> названия SchoolGrade
SUBJECT_ALIASES = {
    "ист. бел. в кон. всем. ист.": "История Беларуси в контексте всемирной истории",
    "дп/мп": "Допризывная и медицинская подготовка",
    "физкультура": "Физическая культура и здоровье",
}

PASSPORT_MAP = {
    "Наименование": "Наименование", "Номер": "Номер",
    "Вышестоящий орган": "Подчинённость (вышестоящий орган)", "Подчинение": "Подчинённость (вышестоящий орган)",
    "Организационно-правовая форма": "Организационно-правовая форма", "Попечительский совет": "Попечительский совет",
    "Тип": "Тип", "Вид": "Вид", "Язык преподавания": "Язык обучения и воспитания", "Специализация": "Специализация",
    "Пачтовый адрес": "Почтовый адрес", "Почтовый адрес": "Почтовый адрес", "Директор": "Директор",
    "Председатель профкома": "Председатель профкома",
    "Председатель Попечительского совета": "Председатель попечительского совета",
    "Телефон директора": "Телефон директора", "телефон приёмной": "Телефон приёмной", "Телефон приёмной": "Телефон приёмной",
    "Факс": "Факс", "Электронная почта": "Электронная почта", "WWW-адрес": "Сайт",
    "Библиотечный фонд": "Библиотечный фонд", "Количество школьных учебников": "Количество учебных пособий",
    "Музей": "Музей", "Логопедический пункт": "Пункт коррекционно-педагогической помощи",
    "Колич. Раб. мест в каб. информатики": "Рабочих мест в кабинетах информатики",
    "Всего комп. с ОС Windows 95 и выше": "Всего компьютеров", "Доступ в Internet": "Доступ в Интернет (скорость)",
    "Автобусов на балансе": "Автобусов на балансе", "Автомобилей для учебных целей": "Автомобилей для учебных целей",
    "Зданий": "Зданий", "Учебных помещений": "Учебных помещений", "Учебных мест": "Ученических мест",
    "Количество мастерских": "Мастерских", "Площадь общая всех зданий": "Общая площадь зданий, м²",
    "Площадь физкультурного зала": "Площадь спортивного зала, м²",
    "Посадочный мест в столовой (буфете)": "Столовая (мест)",
    "К-во у-ся пользующихся горячим питанием": "Учащихся, получающих горячее питание",
    "Требуется капитальный ремонт": "Требуется капитальный ремонт",
    "Общая площадь территории школы (га)": "Площадь территории, га",
    "Площадь учебно-опытного зем.участка (га)": "Учебно-опытный участок, га",
    "Тир": "Тир", "Плащадка для изучения ПДД": "Площадка по изучению ПДД", "Спортплощадка": "Спортивная площадка",
    "Стадион": "Стадион", "Бассейн": "Бассейн",
    "Базовой школы": "Окончили II ступень (свидетельство об общем базовом)",
    "Средней школы": "Окончили III ступень (аттестат)", "С золотой медалью": "С золотой медалью",
    "С серебряной медалью": "С серебряной медалью", "Поступилов ССУЗ": "Поступили в учреждения ССО",
    "Поступило в ВУЗ": "Поступили в учреждения ВО",
}

_LAT2CYR = str.maketrans("ABCEHKMOPTXaceopxy", "АВСЕНКМОРТХасеорху")


class ImportError_(Exception):
    pass


# ------------------------------------------------------------------ helpers

def norm(s):
    s = str(s or "").replace("ё", "е").replace("Ё", "Е").replace("i", "і").replace("I", "І")
    s = re.sub(r"[–—−-]", "-", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def parse_class_name(s):
    """'5 "А"' -> (5, 'А'); '10' -> (10, '')."""
    m = re.match(r"\s*(\d{1,2})\s*[\"«'“]?\s*([A-Za-zА-Яа-яЁё]?)", str(s or ""))
    if not m:
        return None
    grade = int(m.group(1))
    letter = m.group(2).translate(_LAT2CYR).upper()
    if not 1 <= grade <= 12:
        return None
    return grade, letter


def class_title(grade, letter):
    return "%d «%s»" % (grade, letter) if letter else str(grade)


def split_fio(s):
    s = re.sub(r"([а-яё])([А-ЯЁ])", r"\1 \2", str(s).strip())   # «ТрегубоваТина»
    p = s.split()
    return (p[0] if p else ""), (p[1] if len(p) > 1 else ""), " ".join(p[2:])


def excel_date(v):
    if isinstance(v, float) and 1 < v < 80000:
        return (datetime.date(1899, 12, 30) + datetime.timedelta(days=int(v))).isoformat()
    m = re.match(r"\s*(\d{1,2})\.(\d{1,2})\.(\d{4})", str(v or ""))
    if m:
        try:
            return datetime.date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
        except ValueError:
            return ""
    return ""


def _mark(v):
    """Отметка из ячейки: 1..10 -> '7'; прочее (0, пусто, мусор) -> None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, float):
        if v.is_integer() and 1 <= v <= 10:
            return str(int(v))
        return None
    s = str(v or "").strip()
    if not s:
        return None
    try:
        n = G.normalize(s, "final")
    except ValueError:
        return None
    return n or None


# ------------------------------------------------------------------ разбор файлов

def parse_file(filename, data):
    """Разобрать одну книгу. Возвращает словарь с ключом kind: main | parallel."""
    try:
        book = xls.open_workbook(data)
    except xls.XlsError as e:
        raise ImportError_("%s: %s" % (filename, e))
    except Exception:
        raise ImportError_("%s: не удалось прочитать файл. Нужна книга Excel 97–2003 (.xls) из «Старт ШКОЛА»" % filename)
    names = book.sheet_names()
    if "ПаспортУчебногоЗаведения" in names:
        return _parse_main(filename, book)
    if "Класс1" in names and "1ч" in names:
        return _parse_parallel(filename, book)
    raise ImportError_("%s: это не книга «Старт ШКОЛА» (нет листов «ПаспортУчебногоЗаведения» или «Класс1»/«1ч»)" % filename)


def _parse_main(filename, book):
    out = {"kind": "main", "file": filename, "passport": {}, "classes": [], "staff": []}
    sh = book.sheet("ПаспортУчебногоЗаведения")
    for r in range(sh.nrows):
        label, value = sh.text(r, 2), sh.text(r, 3)
        if label and value:
            out["passport"][label] = value
    if book.has_sheet("ВсеКлассы"):
        sh = book.sheet("ВсеКлассы")
        for r in range(2, sh.nrows):
            p = parse_class_name(sh.text(r, 1))
            if p:
                out["classes"].append({"grade": p[0], "letter": p[1], "teacher": sh.text(r, 2)})
    if book.has_sheet("Сотрудники"):
        sh = book.sheet("Сотрудники")
        for r in range(6, sh.nrows):
            last = sh.text(r, 1)
            if not last:
                continue
            out["staff"].append({
                "last_name": last, "first_name": sh.text(r, 2), "middle_name": sh.text(r, 3),
                "education": sh.text(r, 4), "category": sh.text(r, 5), "pc_skill": sh.text(r, 6),
                "degree": sh.text(r, 8), "title": sh.text(r, 9),
                "gender": {"мужской": "м", "женский": "ж"}.get(sh.text(r, 10).lower(), ""),
                "birth_date": excel_date(sh.cell(r, 11)), "phone": sh.text(r, 29) or sh.text(r, 28),
                "position": sh.text(r, 31), "hire_date": excel_date(sh.cell(r, 33)), "note": sh.text(r, 51),
            })
    return out


def _parse_parallel(filename, book):
    out = {"kind": "parallel", "file": filename, "classes": []}
    names = book.sheet_names()
    for n in range(1, 6):
        suf = "" if n == 1 else str(n)
        if ("1ч" + suf) not in names:
            continue
        first = book.sheet("1ч" + suf)
        p = parse_class_name(first.text(3, 3))
        if not p:
            continue
        cls = {"grade": p[0], "letter": p[1], "teacher": first.text(3, 1), "students": [],
               "subject_teachers": {}}
        # учителя предметов — лист «КлассN»
        if ("Класс%d" % n) in names:
            ks = book.sheet("Класс%d" % n)
            for r in range(4, min(ks.nrows, 60)):
                subj, t = ks.text(r, 1), ks.text(r, 2)
                if subj and t:
                    cls["subject_teachers"][subj] = t
        studs = {}      # ключ — имя (+ номер повтора)
        order = []
        for kind, sheet_prefix, _nm in KINDS:
            sn = sheet_prefix + suf
            if sn not in names:
                continue
            sh = book.sheet(sn)
            subjects = []
            for c in range(5, sh.ncols):
                t = sh.text(1, c)
                if t.lower().startswith("количество оценок"):
                    break
                if t:
                    subjects.append((c, t))
            seen = {}
            for r in range(6, sh.nrows):
                name = sh.text(r, 1)
                # строка учащегося — с порядковым номером в столбце A;
                # итоговые строки под списком («Оценки», «Всего аттестованно») без номера
                if not isinstance(sh.cell(r, 0), float) or not name or re.fullmatch(r"[\d.\s]+", name):
                    continue
                seen[name] = seen.get(name, 0) + 1
                key = name if seen[name] == 1 else "%s#%d" % (name, seen[name])
                if key not in studs:
                    studs[key] = {"name": name, "marks": {}}
                    order.append(key)
                for c, subj in subjects:
                    m = _mark(sh.cell(r, c))
                    if m:
                        studs[key]["marks"].setdefault(kind, {})[subj] = m
        cls["students"] = [studs[k] for k in order]
        if cls["students"] or cls["teacher"]:
            out["classes"].append(cls)
    return out


def file_summary(p):
    if p["kind"] == "main":
        return {"file": p["file"], "kind": "main",
                "text": "Главная книга: паспорт учреждения, %d классов в списке, %d сотрудников"
                        % (len(p["classes"]), len(p["staff"]))}
    parts = []
    for c in p["classes"]:
        nm = sum(len(v) for s in c["students"] for v in s["marks"].values())
        parts.append("%s — %d уч., %d отм." % (class_title(c["grade"], c["letter"]), len(c["students"]), nm))
    return {"file": p["file"], "kind": "parallel",
            "text": "Параллель: " + ("; ".join(parts) if parts else "классы не заполнены")}


# ------------------------------------------------------------------ применение

class _Rollback(Exception):
    pass


def apply(db, parsed, opts, user=None, dry_run=True):
    """Применить разобранные книги к базе текущего учебного года.

    opts: kinds — какие периоды переносить (q1..q4, year);
          overwrite — заменять уже выставленные отметки, если они отличаются;
          passport — заполнить пустые поля паспорта; staff — добавить сотрудников;
          create_teachers — добавлять классных руководителей/учителей,
          которых нет среди сотрудников (по фамилии и инициалам).
    Возвращает отчёт; при dry_run изменения откатываются."""
    kinds = [k for k in (opts.get("kinds") or [k for k, _s, _n in KINDS]) if k in KIND_NAMES]
    overwrite = bool(opts.get("overwrite"))
    rep = {"log": [], "warnings": [], "classes": [],
           "summary": {"classes_new": 0, "classes_existing": 0, "students_new": 0, "students_matched": 0,
                       "students_fuzzy": 0, "staff_new": 0, "subjects_new": 0, "assignments_new": 0,
                       "groups": 0, "passport_fields": 0},
           "marks": dict((k, {"new": 0, "same": 0, "diff_kept": 0, "replaced": 0, "locked": 0}) for k in kinds)}
    year = db.one("SELECT * FROM years WHERE is_current=1")
    if not year:
        raise ImportError_("Не создан учебный год. Создайте его в разделе «Учебный год» и повторите импорт.")
    if (year.get("scheme") or "quarters") != "quarters":
        raise ImportError_("В «Старт ШКОЛА» отметки ведутся по четвертям, а текущий учебный год разделён на полугодия.")
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    uid = user.get("id") if user else None
    locked = set(r["kind"] for r in db.q("SELECT kind FROM periods WHERE year_id=? AND locked=1", (year["id"],)))

    with db.lock:
        db.conn.execute("BEGIN")
        try:
            _apply(db, parsed, opts, kinds, overwrite, rep, year, now, uid, locked)
            if dry_run:
                raise _Rollback()
            db.conn.execute("COMMIT")
        except _Rollback:
            db.conn.execute("ROLLBACK")
        except Exception:
            db.conn.execute("ROLLBACK")
            raise
    return rep


def _apply(db, parsed, opts, kinds, overwrite, rep, year, now, uid, locked):
    S = rep["summary"]
    log = rep["log"]
    mains = [p for p in parsed if p["kind"] == "main"]
    pars = [p for p in parsed if p["kind"] == "parallel"]

    # --- паспорт
    if opts.get("passport", True) and mains:
        school = dict(db.get_setting("school", {}) or {})
        for label, value in mains[0]["passport"].items():
            key = PASSPORT_MAP.get(label)
            if key and value and not (school.get(key) or "").strip():
                school[key] = value
                S["passport_fields"] += 1
        if S["passport_fields"]:
            db.set_setting("school", school)
            log.append("Паспорт учреждения: заполнено пустых полей — %d" % S["passport_fields"])

    # --- сотрудники
    staff = [crypto.dec_row("staff", r) for r in db.q("SELECT * FROM staff")]

    def staff_key(last, first, middle):
        return (norm(last), norm(first), norm(middle))

    by_full = dict((staff_key(s["last_name"], s["first_name"], s["middle_name"]), s["id"]) for s in staff)
    if opts.get("staff", True) and mains:
        for st in mains[0]["staff"]:
            k = staff_key(st["last_name"], st["first_name"], st["middle_name"])
            if k in by_full:
                continue
            row = dict((f, st.get(f, "")) for f in ("last_name", "first_name", "middle_name", "education", "category",
                                                     "pc_skill", "degree", "title", "gender", "birth_date", "phone",
                                                     "position", "hire_date", "note"))
            row = crypto.enc_row("staff", row)
            sid = db.ex("INSERT INTO staff(%s) VALUES(%s)" % (",".join(row), ",".join("?" * len(row))), list(row.values()))
            by_full[k] = sid
            staff.append({"id": sid, "last_name": st["last_name"], "first_name": st["first_name"],
                          "middle_name": st["middle_name"]})
            S["staff_new"] += 1
            log.append("Сотрудник добавлен: %s %s %s" % (st["last_name"], st["first_name"], st["middle_name"]))

    created_abbr = {}

    def teacher_id(abbr):
        """«Орлов Е.Н.» -> id сотрудника (по фамилии и инициалам)."""
        abbr = (abbr or "").strip()
        if not abbr:
            return None
        m = re.match(r"([^\s.]+(?:-[^\s.]+)?)\s*([А-ЯЁA-Z])?\.?\s*([А-ЯЁA-Z])?\.?", abbr)
        if not m:
            return None
        last, i1, i2 = norm(m.group(1)), norm(m.group(2) or ""), norm(m.group(3) or "")
        cands = [s for s in staff if norm(s["last_name"]) == last
                 and (not i1 or norm(s["first_name"])[:1] == i1)
                 and (not i2 or not s.get("middle_name") or norm(s["middle_name"])[:1] == i2)]
        if len(cands) >= 1:
            return cands[0]["id"]
        if not opts.get("create_teachers", True):
            return None
        if abbr in created_abbr:
            return created_abbr[abbr]
        row = crypto.enc_row("staff", {"last_name": m.group(1), "first_name": (m.group(2) or "") + ("." if m.group(2) else ""),
                                       "middle_name": (m.group(3) or "") + ("." if m.group(3) else ""),
                                       "position": "учитель",
                                       "note": "Добавлен при импорте из «Старт ШКОЛА» — уточните имя и отчество"})
        sid = db.ex("INSERT INTO staff(%s) VALUES(%s)" % (",".join(row), ",".join("?" * len(row))), list(row.values()))
        staff.append({"id": sid, "last_name": m.group(1), "first_name": m.group(2) or "", "middle_name": m.group(3) or ""})
        created_abbr[abbr] = sid
        S["staff_new"] += 1
        log.append("Сотрудник добавлен по сокращению «%s» — уточните имя и отчество в разделе «Сотрудники»" % abbr)
        return sid

    # --- предметы
    subjects = db.q("SELECT id, name, short FROM subjects")
    subj_index = {}
    for s in subjects:
        subj_index.setdefault(norm(s["name"]), s["id"])
        if s.get("short"):
            subj_index.setdefault(norm(s["short"]), s["id"])

    def subject_id(name):
        k = norm(name)
        if k in subj_index:
            return subj_index[k]
        alias = SUBJECT_ALIASES.get(k)
        if alias and norm(alias) in subj_index:
            subj_index[k] = subj_index[norm(alias)]
            return subj_index[k]
        real = alias or name.strip()
        from .db import SUBJECT_SHORT
        sort = (db.val("SELECT MAX(sort) FROM subjects") or 0) + 10
        sid = db.ex("INSERT INTO subjects(name,short,sort,category) VALUES(?,?,?,'subject')",
                    (real, SUBJECT_SHORT.get(real, ""), sort))
        subj_index[k] = sid
        subj_index[norm(real)] = sid
        S["subjects_new"] += 1
        log.append("Предмет добавлен: %s" % real)
        return sid

    # --- классы из главной книги (без учащихся)
    want = {}
    if mains:
        for c in mains[0]["classes"]:
            want[(c["grade"], c["letter"])] = {"grade": c["grade"], "letter": c["letter"], "teacher": c["teacher"],
                                               "students": [], "subject_teachers": {}}
    for p in pars:
        for c in p["classes"]:
            key = (c["grade"], c["letter"])
            base = want.get(key, {})
            c = dict(c)
            if not c.get("teacher") and base.get("teacher"):
                c["teacher"] = base["teacher"]
            want[key] = c
    if not want:
        raise ImportError_("В выбранных файлах нет ни одного класса")

    start = (year.get("start_date") or "")[:10] or "%s-09-01" % year["name"][:4]
    for key in sorted(want):
        c = want[key]
        title = class_title(c["grade"], c["letter"])
        crep = {"name": title, "status": "", "teacher": c.get("teacher") or "", "students": len(c["students"]),
                "new_students": 0, "fuzzy": [], "missing": 0, "marks": {}}
        row = db.one("SELECT * FROM classes WHERE year_id=? AND grade=? AND letter=?", (year["id"], c["grade"], c["letter"]))
        if row:
            cid = row["id"]
            crep["status"] = "есть"
            S["classes_existing"] += 1
            if not row.get("teacher_id"):
                tid = teacher_id(c.get("teacher"))
                if tid:
                    db.ex("UPDATE classes SET teacher_id=? WHERE id=?", (tid, cid))
        else:
            tid = teacher_id(c.get("teacher"))
            cid = db.ex("INSERT INTO classes(year_id,grade,letter,teacher_id) VALUES(?,?,?,?)",
                        (year["id"], c["grade"], c["letter"], tid))
            crep["status"] = "новый"
            S["classes_new"] += 1
            log.append("Класс создан: %s" % title)
        if not c["students"]:
            rep["classes"].append(crep)
            continue

        # учащиеся
        cur = db.q("SELECT s.id, s.last_name, s.first_name FROM students s JOIN class_students cs ON cs.student_id=s.id "
                   "WHERE cs.class_id=? AND (cs.date_out IS NULL OR cs.date_out='')", (cid,))
        pool = dict((s["id"], norm(s["last_name"] + " " + s["first_name"])) for s in cur)
        used, mapping = set(), {}
        for i, st in enumerate(c["students"]):
            nm = norm(" ".join(split_fio(st["name"])[:2]))
            for sid, pn in pool.items():
                if sid not in used and pn == nm:
                    mapping[i] = sid
                    used.add(sid)
                    S["students_matched"] += 1
                    break
        for i, st in enumerate(c["students"]):
            if i in mapping:
                continue
            nm = norm(" ".join(split_fio(st["name"])[:2]))
            cand = sorted(((difflib.SequenceMatcher(None, nm, pn).ratio(), sid) for sid, pn in pool.items()
                           if sid not in used), reverse=True)
            if cand and cand[0][0] >= 0.8 and (len(cand) == 1 or cand[0][0] - cand[1][0] > 0.1):
                sid = cand[0][1]
                mapping[i] = sid
                used.add(sid)
                S["students_fuzzy"] += 1
                r = next(s for s in cur if s["id"] == sid)
                crep["fuzzy"].append("«%s» → «%s %s»" % (st["name"], r["last_name"], r["first_name"]))
                log.append("%s: «%s» (Старт ШКОЛА) сопоставлен с «%s %s» (SchoolGrade)"
                           % (title, st["name"], r["last_name"], r["first_name"]))
        for i, st in enumerate(c["students"]):
            if i in mapping:
                continue
            ln, fn, mn = split_fio(st["name"])
            sid = db.ex("INSERT INTO students(last_name,first_name,middle_name) VALUES(?,?,?)", (ln, fn, mn))
            db.ex("INSERT INTO class_students(class_id,student_id,date_in) VALUES(?,?,?)", (cid, sid, start))
            mapping[i] = sid
            S["students_new"] += 1
            crep["new_students"] += 1
        crep["missing"] = len([s for s in pool if s not in used])
        if crep["missing"]:
            rep["warnings"].append("%s: в SchoolGrade есть учащиеся, которых нет в файле «Старт ШКОЛА» (%d) — "
                                   "проверьте, не выбыли ли они" % (title, crep["missing"]))

        # отметки: предмет -> [(учащийся, вид, отметка)]
        per_subj = {}
        for i, st in enumerate(c["students"]):
            for kind, marks in st["marks"].items():
                for subj, v in marks.items():
                    per_subj.setdefault(subj, []).append((mapping[i], kind, v))
        lang_subjs = [s for s in per_subj if "язык" in s.lower() and not s.lower().startswith(("бел", "рус"))]
        nstud = len(c["students"])
        for subj, items in per_subj.items():
            sbid = subject_id(subj)
            a = db.one("SELECT * FROM assignments WHERE class_id=? AND subject_id=? ORDER BY group_name LIMIT 1", (cid, sbid))
            if a:
                aid = a["id"]
            else:
                aid = db.ex("INSERT INTO assignments(class_id,subject_id,teacher_id,group_name,hours,eval_type) "
                            "VALUES(?,?,?,?,?,'points')",
                            (cid, sbid, teacher_id(c["subject_teachers"].get(subj)), "", 1))
                S["assignments_new"] += 1
            students_with = sorted(set(sid for sid, _k, _v in items))
            # группа по иностранному языку, если в классе изучают несколько языков
            if subj in lang_subjs and len(lang_subjs) > 1 and len(students_with) < nstud and not a:
                for sid in students_with:
                    db.ex("INSERT OR IGNORE INTO assignment_students(assignment_id,student_id) VALUES(?,?)", (aid, sid))
                S["groups"] += 1
                log.append("%s: «%s» — группа из %d уч. (в классе изучают несколько иностранных языков)"
                           % (title, subj, len(students_with)))
            for sid, kind, v in items:
                if kind not in rep["marks"]:
                    continue
                mk = rep["marks"][kind]
                crep["marks"][kind] = crep["marks"].get(kind, 0) + 1
                if kind in locked:
                    mk["locked"] += 1
                    continue
                old = db.val("SELECT value FROM final_marks WHERE assignment_id=? AND student_id=? AND kind=?",
                             (aid, sid, kind))
                if old is None:
                    db.ex("INSERT INTO final_marks(assignment_id,student_id,kind,value,excused,updated_by,updated_at) "
                          "VALUES(?,?,?,?,0,?,?)", (aid, sid, kind, v, uid, now))
                    mk["new"] += 1
                elif old == v:
                    mk["same"] += 1
                elif overwrite:
                    db.ex("UPDATE final_marks SET value=?, updated_by=?, updated_at=? WHERE assignment_id=? AND student_id=? AND kind=?",
                          (v, uid, now, aid, sid, kind))
                    mk["replaced"] += 1
                else:
                    mk["diff_kept"] += 1
        rep["classes"].append(crep)

    for k in kinds:
        if k in locked and rep["marks"][k]["locked"]:
            rep["warnings"].append("Период «%s» закрыт от изменений — отметки за него не перенесены (%d)"
                                   % (KIND_NAMES[k], rep["marks"][k]["locked"]))
        if rep["marks"][k]["diff_kept"]:
            rep["warnings"].append("%s: %d отметок в SchoolGrade отличаются от «Старт ШКОЛА» и оставлены без изменений "
                                   "(включите «Заменять отличающиеся отметки», чтобы заменить)"
                                   % (KIND_NAMES[k], rep["marks"][k]["diff_kept"]))
    if S["assignments_new"]:
        rep["warnings"].append("Для %d новых предметов в нагрузке указано 1 ч в неделю и учитель не всегда известен — "
                               "проверьте вкладку «Нагрузка» в классах" % S["assignments_new"])
