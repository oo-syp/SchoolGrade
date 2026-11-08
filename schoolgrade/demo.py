# -*- coding: utf-8 -*-
"""Демонстрационные данные (вымышленные) для знакомства с программой."""
import datetime
import random

from . import auth, grading as G

LAST_M = ["Иванов", "Ковалёв", "Новик", "Шевчук", "Климович", "Мельников", "Козлов", "Лукашевич", "Савицкий", "Жук",
          "Ткачук", "Бондарь", "Мороз", "Кравец", "Гурин", "Сидоренко"]
FIRST_M = ["Артём", "Максим", "Матвей", "Иван", "Даниил", "Кирилл", "Михаил", "Тимофей", "Егор", "Никита"]
FIRST_F = ["Анна", "Мария", "София", "Ева", "Алиса", "Виктория", "Полина", "Дарья", "Ксения", "Валерия"]


def fem(last):
    if last.endswith(("ов", "ёв", "ев", "ин")):
        return last + "а"
    if last.endswith("ский"):
        return last[:-2] + "ая"
    return last


def fill(db, seed=7):
    rnd = random.Random(seed)
    today = datetime.date.today()
    if not db.get_setting("org_type"):
        db.set_setting("org_type", "secondary")
    if db.val("SELECT COUNT(*) FROM subjects") == 0:
        from .db import DEFAULT_SUBJECTS, SUBJECT_SHORT
        for i, name in enumerate(DEFAULT_SUBJECTS):
            db.ex("INSERT INTO subjects(name,short,sort,category) VALUES(?,?,?,'subject')",
                  (name, SUBJECT_SHORT.get(name, ""), (i + 1) * 10))
    start = today.year if today.month >= 9 else today.year - 1
    y = db.one("SELECT * FROM years WHERE name=?", ("%d/%d" % (start, start + 1),))
    if not y:
        from .api import _default_periods
        yid = db.ex("INSERT INTO years(name,start_date,end_date,is_current,scheme) VALUES(?,?,?,1,'quarters')",
                    ("%d/%d" % (start, start + 1), "%d-09-01" % start, "%d-08-31" % (start + 1)))
        for kind, nm, a, b in _default_periods(start, "quarters"):
            db.ex("INSERT INTO periods(year_id,kind,name,start_date,end_date) VALUES(?,?,?,?,?)", (yid, kind, nm, a, b))
        for kind, nm in G.FINAL_KINDS:
            db.ex("INSERT INTO periods(year_id,kind,name,start_date,end_date) VALUES(?,?,?,'','')", (yid, kind, nm))
    else:
        yid = y["id"]
    db.set_setting("school", {"Наименование": "ГУО «Средняя школа № 0 г. Энска» (демо)", "Директор": "Петрова Ирина Сергеевна",
                              "Почтовый адрес": "210000, г. Энск, ул. Школьная, 1"})
    staff = [("Петрова", "Ирина", "Сергеевна", "директор"), ("Соколова", "Елена", "Викторовна", "учитель"),
             ("Кузьмич", "Андрей", "Петрович", "учитель"), ("Романович", "Ольга", "Ивановна", "учитель"),
             ("Шульга", "Наталья", "Николаевна", "учитель")]
    sids = []
    for ln, fn, mn, pos in staff:
        sids.append(db.ex("INSERT INTO staff(last_name,first_name,middle_name,position,education,category) VALUES(?,?,?,?,?,?)",
                          (ln, fn, mn, pos, "высшее", rnd.choice(["первая", "высшая", "вторая"]))))
    h, s = auth.hash_password("teacher")
    db.ex("INSERT OR IGNORE INTO users(login,pw_hash,pw_salt,full_name,role,staff_id,must_change) VALUES(?,?,?,?,?,?,0)",
          ("sokolova", h, s, "Соколова Е.В.", "classteacher", sids[1]))
    subj = {r["name"]: r["id"] for r in db.q("SELECT id,name FROM subjects")}
    plan = {
        5: [("Белорусский язык", 3, 1), ("Русский язык", 4, 1), ("Математика", 5, 2), ("Иностранный язык", 3, 3),
            ("Всемирная история", 1, 4), ("Биология", 1, 4), ("Физическая культура и здоровье", 3, 2)],
        9: [("Белорусский язык", 2, 1), ("Русский язык", 2, 1), ("Алгебра", 3, 2), ("Геометрия", 2, 2), ("Физика", 2, 2),
            ("Химия", 2, 4), ("История Беларуси", 2, 3), ("Информатика", 1, 2), ("Физическая культура и здоровье", 3, 4)],
    }
    p = {r["kind"]: r for r in db.q("SELECT * FROM periods WHERE year_id=?", (yid,))}
    q1s = datetime.date(*map(int, p["q1"]["start_date"].split("-")))
    q1e = datetime.date(*map(int, p["q1"]["end_date"].split("-")))
    for grade, letter, ct in [(5, "А", 1), (5, "Б", 3), (9, "А", 4)]:
        cid = db.ex("INSERT INTO classes(year_id,grade,letter,teacher_id) VALUES(?,?,?,?)", (yid, grade, letter, sids[ct]))
        studs = []
        for i in range(rnd.randint(14, 20)):
            last = rnd.choice(LAST_M)
            if rnd.random() < 0.5:
                last, first, g = fem(last), rnd.choice(FIRST_F), "ж"
            else:
                first, g = rnd.choice(FIRST_M), "м"
            sid = db.ex("INSERT INTO students(last_name,first_name,gender,birth_date,pe_group,health_group) VALUES(?,?,?,?,?,?)",
                        (last, first, g, "%d-%02d-%02d" % (start - grade - 6, rnd.randint(1, 12), rnd.randint(1, 28)),
                         "основная", rnd.choice(["I", "II", "II", "III"])))
            db.ex("INSERT INTO class_students(class_id,student_id,date_in) VALUES(?,?,?)", (cid, sid, "%d-09-01" % start))
            studs.append((sid, rnd.gauss(6.5, 1.7)))
        for name, hours, t in plan[grade]:
            if name not in subj:
                continue
            aid = db.ex("INSERT INTO assignments(class_id,subject_id,teacher_id,hours,eval_type) VALUES(?,?,?,?,'points')",
                        (cid, subj[name], sids[t], hours))
            d = q1s
            lessons = []
            while d <= min(q1e, today) and len(lessons) < hours * 8:
                if d.weekday() < 5 and rnd.random() < hours / 5.0:
                    lessons.append(db.ex("INSERT INTO lessons(assignment_id,date,topic) VALUES(?,?,?)",
                                         (aid, d.isoformat(), "Тема урока {}".format(len(lessons) + 1))))
                d += datetime.timedelta(days=1)
            for sid, ability in studs:
                vals = []
                for lid in lessons:
                    r = rnd.random()
                    if r < 0.06:
                        v = "н"
                    elif r < 0.45:
                        v = str(max(1, min(10, int(round(rnd.gauss(ability, 1.2))))))
                        vals.append(v)
                    else:
                        continue
                    db.ex("INSERT INTO marks(lesson_id,student_id,value,updated_at) VALUES(?,?,?,datetime('now'))", (lid, sid, v))
                if today > q1e:
                    sug = G.suggest_period_mark(vals, hours)
                    if sug["value"]:
                        db.ex("INSERT INTO final_marks(assignment_id,student_id,kind,value,updated_at) VALUES(?,?,?,?,datetime('now'))",
                              (aid, sid, "q1", sug["value"]))
        if today > q1e:
            for sid, ability in studs:
                db.ex("INSERT INTO behavior(class_id,student_id,kind,value) VALUES(?,?,?,?)",
                      (cid, sid, "q1", "прим." if ability > 5 else "уд."))
