# -*- coding: utf-8 -*-
"""Тесты правил оценивания и показателей. Запуск: python -m unittest discover tests"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from schoolgrade import grading as G  # noqa: E402
from schoolgrade import xlsx  # noqa: E402


class TestNormalize(unittest.TestCase):
    def test_points(self):
        self.assertEqual(G.normalize(" 7 "), "7")
        self.assertEqual(G.normalize("10"), "10")
        for bad in ("0", "11", "-1", "7.5", "abc"):
            with self.assertRaises(ValueError):
                G.normalize(bad)

    def test_specials(self):
        self.assertEqual(G.normalize("зач"), "зач.")
        self.assertEqual(G.normalize("НЕЗАЧ."), "незач.")
        self.assertEqual(G.normalize("осв"), "осв.")
        self.assertEqual(G.normalize("на"), "н/а")
        self.assertEqual(G.normalize("н/и"), "н/и")
        self.assertEqual(G.normalize("н", "current"), "н")
        with self.assertRaises(ValueError):
            G.normalize("н/а", "current")   # н/а — только итоговая
        with self.assertRaises(ValueError):
            G.normalize("н", "final")

    def test_behavior(self):
        self.assertEqual(G.normalize("прим", "behavior"), "прим.")
        self.assertEqual(G.normalize("неуд.", "behavior"), "неуд.")
        with self.assertRaises(ValueError):
            G.normalize("5", "behavior")


class TestRounding(unittest.TestCase):
    def test_half_up(self):
        # Python round(6.5) == 6 («банковское»), а по правилам аттестации — 7
        self.assertEqual(G.round_math(6.5), 7)
        self.assertEqual(G.round_math(7.5), 8)
        self.assertEqual(G.round_math(6.49), 6)
        self.assertEqual(G.round_math(0.5), 1)


class TestPeriodMark(unittest.TestCase):
    def test_average(self):
        r = G.suggest_period_mark(["7", "8", "н", "6", "9"], hours_per_week=3)
        self.assertEqual(r["value"], "8")  # 7.5 -> 8
        self.assertEqual(r["count"], 4)

    def test_minimum(self):
        r = G.suggest_period_mark(["9", "8"], hours_per_week=3)
        self.assertEqual(r["value"], "н/а")
        r = G.suggest_period_mark(["9"], hours_per_week=1)
        self.assertEqual(r["value"], "9")

    def test_pass(self):
        self.assertEqual(G.suggest_period_mark(["зач."])["value"], "зач.")
        self.assertEqual(G.suggest_period_mark([])["value"], "")


class TestYearMark(unittest.TestCase):
    def test_simple(self):
        r = G.suggest_year_mark([{"value": "7"}, {"value": "8"}, {"value": "8"}, {"value": "8"}])
        self.assertEqual(r["value"], "8")  # 7.75

    def test_excused_na_skipped(self):
        r = G.suggest_year_mark([{"value": "н/а", "excused": 1}, {"value": "6"}, {"value": "7"}, {"value": "7"}])
        self.assertEqual(r["value"], "7")  # 6.67

    def test_unexcused_na_zero(self):
        r = G.suggest_year_mark([{"value": "н/а"}, {"value": "6"}, {"value": "7"}, {"value": "7"}])
        self.assertEqual(r["value"], "5")  # (0+6+7+7)/4 = 5
        r = G.suggest_year_mark([{"value": "н/а"}, {"value": "6"}, {"value": "7"}, {"value": "7"}],
                                {"unexcused_na_as_zero": False})
        self.assertEqual(r["value"], "7")

    def test_exempt(self):
        self.assertEqual(G.suggest_year_mark([{"value": "осв."}] * 4)["value"], "осв.")
        self.assertEqual(G.suggest_year_mark([{"value": "зач."}] * 4)["value"], "зач.")


class TestStats(unittest.TestCase):
    def test_stats(self):
        st = G.stats(["10", "9", "6", "5", "2", "н/а", "осв."])
        self.assertEqual(st["count"], 5)
        self.assertEqual(st["avg"], 6.4)
        self.assertEqual(st["kz"], 60.0)                       # 10, 9, 6 из 5
        self.assertEqual(st["sou"], round((100 + 96 + 55 + 45 + 20) / 5.0, 1))
        self.assertEqual(st["levels"]["high"], 2)
        self.assertEqual(st["levels"]["low"], 1)
        self.assertEqual(st["other"]["н/а"], 1)

    def test_kz_threshold(self):
        self.assertEqual(G.stats(["6", "7"], {"kz_min_point": 7})["kz"], 50.0)

    def test_level(self):
        self.assertEqual(G.student_level(["9", "10", "7"]), "suf")
        self.assertEqual(G.student_level(["зач."]), None)


class TestXlsx(unittest.TestCase):
    def test_roundtrip(self):
        data = xlsx.build([{"name": "Лист", "title": "Тест", "headers": ["Фамилия", "Имя", "Балл"],
                            "rows": [["Иванов", "Иван", 9], ["Петрова", "Анна", "н/а"]]}])
        rows = xlsx.read_first_sheet(data)
        self.assertEqual(rows[0][0], "Тест")
        self.assertIn(["Иванов", "Иван", "9"], rows)


class TestPasteList(unittest.TestCase):
    def test_split(self):
        from schoolgrade.api import _split_fio_lines
        r = _split_fio_lines("1. Гнедько Дарья Сергеевна\n\n2)  Мамедова\tСурая-Наргиз\nИванов Иван Иванович Оглы\n")
        self.assertEqual(len(r), 3)
        self.assertEqual(r[0], {"last_name": "Гнедько", "first_name": "Дарья", "middle_name": "Сергеевна"})
        self.assertEqual(r[1]["first_name"], "Сурая-Наргиз")
        self.assertEqual(r[1]["middle_name"], "")
        self.assertEqual(r[2]["middle_name"], "Иванович Оглы")


class TestApiPermissions(unittest.TestCase):
    """Проверка разграничения доступа через реальный HTTP-сервер."""
    @classmethod
    def setUpClass(cls):
        import json
        import threading
        from schoolgrade.main import init_app
        from schoolgrade.server import ServerThread
        cls.tmp = tempfile.mkdtemp()
        cls.app = init_app(cls.tmp)
        cls.srv = ServerThread(cls.app, "127.0.0.1", 0)
        cls.port = cls.srv.httpd.server_address[1]
        cls.srv.start()
        cls.json = json

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()
        cls.app.db.close()

    def client(self):
        import http.cookiejar
        import urllib.request
        cj = http.cookiejar.CookieJar()
        # ProxyHandler({}) — к локальному серверу всегда напрямую, мимо системного прокси
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(cj))
        base = "http://127.0.0.1:%d" % self.port

        def call(method, path, data=None):
            req = urllib.request.Request(base + path, method=method,
                                         data=self.json.dumps(data).encode() if data is not None else None,
                                         headers={"X-SchoolGrade": "1", "Content-Type": "application/json"})
            try:
                return 200, self.json.loads(op.open(req).read())
            except urllib.error.HTTPError as e:
                return e.code, self.json.loads(e.read())
        return call

    def test_flow(self):
        a = self.client()
        self.assertEqual(a("GET", "/api/me")[0], 401)
        self.assertEqual(a("POST", "/api/login", {"login": "admin", "password": "x"})[0], 401)
        self.assertEqual(a("POST", "/api/login", {"login": "admin", "password": "admin"})[0], 200)
        self.assertEqual(a("GET", "/api/classes")[0], 403)  # сначала смена пароля
        self.assertEqual(a("POST", "/api/me/password", {"old": "admin", "new": "secret1"})[0], 200)
        self.assertEqual(a("POST", "/api/demo", {})[0], 200)
        # учитель Соколова (демо) не может править журнал чужого предмета
        t = self.client()
        self.assertEqual(t("POST", "/api/login", {"login": "sokolova", "password": "teacher"})[0], 200)
        code, asg = a("GET", "/api/assignments?class_id=1")
        own = [x for x in asg if x["teacher"].startswith("Соколова")][0]
        alien = [x for x in asg if not x["teacher"].startswith("Соколова")][0]
        code, l1 = t("POST", "/api/journal/%d/lessons" % own["id"], {"date": "2026-09-15"})
        self.assertEqual(code, 200)
        self.assertEqual(t("POST", "/api/journal/%d/lessons" % alien["id"], {"date": "2026-09-15"})[0], 403)
        self.assertEqual(t("GET", "/api/users")[0], 403)
        # закрытый период
        code, years = a("GET", "/api/years")
        q = [p for p in years[0]["periods"] if p["kind"] == "q1"][0]
        a("PUT", "/api/periods/%d" % q["id"], {"locked": True})
        code, j = t("GET", "/api/journal/%d?period=q1" % own["id"])
        self.assertEqual(t("POST", "/api/marks", {"items": [{"lesson_id": l1["id"], "student_id": j["students"][0]["id"], "value": "8"}]})[0], 400)
        a("PUT", "/api/periods/%d" % q["id"], {"locked": False})
        self.assertEqual(t("POST", "/api/marks", {"items": [{"lesson_id": l1["id"], "student_id": j["students"][0]["id"], "value": "8"}]})[0], 200)
        # классный руководитель может выставить четвертную по чужому предмету своего класса
        code, sheet = t("GET", "/api/sheet/1?kind=q2")
        self.assertEqual(code, 200)
        st = sheet["students"][0]["id"]
        self.assertEqual(t("POST", "/api/finals", {"items": [{"assignment_id": alien["id"], "student_id": st, "kind": "q2", "value": "зач"}]})[1]["saved"][0]["value"], "зач.")
        # вставка списка
        code, r = a("POST", "/api/classes/1/import_text", {"text": "Новый Ученик Тестович\nНовый Ученик Тестович"})
        self.assertEqual((r["added"], len(r["skipped"])), (1, 1))
        # режим «только ведомости»
        self.assertEqual(t("PUT", "/api/features", {"journal": False})[0], 403)
        self.assertEqual(a("PUT", "/api/features", {"journal": False})[0], 200)
        self.assertEqual(t("POST", "/api/journal/%d/lessons" % own["id"], {"date": "2026-09-16"})[0], 400)
        self.assertEqual(t("POST", "/api/finals", {"items": [{"assignment_id": own["id"], "student_id": st, "kind": "q2", "value": "9"}]})[0], 200)
        a("PUT", "/api/features", {"journal": True})
        # запрос без заголовка интерфейса отклоняется (CSRF)
        import urllib.request
        req = urllib.request.Request("http://127.0.0.1:%d/api/logout" % self.port, method="POST", data=b"{}")
        direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with self.assertRaises(urllib.error.HTTPError):
            direct.open(req)


if __name__ == "__main__":
    unittest.main()
