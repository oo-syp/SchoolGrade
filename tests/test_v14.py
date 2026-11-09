# -*- coding: utf-8 -*-
"""Тесты версии 1.4: отключение функций, причины пропусков, журнал ФЗ/ОпИ в каникулы, замены,
расписание (чётные/нечётные недели), прогноз, мониторинг, анализ, кабинет классного руководителя."""
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from schoolgrade import db as DB, main as M, server as S  # noqa: E402
from test_v13 import Client  # noqa: E402


class V14Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.app = M.init_app(cls.tmp)
        cls.srv = S.ServerThread(cls.app, "127.0.0.1", 0)
        cls.srv.start()
        cls.base = "http://127.0.0.1:%d" % cls.srv.httpd.server_address[1]
        c = Client(cls.base)
        c.call("POST", "/api/login", {"login": "admin", "password": "admin"})
        c.call("POST", "/api/me/password", {"old": "admin", "new": "Secret123"})
        c.call("PUT", "/api/org_type", {"org_type": "secondary"})
        c.call("POST", "/api/years", {"start_year": 2026})
        cls.cid = c.call("POST", "/api/classes", {"grade": 6, "letter": "А"})["id"]
        cls.cid2 = c.call("POST", "/api/classes", {"grade": 6, "letter": "Б"})["id"]
        cls.s1 = c.call("POST", "/api/students", {"class_id": cls.cid, "last_name": "Иванов", "first_name": "Иван"})["id"]
        cls.s2 = c.call("POST", "/api/students", {"class_id": cls.cid, "last_name": "Петров", "first_name": "Пётр"})["id"]
        cls.t1 = c.call("POST", "/api/staff", {"last_name": "Сидорова", "first_name": "Анна", "middle_name": "Ивановна"})["id"]
        cls.t2 = c.call("POST", "/api/staff", {"last_name": "Козлов", "first_name": "Олег", "middle_name": "Петрович"})["id"]
        subs = dict((s["name"], s["id"]) for s in c.call("GET", "/api/subjects"))
        db = cls.app.db
        c.call("POST", "/api/assignments", {"class_id": cls.cid, "subject_id": subs["Математика"], "teacher_id": cls.t1, "hours": 5})
        cls.aid = db.val("SELECT id FROM assignments WHERE class_id=? AND subject_id=?", (cls.cid, subs["Математика"]))
        c.call("POST", "/api/assignments", {"class_id": cls.cid2, "subject_id": subs["Математика"], "teacher_id": cls.t1, "hours": 5})
        cls.aid2 = db.val("SELECT id FROM assignments WHERE class_id=? AND subject_id=?", (cls.cid2, subs["Математика"]))
        c.call("POST", "/api/assignments", {"class_id": cls.cid, "subject_id": subs["Информатика"], "teacher_id": cls.t2, "hours": 1})
        cls.aid_inf = db.val("SELECT id FROM assignments WHERE class_id=? AND subject_id=?", (cls.cid, subs["Информатика"]))
        fz = c.call("POST", "/api/subjects", {"name": "Юный математик", "category": "fz"})["id"]
        c.call("POST", "/api/assignments", {"class_id": cls.cid, "subject_id": fz, "teacher_id": cls.t1, "hours": 1})
        cls.aid_fz = db.val("SELECT id FROM assignments WHERE class_id=? AND subject_id=?", (cls.cid, fz))
        c.call("POST", "/api/users", {"login": "kozlov", "password": "Kozlov123", "role": "teacher", "staff_ids": [cls.t2]})
        cls.c = c
        t = Client(cls.base)
        t.call("POST", "/api/login", {"login": "kozlov", "password": "Kozlov123"})
        t.call("POST", "/api/me/password", {"old": "Kozlov123", "new": "Kozlov456"})
        cls.t = t

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()
        cls.app.db.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def lesson(self, aid, date, client=None, expect=200):
        r = (client or self.c).call("POST", "/api/journal/%d/lessons" % aid, {"date": date, "topic": "Т"}, expect=expect)
        return r.get("id") if isinstance(r, dict) else None

    # ---------------------------------------------------------------- функции
    def test_01_features(self):
        me = self.c.call("GET", "/api/me")
        self.assertIn("feature_list", me)
        self.c.call("PUT", "/api/features", {"analysis": False})
        try:
            self.t.call("GET", "/api/reports/analysis", expect=403)
            self.assertFalse(self.t.call("GET", "/api/me")["features"]["analysis"])
        finally:
            self.c.call("PUT", "/api/features", {"analysis": True})
        self.t.call("GET", "/api/reports/analysis")
        self.t.call("PUT", "/api/features", {"analysis": False}, expect=403)   # только администратор

    def test_02_update_check_off(self):
        self.c.call("PUT", "/api/update_check", {"enabled": False})
        r = self.c.call("GET", "/api/update_check")
        self.assertFalse(r["enabled"])
        self.assertIn("github.com/oo-syp/SchoolGrade/releases/latest", r["url"])

    # ---------------------------------------------------------------- причины пропусков
    def test_03_absence_reasons(self):
        lid = self.lesson(self.aid, "2026-11-10")
        r = self.c.call("POST", "/api/marks", {"items": [{"lesson_id": lid, "student_id": self.s1, "value": "нб"},
                                                         {"lesson_id": lid, "student_id": self.s2, "value": "н"}]})
        self.assertEqual([x["value"] for x in r["saved"]] if "saved" in r else None, ["нб", "н"])
        J = self.c.call("GET", "/api/journal/%d?period=q2" % self.aid)
        self.assertEqual(J["marks"]["%d_%d" % (lid, self.s1)], "нб")
        # причина на день от классного руководителя перекрывает причину учителя
        self.c.call("PUT", "/api/classroom/%d/absences" % self.cid, {"student_id": self.s1, "date": "2026-11-10", "reason": "у", "note": "заявление"})
        self.c.call("PUT", "/api/classroom/%d/absences" % self.cid, {"student_id": self.s2, "date": "2026-11-10", "reason": "н"})
        J = self.c.call("GET", "/api/journal/%d?period=q2" % self.aid)
        self.assertEqual(J["marks"]["%d_%d" % (lid, self.s1)], "ну")
        self.assertEqual(J["marks"]["%d_%d" % (lid, self.s2)], "нн")
        A = self.c.call("GET", "/api/classroom/%d/absences?month=2026-11" % self.cid)
        self.assertEqual(A["counts"]["%d_2026-11-10" % self.s1]["n"], 1)
        self.assertEqual(A["days"]["%d_2026-11-10" % self.s1]["note"], "заявление")
        rep = self.c.call("GET", "/api/reports/absences?class_id=%d&from=2026-11-01&to=2026-11-30" % self.cid)
        self.assertTrue(rep["rows"])
        # примечание хранится зашифрованным
        raw = self.app.db.val("SELECT note FROM absence_days WHERE student_id=?", (self.s1,))
        self.assertNotEqual(raw, "заявление")

    # ---------------------------------------------------------------- ФЗ/ОпИ в каникулы
    def test_04_club_holidays(self):
        lid = self.lesson(self.aid_fz, "2026-11-02")      # осенние каникулы (вне четвертей)
        J = self.c.call("GET", "/api/journal/%d?month=2026-11" % self.aid_fz)
        self.assertIn(lid, [l["id"] for l in J["lessons"]])
        self.assertEqual(J["month"], "2026-11")
        lid2 = self.lesson(self.aid_fz, "2027-07-05")     # летом
        J = self.c.call("GET", "/api/journal/%d?month=2027-07" % self.aid_fz)
        self.assertEqual([l["id"] for l in J["lessons"]], [lid2])

    # ---------------------------------------------------------------- замены
    def test_05_substitution_access(self):
        lid = self.lesson(self.aid, "2026-11-12")
        # Козлов не ведёт математику — доступа нет
        self.t.call("POST", "/api/marks", {"items": [{"lesson_id": lid, "student_id": self.s1, "value": "8"}]}, expect=403)
        self.c.call("POST", "/api/subs", {"date": "2026-11-12", "absent_id": self.t1, "reason": "болезнь",
                                          "items": [{"lesson_no": 2, "assignment_id": self.aid, "substitute_id": self.t2}]})
        self.t.call("POST", "/api/marks", {"items": [{"lesson_id": lid, "student_id": self.s1, "value": "8"}]})
        J = self.t.call("GET", "/api/journal/%d?period=q2" % self.aid)
        self.assertEqual(J["sub_dates"], ["2026-11-12"])
        # другая дата — нет доступа
        other = self.lesson(self.aid, "2026-11-13")
        self.t.call("POST", "/api/marks", {"items": [{"lesson_id": other, "student_id": self.s1, "value": "8"}]}, expect=403)
        self.lesson(self.aid, "2026-11-13", client=self.t, expect=403)
        subs = self.t.call("GET", "/api/subs?from=2026-11-01&to=2026-11-30")
        self.assertEqual(len(subs["items"]), 1)
        rep = self.c.call("GET", "/api/reports/subs?from=2026-11-01&to=2026-11-30")
        self.assertTrue(any("Козлов" in str(r) for r in rep["rows"]))

    # ---------------------------------------------------------------- расписание
    def test_06_schedule(self):
        y = self.c.call("GET", "/api/me")["current_year"]["id"]
        sid = self.c.call("POST", "/api/schedule", {"year_id": y, "name": "II четверть", "start_date": "2026-11-09", "weeks": 2})["id"]
        items = [{"week": 1, "weekday": 1, "lesson_no": 1, "assignment_id": self.aid, "room": "21"},
                 {"week": 2, "weekday": 2, "lesson_no": 3, "assignment_id": self.aid, "room": "21"},
                 {"week": 1, "weekday": 1, "lesson_no": 2, "assignment_id": self.aid_inf, "room": "5"}]
        r = self.c.call("PUT", "/api/schedule/%d/class/%d" % (sid, self.cid), {"items": items})
        self.assertEqual(r["conflicts"], [])
        # 6Б: тот же учитель в то же время — пересечение
        r = self.c.call("PUT", "/api/schedule/%d/class/%d" % (sid, self.cid2),
                        {"items": [{"week": 1, "weekday": 1, "lesson_no": 1, "assignment_id": self.aid2}]})
        self.assertEqual(len(r["conflicts"]), 1)
        self.c.call("PUT", "/api/schedule/%d/class/%d" % (sid, self.cid2), {"items": []})
        d1 = self.c.call("GET", "/api/schedule/day?date=2026-11-09&class_id=%d" % self.cid)   # пн, 1-я неделя
        self.assertEqual(d1["week"], 1)
        self.assertEqual([l["lesson_no"] for l in d1["lessons"]], [1, 2])
        d2 = self.c.call("GET", "/api/schedule/day?date=2026-11-16&class_id=%d" % self.cid)   # пн, 2-я неделя
        self.assertEqual(d2["lessons"], [])
        d3 = self.c.call("GET", "/api/schedule/day?date=2026-11-17&class_id=%d" % self.cid)   # вт, 2-я неделя
        self.assertEqual([l["lesson_no"] for l in d3["lessons"]], [3])
        # план замены: уроки учителя и свободные учителя
        P = self.c.call("GET", "/api/subs/plan?date=2026-11-09&staff_id=%d" % self.t1)
        self.assertEqual([l["lesson_no"] for l in P["lessons"]], [1])
        self.assertIn(self.t2, [f["id"] for f in P["lessons"][0]["free"]])
        self.c.call("PUT", "/api/schedule/bells", {"bells": {"1": ["08:30", "09:15"]}})
        self.assertEqual(self.c.call("GET", "/api/schedule")["bells"], {"1": ["08:30", "09:15"]})
        T = self.c.call("GET", "/api/schedule/%d/teachers" % sid)
        self.assertEqual(sum(t["hours"] for t in T["teachers"]), 3)
        # уроки из КТП по расписанию (чередование недель)
        self.c.call("PUT", "/api/assignments/%d/ktp" % self.aid, {"items": [{"topic": "Тема %d" % i, "hours": 1} for i in range(1, 5)]})
        pv = self.c.call("POST", "/api/assignments/%d/ktp/schedule" % self.aid,
                         {"mode": "lessons", "use_schedule": True, "start": "2026-11-18", "end": "2026-12-01", "dry_run": True})
        self.assertEqual([s["date"] for s in pv["slots"]], ["2026-11-23", "2026-12-01"])

    # ---------------------------------------------------------------- отчёты
    def test_08_risk_monitor_analysis(self):
        y = self.c.call("GET", "/api/me")["current_year"]["id"]
        r = self.c.call("GET", "/api/reports/risk?year_id=%d&kind=q2&all=1" % y)
        self.assertIn("rows", r)
        m = self.c.call("GET", "/api/reports/monitor?year_id=%d&from=2026-11-01&to=2026-11-30&all=1" % y)
        self.assertIn("rows", m)
        self.t.call("GET", "/api/reports/monitor?year_id=%d" % y, expect=403)
        db = self.app.db
        db.ex("INSERT INTO final_marks(assignment_id,student_id,kind,value) VALUES(?,?,?,?)", (self.aid, self.s1, "q1", "8"))
        db.ex("INSERT INTO final_marks(assignment_id,student_id,kind,value) VALUES(?,?,?,?)", (self.aid, self.s2, "q1", "5"))
        a = self.c.call("GET", "/api/reports/analysis?group=parallel")
        self.assertEqual(a["rows"][0]["name"], "6 классы")
        self.assertEqual(a["rows"][0]["values"][0]["avg"], 6.5)
        self.assertEqual(a["rows"][0]["values"][0]["kz"], 50.0)
        x = self.c.call("GET", "/api/reports/analysis?group=teacher&format=xlsx")
        self.assertEqual(x[:2], b"PK")

    # ---------------------------------------------------------------- кабинет классного руководителя
    def test_09_classroom(self):
        base = "/api/classroom/%d" % self.cid
        self.c.call("PUT", base + "/student/%d" % self.s1, {
            "parents": [{"relation": "мать", "fio": "Иванова Мария", "phone": "+375291112233", "work": "школа"}],
            "social": ["many", "bogus"], "characteristic": "Иван — старательный ученик.\n\nУчаствует в олимпиадах.",
            "activities": [{"name": "Плавание", "place": "ДЮСШ"}]})
        C = self.c.call("GET", base)
        st = [s for s in C["students"] if s["id"] == self.s1][0]
        self.assertEqual(st["parents"][0]["phone"], "+375291112233")
        self.assertEqual(st["social"], ["many"])
        self.assertIn("Юный математик", [x["name"] for x in st["clubs"]])
        self.assertEqual(st["activities"][0]["name"], "Плавание")
        self.assertNotEqual(self.app.db.val("SELECT phone FROM parents"), "+375291112233")   # зашифровано
        soc = self.c.call("GET", base + "/social")
        self.assertTrue(any("Многодетн" in str(r) for r in soc["rows"]))
        self.assertTrue(any("Иванова" in str(r) for r in self.c.call("GET", base + "/contacts")["rows"]))
        self.assertTrue(any("Плавание" in str(r) for r in self.c.call("GET", base + "/activities")["rows"]))
        doc = self.c.call("GET", base + "/characteristic/%d" % self.s1)
        self.assertEqual(doc[:2], b"PK")
        import io
        import zipfile
        xml = zipfile.ZipFile(io.BytesIO(doc)).read("word/document.xml").decode("utf-8")
        self.assertIn("старательный", xml)
        # учитель без классного руководства — нет доступа
        self.t.call("GET", base, expect=403)


class MigrationTest(unittest.TestCase):
    def test_old_db(self):
        """База версии 1.3 открывается и дополняется новыми таблицами и столбцами."""
        tmp = tempfile.mkdtemp()
        try:
            app = M.init_app(tmp)
            path = app.db.path
            app.db.close()
            con = sqlite3.connect(path)
            for t in ("absence_days", "schedules", "schedule_items", "substitutions", "parents", "student_social", "characteristics", "activities"):
                con.execute("DROP TABLE IF EXISTS " + t)
            con.execute("ALTER TABLE lessons DROP COLUMN sub_id") if sqlite3.sqlite_version_info >= (3, 35) else None
            con.execute("UPDATE meta SET value='4' WHERE key='schema'")
            con.commit()
            con.close()
            d = DB.Database(path)
            cols = [r["name"] for r in d.q("PRAGMA table_info(lessons)")]
            self.assertIn("sub_id", cols)
            self.assertIn("reason", [r["name"] for r in d.q("PRAGMA table_info(marks)")])
            self.assertTrue(d.val("SELECT name FROM sqlite_master WHERE name='substitutions'"))
            d.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
