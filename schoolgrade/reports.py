# -*- coding: utf-8 -*-
"""Отчёты и аналитика. Каждый отчёт возвращает dict:
{title, subtitle, headers, rows, widths, footer, name} — одинаково для показа
в интерфейсе, печати и выгрузки в Excel."""
from . import grading as G
from . import crypto

LEVEL_COLS = [("high", "9–10"), ("suf", "7–8"), ("mid", "5–6"), ("sat", "3–4"), ("low", "1–2")]


def fio(s, full=False):
    if full:
        return " ".join(x for x in [s.get("last_name"), s.get("first_name"), s.get("middle_name")] if x)
    return " ".join(x for x in [s.get("last_name"), s.get("first_name")] if x)


def staff_short(s):
    if not s or not s.get("last_name"):
        return ""
    ini = "".join((x[0] + ".") for x in [s.get("first_name"), s.get("middle_name")] if x)
    return "{} {}".format(s["last_name"], ini).strip()


def class_name(c):
    return "{}{}".format(c["grade"], (" «" + c["letter"] + "»") if c.get("letter") else "")


def fmt(x, nd=1):
    if x is None:
        return ""
    if isinstance(x, float):
        s = ("{:." + str(nd) + "f}").format(x)
        return s.replace(".", ",")
    return x


def rules(db):
    r = dict(G.DEFAULT_RULES)
    r.update(db.get_setting("rules", {}) or {})
    return r


def school_name(db):
    p = db.get_setting("school", {}) or {}
    return p.get("Наименование") or "Учреждение образования"


# ------------------------------------------------------------- выборки
def class_students(db, class_id, include_left=False):
    sql = ("SELECT s.*, cs.date_in, cs.date_out, cs.year_result FROM class_students cs "
           "JOIN students s ON s.id=cs.student_id WHERE cs.class_id=? ")
    if not include_left:
        sql += "AND (cs.date_out IS NULL OR cs.date_out='') "
    sql += "ORDER BY s.last_name COLLATE NOCASE, s.first_name COLLATE NOCASE"
    return [crypto.dec_row("students", r) for r in db.q(sql, (class_id,))]


def assignment_students(db, a):
    sub = db.q("SELECT student_id FROM assignment_students WHERE assignment_id=?", (a["id"],))
    studs = class_students(db, a["class_id"])
    if sub:
        ids = set(r["student_id"] for r in sub)
        studs = [s for s in studs if s["id"] in ids]
    return studs


def class_assignments(db, class_id, graded_only=False):
    sql = ("SELECT a.*, sb.name AS subject, sb.sort, sb.short, sb.category, st.last_name AS t_last, st.first_name AS t_first, "
           "st.middle_name AS t_middle FROM assignments a JOIN subjects sb ON sb.id=a.subject_id "
           "LEFT JOIN staff st ON st.id=a.teacher_id WHERE a.class_id=? ")
    if graded_only:
        sql += "AND sb.category='subject' "
    sql += "ORDER BY sb.sort, sb.name, a.group_name"
    return db.q(sql, (class_id,))


def teacher_of(a):
    return staff_short({"last_name": a.get("t_last"), "first_name": a.get("t_first"), "middle_name": a.get("t_middle")})


def finals_map(db, class_id, kind):
    """{(student_id, subject_id): value} — по всем группам предмета."""
    rows = db.q("SELECT f.student_id, a.subject_id, f.value, f.excused FROM final_marks f "
                "JOIN assignments a ON a.id=f.assignment_id WHERE a.class_id=? AND f.kind=?", (class_id, kind))
    return {(r["student_id"], r["subject_id"]): r["value"] for r in rows}


def subjects_of_class(db, class_id):
    """Учебные предметы класса (без факультативов и объединений по интересам)."""
    seen, out = set(), []
    for a in class_assignments(db, class_id, graded_only=True):
        if a["subject_id"] not in seen:
            seen.add(a["subject_id"])
            out.append(a)
    return out


def kind_title(db, kind):
    return G.kind_name(kind)


def year_of_class(db, class_id):
    return db.one("SELECT y.* FROM years y JOIN classes c ON c.year_id=y.id WHERE c.id=?", (class_id,))


def get_class(db, class_id):
    c = db.one("SELECT c.*, st.last_name, st.first_name, st.middle_name FROM classes c "
               "LEFT JOIN staff st ON st.id=c.teacher_id WHERE c.id=?", (class_id,))
    if c:
        c["teacher"] = staff_short(c)
    return c


# ------------------------------------------------------------- отчёты
def class_sheet(db, class_id, kind):
    """Сводная ведомость: учащиеся × предметы (+ поведение, показатели)."""
    c = get_class(db, class_id)
    y = year_of_class(db, class_id)
    subs = subjects_of_class(db, class_id)
    studs = class_students(db, class_id)
    fm = finals_map(db, class_id, kind)
    beh = {r["student_id"]: r["value"] for r in db.q("SELECT student_id,value FROM behavior WHERE class_id=? AND kind=?", (class_id, kind))}
    rl = rules(db)
    headers = ["№", "Фамилия, имя"] + [s["short"] or s["subject"] for s in subs] + ["Поведение", "Ср. балл", "КЗ %", "СОУ %", "Уровень"]
    rows = []
    for i, s in enumerate(studs):
        vals = [fm.get((s["id"], sb["subject_id"]), "") for sb in subs]
        st = G.stats(vals, rl)
        rows.append([i + 1, fio(s)] + vals + [beh.get(s["id"], ""), fmt(st["avg"], 2), fmt(st["kz"]), fmt(st["sou"]),
                                             G.level_name(G.student_level(vals))])
    # итоговая строка по предметам
    if studs:
        avg_row = ["", "Средний балл"]
        for sb in subs:
            st = G.stats([fm.get((s["id"], sb["subject_id"]), "") for s in studs], rl)
            avg_row.append(fmt(st["avg"], 2))
        avg_row += ["", "", "", "", ""]
        rows.append(avg_row)
    return {
        "name": "Ведомость " + class_name(c),
        "title": "Сводная ведомость результатов учебной деятельности: {} класс, {}".format(class_name(c), kind_title(db, kind).lower()),
        "subtitle": "{}\n{} учебный год. Классный руководитель: {}".format(school_name(db), y["name"] if y else "", c.get("teacher") or "—"),
        "headers": headers, "rows": rows,
        "widths": [4, 26] + [7] * len(subs) + [10, 8, 7, 7, 16],
        "vertical_from": 2, "vertical_to": 2 + len(subs),
    }


