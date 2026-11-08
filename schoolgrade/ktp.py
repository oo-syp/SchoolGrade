# -*- coding: utf-8 -*-
"""Календарно-тематическое планирование (КТП): разбор файлов учителя.

Учителя обычно ведут КТП таблицей в Word, реже — в Excel. Здесь таблица
из .docx / .xlsx / .xls / .csv / вставленного текста превращается в список
уроков: номер, тема, количество часов, плановая дата, раздел.
Столбцы определяются по заголовкам, а если их нет — по содержимому.
"""
import io
import re
import zipfile
import xml.etree.ElementTree as ET

from . import xls as XLS
from . import xlsx as XLSX

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class KtpError(Exception):
    pass


# ------------------------------------------------------------------ чтение таблиц

def _docx_tables(data):
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        root = ET.fromstring(z.read("word/document.xml"))
    except Exception:
        raise KtpError("Не удалось прочитать документ Word (.docx)")
    tables = []
    for tbl in root.iter(W + "tbl"):
        rows = []
        for tr in tbl.findall(W + "tr"):
            cells = []
            for tc in tr.findall(W + "tc"):
                paras = []
                for p in tc.iter(W + "p"):
                    t = "".join(x.text or "" for x in p.iter(W + "t"))
                    if t.strip():
                        paras.append(t.strip())
                span = tc.find(W + "tcPr/" + W + "gridSpan")
                n = int(span.get(W + "val", "1")) if span is not None else 1
                cells.append(" ".join(paras))
                cells.extend([""] * (n - 1))
            rows.append(cells)
        if rows:
            tables.append(rows)
    if not tables:
        # таблиц нет — каждая строка документа как строка текста
        lines = []
        for p in root.iter(W + "p"):
            t = "".join(x.text or "" for x in p.iter(W + "t")).strip()
            if t:
                lines.append(t)
        return [_text_rows("\n".join(lines))]
    return tables


def _xls_rows(data):
    book = XLS.open_workbook(data)
    names = book.sheet_names()
    if not names:
        return []
    sh = book.sheet(names[0])
    return [[sh.text(r, c) for c in range(sh.ncols)] for r in range(sh.nrows)]


def _xlsx_rows(data):
    rows = XLSX.read_first_sheet(data)
    return [[("" if v is None else (str(int(v)) if isinstance(v, float) and v.is_integer() else str(v))).strip()
             for v in r] for r in rows]


def _text_rows(text):
    lines = [l for l in text.replace("\r", "").split("\n") if l.strip()]
    if not lines:
        return []
    sample = "\n".join(lines[:20])
    if "\t" in sample:
        sep = "\t"
    elif sample.count(";") >= len(lines[:20]):
        sep = ";"
    else:
        sep = None
    rows = []
    for l in lines:
        if sep:
            rows.append([c.strip().strip('"') for c in l.split(sep)])
        else:
            # «1. Тема урока» / «1 Тема урока (2 ч)»
            m = re.match(r"\s*(\d{1,3})[.)]?\s+(.*)$", l)
            rows.append([m.group(1), m.group(2).strip()] if m else ["", l.strip()])
    return rows


def read_table(filename, data=None, text=None):
    """Вернуть строки таблицы (списки ячеек-строк)."""
    if text is not None:
        return _text_rows(text)
    name = (filename or "").lower()
    if name.endswith(".docx"):
        tables = _docx_tables(data)
        return max(tables, key=len) if tables else []
    if name.endswith(".xlsx"):
        return _xlsx_rows(data)
    if name.endswith(".xls"):
        try:
            return _xls_rows(data)
        except XLS.XlsError as e:
            raise KtpError(str(e))
    if name.endswith(".doc"):
        raise KtpError("Старый формат Word (.doc) не поддерживается — откройте файл в Word и сохраните как .docx")
    if name.endswith((".csv", ".txt")):
        for enc in ("utf-8-sig", "cp1251"):
            try:
                return _text_rows(data.decode(enc))
            except UnicodeDecodeError:
                continue
    raise KtpError("Поддерживаются файлы .docx, .xlsx, .xls, .csv и .txt")


# ------------------------------------------------------------------ распознавание столбцов

_HDR = {
    "num": re.compile(r"^(№|n|no|номер|№\s*урока|№\s*п/п|п/п|урок)\b", re.I),
    "topic": re.compile(r"(тема|содержание|наименование|название)", re.I),
    "hours": re.compile(r"(час|кол-во|количество)", re.I),
    "date": re.compile(r"(дата|срок|число)", re.I),
}
_DATE = re.compile(r"^\s*(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?\s*$")
_NUM = re.compile(r"^\s*(\d{1,3})\s*[.)]?\s*$")
_HOURS = re.compile(r"^\s*(\d(?:[.,]\d)?)\s*(ч\.?|час\w*)?\s*$", re.I)


def _letters(s):
    return len(re.findall(r"[A-Za-zА-Яа-яЁёІіЎў]", s or ""))


