# -*- coding: utf-8 -*-
"""Тесты версии 1.3: КТП — домашнее задание, расстановка дат, уроки в журнал по расписанию,
отчёт «Выполнение программы»."""
import http.cookiejar
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from schoolgrade import ktp, main as M, server as S  # noqa: E402


class Client(object):
    def __init__(self, base):
        self.base = base
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
                                              urllib.request.ProxyHandler({}))

    def call(self, method, path, data=None, expect=200):
        rq = urllib.request.Request(self.base + path, data=json.dumps(data).encode() if data is not None else None, method=method)
        rq.add_header("X-SchoolGrade", "1")
        rq.add_header("Content-Type", "application/json")
        try:
            r = self.op.open(rq, timeout=20)
            st, body = r.status, r.read()
        except urllib.error.HTTPError as e:
            st, body = e.code, e.read()
        if st != expect:
            raise AssertionError("%s %s -> %s %s" % (method, path, st, body[:300].decode("utf-8", "replace")))
        return json.loads(body.decode("utf-8")) if body[:1] in (b"{", b"[") else body


class KtpV13Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.app = M.init_app(cls.tmp)
        cls.srv = S.ServerThread(cls.app, "127.0.0.1", 0)
        cls.srv.start()
        c = Client("http://127.0.0.1:%d" % cls.srv.httpd.server_address[1])
        c.call("POST", "/api/login", {"login": "admin", "password": "admin"})
        c.call("POST", "/api/me/password", {"old": "admin", "new": "Secret123"})
        c.call("PUT", "/api/org_type", {"org_type": "secondary"})
        c.call("POST", "/api/years", {"start_year": 2026})
        cid = c.call("POST", "/api/classes", {"grade": 6, "letter": "А"})["id"]
        c.call("POST", "/api/students", {"class_id": cid, "last_name": "Иванов", "first_name": "Иван"})
        subj = [s for s in c.call("GET", "/api/subjects") if s["name"] == "Информатика"][0]
        c.call("POST", "/api/assignments", {"class_id": cid, "subject_id": subj["id"]})
        cls.aid = cls.app.db.val("SELECT id FROM assignments WHERE class_id=?", (cid,))
        cls.c = c

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()
        cls.app.db.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def put_ktp(self, items):
        return self.c.call("PUT", "/api/assignments/%d/ktp" % self.aid, {"items": items})

    def test_1_progress_without_dates(self):
        # КТП без плановых дат: отчёт «Выполнение программы» раньше падал ('int' object has no attribute 'replace')
        self.put_ktp([{"topic": "Тема 1", "hours": 1}, {"topic": "Тема 2", "hours": 2}])
        rep = self.c.call("GET", "/api/reports/ktp_progress")
        row = [r for r in rep["rows"] if r[0] != "ИТОГО"][0]
        self.assertEqual(row[4], "3")
        self.c.call("PUT", "/api/assignments/%d/ktp" % self.aid, {"items": [{"topic": "Т", "hours": 1, "planned_date": "2099-01-01"}]})
        rep = self.c.call("GET", "/api/reports/ktp_progress")       # даты есть, но все в будущем: план 0
        self.assertEqual([r for r in rep["rows"] if r[0] != "ИТОГО"][0][5], "0")

    def test_2_parse_homework(self):
        text = "№\tТема урока\tКол-во часов\tДата\tДомашнее задание\n1\tТекстовый редактор\t1\t10.11\t§ 5, упр. 3\n2\tФорматирование\t2\t17.11\t§ 6\n"
        items, notes = ktp.parse(text=text, start_year=2026)
        self.assertEqual([i["homework"] for i in items], ["§ 5, упр. 3", "§ 6"])
        self.assertIn("домашнее задание", notes[0])

    def test_3_schedule(self):
        K = self.put_ktp([{"topic": "Введение", "hours": 1, "homework": "§1"},
                          {"topic": "Практикум", "hours": 2, "homework": "§2"},
                          {"topic": "Итоги", "hours": 1, "homework": ""}])
        base = {"days": {"2": 1, "4": 1}, "start": "2026-11-09", "holidays": "12.11"}
        # плановые даты: вт 10.11, чт 12.11 — праздник, вт 17.11, чт 19.11
        r = self.c.call("POST", "/api/assignments/%d/ktp/schedule" % self.aid, dict(base, mode="dates"))
        self.assertEqual(r["created"], 4)
        K = self.c.call("GET", "/api/assignments/%d/ktp" % self.aid)
        self.assertEqual([i["planned_date"] for i in K["items"]], ["2026-11-10", "2026-11-17", "2026-11-24"])
        # уроки в журнал: предпросмотр, затем создание
        pv = self.c.call("POST", "/api/assignments/%d/ktp/schedule" % self.aid, dict(base, mode="lessons", dry_run=True))
        self.assertEqual([s["date"] for s in pv["slots"]], ["2026-11-10", "2026-11-17", "2026-11-19", "2026-11-24"])
        r = self.c.call("POST", "/api/assignments/%d/ktp/schedule" % self.aid, dict(base, mode="lessons"))
        self.assertEqual(r["created"], 4)
        J = self.app.db.q("SELECT date, topic, homework, ktp_id FROM lessons WHERE assignment_id=? ORDER BY date", (self.aid,))
        self.assertEqual([(l["topic"], l["homework"]) for l in J],
                         [("Введение", "§1"), ("Практикум", "§2"), ("Практикум", "§2"), ("Итоги", "")])
        # повторно — все темы уже проведены, дублей нет
        pv = self.c.call("POST", "/api/assignments/%d/ktp/schedule" % self.aid, dict(base, mode="lessons", dry_run=True))
        self.assertEqual(pv["slots"], [])
        # урок из КТП вручную: домашнее задание подставляется
        kid = K["items"][0]["id"]
        lid = self.c.call("POST", "/api/journal/%d/lessons" % self.aid, {"date": "2026-12-01", "ktp_id": kid})["id"]
        self.assertEqual(self.app.db.val("SELECT homework FROM lessons WHERE id=?", (lid,)), "§1")
        # каникулы (вне четвертей) пропускаются
        pv = self.c.call("POST", "/api/assignments/%d/ktp/schedule" % self.aid,
                         {"mode": "dates", "days": {"1": 1}, "start": "2026-10-26", "dry_run": True})
        self.assertNotIn("2026-11-02", [s["date"] for s in pv["slots"]])


if __name__ == "__main__":
    unittest.main(verbosity=2)