def _level_cells(st):
    out = []
    for code, _ in LEVEL_COLS:
        out += [st["levels"][code], fmt(st["levels_pct"][code])]
    return out


def _level_headers():
    h = []
    for code, lbl in LEVEL_COLS:
        h += [lbl, "%"]
    return h


def class_subjects(db, class_id, kind):
    """Анализ по предметам в классе (как лист «Класс» в «Старт ШКОЛА»)."""
    c = get_class(db, class_id)
    y = year_of_class(db, class_id)
    rl = rules(db)
    studs = class_students(db, class_id)
    fm = finals_map(db, class_id, kind)
    headers = ["№", "Предмет", "Учитель", "Уч-ся", "Аттест.", "н/а", "осв.", "зач.", "незач."] + _level_headers() + ["Ср. балл", "КЗ %", "СОУ %"]
    rows = []
    teachers = {}
    for a in class_assignments(db, class_id):
        teachers.setdefault(a["subject_id"], [])
        t = teacher_of(a)
        if t and t not in teachers[a["subject_id"]]:
            teachers[a["subject_id"]].append(t)
    all_vals = []
    for i, sb in enumerate(subjects_of_class(db, class_id)):
        vals = [fm.get((s["id"], sb["subject_id"]), "") for s in studs]
        all_vals += vals
        st = G.stats(vals, rl)
        o = st["other"]
        rows.append([i + 1, sb["subject"], ", ".join(teachers.get(sb["subject_id"], [])), len(studs), st["count"],
                     o[G.NOT_ATT], o[G.EXEMPT], o[G.PASS], o[G.FAIL]] + _level_cells(st) + [fmt(st["avg"], 2), fmt(st["kz"]), fmt(st["sou"])])
    st = G.stats(all_vals, rl)
    o = st["other"]
    rows.append(["", "ИТОГО по классу", "", len(studs), st["count"], o[G.NOT_ATT], o[G.EXEMPT], o[G.PASS], o[G.FAIL]]
                + _level_cells(st) + [fmt(st["avg"], 2), fmt(st["kz"]), fmt(st["sou"])])
    return {
        "name": "Предметы " + class_name(c),
        "title": "Результаты учебной деятельности по предметам: {} класс, {}".format(class_name(c), kind_title(db, kind).lower()),
        "subtitle": "{}\n{} учебный год. Классный руководитель: {}".format(school_name(db), y["name"] if y else "", c.get("teacher") or "—"),
        "headers": headers, "rows": rows,
        "widths": [4, 28, 20, 6, 7, 5, 5, 5, 6] + [5, 6] * 5 + [8, 7, 7],
        "footer": ["КЗ — качество знаний (доля отметок {}–10 баллов); СОУ — степень обученности учащихся.".format(rl["kz_min_point"])],
    }


def class_students_report(db, class_id, kind):
    c = get_class(db, class_id)
    y = year_of_class(db, class_id)
    rl = rules(db)
    studs = class_students(db, class_id)
    fm = finals_map(db, class_id, kind)
    subs = subjects_of_class(db, class_id)
    headers = ["№", "Фамилия, имя", "Отметок", "Ср. балл", "КЗ %", "СОУ %", "Уровень", "н/а", "Мин.", "Макс."]
    rows = []
    for i, s in enumerate(studs):
        vals = [fm.get((s["id"], sb["subject_id"]), "") for sb in subs]
        st = G.stats(vals, rl)
        pts = [int(v) for v in vals if G.is_point(v)]
        rows.append([i + 1, fio(s), st["count"], fmt(st["avg"], 2), fmt(st["kz"]), fmt(st["sou"]),
                     G.level_name(G.student_level(vals)), st["other"][G.NOT_ATT],
                     min(pts) if pts else "", max(pts) if pts else ""])
    rows.sort(key=lambda r: -(float(str(r[3]).replace(",", ".")) if r[3] != "" else -1))
    for i, r in enumerate(rows):
        r[0] = i + 1
    return {
        "name": "Учащиеся " + class_name(c),
        "title": "Рейтинг учащихся: {} класс, {}".format(class_name(c), kind_title(db, kind).lower()),
        "subtitle": "{}\n{} учебный год".format(school_name(db), y["name"] if y else ""),
        "headers": headers, "rows": rows, "widths": [4, 30, 8, 8, 7, 7, 18, 5, 5, 5],
        "footer": ["Уровень учащегося определяется по самой низкой отметке."],
    }


def school_summary(db, year_id, kind):
    """Успеваемость по классам и параллелям школы."""
    rl = rules(db)
    y = db.one("SELECT * FROM years WHERE id=?", (year_id,))
    classes = db.q("SELECT * FROM classes WHERE year_id=? ORDER BY grade, letter", (year_id,))
    headers = ["Класс", "Кл. руководитель", "Уч-ся", "Отметок"] + _level_headers() + ["н/а", "Ср. балл", "КЗ %", "СОУ %", "Уч-ся на 9–10", "Уч-ся с н/а или 1–2"]
    rows = []
    total_vals, total_n, total_ex, total_bad = [], 0, 0, 0
    by_grade = {}
    for c in classes:
        cc = get_class(db, c["id"])
        studs = class_students(db, c["id"])
        fm = finals_map(db, c["id"], kind)
        vals = list(v for (sid, _), v in fm.items() if any(s["id"] == sid for s in studs))
        st = G.stats(vals, rl)
        ex = bad = 0
        for s in studs:
            sv = [v for (sid, _), v in fm.items() if sid == s["id"]]
            lv = G.student_level(sv)
            if lv == "high" and all(G.is_point(v) or v in (G.PASS, G.EXEMPT, G.NOT_STUDIED) for v in sv):
                ex += 1
            if G.NOT_ATT in sv or lv == "low":
                bad += 1
        rows.append([class_name(c), cc.get("teacher") or "", len(studs), st["count"]] + _level_cells(st)
                    + [st["other"][G.NOT_ATT], fmt(st["avg"], 2), fmt(st["kz"]), fmt(st["sou"]), ex, bad])
        g = by_grade.setdefault(c["grade"], {"vals": [], "n": 0, "ex": 0, "bad": 0})
        g["vals"] += vals; g["n"] += len(studs); g["ex"] += ex; g["bad"] += bad
        total_vals += vals; total_n += len(studs); total_ex += ex; total_bad += bad
    prow = []
    for grade in sorted(by_grade):
        g = by_grade[grade]
        st = G.stats(g["vals"], rl)
        prow.append(["{} классы".format(grade), "", g["n"], st["count"]] + _level_cells(st)
                    + [st["other"][G.NOT_ATT], fmt(st["avg"], 2), fmt(st["kz"]), fmt(st["sou"]), g["ex"], g["bad"]])
    st = G.stats(total_vals, rl)
    total = ["ИТОГО по учреждению", "", total_n, st["count"]] + _level_cells(st) + [st["other"][G.NOT_ATT], fmt(st["avg"], 2), fmt(st["kz"]), fmt(st["sou"]), total_ex, total_bad]
    return {
        "name": "Школа",
        "title": "Результаты учебной деятельности по учреждению образования, {}".format(kind_title(db, kind).lower()),
        "subtitle": "{}\n{} учебный год".format(school_name(db), y["name"] if y else ""),
        "headers": headers, "rows": rows + ([[""] * len(headers)] if prow else []) + prow + [total],
        "widths": [14, 22, 6, 8] + [5, 6] * 5 + [5, 8, 7, 7, 9, 10],
    }


