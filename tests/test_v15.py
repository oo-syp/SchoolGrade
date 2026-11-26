# -*- coding: utf-8 -*-
"""Тесты версии 1.5: 20 уроков и звонки, кабинеты, импорт aSc, свободные кабинеты, дежурство,
киоски, экстренное оповещение (SSE), HTTPS-сертификат (генерация, экспорт/импорт, TLS на том же порту)."""
import base64
import http.client
import json
import os
import shutil
import socket
import sqlite3
import ssl
import sys
import tempfile
import threading
import time
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from schoolgrade import asc, cert, db as DB, main as M, server as S, v15  # noqa: E402
from test_v13 import Client  # noqa: E402

XML = """<?xml version="1.0" encoding="windows-1251"?>
<timetable ascttversion="2024.24.1" importtype="database" options="">
<periods options="canadd,canremove" columns="period,name,short,starttime,endtime">
<period period="1" name="1" short="1" starttime="8:00" endtime="8:45"/>
<period period="2" name="2" short="2" starttime="8:55" endtime="9:40"/>
<period period="3" name="3" short="3" starttime="9:50" endtime="10:35"/>
</periods>
<daysdefs><daysdef id="d1" name="Пн" short="Пн" days="100000"/></daysdefs>
<weeksdefs><weeksdef id="w1" name="Каждая" short="" weeks="11"/><weeksdef id="w2" name="Две" short="" weeks="10,01"/></weeksdefs>
<subjects>
<subject id="s1" name="Математика" short="Матем."/>
<subject id="s2" name="Информатика" short="Инф."/>
<subject id="s3" name="Танцы" short="Тан."/>
</subjects>
<teachers>
<teacher id="t1" name="Сидорова Анна Ивановна" short="Сидорова А.И." firstname="Анна" lastname="Сидорова"/>
<teacher id="t2" name="Козлов О.П." short="Козлов"/>
</teachers>
<classrooms>
<classroom id="r1" name="301" short="301" capacity="*"/>
<classroom id="r2" name="Спортзал" short="Спз" capacity="60"/>
<classroom id="r3" name="Каб. 99 (новый)" short="99" capacity="25"/>
</classrooms>
<classes>
<class id="c1" name="6А" short="6А"/>
<class id="c2" name="6-Б" short="6Б"/>
</classes>
<groups>
<group id="g1" classid="c1" name="Весь класс" entireclass="1"/>
<group id="g2" classid="c1" name="1 группа" entireclass="0"/>
</groups>
<lessons>
<lesson id="l1" classids="c1" subjectid="s1" teacherids="t1" groupids="g1" classroomids="r1"/>
<lesson id="l2" classids="c1" subjectid="s2" teacherids="t2" groupids="g2" classroomids="r3"/>
<lesson id="l3" classids="c2" subjectid="s1" teacherids="t1" groupids="" classroomids="r2"/>
<lesson id="l4" classids="c1" subjectid="s3" teacherids="t1" groupids="" classroomids=""/>
</lessons>
<cards>
<card lessonid="l1" period="1" weeks="11" days="100000" classroomids="r1"/>
<card lessonid="l2" period="2" weeks="10" days="100000" classroomids="r3"/>
<card lessonid="l2" period="2" weeks="01" days="010000" classroomids="r3"/>
<card lessonid="l3" period="1" weeks="11" days="010000" classroomids="r2"/>
<card lessonid="l4" period="3" weeks="11" days="100000"/>
<card lessonid="l1" period="21" weeks="11" days="001000" classroomids=""/>
</cards>
</timetable>
""".encode("cp1251")


def b64(b):
    return base64.b64encode(b).decode()