def detect_columns(rows):
    """Определить номера столбцов: num, topic, hours, date; и строку заголовка."""
    ncols = max([len(r) for r in rows] + [0])
    if not ncols:
        raise KtpError("В файле нет таблицы")
    cols = {}
    header_row = -1
    for i, r in enumerate(rows[:8]):
        hits = {}
        for c, v in enumerate(r):
            v = (v or "").strip()
            if not v or len(v) > 60:
                continue
            for k, rx in _HDR.items():
                if k not in hits and rx.search(v):
                    if k == "num" and _HDR["topic"].search(v):
                        continue
                    hits[k] = c
                    break
        if "topic" in hits:
            cols, header_row = hits, i
            break
    body = rows[header_row + 1:] if header_row >= 0 else rows
    col_vals = [[(r[c] if c < len(r) else "") for r in body] for c in range(ncols)]

    def share(c, rx):
        vals = [v for v in col_vals[c] if v.strip()]
        return (sum(1 for v in vals if rx.match(v)) / float(len(vals))) if vals else 0.0

    if "topic" not in cols:
        avg_letters = [sum(_letters(v) for v in col_vals[c]) / float(max(len(body), 1)) for c in range(ncols)]
        cols["topic"] = max(range(ncols), key=lambda c: avg_letters[c])
    if "num" not in cols:
        cands = [c for c in range(ncols) if c != cols["topic"] and share(c, _NUM) > 0.6]
        if cands:
            cols["num"] = min(cands)
    if "date" not in cols:
        cands = [c for c in range(ncols) if c not in cols.values() and share(c, _DATE) > 0.5]
        if cands:
            cols["date"] = cands[0]
    if "hours" not in cols:
        cands = [c for c in range(ncols) if c not in cols.values() and share(c, _HOURS) > 0.6]
        if cands:
            cols["hours"] = cands[0]
    return cols, header_row


def _date_iso(v, start_year):
    m = _DATE.match(v or "")
    if not m:
        return ""
    d, mo = int(m.group(1)), int(m.group(2))
    if not (1 <= d <= 31 and 1 <= mo <= 12):
        return ""
    y = m.group(3)
    if y:
        y = int(y)
        if y < 100:
            y += 2000
    else:
        y = start_year if mo >= 8 else start_year + 1
    return "%04d-%02d-%02d" % (y, mo, d)


def parse_rows(rows, start_year):
    """Строки таблицы -> список уроков КТП и пояснения к разбору."""
    rows = [[(c or "").strip() for c in r] for r in rows if any((c or "").strip() for c in r)]
    if not rows:
        raise KtpError("Таблица пуста")
    cols, header_row = detect_columns(rows)
    body = rows[header_row + 1:] if header_row >= 0 else rows
    tc = cols["topic"]
    out, section, skipped = [], "", 0
    has_num = "num" in cols
    for r in body:
        get = lambda k: (r[cols[k]] if k in cols and cols[k] < len(r) else "")  # noqa: E731
        topic = get("topic")
        num = get("num")
        # строка-раздел: одна заполненная ячейка (объединённая строка таблицы) без номера урока
        nonempty = [x for x in r if x]
        if len(nonempty) == 1 and _letters(nonempty[0]) > 3 and not _NUM.match(num or "") \
                and (has_num or re.match(r"(раздел|тема\s*\d|глава|модуль|[IVX]+\.)", nonempty[0], re.I)):
            section = re.sub(r"\s*\(\s*\d+\s*ч\.?\s*\)\s*$", "", nonempty[0])
            continue
        if topic and has_num and not _NUM.match(num or ""):
            if len(nonempty) <= 2 and _letters(topic) > 3:
                section = re.sub(r"\s*\(\s*\d+\s*ч\.?\s*\)\s*$", "", topic)
            else:
                skipped += 1
            continue
        if not topic or _letters(topic) < 2:
            if any(nonempty):
                skipped += 1
            continue
        if _HDR["topic"].search(topic) and len(topic) < 40 and not out:
            continue   # повтор заголовка
        hours = 1.0
        hv = get("hours")
        m = _HOURS.match(hv or "")
        if m:
            try:
                hours = float(m.group(1).replace(",", ".")) or 1.0
            except ValueError:
                hours = 1.0
        else:
            m2 = re.search(r"\((\d)\s*ч\.?\)\s*$", topic)
            if m2:
                hours = float(m2.group(1))
                topic = topic[:m2.start()].strip()
        out.append({"num": int(_NUM.match(num).group(1)) if (num and _NUM.match(num)) else len(out) + 1,
                    "topic": re.sub(r"\s+", " ", topic)[:500], "hours": hours,
                    "planned_date": _date_iso(get("date"), start_year), "section": section[:200]})
    if not out:
        raise KtpError("Не удалось найти уроки в таблице. Проверьте, что в ней есть столбец с темами уроков")
    # если номера не по порядку — перенумеровать
    nums = [x["num"] for x in out]
    if nums != sorted(nums) or len(set(nums)) != len(nums):
        for i, x in enumerate(out):
            x["num"] = i + 1
    names = {"num": "№", "topic": "тема", "hours": "часы", "date": "дата"}
    found = ", ".join("%s — столбец %d" % (names[k], v + 1) for k, v in sorted(cols.items(), key=lambda kv: kv[1]))
    notes = ["Распознано: " + found + (", строка заголовка %d" % (header_row + 1) if header_row >= 0 else "")]
    if skipped:
        notes.append("Пропущено строк без темы или номера: %d" % skipped)
    return out, notes


def parse(filename=None, data=None, text=None, start_year=2026):
    return parse_rows(read_table(filename, data, text), start_year)


def to_rows(items):
    return [[x["num"], x.get("section", ""), x["topic"], x["hours"], x.get("planned_date", "")] for x in items]