def parallel_subjects(db, year_id, grade, kind):
    """Аналитика по предметам в параллели (как «Предметы-1ч» в «Старт ШКОЛА»)."""
    rl = rules(db)
    y = db.one("SELECT * FROM years WHERE id=?", (year_id,))
    classes = db.q("SELECT * FROM classes WHERE year_id=? AND grade=? ORDER BY letter", (year_id, grade))
    data = {}
    order = {}
    for c in classes:
        studs = set(s["id"] for s in class_students(db, c["id"]))
        fm = finals_map(db, c["id"], kind)
        for sb in subjects_of_class(db, c["id"]):
            order[sb["subject_id"]] = (sb["sort"], sb["subject"])
            d = data.setdefault(sb["subject_id"], [])
            d += [v for (sid, subj), v in fm.items() if subj == sb["subject_id"] and sid in studs]
    headers = ["Предмет", "Отметок"] + _level_headers() + ["н/а"] + [str(p) for p in range(10, 0, -1)] + ["Ср. балл", "КЗ %", "СОУ %"]
    rows = []
    for sid in sorted(order, key=lambda k: order[k]):
        st = G.stats(data[sid], rl)
        rows.append([order[sid][1], st["count"]] + _level_cells(st) + [st["other"][G.NOT_ATT]]
                    + [st["by_point"][p] for p in range(10, 0, -1)] + [fmt(st["avg"], 2), fmt(st["kz"]), fmt(st["sou"])])
    return {
        "name": "Параллель {}".format(grade),
        "title": "Аналитическая информация по предметам: {}-е классы, {}".format(grade, kind_title(db, kind).lower()),
        "subtitle": "{}\n{} учебный год, классов в параллели: {}".format(school_name(db), y["name"] if y else "", len(classes)),
        "headers": headers, "rows": rows,
        "widths": [28, 8] + [5, 6] * 5 + [5] + [4] * 10 + [8, 7, 7],
    }


ASC_LEVELS = [("low", "Низкий"), ("sat", "Удовлетворит."), ("mid", "Средний"), ("suf", "Достаточный"), ("high", "Высокий")]


def school_subjects(db, year_id, kind):
    """Аналитическая информация по предметам в целом по учреждению
    (как «Аналитическая информация по предметам» в «Старт ШКОЛА»)."""
    rl = rules(db)
    y = db.one("SELECT * FROM years WHERE id=?", (year_id,))
    classes = db.q("SELECT * FROM classes WHERE year_id=? ORDER BY grade, letter", (year_id,))
    data, order, nstud = {}, {}, {}
    for c in classes:
        studs = set(s["id"] for s in class_students(db, c["id"]))
        fm = finals_map(db, c["id"], kind)
        for sb in subjects_of_class(db, c["id"]):
            order[sb["subject_id"]] = (sb["sort"], sb["subject"])
            data.setdefault(sb["subject_id"], [])
            vals = [v for (sid, subj), v in fm.items() if subj == sb["subject_id"] and sid in studs]
            data[sb["subject_id"]] += vals
            nstud[sb["subject_id"]] = nstud.get(sb["subject_id"], 0) + len(studs)
    headers = ["Предмет", "Уч-ся"]
    for _, lbl in ASC_LEVELS:
        headers += [lbl, "%"]
    headers += ["Ср. балл", "СОУ %", "КЗ %"]
    rows = []
    tot = []
    for sid in sorted(order, key=lambda k: order[k]):
        st = G.stats(data[sid], rl)
        tot += data[sid]
        row = [order[sid][1], nstud.get(sid, 0)]
        for code, _ in ASC_LEVELS:
            row += [st["levels"][code], fmt(st["levels_pct"][code])]
        rows.append(row + [fmt(st["avg"], 2), fmt(st["sou"]), fmt(st["kz"])])
    st = G.stats(tot, rl)
    trow = ["ИТОГО по школе", ""]
    for code, _ in ASC_LEVELS:
        trow += [st["levels"][code], fmt(st["levels_pct"][code])]
    rows.append(trow + [fmt(st["avg"], 2), fmt(st["sou"]), fmt(st["kz"])])
    return {
        "name": "Предметы (школа)",
        "title": "Аналитическая информация по предметам: {}".format(kind_title(db, kind).lower()),
        "subtitle": "{}\n{} учебный год".format(school_name(db), y["name"] if y else ""),
        "headers": headers, "rows": rows,
        "widths": [28, 6] + [6, 6] * 5 + [8, 7, 7],
    }


LEVELS_PAR_HEADERS = (["Параллель", "К-во учащихся"]
                      + sum([[lbl + ", к-во", "%"] for _, lbl in ASC_LEVELS], [])
                      + ["Средний балл", "СОУ", "Качество знаний"])


