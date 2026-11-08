# -*- coding: utf-8 -*-
"""Отчёты и аналитика. Каждый отчёт возвращает dict:
{title, subtitle, headers, rows, widths, footer, name} — одинаково для показа
в интерфейсе, печати и выгрузки в Excel."""
from . import grading as G

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
    return db.q(sql, (class_id,))


def assignment_students(db, a):
    sub = db.q("SELECT student_id FROM assignment_students WHERE assignment_id=?", (a["id"],))
    studs = class_students(db, a["class_id"])
    if sub:
        ids = set(r["student_id"] for r in sub)
        studs = [s for s in studs if s["id"] in ids]
    return studs


def class_assignments(db, class_id):
    return db.q("SELECT a.*, sb.name AS subject, sb.sort, sb.short, st.last_name AS t_last, st.first_name AS t_first, "
                "st.middle_name AS t_middle FROM assignments a JOIN subjects sb ON sb.id=a.subject_id "
                "LEFT JOIN staff st ON st.id=a.teacher_id WHERE a.class_id=? "
                "ORDER BY sb.sort, sb.name, a.group_name", (class_id,))


def teacher_of(a):
    return staff_short({"last_name": a.get("t_last"), "first_name": a.get("t_first"), "middle_name": a.get("t_middle")})


def finals_map(db, class_id, kind):
    """{(student_id, subject_id): value} — по всем группам предмета."""
    rows = db.q("SELECT f.student_id, a.subject_id, f.value, f.excused FROM final_marks f "
                "JOIN assignments a ON a.id=f.assignment_id WHERE a.class_id=? AND f.kind=?", (class_id, kind))
    return {(r["student_id"], r["subject_id"]): r["value"] for r in rows}


def subjects_of_class(db, class_id):
    seen, out = set(), []
    for a in class_assignments(db, class_id):
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
