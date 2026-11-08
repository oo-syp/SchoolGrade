# -*- coding: utf-8 -*-
"""Тесты версии 1.2: чтение .xls, импорт из «Старт ШКОЛА», КТП,
контроль накопляемости, табели."""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from schoolgrade import auth, ktp, reports as R, startschool as SS, xls  # noqa: E402
from schoolgrade import grading as G  # noqa: E402
from schoolgrade.db import Database  # noqa: E402

FX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def fx(name):
    with open(os.path.join(FX, name), "rb") as f:
        return f.read()


def new_db(tmp):
    from schoolgrade.api import _default_periods
    db = Database(os.path.join(tmp, "t.db"))
    auth.ensure_admin(db)
    db.set_setting("org_type", "secondary")
    yid = db.ex("INSERT INTO years(name,start_date,end_date,is_current,scheme) VALUES('2026/2027','2026-09-01','2027-08-31',1,'quarters')")
    for kind, nm, a, b in _default_periods(2026, "quarters"):
        db.ex("INSERT INTO periods(year_id,kind,name,start_date,end_date) VALUES(?,?,?,?,?)", (yid, kind, nm, a, b))
    for kind, nm in G.FINAL_KINDS:
        db.ex("INSERT INTO periods(year_id,kind,name,start_date,end_date) VALUES(?,?,?,'','')", (yid, kind, nm))
    return db, yid


class XlsReaderTest(unittest.TestCase):
    def test_values_formulas_and_long_sst(self):
        b = xls.open_workbook(fx("startschool_parallel.xls"))
        self.assertIn("1ч", b.sheet_names())
        sh = b.sheet("1ч")
        self.assertEqual(sh.cell(3, 3), '7 "Б"')           # строковый результат формулы
        self.assertEqual(sh.cell(6, 6), 8.0)               # числовая формула =6+2
        self.assertEqual(sh.text(6, 0), "1")
        pad = b.sheet("Строки")                            # таблица строк с CONTINUE
        self.assertEqual(pad.cell(2499, 0), "Уникальная строка № 2499 — ёжикёжикёжикёжик")
        self.assertEqual(pad.nrows, 2500)

    def test_rejects_non_xls(self):
        with self.assertRaises(xls.XlsError):
            xls.open_workbook(b"PK\x03\x04 not an xls")


class StartSchoolTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db, self.yid = new_db(self.tmp)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def parsed(self):
        return [SS.parse_file("Старт ШКОЛА.xls", fx("startschool_main.xls")),
                SS.parse_file("Параллель_7.xls", fx("startschool_parallel.xls"))]

    def test_parse(self):
        main, par = self.parsed()
        self.assertEqual(main["kind"], "main")
        self.assertEqual(main["passport"]["Наименование"], "ГУО «Тестовая средняя школа»")
        self.assertEqual(par["kind"], "parallel")
        c = par["classes"][0]
        self.assertEqual((c["grade"], c["letter"], c["teacher"]), (7, "Б", "Орлова Е.Н."))
        names = [s["name"] for s in c["students"]]
        self.assertEqual(names, ["Иванов Иван", "Петрова Анна", "Сидоров Пётр", "ТрегубоваТина"])  # без итоговых строк
        self.assertEqual(c["students"][0]["marks"]["q1"]["Белорусский язык"], "7")
        self.assertEqual(c["students"][0]["marks"]["q1"]["Математика"], "8")
        self.assertEqual(c["students"][1]["marks"]["year"]["Математика"], "6")

    def test_preview_then_commit_then_reimport(self):
        p = self.parsed()
        pre = SS.apply(self.db, p, {}, dry_run=True)
        self.assertEqual(self.db.val("SELECT COUNT(*) FROM students"), 0)     # просмотр ничего не меняет
        rep = SS.apply(self.db, p, {}, dry_run=False)
        self.assertEqual(pre["summary"], rep["summary"])
        self.assertEqual(rep["summary"]["students_new"], 4)
        self.assertEqual(rep["summary"]["classes_new"], 2)                    # 7 «Б» и 1 «А» из главной книги
        self.assertEqual(rep["marks"]["q1"]["new"], 16)
        self.assertEqual(rep["marks"]["year"]["new"], 16)
        self.assertEqual(rep["summary"]["groups"], 2)                         # английский и немецкий — группы
        self.assertEqual(self.db.get_setting("school")["Директор"], "Директоров Д.Д.")
        st = self.db.one("SELECT * FROM students WHERE last_name='Трегубова'")
        self.assertEqual(st["first_name"], "Тина")                            # «ТрегубоваТина» разделено
        cls = self.db.one("SELECT * FROM classes WHERE grade=7")
        t = self.db.one("SELECT * FROM staff WHERE id=?", (cls["teacher_id"],))
        self.assertEqual(t["last_name"], "Орлова")                            # найдена в сотрудниках главной книги
        again = SS.apply(self.db, p, {}, dry_run=True)
        self.assertEqual(again["summary"]["students_new"], 0)
        self.assertEqual(again["marks"]["q1"]["same"], 16)

    def test_overwrite_and_fuzzy(self):
        p = self.parsed()
        SS.apply(self.db, p, {}, dry_run=False)
        self.db.ex("UPDATE students SET first_name='Михаил' WHERE last_name='Иванов'")
        self.db.ex("UPDATE students SET last_name='Сидоровв' WHERE last_name='Сидоров'")
        aid = self.db.val("SELECT a.id FROM assignments a JOIN subjects s ON s.id=a.subject_id WHERE s.name='Математика'")
        sid = self.db.val("SELECT id FROM students WHERE last_name='Петрова'")
        self.db.ex("UPDATE final_marks SET value='3' WHERE assignment_id=? AND student_id=? AND kind='q1'", (aid, sid))
        keep = SS.apply(self.db, p, {"kinds": ["q1"]}, dry_run=True)
        self.assertEqual(keep["marks"]["q1"]["diff_kept"], 1)
        self.assertEqual(keep["summary"]["students_fuzzy"], 1)                # «Сидоровв» ~ «Сидоров»
        self.assertEqual(keep["summary"]["students_new"], 1)                  # «Иванов Михаил» ≠ «Иванов Иван»
        rep = SS.apply(self.db, p, {"kinds": ["q1"], "overwrite": True}, dry_run=False)
        self.assertEqual(rep["marks"]["q1"]["replaced"], 1)
        self.assertEqual(self.db.val("SELECT value FROM final_marks WHERE assignment_id=? AND student_id=? AND kind='q1'", (aid, sid)), "8")

    def test_locked_period(self):
        self.db.ex("UPDATE periods SET locked=1 WHERE kind='q1'")
        rep = SS.apply(self.db, self.parsed(), {}, dry_run=False)
        self.assertEqual(rep["marks"]["q1"]["new"], 0)
        self.assertEqual(rep["marks"]["q1"]["locked"], 16)

    def test_not_a_startschool_book(self):
        with self.assertRaises(SS.ImportError_):
            SS.parse_file("x.xls", b"garbage")