def _pct(n, total):
    return fmt(100.0 * n / total, 2) if (n and total) else ""


def _parallel_levels_rows(db, classes, grades, kind, rl):
    """Строки «параллель × уровни» за один период (как в «Старт ШКОЛА»).
    Учащийся относится к уровню по самой низкой отметке; учитываются
    учащиеся, у которых есть балльные отметки за период."""
    per = {g: {"n": 0, "lv": dict((c, 0) for c, _ in ASC_LEVELS), "vals": []} for g in grades}
    for c in classes:
        if c["grade"] not in per:
            per[c["grade"]] = {"n": 0, "lv": dict((x, 0) for x, _ in ASC_LEVELS), "vals": []}
        p = per[c["grade"]]
        subj = set(sb["subject_id"] for sb in subjects_of_class(db, c["id"]))
        fm = finals_map(db, c["id"], kind)
        for s in class_students(db, c["id"]):
            vals = [v for (sid, sb), v in fm.items() if sid == s["id"] and sb in subj]
            lv = G.student_level(vals)
            if lv:
                p["n"] += 1
                p["lv"][lv] += 1
            p["vals"] += vals
    rows = []
    tot = {"n": 0, "lv": dict((c, 0) for c, _ in ASC_LEVELS), "vals": []}
    for g in sorted(per):
        p = per[g]
        st = G.stats(p["vals"], rl)
        row = [g, p["n"]]
        for code, _ in ASC_LEVELS:
            row += [p["lv"][code], _pct(p["lv"][code], p["n"])]
            tot["lv"][code] += p["lv"][code]
        row += [fmt(st["avg"], 2), fmt(st["sou"], 2), fmt(st["kz"], 2)] if st["count"] else ["", "", ""]
        rows.append(row)
        tot["n"] += p["n"]
        tot["vals"] += p["vals"]
    st = G.stats(tot["vals"], rl)
    trow = ["Итого по школе", tot["n"]]
    for code, _ in ASC_LEVELS:
        trow += [tot["lv"][code], _pct(tot["lv"][code], tot["n"])]
    trow += [fmt(st["avg"], 2), fmt(st["sou"], 2), fmt(st["kz"], 2)] if st["count"] else ["", "", ""]
    rows.append(trow)
    return rows


def levels_by_parallel(db, year_id, kind):
    """Успеваемость по параллелям по уровням: за один период или
    сводно за все периоды года (kind='all') — блоками, как в «Старт ШКОЛА»."""
    from . import plans
    rl = rules(db)
    y = db.one("SELECT * FROM years WHERE id=?", (year_id,)) or {}
    classes = db.q("SELECT * FROM classes WHERE year_id=? ORDER BY grade, letter", (year_id,))
    lo, hi = plans.org_range(db.get_setting("org_type") or "secondary")
    grades = list(range(lo, hi + 1))
    sub = "{}\n{} учебный год".format(school_name(db), y.get("name", ""))
    base = {"headers": LEVELS_PAR_HEADERS, "widths": [16, 9] + [7, 7] * 5 + [9, 8, 9],
            "footer": ["Уровень учащегося определяется по самой низкой отметке; % — от количества учащихся параллели, имеющих отметки. "
                       "Средний балл, СОУ и качество знаний (доля отметок {}–10) — по всем отметкам параллели.".format(rl["kz_min_point"])]}
    if kind != "all":
        rep = dict(base)
        rep.update({"name": "Уровни по параллелям",
                    "title": "Из них имеют оценки (по уровням): {}".format(kind_title(db, kind).lower()),
                    "subtitle": sub, "rows": _parallel_levels_rows(db, classes, grades, kind, rl)})
        return rep
    rows = []
    for k, nm in G.all_kinds(y.get("scheme") or "quarters"):
        rows.append({"group": "Из них имеют оценки (по уровням): " + nm.lower()})
        rows += _parallel_levels_rows(db, classes, grades, k, rl)
    rep = dict(base)
    rep.update({"name": "Уровни по параллелям (год)",
                "title": "Успеваемость по параллелям по уровням за все периоды",
                "subtitle": sub, "rows": rows, "repeat_headers": True})
    return rep


def clubs_attendance(db, year_id, category):
    """Посещаемость факультативов (ФЗ) или объединений по интересам (ОпИ).
    Считается по записям в журнале (пропуски «н»)."""
    y = db.one("SELECT * FROM years WHERE id=?", (year_id,))
    cat_name = G.CATEGORIES.get(category, category)
    rows = []
    q = db.q("SELECT a.id AS aid, a.group_name, sb.name AS subject, c.id AS cid, c.grade, c.letter "
             "FROM assignments a JOIN subjects sb ON sb.id=a.subject_id JOIN classes c ON c.id=a.class_id "
             "WHERE c.year_id=? AND sb.category=? ORDER BY sb.name, c.grade, c.letter", (year_id, category))
    for a in q:
        studs = assignment_students(db, {"id": a["aid"], "class_id": a["cid"]})
        lessons = db.val("SELECT COUNT(*) FROM lessons WHERE assignment_id=?", (a["aid"],)) or 0
        absent = db.val("SELECT COUNT(*) FROM marks m JOIN lessons l ON l.id=m.lesson_id "
                        "WHERE l.assignment_id=? AND m.value='н'", (a["aid"],)) or 0
        left = db.val("SELECT COUNT(DISTINCT m.student_id) FROM marks m JOIN lessons l ON l.id=m.lesson_id "
                      "WHERE l.assignment_id=? AND m.value='выб'", (a["aid"],)) or 0
        rows.append([a["subject"] + (" (" + a["group_name"] + ")" if a["group_name"] else ""),
                     class_name({"grade": a["grade"], "letter": a["letter"]}), len(studs), lessons, absent, left])
    return {
        "name": cat_name,
        "title": "Посещаемость: {}".format(cat_name.lower()),
        "subtitle": "{}\n{} учебный год".format(school_name(db), y["name"] if y else ""),
        "headers": ["Занятие", "Класс", "Участников", "Проведено", "Пропусков «н»", "Выбыло"],
        "rows": rows, "widths": [34, 10, 11, 11, 13, 9],
        "footer": ["Учитываются записи в журнале по занятиям данной категории."],
    }