class V15Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.app = M.init_app(cls.tmp)
        cls.srv = S.ServerThread(cls.app, "127.0.0.1", 0)
        cls.app.port = cls.srv.httpd.server_address[1]
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
        cls.t1 = c.call("POST", "/api/staff", {"last_name": "Сидорова", "first_name": "Анна", "middle_name": "Ивановна"})["id"]
        cls.t2 = c.call("POST", "/api/staff", {"last_name": "Козлов", "first_name": "Олег", "middle_name": "Петрович"})["id"]
        subs = dict((s["name"], s["id"]) for s in c.call("GET", "/api/subjects"))
        db = cls.app.db
        for cid, sub, t in ((cls.cid, "Математика", cls.t1), (cls.cid2, "Математика", cls.t1), (cls.cid, "Информатика", cls.t2)):
            c.call("POST", "/api/assignments", {"class_id": cid, "subject_id": subs[sub], "teacher_id": t, "hours": 2})
        cls.a_math = db.val("SELECT id FROM assignments WHERE class_id=? AND subject_id=?", (cls.cid, subs["Математика"]))
        cls.a_math2 = db.val("SELECT id FROM assignments WHERE class_id=? AND subject_id=?", (cls.cid2, subs["Математика"]))
        cls.a_inf = db.val("SELECT id FROM assignments WHERE class_id=? AND subject_id=?", (cls.cid, subs["Информатика"]))
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

    def get(self, path, ctx=None, base=None):
        return urllib.request.build_opener(urllib.request.ProxyHandler({})).open((base or self.base) + path, timeout=10)

    # ---------------------------------------------------------------- 2.1 звонки
    def test_01_bells_20_slots(self):
        b = dict((str(i), ["%02d:00" % (7 + i // 2 if i < 12 else 12), "%02d:45" % (7 + i // 2 if i < 12 else 12)]) for i in range(0, 21))
        r = self.c.call("PUT", "/api/schedule/bells", {"bells": b})
        self.assertEqual(len(r), 21)
        r = self.c.call("PUT", "/api/schedule/bells", {"bells": {"1": ["08:00", "08:45"], "2": ["08:55", "09:40"], "21": ["20:00", "20:45"], "3": ["xx", "09"]}})
        self.assertEqual(sorted(r), ["1", "2"])      # урока 21 и неверного времени нет
        self.t.call("PUT", "/api/schedule/bells", {"bells": {"1": ["08:00", "08:45"]}}, expect=403)

    def test_02_schedule_20_lessons(self):
        sid = self.c.call("POST", "/api/schedule", {"start_date": "2026-09-01", "name": "Осн"})["id"]
        type(self).sid = sid
        items = [{"week": 1, "weekday": 1, "lesson_no": 20, "assignment_id": self.a_math}]
        self.c.call("PUT", "/api/schedule/%d/class/%d" % (sid, self.cid), {"items": items + [{"week": 1, "weekday": 2, "lesson_no": 21, "assignment_id": self.a_math}]})
        D = self.c.call("GET", "/api/schedule/%d/class/%d" % (sid, self.cid))
        self.assertEqual([(i["weekday"], i["lesson_no"]) for i in D["items"]], [(1, 20)])   # 21-й отброшен

    # ---------------------------------------------------------------- 2.2 кабинеты
    def test_03_classrooms(self):
        r1 = self.c.call("POST", "/api/classrooms", {"name": "301", "capacity": 30})["id"]
        r2 = self.c.call("POST", "/api/classrooms", {"name": "Спортзал", "short": "Спз", "kind": "gym", "capacity": 60})["id"]
        self.c.call("POST", "/api/classrooms", {"name": "301"}, expect=400)
        self.c.call("POST", "/api/classrooms", {"name": "X", "kind": "bad"}, expect=400)
        self.t.call("POST", "/api/classrooms", {"name": "Z"}, expect=403)
        lst = self.c.call("GET", "/api/classrooms")
        self.assertEqual([x["kind_name"] for x in lst["items"]], ["Обычный", "Спортзал"])
        self.assertEqual(self.t.call("GET", "/api/classrooms")["can_edit"], False)
        type(self).r1, type(self).r2 = r1, r2
        sid = self.sid
        self.c.call("PUT", "/api/schedule/%d/class/%d" % (sid, self.cid), {"items": [
            {"week": 1, "weekday": 1, "lesson_no": 1, "assignment_id": self.a_math, "room_id": r1},
            {"week": 1, "weekday": 1, "lesson_no": 2, "assignment_id": self.a_inf, "room": "спз"}]})
        it = self.c.call("GET", "/api/schedule/%d/class/%d" % (sid, self.cid))["items"]
        self.assertEqual(sorted((i["lesson_no"], i["room_id"]) for i in it), [(1, r1), (2, r2)])   # текст «спз» распознан
        self.c.call("PUT", "/api/classrooms/%d" % r1, {"name": "301а", "short": "301а"})
        it = self.c.call("GET", "/api/schedule/%d/class/%d" % (sid, self.cid))["items"]
        self.assertIn("301а", [i["room"] for i in it])

    @unittest.skipIf(sqlite3.sqlite_version_info < (3, 35), "нужен SQLite 3.35+ (DROP COLUMN)")
    def test_04_migration_from_v5(self):
        p = os.path.join(self.tmp, "old.db")
        d = DB.Database(p)
        d.ex("INSERT INTO years(id,name,start_date,end_date) VALUES(1,'x','2026-09-01','2027-05-31')") if False else None
        d.close()
        con = sqlite3.connect(p)
        con.execute("PRAGMA foreign_keys=OFF")
        con.execute("ALTER TABLE schedule_items DROP COLUMN room_id")
        con.execute("DROP TABLE classrooms")
        con.execute("UPDATE meta SET value='5' WHERE key='schema'")
        cols = [r[1] for r in con.execute("PRAGMA table_info(years)")]
        con.execute("INSERT INTO years(start_date,end_date) VALUES('2026-09-01','2027-05-31')" if "name" not in cols else
                    "INSERT INTO years(name,start_date,end_date) VALUES('x','2026-09-01','2027-05-31')")
        con.commit()
        con.close()
        d = DB.Database(p)       # миграция 5 -> 6
        self.assertEqual(d.val("SELECT value FROM meta WHERE key='schema'"), "7")
        self.assertTrue(any(r["name"] == "room_id" for r in d.q("PRAGMA table_info(schedule_items)")))
        self.assertEqual(d.val("SELECT COUNT(*) FROM classrooms"), 0)
        d.close()

    # ---------------------------------------------------------------- 2.6 свободные кабинеты
    def test_05_free_rooms(self):
        # понедельник 2026-11-16; 1 урок 08:00–08:45 занят кабинетом 301а (6А), 2 урок — спортзал
        r = self.c.call("GET", "/api/rooms/free?date=2026-11-16&time=08:10")
        self.assertEqual(r["now"]["lesson_no"], 1)
        self.assertEqual([x["name"] for x in r["now"]["free"]], ["Спортзал"])
        self.assertEqual(r["now"]["busy"][0]["by"], "6А")
        self.assertEqual(r["next"]["lesson_no"], 2)
        self.assertEqual([x["name"] for x in r["next"]["free"]], ["301а"])
        r = self.c.call("GET", "/api/rooms/free?date=2026-11-16&time=08:50")      # перемена
        self.assertIsNone(r["now"])
        self.assertEqual(r["next"]["lesson_no"], 2)
        self.t.call("GET", "/api/rooms/free", expect=403)

    # ---------------------------------------------------------------- 2.5 дежурство
    def test_06_duty(self):
        today = time.strftime("%Y-%m-%d")
        did = self.c.call("POST", "/api/duty", {"class_id": self.cid, "start_date": today, "end_date": today, "place": "1 этаж"})["id"]
        self.assertEqual(self.c.call("GET", "/api/duty/today")["text"], "Сегодня дежурит: 6А (1 этаж)")
        self.c.call("POST", "/api/duty", {"class_id": self.cid2, "start_date": "2000-01-01", "end_date": "2000-01-07"})
        self.assertEqual(len(self.c.call("GET", "/api/duty")["items"]), 2)
        self.c.call("POST", "/api/duty", {"class_id": self.cid, "start_date": today, "end_date": "2000-01-01"}, expect=400)
        self.t.call("POST", "/api/duty", {"class_id": self.cid, "start_date": today}, expect=403)
        type(self).duty_id = did

    # ---------------------------------------------------------------- 2.4 киоски
    def _fill_week(self):
        for wd in range(1, 7):
            self.c.call("PUT", "/api/schedule/%d/class/%d" % (self.sid, self.cid), {"items": [
                {"week": 1, "weekday": w, "lesson_no": 1, "assignment_id": self.a_math, "room_id": self.r1} for w in range(1, 7)] +
                [{"week": 1, "weekday": w, "lesson_no": 2, "assignment_id": self.a_inf, "room_id": self.r2} for w in range(1, 7)]})

    def test_07_kiosk(self):
        self._fill_week()
        v15._cache.clear()
        for path, key in (("/kiosk/class/%d" % self.cid, "class"), ("/kiosk/teacher/%d" % self.t1, "teacher"),
                          ("/kiosk/room/%d" % self.r1, "room"), ("/kiosk/school", "school")):
            html = self.get(path).read().decode()          # без входа в систему
            self.assertIn('var KIND="%s"' % key, html)
        D = json.loads(self.get("/kiosk/data/class/%d" % self.cid).read().decode())
        self.assertEqual(D["title"], "6А класс")
        self.assertTrue(D["duty"].startswith("Сегодня дежурит: 6А"))
        self.assertEqual(sorted(l["n"] for l in D["lessons"]) if time.localtime().tm_wday < 6 else [1, 2], [1, 2])
        S_ = json.loads(self.get("/kiosk/data/school").read().decode())
        self.assertIn("6А", S_["duty"])
        self.assertIn("alert", S_)
        # 404 для несуществующего
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.get("/kiosk/data/class/99999")
        self.assertEqual(cm.exception.code, 404)
        # кэш: второй запрос в пределах TTL берёт данные из кэша (изменение расписания не видно сразу)
        n = len(v15._cache)
        self.get("/kiosk/data/class/%d" % self.cid)
        self.assertEqual(len(v15._cache), n)
        self.assertTrue(30 <= v15.KIOSK_TTL <= 60)

    def test_08_kiosk_substitutions(self):
        if time.localtime().tm_wday >= 6:
            self.skipTest("воскресенье")
        today = time.strftime("%Y-%m-%d")
        sid = self.c.call("POST", "/api/subs", {"date": today, "assignment_id": self.a_math, "lesson_no": 1, "substitute_id": self.t2})["id"] if False else \
            self.c.call("POST", "/api/subs", {"date": today, "assignment_id": self.a_math, "lesson_no": 1, "substitute_id": self.t2})["ids"][0]
        v15._cache.clear()
        D = json.loads(self.get("/kiosk/data/teacher/%d" % self.t2).read().decode())
        self.assertTrue(any(l["sub"] and l["n"] == 1 for l in D["lessons"]))            # у Козлова появился урок по замене
        D = json.loads(self.get("/kiosk/data/teacher/%d" % self.t1).read().decode())
        self.assertTrue(any(l.get("replaced") and l["n"] == 1 for l in D["lessons"]))   # у Сидоровой он перечёркнут
        self.c.call("DELETE", "/api/subs/%d" % sid)

    def test_09_kiosk_disable(self):
        self.t.call("PUT", "/api/kiosk", {"enabled": False}, expect=403)
        self.c.call("PUT", "/api/kiosk", {"enabled": False})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.get("/kiosk/school")
        self.assertEqual(cm.exception.code, 404)
        self.c.call("PUT", "/api/kiosk", {"enabled": True})

    # ---------------------------------------------------------------- 2.7 SSE
    def test_10_emergency_sse(self):
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        s.sendall(b"GET /kiosk/events HTTP/1.1\r\nHost: x\r\n\r\n")
        buf = b""
        while b"retry" not in buf:
            buf += s.recv(4096)
        self.assertIn(b"text/event-stream", buf)
        self.t.call("POST", "/api/alert", {"text": "Т"}, expect=403)
        self.c.call("POST", "/api/alert", {"text": "Эвакуация! Покиньте здание", "bg": "#c62828", "minutes": 3})
        t0 = time.time()
        while b"event: alert" not in buf and time.time() - t0 < 5:
            buf += s.recv(4096)
        self.assertIn("Эвакуация".encode(), buf)
        self.assertLess(time.time() - t0, 2)                                    # мгновенно, без опроса
        D = json.loads(self.get("/kiosk/data/school").read().decode())
        self.assertEqual(D["alert"]["text"], "Эвакуация! Покиньте здание")
        self.assertEqual(D["alert"]["fg"], "#ffffff")
        self.c.call("POST", "/api/alert", {"text": "x", "bg": "red"}, expect=400)
        self.c.call("DELETE", "/api/alert")
        buf = b""
        t0 = time.time()
        while b"event: clear" not in buf and time.time() - t0 < 5:
            buf += s.recv(4096)
        self.assertIn(b"event: clear", buf)
        self.assertIsNone(self.c.call("GET", "/api/alert")["alert"])
        s.close()

    # ---------------------------------------------------------------- 2.3 aSc
    def test_11_asc_parser(self):
        p = asc.parse(XML)
        self.assertEqual(p["version"], "2024.24.1")
        self.assertEqual(p["cycle"], 2)
        self.assertEqual(p["lost_chars"], 0)
        self.assertEqual(p["subjects"]["s1"]["name"], "Математика")
        self.assertEqual(p["max_period"], 21)
        with self.assertRaises(asc.AscError):
            asc.parse(b"<html/>")
        with self.assertRaises(asc.AscError):
            asc.parse(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><timetable/>')

    def test_12_asc_lost_letters_warning(self):
        import re
        bad = re.sub(r"[^\x00-\x7f]", "\ufffd", XML.decode("cp1251")).replace("windows-1251", "utf-8").encode("utf-8")
        p = asc.parse(bad)
        self.assertGreater(p["lost_chars"], 10)
        self.assertTrue(any("утрачены" in w for w in p["warnings"]))

    def test_13_asc_wizard(self):
        up = self.c.call("POST", "/api/schedule/asc/upload", {"filename": "t.xml", "data": b64(XML)})
        self.assertEqual(up["cycle"], 2)
        self.assertEqual(up["overflow"], [21])
        self.assertTrue(any("за пределами сетки" in w for w in up["warnings"]))
        cl = dict((c["name"], c["sg_id"]) for c in up["classes"])
        self.assertEqual(cl, {"6А": self.cid, "6-Б": self.cid2})                 # автосопоставление классов («6-Б»)
        tc = dict((t["name"], t["sg_id"]) for t in up["teachers"])
        self.assertEqual(tc["Сидорова Анна Ивановна"], self.t1)
        self.assertEqual(tc["Козлов О.П."], self.t2)                              # по фамилии и инициалам
        sb = dict((s["name"], s["sg_id"]) for s in up["subjects"])
        self.assertIsNone(sb["Танцы"])                                            # вручную
        self.assertIsNotNone(sb["Математика"])
        rooms = dict((r["name"], r["sg_id"]) for r in up["rooms"])
        self.assertEqual(rooms["Спортзал"], self.r2)
        self.assertIsNone(rooms["Каб. 99 (новый)"])                                # будет предложено добавить
        self.assertEqual([r["kind"] for r in up["rooms"] if r["name"] == "Спортзал"], ["gym"])
        before = self.c.call("GET", "/api/schedule")["versions"]
        tok = up["token"]
        r = self.c.call("POST", "/api/schedule/asc/apply", {"token": tok, "start_date": "2026-09-01", "dry_run": True}, expect=400)  # дата занята
        r = self.c.call("POST", "/api/schedule/asc/apply", {"token": tok, "start_date": "2026-10-05", "dry_run": True})
        self.assertTrue(r["overflow"])
        self.assertTrue(any("не поместились" in x for x in r["problems"]))
        self.assertEqual([x["name"] for x in r["rooms_new"]], ["301", "Каб. 99 (новый)"])   # «301» переименован в «301а» — предлагается добавить
        self.assertEqual(len(self.c.call("GET", "/api/schedule")["versions"]), len(before))   # dry-run ничего не создал
        r = self.c.call("POST", "/api/schedule/asc/apply", {"token": tok, "start_date": "2026-10-05", "name": "aSc", "add_rooms": True, "import_bells": True})
        self.assertGreater(r["items"], 0)
        vs = self.c.call("GET", "/api/schedule")["versions"]
        self.assertEqual(len(vs), len(before) + 1)                                  # новая версия
        new = [v for v in vs if v["id"] == r["id"]][0]
        self.assertEqual(new["weeks"], 2)
        self.assertEqual(self.c.call("GET", "/api/schedule/%d/class/%d" % (self.sid, self.cid))["schedule"]["weeks"], 1)   # прежняя цела
        D = self.c.call("GET", "/api/schedule/%d/class/%d" % (new["id"], self.cid))
        pairs = sorted((i["week"], i["weekday"], i["lesson_no"], i["assignment_id"]) for i in D["items"])
        # 6А: математика 1 урок каждый ПН на обеих неделях; информатика (группа, Козлов) 2 урок: пн — 1 нед., вт — 2 нед.
        self.assertEqual(pairs, [(1, 1, 1, self.a_math), (1, 1, 2, self.a_inf), (2, 1, 1, self.a_math), (2, 2, 2, self.a_inf)])
        D2 = self.c.call("GET", "/api/schedule/%d/class/%d" % (new["id"], self.cid2))
        self.assertEqual([(i["week"], i["weekday"], i["lesson_no"]) for i in D2["items"]], [(1, 2, 1), (2, 2, 1)])
        names = [x["name"] for x in self.c.call("GET", "/api/classrooms")["items"]]
        self.assertIn("Каб. 99 (новый)", names)
        self.assertEqual(self.c.call("GET", "/api/schedule")["bells"]["2"], ["08:55", "09:40"])
        self.c.call("POST", "/api/schedule/asc/apply", {"token": tok, "start_date": "2026-10-06"}, expect=400)   # сеанс использован
        self.t.call("POST", "/api/schedule/asc/upload", {"data": b64(XML)}, expect=403)

    # ---------------------------------------------------------------- HTTPS
    def test_14_cert_unit(self):
        d = os.path.join(self.tmp, "cu")
        st = cert.CertStore(d).generate(0, ["school.local"])
        self.assertEqual(st["server"]["not_after"], "unlimited")
        self.assertTrue(st["server"]["covers_host"])
        st2 = cert.CertStore(d + "2").generate(2)
        self.assertNotEqual(st2["server"]["not_after"], "unlimited")
        self.assertEqual(int(st2["server"]["not_after"][:4]) - time.gmtime().tm_year, 2)

    def test_15_https_same_port(self):
        st = self.c.call("POST", "/api/https/generate", {"years": 0, "names": "school.local"})
        self.assertTrue(st["has_ca"] and st["has_server"])
        self.c.call("POST", "/api/https/enable", {"enabled": True})
        self.t.call("GET", "/api/https", expect=403)
        ca = self.get("/ca.cer").read()                                           # корневой сертификат — без входа
        self.assertEqual(ca[:1], b"\x30")
        ctx = ssl.create_default_context(cadata=ca)
        con = http.client.HTTPSConnection("localhost", self.port, context=ctx, timeout=10)
        con.request("GET", "/kiosk/data/school")
        self.assertEqual(con.getresponse().status, 200)                          # HTTPS на том же порту
        self.assertEqual(self.get("/kiosk/data/school").status, 200)              # HTTP продолжает работать
        # без доверия к корневому сертификату — отказ (проверка включена)
        with self.assertRaises(ssl.SSLError):
            http.client.HTTPSConnection("localhost", self.port, context=ssl.create_default_context(), timeout=10).request("GET", "/")
        # выключили HTTPS — TLS-соединения закрываются
        self.c.call("POST", "/api/https/enable", {"enabled": False})
        with self.assertRaises((ssl.SSLError, ConnectionError, OSError)):
            c2 = http.client.HTTPSConnection("localhost", self.port, context=ctx, timeout=5)
            c2.request("GET", "/")
            c2.getresponse()
        self.c.call("POST", "/api/https/enable", {"enabled": True})

    def test_16_https_export_import(self):
        z = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        rq = urllib.request.Request(self.base + "/api/https/export")
        # скачивание только с сеансом администратора
        data = None
        for h in (self.c.op,):
            data = h.open(rq, timeout=10).read()
        self.assertEqual(data[:2], b"PK")
        import io
        import zipfile
        names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        for n in ("schoolca.crt", "schoolca.cer", "schoolca.key", "server.crt", "server.key", "install_ca.bat"):
            self.assertIn(n, names)
        old_fp = self.c.call("GET", "/api/https")["ca"]["sha256"]
        self.c.call("DELETE", "/api/https")
        self.assertFalse(self.c.call("GET", "/api/https")["has_server"])
        r = self.c.call("POST", "/api/https/import", {"files": [{"name": "SchoolCert.zip", "data": b64(data)}]})
        self.assertEqual(r["ca"]["sha256"], old_fp)                               # тот же корневой сертификат
        self.assertTrue(r["has_server"])
        # без ключа сервера — понятная ошибка
        pub = cert.CertStore(os.path.join(self.app.data_dir, "certs")).export_zip(False)
        self.c.call("POST", "/api/https/import", {"files": [{"name": "p.zip", "data": b64(pub)}]}, expect=400)

    def test_18_backdate_version_and_ktp_hint(self):
        # дату начала версии можно изменить на прошедшую; КТП по расписанию подсказывает причину, если уроков в сетке нет
        sid = self.c.call("POST", "/api/schedule", {"start_date": "2026-12-20", "name": "поздняя"})["id"]
        self.c.call("PUT", "/api/schedule/%d" % sid, {"start_date": "2026-09-02"})
        v = [x for x in self.c.call("GET", "/api/schedule")["versions"] if x["id"] == sid][0]
        self.assertEqual(v["start_date"], "2026-09-02")
        self.c.call("PUT", "/api/schedule/%d" % sid, {"start_date": "2026-09-01"}, expect=400)   # дата занята основной версией
        subs = dict((x["name"], x["id"]) for x in self.c.call("GET", "/api/subjects"))
        self.c.call("POST", "/api/assignments", {"class_id": self.cid2, "subject_id": subs["Физика"], "teacher_id": self.t1, "hours": 1})
        aid = self.app.db.val("SELECT MAX(id) FROM assignments")
        self.c.call("PUT", "/api/assignments/%d/ktp" % aid, {"items": [{"num": 1, "topic": "Т1", "hours": 1}]})
        r = self.c.call("POST", "/api/assignments/%d/ktp/schedule" % aid, {"mode": "lessons", "use_schedule": True, "start": "2026-09-01", "from": "first", "dry_run": True})
        self.assertEqual(r["slots"], [])
        self.assertIn("нет ни одного урока", r["hint"])

    def test_19_subgroups(self):
        """1.5.1: состав подгруппы возвращается в списке (галочки не теряются), деление на подгруппы одним действием."""
        c = self.c
        cid = c.call("POST", "/api/classes", {"grade": 7, "letter": "В"})["id"]
        ids = []
        for i, (ln, g) in enumerate((("Андреев", "м"), ("Борисова", "ж"), ("Власов", "м"), ("Громова", "ж"))):
            ids.append(c.call("POST", "/api/students", {"class_id": cid, "last_name": ln, "first_name": "Имя", "gender": g})["id"])
        subs = dict((s["name"], s["id"]) for s in c.call("GET", "/api/subjects"))
        sid = subs.get("Иностранный язык") or subs["Математика"]
        c.call("POST", "/api/assignments", {"class_id": cid, "subject_id": sid, "teacher_id": self.t1, "hours": 3})
        a = [x for x in c.call("GET", "/api/assignments?class_id=%d" % cid)][0]
        self.assertEqual(a["student_ids"], [])
        # ручной состав → при повторном чтении виден
        c.call("PUT", "/api/assignments/%d" % a["id"], {"student_ids": ids[:2], "group_name": "Ручная"})
        a = c.call("GET", "/api/assignments?class_id=%d" % cid)[0]
        self.assertEqual(sorted(a["student_ids"]), sorted(ids[:2]))
        self.assertEqual(a["n_sub"], 2)
        # деление: ошибки
        base = "/api/assignments/%d/split" % a["id"]
        g = lambda n, t, s_: {"name": n, "teacher_id": t, "student_ids": s_}
        c.call("POST", base, {"groups": [g("1", self.t1, ids[:2])]}, expect=400)                         # одна подгруппа
        c.call("POST", base, {"groups": [g("1", self.t1, ids[:2]), g("2", self.t2, ids[2:3])]}, expect=400)  # не все
        c.call("POST", base, {"groups": [g("1", self.t1, ids[:3]), g("2", self.t2, ids[2:])]}, expect=400)   # пересечение
        c.call("POST", base, {"groups": [g("Х", self.t1, ids[:2]), g("х", self.t2, ids[2:])]}, expect=400)   # одинаковые названия
        # успех
        r = c.call("POST", base, {"groups": [g("Мальчики", self.t1, [ids[0], ids[2]]), g("Девочки", self.t2, [ids[1], ids[3]])]})
        self.assertEqual(len(r["ids"]), 2)
        rows = c.call("GET", "/api/assignments?class_id=%d" % cid)
        self.assertEqual(sorted(x["group_name"] for x in rows), ["Девочки", "Мальчики"])
        by = dict((x["group_name"], x) for x in rows)
        self.assertEqual(sorted(by["Девочки"]["student_ids"]), sorted([ids[1], ids[3]]))
        self.assertEqual(by["Девочки"]["teacher_id"], self.t2)
        self.assertEqual(by["Девочки"]["hours"], by["Мальчики"]["hours"])
        # учитель без прав управления делить не может
        self.t.call("POST", base, {"groups": [g("1", self.t1, ids[:2]), g("2", self.t2, ids[2:])]}, expect=403)


    def test_20_help_served(self):
        body = self.get("/help.js").read().decode("utf-8")
        self.assertIn("window.SG_HELP", body)
        self.assertIn("Как создать подгруппы", body)
        self.assertIn("help.js", self.get("/").read().decode("utf-8"))

    def test_17_server_info(self):
        i = self.c.call("GET", "/api/server_info")
        self.assertTrue(i["urls"][0].startswith("http://"))
        self.assertIn(":%d" % self.port, i["urls"][0])


if __name__ == "__main__":
    unittest.main()
