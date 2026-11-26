# -*- coding: utf-8 -*-
"""Тесты SchoolPulse (SchoolGrade 1.6): права, длительность отсутствий, формула поведения, миграция 6→7,
интеграция «событие → рекомендация → ведомость», фильтрация имён на киоске (SSE), нагрузка."""
import datetime as DT
import http.client
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from schoolgrade import db as DB, main as M, server as S, pulse  # noqa: E402
from test_v13 import Client  # noqa: E402

DAY = "2026-11-09"       # понедельник, 1-я неделя расписания


def at(h, m, s=0):
    d = DT.datetime.strptime(DAY, "%Y-%m-%d")
    return d.replace(hour=h, minute=m, second=s)


class Clock(object):
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class PulseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.clock = Clock(at(9, 0))
        pulse.CLOCK["fn"] = cls.clock
        cls.tmp = tempfile.mkdtemp()
        cls.app = M.init_app(cls.tmp)
        cls.srv = S.ServerThread(cls.app, "127.0.0.1", 0)
        cls.srv.start()
        cls.port = cls.srv.httpd.server_address[1]
        cls.base = "http://127.0.0.1:%d" % cls.port
        c = Client(cls.base)
        c.call("POST", "/api/login", {"login": "admin", "password": "admin"})
        c.call("POST", "/api/me/password", {"old": "admin", "new": "Secret123"})
        c.call("PUT", "/api/org_type", {"org_type": "secondary"})
        c.call("POST", "/api/years", {"start_year": 2026})
        cls.cid = c.call("POST", "/api/classes", {"grade": 6, "letter": "А"})["id"]
        cls.cid2 = c.call("POST", "/api/classes", {"grade": 6, "letter": "Б"})["id"]
        cls.studs = []
        for ln, fn in (("Иванов", "Иван"), ("Петров", "Пётр"), ("Сидоров", "Сидор"), ("Фёдоров", "Фёдор"), ("Яковлев", "Яков")):
            cls.studs.append(c.call("POST", "/api/students", {"class_id": cls.cid, "last_name": ln, "first_name": fn})["id"])
        cls.other_stud = c.call("POST", "/api/students", {"class_id": cls.cid2, "last_name": "Чужой", "first_name": "Чужак"})["id"]
        cls.t1 = c.call("POST", "/api/staff", {"last_name": "Сидорова", "first_name": "Анна", "middle_name": "Ивановна"})["id"]
        cls.t2 = c.call("POST", "/api/staff", {"last_name": "Козлов", "first_name": "Олег", "middle_name": "Петрович"})["id"]
        subs = dict((s["name"], s["id"]) for s in c.call("GET", "/api/subjects"))
        db = cls.app.db
        c.call("POST", "/api/assignments", {"class_id": cls.cid, "subject_id": subs["Математика"], "teacher_id": cls.t1, "hours": 5})
        c.call("POST", "/api/assignments", {"class_id": cls.cid, "subject_id": subs["Информатика"], "teacher_id": cls.t2, "hours": 1})
        c.call("POST", "/api/assignments", {"class_id": cls.cid2, "subject_id": subs["Математика"], "teacher_id": cls.t1, "hours": 5})
        cls.a_math = db.val("SELECT id FROM assignments WHERE class_id=? AND subject_id=?", (cls.cid, subs["Математика"]))
        cls.a_inf = db.val("SELECT id FROM assignments WHERE class_id=? AND subject_id=?", (cls.cid, subs["Информатика"]))
        cls.a_math2 = db.val("SELECT id FROM assignments WHERE class_id=? AND subject_id=?", (cls.cid2, subs["Математика"]))
        # информатика — подгруппа из двух учащихся
        for sid in cls.studs[:2]:
            db.ex("INSERT INTO assignment_students(assignment_id,student_id) VALUES(?,?)", (cls.a_inf, sid))
        y = c.call("GET", "/api/me")["current_year"]["id"]
        sid = c.call("POST", "/api/schedule", {"year_id": y, "name": "II четверть", "start_date": DAY, "weeks": 2})["id"]
        c.call("PUT", "/api/schedule/%d/class/%d" % (sid, cls.cid),
               {"items": [{"week": 1, "weekday": 1, "lesson_no": 1, "assignment_id": cls.a_math},
                          {"week": 1, "weekday": 1, "lesson_no": 2, "assignment_id": cls.a_inf}]})
        c.call("PUT", "/api/schedule/bells", {"bells": {"1": ["08:30", "09:15"], "2": ["09:25", "10:10"]}})
        # учётные записи
        c.call("POST", "/api/users", {"login": "sidorova", "password": "Sidorova123", "role": "teacher", "staff_ids": [cls.t1]})
        c.call("POST", "/api/users", {"login": "kozlov", "password": "Kozlov123", "role": "teacher", "staff_ids": [cls.t2]})
        c.call("POST", "/api/users", {"login": "kruk", "password": "Kruk12345", "role": "classteacher", "staff_ids": [cls.t2]})
        c.call("POST", "/api/users", {"login": "uchit", "password": "Uchit12345", "role": "workspace"})
        c.call("POST", "/api/users", {"login": "dir", "password": "Direct12345", "role": "director"})
        cls.c = c
        cls.cl = {}
        for login, pw in (("sidorova", "Sidorova123"), ("kozlov", "Kozlov123"), ("uchit", "Uchit12345"), ("dir", "Direct12345")):
            x = Client(cls.base)
            x.call("POST", "/api/login", {"login": login, "password": pw})
            x.call("POST", "/api/me/password", {"old": pw, "new": pw + "x"})
            cls.cl[login] = x
        # классный руководитель 6А = Козлов (t2): его вторая учётка
        db.ex("UPDATE classes SET teacher_id=? WHERE id=?", (cls.t2, cls.cid))

    @classmethod
    def tearDownClass(cls):
        pulse.CLOCK["fn"] = DT.datetime.now
        cls.srv.stop()
        cls.app.db.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.app.db.ex("DELETE FROM pulse_events")
        self.app.db.ex("DELETE FROM pulse_lesson_end")
        self.clock.t = at(9, 0)

    # ------------------------------------------------------------ вспомогательное
    def open_lesson(self, who="sidorova", aid=None, num=1):
        r = self.cl[who].call("POST", "/api/pulse/lessons/open", {"assignment_id": aid or self.a_math, "num": num})
        return r["lesson_id"]

    def ev(self, who, lid, code, sids, expect=200):
        return self.cl[who].call("POST", "/api/pulse/events", {"lesson_id": lid, "type_code": code, "student_ids": sids}, expect=expect)

    # ------------------------------------------------------------ справочник
    def test_01_types_default(self):
        r = self.cl["sidorova"].call("GET", "/api/pulse/event-types")
        codes = [t["code"] for t in r["types"]]
        self.assertEqual(codes, ["~ушёл", "~вернулся", "-опоздание", "+ответил", "+опорный конспект", "-поведение", "-нет ДЗ"])
        ra = self.c.call("GET", "/api/pulse/event-types?all=1")
        self.assertEqual(len(ra["types"]), 9)
        ex = [t for t in ra["types"] if t["is_extracurricular"]]
        self.assertEqual(sorted(t["code"] for t in ex), ["!дежурство", "!столовая"])
        self.assertTrue(all(t["is_active"] == 0 and t["behavior_weight"] == 0.5 for t in ex))
        # справочник правит только администратор
        self.cl["sidorova"].call("POST", "/api/pulse/event-types", {"code": "+x", "name": "x", "category": "positive", "color": "#112233"}, expect=403)

    def test_02_types_admin_crud_and_svg(self):
        r = self.c.call("POST", "/api/pulse/event-types", {"code": "+проект", "name": "Работа над проектом", "category": "positive", "polarity": "pos",
                                                            "color": "#0969da", "affects_behavior_recommendation": True, "behavior_weight": 2,
                                                            "max_per_lesson": 1, "icon": '<svg viewBox="0 0 24 24"><path d="M5 12h14"/></svg>'})
        tid = r["id"]
        self.c.call("POST", "/api/pulse/event-types", {"code": "+проект", "name": "дубль", "category": "positive", "color": "#112233"}, expect=400)
        self.c.call("POST", "/api/pulse/event-types", {"code": "+bad", "name": "x", "category": "positive", "color": "red"}, expect=400)
        for bad in ('<svg><script>alert(1)</script></svg>', '<svg onload="x()"></svg>', '<div></div>', '<svg><a href="javascript:1"></a></svg>'):
            self.c.call("POST", "/api/pulse/event-types", {"code": "+bad", "name": "x", "category": "positive", "color": "#112233", "icon": bad}, expect=400)
        self.c.call("PUT", "/api/pulse/event-types/%d" % tid, {"name": "Проект", "is_active": False})
        self.assertNotIn("+проект", [t["code"] for t in self.cl["sidorova"].call("GET", "/api/pulse/event-types")["types"]])
        self.c.call("DELETE", "/api/pulse/event-types/%d" % tid)
        self.assertIsNone(self.app.db.val("SELECT 1 FROM pulse_types WHERE id=?", (tid,)))

    def test_03_visible_types(self):
        s = self.cl["sidorova"]
        s.call("PUT", "/api/pulse/visible-types", {"codes": ["+ответил", "-поведение"]})
        self.assertEqual([t["code"] for t in s.call("GET", "/api/pulse/event-types")["types"]], ["+ответил", "-поведение"])
        s.call("PUT", "/api/pulse/visible-types", {"codes": None})
        self.assertEqual(len(s.call("GET", "/api/pulse/event-types")["types"]), 7)
        # чужие настройки — только администратор
        s.call("PUT", "/api/pulse/visible-types", {"user_id": 1, "codes": []}, expect=403)

    # ------------------------------------------------------------ права
    def test_10_permissions(self):
        lid = self.open_lesson()
        s, k, u, d = self.cl["sidorova"], self.cl["kozlov"], self.cl["uchit"], self.cl["dir"]
        self.ev("sidorova", lid, "+ответил", [self.studs[0]])
        # чужой учитель не вводит на чужом уроке
        self.ev("kozlov", lid, "+ответил", [self.studs[0]], expect=403)
        # директор вводить не может
        d.call("GET", "/api/pulse/lessons/today", expect=403)
        self.ev("dir", lid, "+ответил", [self.studs[0]], expect=403)
        # учительская и админ — могут
        self.ev("uchit", lid, "+ответил", [self.studs[1]])
        self.assertTrue(self.c.call("POST", "/api/pulse/events", {"lesson_id": lid, "type_code": "+ответил", "student_ids": [self.studs[2]]})["created"])
        # просмотр ленты урока: чужой учитель нет, директор да
        k.call("GET", "/api/pulse/lesson/%d/events" % lid)        # классный руководитель 6А видит все события класса
        d.call("GET", "/api/pulse/lesson/%d/events" % lid)
        # сводка по классу: учитель-предметник видит только свои события; кл. руководитель (kozlov) — все по своему классу
        r = s.call("GET", "/api/pulse/class/%d/summary?period=today" % self.cid)
        self.assertEqual(r["total_events"], 3)
        lid2 = self.open_lesson("kozlov", self.a_inf, 2)
        self.ev("kozlov", lid2, "-поведение", [self.studs[0]])
        self.assertEqual(s.call("GET", "/api/pulse/class/%d/summary?period=today" % self.cid)["total_events"], 3)
        self.assertEqual(k.call("GET", "/api/pulse/class/%d/summary?period=today" % self.cid)["total_events"], 4)
        self.assertEqual(k.call("GET", "/api/pulse/class/%d/summary?period=today&scope=mine" % self.cid)["total_events"], 1)
        # чужой класс
        s.call("GET", "/api/pulse/class/%d/summary?period=today" % self.cid2)
        k.call("GET", "/api/pulse/class/%d/summary?period=today" % self.cid2, expect=403)
        # завуч видит свод, но не вводит
        self.assertEqual(d.call("GET", "/api/pulse/overview?period=today")["total_events"], 4)
        s.call("GET", "/api/pulse/overview?period=today", expect=403)
        # внеурочное: только кл. руководитель/учительская/админ
        self.c.call("PUT", "/api/pulse/event-types/%d" % self.app.db.val("SELECT id FROM pulse_types WHERE code='!столовая'"), {"is_active": True})
        body = {"class_id": self.cid, "type_code": "!столовая", "student_ids": [self.studs[3]]}
        s.call("POST", "/api/pulse/events", body, expect=403)
        k.call("POST", "/api/pulse/events", body)
        u.call("POST", "/api/pulse/events", body)
        # учащийся не из подгруппы информатики
        r = self.ev("kozlov", lid2, "+ответил", [self.studs[4]])
        self.assertEqual(r["created"], []) and self.assertEqual(len(r["skipped"]), 1)
        # ученик чужого класса
        r = self.ev("sidorova", lid, "+ответил", [self.other_stud])
        self.assertEqual(r["created"], [])
        self.c.call("PUT", "/api/pulse/event-types/%d" % self.app.db.val("SELECT id FROM pulse_types WHERE code='!столовая'"), {"is_active": False})

    def test_11_today_screen_and_substitution(self):
        r = self.cl["sidorova"].call("GET", "/api/pulse/lessons/today")
        self.assertEqual([(x["num"], x["subject"]) for x in r["lessons"]], [(1, "Математика")])
        r = self.cl["kozlov"].call("GET", "/api/pulse/lessons/today")
        self.assertEqual([(x["num"], x["subject"]) for x in r["lessons"]], [(2, "Информатика")])
        # замена: Козлов ведёт математику 1-м уроком вместо Сидоровой
        self.app.db.ex("INSERT INTO substitutions(date,lesson_no,assignment_id,absent_id,substitute_id) VALUES(?,?,?,?,?)", (DAY, 1, self.a_math, self.t1, self.t2))
        try:
            self.assertEqual([x["num"] for x in self.cl["kozlov"].call("GET", "/api/pulse/lessons/today")["lessons"]], [1, 2])
            self.assertEqual(self.cl["sidorova"].call("GET", "/api/pulse/lessons/today")["lessons"], [])
            lid = self.open_lesson("kozlov", self.a_math, 1)
            self.ev("kozlov", lid, "+ответил", [self.studs[0]])
        finally:
            self.app.db.ex("DELETE FROM substitutions")
        # администратор и учительская видят все уроки дня
        self.assertEqual(len(self.cl["uchit"].call("GET", "/api/pulse/lessons/today")["lessons"]), 2)

    # ------------------------------------------------------------ события и время
    def test_20_server_time_and_undo_and_delete_rule(self):
        lid = self.open_lesson()
        s = self.cl["sidorova"]
        r = s.call("POST", "/api/pulse/events", {"lesson_id": lid, "type_code": "+ответил", "student_ids": [self.studs[0]],
                                                  "event_time": "2000-01-01 00:00:00"})
        self.assertEqual(r["event_time"], DAY + " 09:00:00")        # время клиента игнорируется
        self.assertEqual(self.app.db.val("SELECT event_time FROM pulse_events WHERE id=?", (r["created"][0],)), DAY + " 09:00:00")
        # «↶» — отмена всей пачки
        b = self.ev("sidorova", lid, "+ответил", self.studs[:4])
        self.assertEqual(len(b["created"]), 4)
        s.call("DELETE", "/api/pulse/batches/%s" % b["batch_id"])
        self.assertEqual(self.app.db.val("SELECT COUNT(*) FROM pulse_events"), 1)
        # чужое событие удалить нельзя
        eid = r["created"][0]
        self.cl["kozlov"].call("DELETE", "/api/pulse/events/%d" % eid, expect=403)
        # автор — в течение 24 часов
        self.clock.t = at(9, 0) + DT.timedelta(hours=23, minutes=59)
        e2 = self.ev("sidorova", lid, "+ответил", [self.studs[2]]) if False else None
        s.call("DELETE", "/api/pulse/events/%d" % eid)
        # после 24 часов — автор уже не может, администратор может (запись в журнале действий)
        e3 = self.app.db.ex("INSERT INTO pulse_events(student_id,class_id,type_id,lesson_id,assignment_id,date,event_time,created_by) "
                            "SELECT ?,?,id,?,?,?,?,? FROM pulse_types WHERE code='+ответил'",
                            (self.studs[0], self.cid, lid, self.a_math, DAY, DAY + " 09:00:00", self.app.db.val("SELECT id FROM users WHERE login='sidorova'")))
        self.clock.t = at(9, 0) + DT.timedelta(hours=24, minutes=1)
        s.call("DELETE", "/api/pulse/events/%d" % e3, expect=403)
        self.c.call("DELETE", "/api/pulse/events/%d" % e3)
        self.assertTrue(self.app.db.val("SELECT 1 FROM audit WHERE action='pulse_event_delete'"))

    def test_21_only_lesson_day_and_limits(self):
        lid = self.open_lesson()
        self.ev("sidorova", lid, "-нет ДЗ", [self.studs[0]])
        r = self.ev("sidorova", lid, "-нет ДЗ", [self.studs[0], self.studs[1]])      # max_per_lesson=1
        self.assertEqual(len(r["created"]), 1)
        self.assertIn("Лимит", r["skipped"][0]["reason"])
        self.clock.t = at(9, 0) + DT.timedelta(days=1)
        self.ev("sidorova", lid, "+ответил", [self.studs[0]], expect=400)

    def test_22_exit_return_sequence(self):
        lid = self.open_lesson()
        s0 = self.studs[0]
        r = self.ev("sidorova", lid, "~вернулся", [s0])
        self.assertEqual(r["created"], [])
        self.ev("sidorova", lid, "~ушёл", [s0])
        r = self.ev("sidorova", lid, "~ушёл", [s0])
        self.assertEqual(r["created"], [])
        st = self.cl["sidorova"].call("GET", "/api/pulse/lesson/%d/events" % lid)
        self.assertEqual([x["state"] for x in st["students"]][:2], ["out", "in"])
        self.ev("sidorova", lid, "~вернулся", [s0])

    # ------------------------------------------------------------ длительность отсутствий
    def test_30_absence_pure(self):
        t = at(9, 0)
        m = lambda n: t + DT.timedelta(minutes=n)
        end = at(9, 15)
        # один выход
        r = pulse.compute_absence([(m(2), "exit"), (m(9), "return")], end)
        self.assertEqual([(i["minutes"], i["closed"]) for i in r], [(7, True)])
        # несколько выходов суммируются
        r = pulse.compute_absence([(m(1), "exit"), (m(4), "return"), (m(6), "exit"), (m(10), "return")], end)
        self.assertEqual(sum(i["minutes"] for i in r), 7)
        self.assertEqual(len(r), 2)
        # незакрытый выход — до конца урока
        r = pulse.compute_absence([(m(5), "exit")], end)
        self.assertEqual([(i["minutes"], i["closed"]) for i in r], [(10, False)])
        # возвращение без выхода игнорируется; выход после конца — 0
        self.assertEqual(pulse.compute_absence([(m(3), "return")], end), [])
        self.assertEqual(pulse.compute_absence([(m(20), "exit")], end)[0]["seconds"], 0)
        # события не по порядку
        r = pulse.compute_absence([(m(9), "return"), (m(2), "exit")], end)
        self.assertEqual(r[0]["minutes"], 7)

    def test_31_absence_api_closed_and_unclosed(self):
        lid = self.open_lesson()            # урок 1: 08:30–09:15
        s = self.cl["sidorova"]
        a, b = self.studs[0], self.studs[1]
        self.clock.t = at(8, 40)
        self.ev("sidorova", lid, "~ушёл", [a])
        self.clock.t = at(8, 47)
        self.ev("sidorova", lid, "~вернулся", [a])
        self.clock.t = at(9, 0)
        self.ev("sidorova", lid, "~ушёл", [b])
        # урок идёт: незакрытый выход считается до конца урока по звонку (09:15)
        r = s.call("GET", "/api/pulse/lesson/%d/events" % lid)
        by = dict((x["id"], x) for x in r["students"])
        self.assertEqual(by[a]["absence_min"], 7)
        self.assertEqual(by[b]["absence_min"], 15)
        # завершение урока без подтверждения → 409; с подтверждением и примерным возвращением 09:10
        s.call("POST", "/api/pulse/lesson/%d/end" % lid, {}, expect=409)
        self.clock.t = at(9, 12)
        s.call("POST", "/api/pulse/lesson/%d/end" % lid, {"fix_open": True, "return_time": "09:10"})
        r = s.call("GET", "/api/pulse/lesson/%d/events" % lid)
        by = dict((x["id"], x) for x in r["students"])
        self.assertEqual(by[b]["absence_min"], 10)
        self.assertEqual(by[b]["state"], "in")
        self.assertIsNotNone(r["lesson"]["ended_at"])
        # без примерного времени отсутствие фиксируется до момента завершения
        lid2 = self.open_lesson("kozlov", self.a_inf, 2)
        self.clock.t = at(9, 30)
        self.ev("kozlov", lid2, "~ушёл", [a])
        self.clock.t = at(9, 50)
        self.cl["kozlov"].call("POST", "/api/pulse/lesson/%d/end" % lid2, {"fix_open": True})
        r = self.cl["kozlov"].call("GET", "/api/pulse/lesson/%d/events" % lid2)
        self.assertEqual([x for x in r["students"] if x["id"] == a][0]["absence_min"], 20)

    def test_32_timeline(self):
        lid = self.open_lesson()
        a = self.studs[0]
        self.clock.t = at(8, 40)
        self.ev("sidorova", lid, "-опоздание", [a])
        self.clock.t = at(8, 50)
        self.ev("sidorova", lid, "~ушёл", [a])
        self.clock.t = at(8, 57)
        self.ev("sidorova", lid, "~вернулся", [a])
        self.ev("sidorova", lid, "+ответил", [a])
        r = self.cl["sidorova"].call("GET", "/api/pulse/student/%d/timeline?period=today" % a)
        texts = [i["text"] for i in r["items"]]
        self.assertEqual(texts, ["Опоздание на урок (отсутствие: 10 мин)", "Выход из класса (отсутствие: 7 мин)", "Активный устный ответ"])
        self.assertEqual(r["absence_total_min"], 7)

    # ------------------------------------------------------------ формула поведения
    def test_40_formula_boundaries(self):
        f = pulse.recommend
        c = dict(pulse.DEFAULT_CFG)
        self.assertEqual(f(0, 0, c)["value"], "уд.")
        self.assertEqual(f(4.9, 0, c)["value"], "уд.")
        self.assertEqual(f(5, 0, c)["value"], "прим.")
        self.assertEqual(f(10, 0, c)["value"], "прим.")          # выше «прим.» не бывает
        self.assertEqual(f(0, 2.5, c)["value"], "уд.")
        self.assertEqual(f(0, 3, c)["value"], "неуд.")
        self.assertEqual(f(0, 9, c)["value"], "неуд.")
        self.assertEqual(f(5, 3, c)["value"], "уд.")             # +1 −1
        self.assertEqual(f(10, 3, c)["value"], "прим.")
        self.assertEqual(f(0, 1.5 * 2, c)["value"], "неуд.")     # внеурочные 0,5 ×6 = 3
        c2 = dict(c, pos_step=3, neg_step=2, base="прим.")
        self.assertEqual(f(0, 2, c2)["value"], "уд.")
        self.assertEqual(f(2.9, 0, c2)["value"], "прим.")

    def test_41_config_api(self):
        self.cl["sidorova"].call("PUT", "/api/pulse/config", {"pos_step": 2}, expect=403)
        self.c.call("PUT", "/api/pulse/config", {"pos_step": 2, "neg_step": 2})
        try:
            self.assertEqual(self.cl["sidorova"].call("GET", "/api/pulse/config")["pos_step"], 2)
            self.c.call("PUT", "/api/pulse/config", {"pos_step": 0}, expect=400)
        finally:
            self.c.call("PUT", "/api/pulse/config", dict(pulse.DEFAULT_CFG))

    def test_42_integration_event_to_recommendation_to_sheet(self):
        lid = self.open_lesson()
        a = self.studs[0]
        for _ in range(5):
            self.ev("sidorova", lid, "+ответил", [a])
        k = self.cl["kozlov"]
        r = k.call("GET", "/api/pulse/behavior-recommendation/%d?period=quarter" % a)
        self.assertEqual((r["positive"], r["negative"], r["recommended"]), (5.0, 0.0, "прим."))
        # учитель-предметник рекомендацию не получает
        self.cl["sidorova"].call("GET", "/api/pulse/behavior-recommendation/%d" % a, expect=403)
        # без подтверждения запись в журнал невозможна
        k.call("POST", "/api/pulse/behavior-recommendation/%d/apply" % a, {"kind": r["kind"], "value": "прим."}, expect=400)
        self.assertIsNone(self.app.db.val("SELECT value FROM behavior WHERE student_id=?", (a,)))
        k.call("POST", "/api/pulse/behavior-recommendation/%d/apply" % a, {"kind": r["kind"], "value": "прим.", "confirm": True})
        self.assertEqual(self.app.db.val("SELECT value FROM behavior WHERE student_id=? AND kind=?", (a, r["kind"])), "прим.")
        beh = k.call("GET", "/api/behavior/%d" % self.cid)
        self.assertEqual(beh["values"]["%d_%s" % (a, r["kind"])], "прим.")
        # учитель-предметник отметку по поведению записать не может
        self.cl["sidorova"].call("POST", "/api/pulse/behavior-recommendation/%d/apply" % a, {"kind": r["kind"], "value": "уд.", "confirm": True}, expect=403)
        # негатив: 3 замечания → «неуд.», удаление события пересчитывает рекомендацию
        b = self.studs[1]
        ids = []
        for _ in range(3):
            ids += self.ev("sidorova", lid, "-поведение", [b])["created"]
        self.assertEqual(k.call("GET", "/api/pulse/behavior-recommendation/%d" % b)["recommended"], "неуд.")
        self.cl["sidorova"].call("DELETE", "/api/pulse/events/%d" % ids[0])
        self.assertEqual(k.call("GET", "/api/pulse/behavior-recommendation/%d" % b)["recommended"], "уд.")

    def test_43_red_flags_and_export(self):
        lid = self.open_lesson()
        a, b = self.studs[0], self.studs[1]
        for _ in range(3):
            self.ev("sidorova", lid, "-поведение", [a])
        self.clock.t = at(8, 35)
        self.ev("sidorova", lid, "~ушёл", [b])
        self.clock.t = at(9, 0)
        self.ev("sidorova", lid, "~вернулся", [b])
        fl = self.cl["kozlov"].call("GET", "/api/pulse/flags/week")["flags"]
        self.assertEqual(sorted(x["student_id"] for x in fl), sorted([a, b]))
        self.cl["sidorova"].call("GET", "/api/pulse/flags/week?class_id=%d" % self.cid2)
        raw = self.cl["kozlov"].call("GET", "/api/pulse/class/%d/summary?period=week&format=xlsx" % self.cid)
        self.assertTrue(raw[:2] == b"PK")

    # ------------------------------------------------------------ миграция
    def test_50_migration_6_to_7(self):
        path = os.path.join(tempfile.mkdtemp(), "old.db")
        d = DB.Database(path)
        d.ex("DROP TABLE pulse_events")
        d.ex("DROP TABLE pulse_types")
        d.ex("DROP TABLE pulse_events_archive")
        d.ex("DROP TABLE pulse_lesson_end")
        d.ex("UPDATE meta SET value='6' WHERE key='schema'")
        d.ex("INSERT INTO classes(grade,letter,year_id) SELECT 5,'А',NULL WHERE 0")
        d.close()
        d = DB.Database(path)
        self.assertEqual(d.val("SELECT value FROM meta WHERE key='schema'"), "7")
        for t in ("pulse_types", "pulse_events", "pulse_events_archive", "pulse_lesson_end"):
            self.assertIsNotNone(d.val("SELECT 1 FROM sqlite_master WHERE name=?", (t,)), t)
        d.close()

    # ------------------------------------------------------------ архив
    def test_60_archive(self):
        lid = self.open_lesson()
        old = (DT.date.fromisoformat(DAY) - DT.timedelta(days=366 * 3 + 5)).isoformat()
        tid = self.app.db.val("SELECT id FROM pulse_types WHERE code='+ответил'")
        self.app.db.ex("INSERT INTO pulse_events(student_id,class_id,type_id,date,event_time,created_by) VALUES(?,?,?,?,?,1)",
                       (self.studs[0], self.cid, tid, old, old + " 10:00:00"))
        self.ev("sidorova", lid, "+ответил", [self.studs[0]])
        self.cl["sidorova"].call("POST", "/api/pulse/archive", {}, expect=403)
        r = self.c.call("POST", "/api/pulse/archive", {})
        self.assertEqual(r["moved"], 1)
        info = self.c.call("GET", "/api/pulse/archive")
        self.assertEqual((info["events"], info["archived"]), (1, 1))
        self.assertEqual(self.c.call("POST", "/api/pulse/archive", {})["moved"], 0)
        self.app.db.ex("DELETE FROM pulse_events_archive")

    # ------------------------------------------------------------ киоск
    def _sse_read(self, path, stop_after=1.5, trigger=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", path)
        resp = conn.getresponse()
        buf = []
        done = threading.Event()

        def rd():
            try:
                while not done.is_set():
                    line = resp.fp.readline()
                    if not line:
                        break
                    buf.append(line.decode("utf-8"))
            except Exception:
                pass
        th = threading.Thread(target=rd)
        th.daemon = True
        th.start()
        time.sleep(0.4)
        if trigger:
            trigger()
        time.sleep(stop_after)
        done.set()
        conn.close()
        return "".join(buf)

    def test_70_kiosk_public_and_name_filter(self):
        lid = self.open_lesson()
        names_neg = ("Петров", "Сидоров")
        base = "/kiosk/pulse/class/%d" % self.cid
        html = Client(self.base).call("GET", base)
        self.assertIn(b"EventSource", html)
        # публичный доступ: без входа
        raw = self._sse_read(base + "/events", trigger=lambda: (
            self.ev("sidorova", lid, "+ответил", [self.studs[0]]),
            self.ev("sidorova", lid, "-поведение", [self.studs[1]]),
            self.ev("sidorova", lid, "~ушёл", [self.studs[2]]),
            self.ev("sidorova", lid, "-опоздание", [self.studs[2]])))
        self.assertIn("event: pulse", raw)
        self.assertIn("Иванов И.", raw)            # позитив — с именем
        for n in names_neg + ("Сидоров",):
            self.assertNotIn(n, raw)               # негатив и присутствие — без имён
        data = json.loads(Client(self.base).call("GET", base + "/data", expect=200).decode("utf-8")) if False else None
        import urllib.request
        data = json.loads(urllib.request.urlopen(self.base + base + "/data").read().decode("utf-8"))
        self.assertEqual(data["lessons"][0]["pos"], 1)
        self.assertEqual(data["lessons"][0]["neg"], 2)           # замечание + опоздание
        self.assertEqual(data["top"], [{"student": "Иванов И.", "count": 1}])
        self.assertNotIn("Петров", json.dumps(data, ensure_ascii=False))
        # отключённый модуль и отключённые киоски
        self.c.call("PUT", "/api/kiosk", {"enabled": False})
        try:
            with self.assertRaises(Exception):
                urllib.request.urlopen(self.base + base)
        finally:
            self.c.call("PUT", "/api/kiosk", {"enabled": True})

    def test_71_top3_order(self):
        lid = self.open_lesson()
        a, b, c3, d4 = self.studs[:4]
        for sid, n in ((a, 2), (b, 3), (c3, 1), (d4, 1)):
            for _ in range(n):
                self.ev("sidorova", lid, "+ответил", [sid])
        import urllib.request
        data = json.loads(urllib.request.urlopen(self.base + "/kiosk/pulse/class/%d/data" % self.cid).read().decode("utf-8"))
        self.assertEqual([t["student"] for t in data["top"]], ["Петров П.", "Иванов И.", "Сидоров С."])

    # ------------------------------------------------------------ функция
    def test_80_feature_toggle(self):
        self.c.call("PUT", "/api/features", {"pulse": False})
        try:
            self.cl["sidorova"].call("GET", "/api/pulse/event-types", expect=403)
            import urllib.request
            with self.assertRaises(Exception):
                urllib.request.urlopen(self.base + "/kiosk/pulse/class/%d/data" % self.cid)
        finally:
            self.c.call("PUT", "/api/features", {"pulse": True})

    # ------------------------------------------------------------ нагрузка
    def test_90_load_five_teachers(self):
        """5 учителей × 25 учащихся × 10 событий одновременно: без блокировок БД, уложиться в разумное время."""
        c = self.c
        cid = c.call("POST", "/api/classes", {"grade": 7, "letter": "А"})["id"]
        subs = c.call("GET", "/api/subjects")
        studs = [c.call("POST", "/api/students", {"class_id": cid, "last_name": "Уч%02d" % i, "first_name": "Имя"})["id"] for i in range(25)]
        clients, lids = [], []
        for i in range(5):
            st = c.call("POST", "/api/staff", {"last_name": "Нагр%d" % i, "first_name": "Т", "middle_name": "Т"})["id"]
            c.call("POST", "/api/assignments", {"class_id": cid, "subject_id": subs[i]["id"], "teacher_id": st, "hours": 1})
            aid = self.app.db.val("SELECT id FROM assignments WHERE class_id=? AND subject_id=? AND teacher_id=?", (cid, subs[i]["id"], st))
            c.call("POST", "/api/users", {"login": "load%d" % i, "password": "Load12345", "role": "teacher", "staff_ids": [st]})
            x = Client(self.base)
            x.call("POST", "/api/login", {"login": "load%d" % i, "password": "Load12345"})
            x.call("POST", "/api/me/password", {"old": "Load12345", "new": "Load12345x"})
            clients.append(x)
            lids.append(x.call("POST", "/api/pulse/lessons/open", {"assignment_id": aid, "num": 3 + i})["lesson_id"])
        errors, times = [], []

        def work(i):
            try:
                for n in range(10):
                    t0 = time.time()
                    code = ("+ответил", "-поведение", "+опорный конспект")[n % 3]
                    clients[i].call("POST", "/api/pulse/events", {"lesson_id": lids[i], "type_code": code, "student_ids": studs})
                    times.append(time.time() - t0)
            except Exception as e:   # noqa
                errors.append(str(e))
        ths = [threading.Thread(target=work, args=(i,)) for i in range(5)]
        t0 = time.time()
        for t in ths:
            t.start()
        for t in ths:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(self.app.db.val("SELECT COUNT(*) FROM pulse_events WHERE class_id=?", (cid,)), 5 * 25 * 10)
        self.assertLess(max(times), 2.0)
        self.assertLess(sum(times) / len(times), 0.5)
        # одно нажатие на одного ученика — быстрее 200 мс
        t0 = time.time()
        clients[0].call("POST", "/api/pulse/events", {"lesson_id": lids[0], "type_code": "+ответил", "student_ids": [studs[0]]})
        self.assertLess(time.time() - t0, 0.2)


def ui_login(page, base, login, pw):
    """Вход через форму; ждём, пока форма отрисована окончательно (иначе введённое может сброситься перерисовкой)."""
    for _ in range(3):
        page.goto(base + "/")
        page.wait_for_selector("input[type=password]")
        page.wait_for_load_state("networkidle")
        page.fill("input[autocomplete=username]", login)
        page.fill("input[type=password]", pw)
        page.keyboard.press("Enter")
        try:
            page.wait_for_selector(".nav", timeout=4000)
            return
        except Exception:
            pass
    raise AssertionError("вход не удался: " + page.inner_text("body")[:200])


try:
    from playwright.sync_api import sync_playwright
except ImportError:      # pragma: no cover
    sync_playwright = None


@unittest.skipIf(sync_playwright is None, "playwright не установлен")
class PulseUITest(unittest.TestCase):
    """Мобильный экран ввода: размеры касаний >= 44x44, один тап = одно событие, «вышел/вернулся», завершение урока."""
    @classmethod
    def setUpClass(cls):
        PulseTest.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        PulseTest.tearDownClass.__func__(cls)

    def setUp(self):
        self.app.db.ex("DELETE FROM pulse_events")
        self.app.db.ex("DELETE FROM pulse_lesson_end")
        self.clock.t = at(8, 50)

    def _run(self, device):
        errors = []
        with sync_playwright() as pw:
            br = pw.chromium.launch()
            ctx = br.new_context(**(pw.devices[device] if device in pw.devices else {"viewport": {"width": 375, "height": 667}, "has_touch": True}))
            page = ctx.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            ui_login(page, self.base, "sidorova", "Sidorova123x")
            page.goto(self.base + "/#/pulse")
            page.wait_for_selector(".pl-lessons .pl-btn", timeout=8000)
            page.click(".pl-lessons .pl-btn >> nth=0")
            page.wait_for_selector(".pl-tile", timeout=8000)
            self.assertEqual(page.locator(".pl-tile").count(), 5)
            first = page.locator(".pl-tile").nth(0)
            first.click()
            page.wait_for_selector(".pl-panel .pl-btn")
            # все интерактивные элементы экрана >= 44x44
            small = page.evaluate("""() => Array.from(document.querySelectorAll('.pl-tile, .pl-panel .pl-btn, .pl-head .pl-btn, .pl-lessons .pl-btn'))
                .map(e => { const r = e.getBoundingClientRect(); return [e.textContent.trim().slice(0, 20), r.width, r.height]; })
                .filter(x => x[1] < 44 || x[2] < 44)""")
            self.assertEqual(small, [], device)
            per_row = page.evaluate("""() => { const t = Array.from(document.querySelectorAll('.pl-tile')); const y = t[0].getBoundingClientRect().top;
                return t.filter(e => Math.abs(e.getBoundingClientRect().top - y) < 2).length; }""")
            self.assertGreaterEqual(per_row, 4)
            self.assertLessEqual(per_row, 6)
            # один тап по кнопке типа = одно событие
            page.locator(".pl-panel .pl-btn", has_text="Активный устный ответ").click()
            page.wait_for_timeout(400)
            self.assertEqual(self.app.db.val("SELECT COUNT(*) FROM pulse_events"), 1)
            # вышел → плитка полупрозрачна с 🚪, панель — одна кнопка «Вернулся»
            page.locator(".pl-tile").nth(0).click() if page.locator(".pl-panel").count() == 0 else None
            page.locator(".pl-panel .pl-btn", has_text="Вышел из класса").click()
            page.wait_for_selector(".pl-tile.out")
            self.assertIn("🚪", page.locator(".pl-tile.out").inner_text())
            page.wait_for_selector(".pl-panel .pl-btn.big")      # выбранный ушедший: вместо панели одна кнопка «Вернулся»
            self.assertEqual(page.locator(".pl-panel .pl-btn").count(), 1)
            # завершить урок → окно подтверждения
            page.locator("text=Завершить урок").click()
            page.wait_for_selector(".modal")
            self.assertIn("не отмечено возвращение", page.locator(".modal").inner_text())
            self.assertIn("Отмена, я исправлю", page.locator(".modal").inner_text())
            page.locator("text=Отмена, я исправлю").click()
            # ↶ отменяет последнее действие
            n0 = self.app.db.val("SELECT COUNT(*) FROM pulse_events")
            page.locator("button[aria-label]").click()
            page.wait_for_timeout(400)
            self.assertEqual(self.app.db.val("SELECT COUNT(*) FROM pulse_events"), n0 - 1)
            br.close()
        self.assertEqual(errors, [])

    def test_phone(self):
        self._run("iPhone SE")

    def test_tablet(self):
        self._run("iPad (gen 7)")

    def test_desktop_tabs_no_errors(self):
        errors = []
        with sync_playwright() as pw:
            br = pw.chromium.launch()
            page = br.new_page(viewport={"width": 1280, "height": 800})
            page.on("pageerror", lambda e: errors.append(str(e)))
            ui_login(page, self.base, "admin", "Secret123")
            for tab in ("summary", "flags", "overview", "settings"):
                page.goto(self.base + "/#/pulse?tab=" + tab)
                page.wait_for_selector(".pl-tabs")
                page.wait_for_timeout(500)
            page.screenshot(path=os.path.join(tempfile.gettempdir(), "pulse_settings.png"))
            br.close()
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