def lists_report(db, year_id, kind):
    """Списки учащихся по уровням: на 9–10, на 7–10, неуспевающие, не аттестованные."""
    y = db.one("SELECT * FROM years WHERE id=?", (year_id,))
    classes = db.q("SELECT * FROM classes WHERE year_id=? ORDER BY grade, letter", (year_id,))
    groups = {"high": [], "suf": [], "one56": [], "low": [], "na": []}
    for c in classes:
        subs = {sb["subject_id"]: sb["subject"] for sb in subjects_of_class(db, c["id"])}
        fm = finals_map(db, c["id"], kind)
        for s in class_students(db, c["id"]):
            items = [(subs.get(subj, ""), v) for (sid, subj), v in fm.items() if sid == s["id"]]
            vals = [v for _, v in items]
            pts = [int(v) for v in vals if G.is_point(v)]
            name = [class_name(c), fio(s)]
            nas = [n for n, v in items if v == G.NOT_ATT]
            lows = ["{} ({})".format(n, v) for n, v in items if G.is_point(v) and int(v) <= 2]
            if nas:
                groups["na"].append(name + [", ".join(nas)])
            if lows:
                groups["low"].append(name + [", ".join(lows)])
            if pts and not nas and not lows:
                mn = min(pts)
                if mn >= 9:
                    groups["high"].append(name + [""])
                elif mn >= 7:
                    groups["suf"].append(name + [""])
                c56 = [n for n, v in items if G.is_point(v) and int(v) in (5, 6)]
                if len(c56) == 1 and all(int(v) >= 7 for v in vals if G.is_point(v) and int(v) not in (5, 6)):
                    groups["one56"].append(name + [c56[0]])
    titles = [("high", "Учащиеся, имеющие отметки только 9–10 баллов (высокий уровень)"),
              ("suf", "Учащиеся, имеющие отметки 7–10 баллов (достаточный и высокий уровни)"),
              ("one56", "Учащиеся с одной отметкой 5–6 баллов (резерв)"),
              ("low", "Учащиеся, имеющие отметки 1–2 балла (низкий уровень)"),
              ("na", "Учащиеся, не аттестованные по предметам (н/а)")]
    rows = []
    for code, t in titles:
        rows.append(["", t + " — " + str(len(groups[code])), "", ""])
        for i, r in enumerate(groups[code]):
            rows.append([i + 1] + r)
        rows.append(["", "", "", ""])
    return {
        "name": "Списки",
        "title": "Списки учащихся по результатам: {}".format(kind_title(db, kind).lower()),
        "subtitle": "{}\n{} учебный год".format(school_name(db), y["name"] if y else ""),
        "headers": ["№", "Класс", "Фамилия, имя", "Предметы"], "rows": rows,
        "widths": [5, 10, 32, 60], "group_rows": True,
    }


def student_card(db, student_id, year_id):
    """Табель / информация для законных представителей."""
    s = db.one("SELECT * FROM students WHERE id=?", (student_id,))
    y = db.one("SELECT * FROM years WHERE id=?", (year_id,))
    c = db.one("SELECT c.* FROM classes c JOIN class_students cs ON cs.class_id=c.id WHERE cs.student_id=? AND c.year_id=? "
               "ORDER BY (cs.date_out='' OR cs.date_out IS NULL) DESC LIMIT 1", (student_id, year_id))
    kinds = G.all_kinds(y["scheme"] if y else "quarters")
    headers = ["Предмет"] + [n for _, n in kinds]
    rows = []
    if c:
        for a in class_assignments(db, c["id"]):
            fm = {r["kind"]: r["value"] for r in db.q("SELECT kind,value FROM final_marks WHERE assignment_id=? AND student_id=?", (a["id"], student_id))}
            if a.get("group_name"):
                if db.q("SELECT 1 FROM assignment_students WHERE assignment_id=?", (a["id"],)) and \
                        not db.q("SELECT 1 FROM assignment_students WHERE assignment_id=? AND student_id=?", (a["id"], student_id)):
                    continue
            rows.append([a["subject"]] + [fm.get(k, "") for k, _ in kinds])
        beh = {r["kind"]: r["value"] for r in db.q("SELECT kind,value FROM behavior WHERE class_id=? AND student_id=?", (c["id"], student_id))}
        rows.append(["Поведение"] + [beh.get(k, "") for k, _ in kinds])
        absent = db.val("SELECT COUNT(*) FROM marks m JOIN lessons l ON l.id=m.lesson_id JOIN assignments a ON a.id=l.assignment_id "
                        "WHERE a.class_id=? AND m.student_id=? AND m.value='н'", (c["id"], student_id))
    else:
        absent = 0
    return {
        "name": "Табель",
        "title": "Сведения о результатах учебной деятельности учащегося",
        "subtitle": "{}\nУчащийся: {}, {} класс, {} учебный год".format(
            school_name(db), fio(s, True), class_name(c) if c else "—", y["name"] if y else ""),
        "headers": headers, "rows": rows, "widths": [34] + [11] * len(kinds),
        "footer": ["Пропущено уроков (по данным журнала): {}".format(absent), "",
                   "Классный руководитель ____________________        Законный представитель ____________________"],
    }


def attendance(db, class_id, date_from, date_to):
    c = get_class(db, class_id)
    studs = class_students(db, class_id)
    rows = []
    args = [class_id]
    cond = ""
    if date_from:
        cond += " AND l.date>=?"; args.append(date_from)
    if date_to:
        cond += " AND l.date<=?"; args.append(date_to)
    cnt = {r["student_id"]: r["n"] for r in db.q(
        "SELECT m.student_id, COUNT(*) AS n FROM marks m JOIN lessons l ON l.id=m.lesson_id "
        "JOIN assignments a ON a.id=l.assignment_id WHERE a.class_id=? AND m.value='н'" + cond + " GROUP BY m.student_id", args)}
    total_lessons = db.val("SELECT COUNT(*) FROM lessons l JOIN assignments a ON a.id=l.assignment_id WHERE a.class_id=?" + cond, args) or 0
    for i, s in enumerate(studs):
        n = cnt.get(s["id"], 0)
        rows.append([i + 1, fio(s), n, fmt(n * 100.0 / total_lessons) if total_lessons else ""])
    rows.append(["", "ИТОГО", sum(cnt.values()), ""])
    return {
        "name": "Пропуски " + class_name(c),
        "title": "Пропуски учебных занятий: {} класс".format(class_name(c)),
        "subtitle": "{}\nПериод: {} — {}. Проведено уроков (записей в журнале): {}".format(school_name(db), date_from or "начало", date_to or "конец", total_lessons),
        "headers": ["№", "Фамилия, имя", "Пропущено уроков", "% от проведённых"],
        "rows": rows, "widths": [4, 30, 14, 14],
    }