class KtpTest(unittest.TestCase):
    def test_docx_with_sections(self):
        items, notes = ktp.parse("ktp.docx", fx("ktp_sample.docx"), start_year=2026)
        self.assertEqual(len(items), 7)
        self.assertEqual(items[0]["planned_date"], "2026-09-02")
        self.assertEqual(items[2]["hours"], 2.0)
        self.assertEqual(items[0]["section"], "Раздел 1. Натуральные числа")
        self.assertTrue(items[4]["section"].startswith("Раздел 2"))

    def test_plain_list_and_spring_dates(self):
        items, _ = ktp.parse(text="1. Введение\n2. Строение клетки (2 ч)\n3. Ткани", start_year=2026)
        self.assertEqual([i["topic"] for i in items], ["Введение", "Строение клетки", "Ткани"])
        self.assertEqual(items[1]["hours"], 2.0)
        items, _ = ktp.parse(text="№;Тема;Дата\n1;Повторение;15.01\n2;Дроби;20.01", start_year=2026)
        self.assertEqual(items[0]["planned_date"], "2027-01-15")

    def test_errors(self):
        with self.assertRaises(ktp.KtpError):
            ktp.parse("plan.doc", b"\xd0\xcf")
        with self.assertRaises(ktp.KtpError):
            ktp.parse(text="   \n  ")


class AccumulationAndCardsTest(unittest.TestCase):
    def setUp(self):
        from schoolgrade import demo
        self.tmp = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.tmp, "d.db"))
        auth.ensure_admin(self.db)
        demo.fill(self.db)
        self.y = self.db.one("SELECT * FROM years WHERE is_current=1")
        self.asg = self.db.q("SELECT a.*, sb.name AS subject, sb.category, c.grade, c.letter, st.last_name t_last, "
                             "st.first_name t_first, st.middle_name t_middle FROM assignments a JOIN subjects sb ON sb.id=a.subject_id "
                             "JOIN classes c ON c.id=a.class_id LEFT JOIN staff st ON st.id=a.teacher_id")

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_expectation_grows_during_period(self):
        p = self.db.one("SELECT * FROM periods WHERE year_id=? AND kind='q2'", (self.y["id"],))
        early = R.accumulation(self.db, self.y["id"], "q2", self.asg, True, today=p["start_date"])
        self.assertEqual(early["counts"]["few"], 0)                 # в начале четверти замечаний нет
        late = R.accumulation(self.db, self.y["id"], "q2", self.asg, True, today=p["end_date"])
        self.assertGreater(late["counts"]["few"], 0)                # в конце — отметок мало у всех

    def test_missing_period_mark_after_end(self):
        p = self.db.one("SELECT * FROM periods WHERE year_id=? AND kind='q1'", (self.y["id"],))
        self.db.ex("DELETE FROM final_marks WHERE kind='q1' AND student_id=(SELECT MIN(student_id) FROM final_marks)")
        r = R.accumulation(self.db, self.y["id"], "q1", self.asg, False, today="2099-01-01")
        self.assertGreater(r["counts"]["missing"], 0)
        self.assertEqual(r["counts"]["few"], 0)                     # без журнала текущие не проверяются
        self.assertTrue(p["end_date"])

    def test_report_cards(self):
        cid = self.db.val("SELECT id FROM classes ORDER BY grade, letter LIMIT 1")
        d = R.report_cards(self.db, cid, ["q1", "year"])
        self.assertEqual([k["kind"] for k in d["kinds"]], ["q1", "year"])
        self.assertTrue(d["students"])
        s = d["students"][0]
        self.assertTrue(all("q1" in r["marks"] for r in s["rows"]))
        self.assertIn("q1", s["absent"])


if __name__ == "__main__":
    unittest.main()