def teacher_report(db, staff_id, year_id, kind):
    rl = rules(db)
    t = db.one("SELECT * FROM staff WHERE id=?", (staff_id,))
    y = db.one("SELECT * FROM years WHERE id=?", (year_id,))
    asg = db.q("SELECT a.*, sb.name AS subject, c.grade, c.letter FROM assignments a JOIN subjects sb ON sb.id=a.subject_id "
               "JOIN classes c ON c.id=a.class_id WHERE a.teacher_id=? AND c.year_id=? ORDER BY c.grade, c.letter, sb.sort", (staff_id, year_id))
    headers = ["Класс", "Предмет", "Группа", "Часов", "Уч-ся", "Отметок"] + _level_headers() + ["н/а", "Ср. балл", "КЗ %", "СОУ %"]
    rows = []
    allv = []
    for a in asg:
        studs = set(s["id"] for s in assignment_students(db, a))
        vals = [r["value"] for r in db.q("SELECT student_id,value FROM final_marks WHERE assignment_id=? AND kind=?", (a["id"], kind)) if r["student_id"] in studs]
        allv += vals
        st = G.stats(vals, rl)
        rows.append([class_name(a), a["subject"], a["group_name"], a["hours"], len(studs), st["count"]] + _level_cells(st)
                    + [st["other"][G.NOT_ATT], fmt(st["avg"], 2), fmt(st["kz"]), fmt(st["sou"])])
    st = G.stats(allv, rl)
    rows.append(["ИТОГО", "", "", sum(float(a["hours"] or 0) for a in asg), "", st["count"]] + _level_cells(st)
                + [st["other"][G.NOT_ATT], fmt(st["avg"], 2), fmt(st["kz"]), fmt(st["sou"])])
    return {
        "name": "Учитель",
        "title": "Результаты учебной деятельности учащихся по учителю: {}".format(staff_short(t)),
        "subtitle": "{}\n{} учебный год, {}".format(school_name(db), y["name"] if y else "", kind_title(db, kind).lower()),
        "headers": headers, "rows": rows, "widths": [8, 26, 10, 6, 6, 8] + [5, 6] * 5 + [5, 8, 7, 7],
    }


def students_list(db, class_id):
    c = get_class(db, class_id)
    rows = []
    for i, s in enumerate(class_students(db, class_id)):
        rows.append([i + 1, fio(s, True), s.get("birth_date", ""), s.get("gender", ""), s.get("address", ""),
                     s.get("parents", ""), s.get("parents_phone", ""), s.get("health_group", ""), s.get("pe_group", "")])
    return {
        "name": "Список " + class_name(c),
        "title": "Список учащихся {} класса".format(class_name(c)),
        "subtitle": school_name(db) + "\nКлассный руководитель: " + (c.get("teacher") or "—"),
        "headers": ["№", "ФИО", "Дата рождения", "Пол", "Адрес", "Законные представители", "Телефон", "Группа здоровья", "Группа по физкультуре"],
        "rows": rows, "widths": [4, 32, 12, 5, 30, 30, 14, 10, 12],
        "footer": ["Документ содержит персональные данные (Закон Республики Беларусь «О защите персональных данных»)."],
    }


# ------------------------------------------------------------- контроль накопляемости
ACC_ISSUES = [
    ("few", "Мало текущих отметок"),
    ("stale", "Давно нет записей в журнале"),
    ("missing", "Не выставлена отметка за период"),
    ("deviation", "Отметка за период расходится со средним баллом"),
]


def _period(db, year_id, kind):
    return db.one("SELECT * FROM periods WHERE year_id=? AND kind=?", (year_id, kind)) or {}


def current_kind(db, year_id, today=None):
    """Текущая четверть (полугодие) по датам; после окончания последней — последняя."""
    import datetime
    today = today or datetime.date.today().isoformat()
    ps = [p for p in db.q("SELECT * FROM periods WHERE year_id=? AND start_date<>'' ORDER BY start_date", (year_id,))
          if p["kind"] in ("q1", "q2", "q3", "q4", "h1", "h2")]
    cur = ps[0]["kind"] if ps else "q1"
    for p in ps:
        if p["start_date"] <= today:
            cur = p["kind"]
    return cur


def accumulation(db, year_id, kind, assignments, journal_on, stale_days=14, today=None):
    """Контроль накопляемости отметок по списку нагрузок (assignments — строки
    assignments с полями subject, class_id, hours, teacher_*).
    Возвращает отчёт: строки «класс — предмет — проблема — учащиеся»."""
    import datetime
    rl = rules(db)
    today = today or datetime.date.today().isoformat()
    p = _period(db, year_id, kind)
    ps, pe = p.get("start_date") or "", p.get("end_date") or ""
    started = bool(ps) and ps <= today
    ended = bool(pe) and pe < today
    days_left = None
    if pe and not ended:
        try:
            days_left = (datetime.date.fromisoformat(pe) - datetime.date.fromisoformat(max(today, ps or today))).days
        except ValueError:
            days_left = None
    stale_border = (datetime.date.fromisoformat(today) - datetime.timedelta(days=stale_days)).isoformat()
    rows, counts = [], dict((k, 0) for k, _ in ACC_ISSUES)
    classes = {}
    for a in sorted(assignments, key=lambda x: (x.get("grade") or 0, x.get("letter") or "", x.get("subject") or "")):
        if (a.get("category") or "subject") != "subject" or a.get("eval_type") not in (None, "", "points"):
            continue
        if (a.get("grade") or 0) <= 2:   # I–II классы — безотметочное обучение
            continue
        if a["class_id"] not in classes:
            classes[a["class_id"]] = get_class(db, a["class_id"])
        cname = class_name(classes[a["class_id"]])
        studs = assignment_students(db, a)
        if not studs:
            continue
        teacher = teacher_of(a)
        subj = a["subject"] + (" (" + a["group_name"] + ")" if a.get("group_name") else "")
        need = rl["min_marks_quarter_1h"] if (a.get("hours") and float(a["hours"]) <= 1) else rl["min_marks_quarter"]
        cur = {}
        last_date = ""
        if journal_on and ps and pe:
            lessons = db.q("SELECT id, date FROM lessons WHERE assignment_id=? AND date>=? AND date<=?", (a["id"], ps, pe))
            last_date = max([l["date"] for l in lessons] or [""])
            if lessons:
                ph = ",".join("?" * len(lessons))
                for m in db.q("SELECT student_id, value FROM marks WHERE lesson_id IN (%s)" % ph, [l["id"] for l in lessons]):
                    cur.setdefault(m["student_id"], []).append(m["value"])
            # 1) мало текущих отметок
            # пока период идёт, ожидаемый минимум растёт пропорционально прошедшему времени;
            # полный минимум — за 10 дней до окончания и позже
            expect = need
            if started and not ended and days_left is not None and days_left > 10:
                try:
                    total = (datetime.date.fromisoformat(pe) - datetime.date.fromisoformat(ps)).days or 1
                    expect = int(need * (total - days_left) / float(total))
                except ValueError:
                    expect = need
            if started and expect > 0:
                few = []
                for s in studs:
                    pts = [v for v in cur.get(s["id"], []) if G.is_point(v)]
                    if len(pts) < expect:
                        few.append("%s — %d" % (fio(s), len(pts)))
                if few:
                    note = ("нужно не менее %d" % need if expect >= need else
                            "к этой дате ожидается не менее %d из %d" % (expect, need)) + \
                        (", до конца периода %d дн." % days_left if days_left is not None else "")
                    rows.append([cname, subj, teacher, "Мало текущих отметок (%s)" % note, len(few), "; ".join(few)])
                    counts["few"] += len(few)
            # 2) давно нет записей
            if started and not ended and ps <= stale_border and (not last_date or last_date < stale_border):
                rows.append([cname, subj, teacher, "Нет записей в журнале более %d дней" % stale_days, "",
                             "последний урок: " + (_ru_date(last_date) if last_date else "уроков в этом периоде нет")])
                counts["stale"] += 1
        # 3) не выставлена отметка за период / 4) расхождение
        fin = dict((r["student_id"], r) for r in db.q("SELECT student_id, value, excused FROM final_marks WHERE assignment_id=? AND kind=?",
                                                       (a["id"], kind)))
        if ended:
            miss = [fio(s) for s in studs if s["id"] not in fin]
            if miss:
                rows.append([cname, subj, teacher, "Не выставлена отметка за период", len(miss), "; ".join(miss)])
                counts["missing"] += len(miss)
        if journal_on:
            dev = []
            for s in studs:
                f = fin.get(s["id"])
                pts = [int(v) for v in cur.get(s["id"], []) if G.is_point(v)]
                if f and G.is_point(f["value"]) and len(pts) >= 2:
                    avg = sum(pts) / float(len(pts))
                    if abs(int(f["value"]) - avg) > 1.0:
                        dev.append("%s — %s при среднем %s" % (fio(s), f["value"], fmt(avg, 2)))
            if dev:
                rows.append([cname, subj, teacher, "Отметка за период расходится со средним баллом текущих более чем на 1", len(dev), "; ".join(dev)])
                counts["deviation"] += len(dev)
    head = "{}\n{} учебный год, {}".format(school_name(db), (db.one("SELECT name FROM years WHERE id=?", (year_id,)) or {}).get("name", ""),
                                         kind_title(db, kind).lower())
    if ps and pe:
        head += " ({} — {}){}".format(_ru_date(ps), _ru_date(pe), "" if not days_left else ", до окончания %d дн." % days_left)
    return {
        "name": "Накопляемость отметок",
        "title": "Контроль накопляемости отметок",
        "subtitle": head,
        "headers": ["Класс", "Предмет", "Учитель", "Замечание", "Уч-ся", "Кого касается"],
        "rows": rows or [["", "", "", "Замечаний нет", "", ""]],
        "widths": [8, 22, 18, 34, 6, 70],
        "counts": counts,
        "issue_names": dict(ACC_ISSUES),
        "footer": ["Минимум текущих отметок для отметки за период: {} (для предметов 1 ч в неделю — {}); настраивается в "
                   "«Администрирование → Настройки и правила». Отметка за период проверяется на отсутствие после окончания периода. "
                   "I–II классы (безотметочное обучение) не проверяются."
                   .format(rl["min_marks_quarter"], rl["min_marks_quarter_1h"])] if journal_on else
                  ["Электронный журнал отключён: проверяется только наличие отметок за период после его окончания. I–II классы (безотметочное обучение) не проверяются."],
    }


def _ru_date(d):
    p = (d or "").split("-")
    return "%s.%s.%s" % (p[2], p[1], p[0]) if len(p) == 3 else (d or "")


# ------------------------------------------------------------- табель для родителей
def report_cards(db, class_id, kinds=None, student_ids=None):
    """Данные для печати табелей успеваемости класса: по одному на учащегося."""
    c = get_class(db, class_id)
    y = db.one("SELECT * FROM years WHERE id=?", (c["year_id"],)) or {}
    all_k = G.all_kinds(y.get("scheme") or "quarters")
    names = dict(all_k)
    kinds = [k for k in (kinds or [k for k, _ in all_k]) if k in names]
    periods = dict((p["kind"], p) for p in db.q("SELECT * FROM periods WHERE year_id=?", (c["year_id"],)))
    studs = class_students(db, class_id)
    if student_ids:
        want = set(int(x) for x in student_ids)
        studs = [s for s in studs if s["id"] in want]
    asg = class_assignments(db, class_id, graded_only=True)
    groups = {}
    for a in asg:
        ids = [r["student_id"] for r in db.q("SELECT student_id FROM assignment_students WHERE assignment_id=?", (a["id"],))]
        groups[a["id"]] = set(ids) if ids else None
    fm = {}
    for r in db.q("SELECT f.assignment_id, f.student_id, f.kind, f.value FROM final_marks f JOIN assignments a ON a.id=f.assignment_id "
                  "WHERE a.class_id=?", (class_id,)):
        fm[(r["assignment_id"], r["student_id"], r["kind"])] = r["value"]
    beh = dict(((r["student_id"], r["kind"]), r["value"]) for r in
               db.q("SELECT student_id, kind, value FROM behavior WHERE class_id=?", (class_id,)))
    # пропуски по данным журнала за каждый период
    absent = {}
    for k in kinds:
        p = periods.get(k) or {}
        if p.get("start_date") and p.get("end_date"):
            for r in db.q("SELECT m.student_id, COUNT(*) n FROM marks m JOIN lessons l ON l.id=m.lesson_id "
                          "JOIN assignments a ON a.id=l.assignment_id WHERE a.class_id=? AND m.value='н' "
                          "AND l.date>=? AND l.date<=? GROUP BY m.student_id", (class_id, p["start_date"], p["end_date"])):
                absent[(r["student_id"], k)] = r["n"]
    year_absent = dict((r["student_id"], r["n"]) for r in db.q(
        "SELECT m.student_id, COUNT(*) n FROM marks m JOIN lessons l ON l.id=m.lesson_id JOIN assignments a ON a.id=l.assignment_id "
        "WHERE a.class_id=? AND m.value='н' GROUP BY m.student_id", (class_id,)))
    has_journal = bool(db.val("SELECT 1 FROM lessons l JOIN assignments a ON a.id=l.assignment_id WHERE a.class_id=? LIMIT 1", (class_id,)))
    teacher = db.one("SELECT * FROM staff WHERE id=?", (c.get("teacher_id"),)) if c.get("teacher_id") else None
    school = db.get_setting("school", {}) or {}
    out = []
    for s in studs:
        rows = []
        for a in asg:
            g = groups.get(a["id"])
            if g is not None and s["id"] not in g:
                continue
            marks = dict((k, fm.get((a["id"], s["id"], k), "")) for k in kinds)
            rows.append({"subject": a["subject"], "marks": marks})
        avg = {}
        for k in kinds:
            pts = [int(r["marks"][k]) for r in rows if G.is_point(r["marks"][k])]
            avg[k] = fmt(sum(pts) / float(len(pts)), 1) if pts else ""
        ab = {}
        for k in kinds:
            if k in ("year", "exam", "final"):
                ab[k] = year_absent.get(s["id"], 0) if k == "year" else ""
            else:
                ab[k] = absent.get((s["id"], k), 0)
        out.append({"id": s["id"], "name": fio(s, True), "gender": s.get("gender") or "", "rows": rows, "avg": avg, "absent": ab,
                    "behavior": dict((k, beh.get((s["id"], k), "")) for k in kinds)})
    return {
        "school": school_name(db), "director": school.get("Директор", ""), "year": y.get("name", ""),
        "class": class_name(c), "teacher": fio(teacher, True) if teacher else "",
        "kinds": [{"kind": k, "name": names[k]} for k in kinds], "students": out, "has_journal": has_journal,
        "all_kinds": [{"kind": k, "name": n} for k, n in all_k],
    }


# ------------------------------------------------------------- выполнение программы (КТП)
def _hrs(x):
    """Часы для отчёта: 1,5 / 12 (без «,0»)."""
    if x is None:
        return ""
    return fmt(float(x), 1).replace(",0", "")


def ktp_progress(db, year_id, assignments, today=None):
    import datetime
    today = today or datetime.date.today().isoformat()
    rows = []
    tot = {"items": 0, "hours": 0.0, "plan": 0.0, "done": 0, "lag": 0.0}
    no_ktp = 0
    classes = {}
    for a in sorted(assignments, key=lambda x: (x.get("grade") or 0, x.get("letter") or "", x.get("subject") or "")):
        if a["class_id"] not in classes:
            classes[a["class_id"]] = get_class(db, a["class_id"])
        items = db.q("SELECT hours, planned_date FROM ktp WHERE assignment_id=?", (a["id"],))
        done = db.val("SELECT COUNT(*) FROM lessons WHERE assignment_id=? AND date<=?", (a["id"], today)) or 0
        unlinked = db.val("SELECT COUNT(*) FROM lessons WHERE assignment_id=? AND date<=? AND ktp_id IS NULL", (a["id"], today)) or 0
        subj = a["subject"] + (" (" + a["group_name"] + ")" if a.get("group_name") else "")
        if not items:
            no_ktp += 1
            rows.append([class_name(classes[a["class_id"]]), subj, teacher_of(a), "КТП не загружено", "", "", done, "", ""])
            continue
        hours = sum(float(i["hours"] or 0) for i in items)
        dated = [i for i in items if i["planned_date"]]
        plan = sum(float(i["hours"] or 0) for i in dated if i["planned_date"] <= today) if dated else None
        lag = (plan - done) if plan is not None else None
        rows.append([class_name(classes[a["class_id"]]), subj, teacher_of(a), len(items), _hrs(hours),
                     "" if plan is None else _hrs(plan), done,
                     "" if lag is None or lag <= 0 else _hrs(lag), unlinked or ""])
        tot["items"] += len(items)
        tot["hours"] += hours
        tot["plan"] += plan or 0
        tot["done"] += done
        tot["lag"] += lag if lag and lag > 0 else 0
    rows.append(["ИТОГО", "", "", tot["items"], _hrs(tot["hours"]), _hrs(tot["plan"]),
                 tot["done"], _hrs(tot["lag"]) if tot["lag"] else "", ""])
    y = db.one("SELECT name FROM years WHERE id=?", (year_id,)) or {}
    return {
        "name": "Выполнение программы",
        "title": "Выполнение учебных программ по календарно-тематическому планированию",
        "subtitle": "{}\n{} учебный год, на {}".format(school_name(db), y.get("name", ""), _ru_date(today)),
        "headers": ["Класс", "Предмет", "Учитель", "Тем в КТП", "Часов по КТП", "По плану на дату, ч", "Проведено уроков",
                    "Отставание, ч", "Уроков без темы КТП"],
        "widths": [8, 24, 18, 9, 9, 11, 10, 10, 11],
        "rows": rows,
        "footer": ["«По плану на дату» считается по датам в КТП; если даты не указаны, столбец пуст. Предметов без КТП: %d." % no_ktp],
    }
