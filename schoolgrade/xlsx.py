# -*- coding: utf-8 -*-
"""Минимальный генератор .xlsx без сторонних библиотек (Office Open XML).
Поддерживает несколько листов, заголовок, жирную шапку, границы, ширину
столбцов, объединение ячеек заголовка, числа и строки."""
import io
import zipfile
from xml.sax.saxutils import escape


def _col(n):
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _cell(ref, v, style):
    if v is None or v == "":
        return '<c r="{}" s="{}"/>'.format(ref, style)
    if isinstance(v, bool):
        v = "да" if v else "нет"
    if isinstance(v, (int, float)):
        return '<c r="{}" s="{}"><v>{}</v></c>'.format(ref, style, v)
    txt = escape(str(v)).replace("\n", "&#10;")
    return '<c r="{}" s="{}" t="inlineStr"><is><t xml:space="preserve">{}</t></is></c>'.format(ref, style, txt)


STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="3"><font><sz val="10"/><name val="Arial"/></font><font><b/><sz val="10"/><name val="Arial"/></font><font><b/><sz val="13"/><name val="Arial"/></font></fonts>
<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FFE8EEF7"/><bgColor indexed="64"/></patternFill></fill></fills>
<borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border><border><left style="thin"><color rgb="FF999999"/></left><right style="thin"><color rgb="FF999999"/></right><top style="thin"><color rgb="FF999999"/></top><bottom style="thin"><color rgb="FF999999"/></bottom><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="5">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1"><alignment vertical="center" wrapText="1"/></xf>
<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>
<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/>
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"><alignment wrapText="1"/></xf>
</cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""


def _sheet_xml(sheet):
    title = sheet.get("title")
    subtitle = sheet.get("subtitle")
    headers = sheet.get("headers") or []
    rows = sheet.get("rows") or []
    widths = sheet.get("widths") or []
    out = []
    r = 0
    merges = []
    ncols = max([len(headers)] + [len(x) for x in rows if not isinstance(x, dict)] + [1])
    if title:
        out.append('<row r="{}">{}</row>'.format(r + 1, _cell("A{}".format(r + 1), title, 3)))
        merges.append("A{0}:{1}{0}".format(r + 1, _col(max(ncols - 1, 0))))
        r += 1
    if subtitle:
        for line in subtitle.split("\n"):
            out.append('<row r="{}">{}</row>'.format(r + 1, _cell("A{}".format(r + 1), line, 0)))
            r += 1
    if title or subtitle:
        r += 1
    if headers and not sheet.get("repeat_headers"):
        cells = "".join(_cell("{}{}".format(_col(i), r + 1), h, 2) for i, h in enumerate(headers))
        out.append('<row r="{}">{}</row>'.format(r + 1, cells))
        r += 1
    for row in rows:
        if isinstance(row, dict):  # заголовок блока (сводный отчёт за несколько периодов)
            if out and sheet.get("repeat_headers"):
                r += 1
            out.append('<row r="{}">{}</row>'.format(r + 1, _cell("A{}".format(r + 1), row.get("group", ""), 3)))
            merges.append("A{0}:{1}{0}".format(r + 1, _col(max(ncols - 1, 0))))
            r += 1
            if sheet.get("repeat_headers") and headers:
                cells = "".join(_cell("{}{}".format(_col(i), r + 1), h, 2) for i, h in enumerate(headers))
                out.append('<row r="{}">{}</row>'.format(r + 1, cells))
                r += 1
            continue
        cells = "".join(_cell("{}{}".format(_col(i), r + 1), v, 1) for i, v in enumerate(row))
        out.append('<row r="{}">{}</row>'.format(r + 1, cells))
        r += 1
    for note in sheet.get("footer") or []:
        r += 1
        out.append('<row r="{}">{}</row>'.format(r + 1, _cell("A{}".format(r + 1), note, 0)))
    cols = ""
    if widths:
        cols = "<cols>" + "".join('<col min="{0}" max="{0}" width="{1}" customWidth="1"/>'.format(i + 1, w)
                                  for i, w in enumerate(widths)) + "</cols>"
    mc = ""
    if merges and ncols > 1:
        mc = '<mergeCells count="{}">{}</mergeCells>'.format(len(merges), "".join('<mergeCell ref="{}"/>'.format(m) for m in merges))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetPr><pageSetUpPr fitToPage="1"/></sheetPr>'
            + cols + '<sheetData>' + "".join(out) + '</sheetData>' + mc +
            '<pageMargins left="0.5" right="0.5" top="0.6" bottom="0.6" header="0.3" footer="0.3"/>'
            '<pageSetup paperSize="9" orientation="landscape" fitToWidth="1" fitToHeight="0"/>'
            '</worksheet>')


def build(sheets):
    """sheets: список dict(name, title, subtitle, headers, rows, widths, footer)."""
    bio = io.BytesIO()
    z = zipfile.ZipFile(bio, "w", zipfile.ZIP_DEFLATED)
    n = len(sheets)
    z.writestr("[Content_Types].xml",
               '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
               '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
               '<Default Extension="xml" ContentType="application/xml"/>'
               '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
               '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
               + "".join('<Override PartName="/xl/worksheets/sheet{}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'.format(i + 1) for i in range(n)) +
               '</Types>')
    z.writestr("_rels/.rels",
               '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
               '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
               '</Relationships>')
    used = set()
    names = []
    for i, s in enumerate(sheets):
        nm = "".join(ch for ch in (s.get("name") or "Лист{}".format(i + 1)) if ch not in '[]:*?/\\')[:31] or "Лист"
        base, k = nm, 2
        while nm.lower() in used:
            nm = (base[:28] + "_" + str(k)); k += 1
        used.add(nm.lower()); names.append(nm)
    z.writestr("xl/workbook.xml",
               '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
               + "".join('<sheet name="{}" sheetId="{}" r:id="rId{}"/>'.format(escape(nm, {'"': "&quot;"}), i + 1, i + 1) for i, nm in enumerate(names)) +
               '</sheets></workbook>')
    z.writestr("xl/_rels/workbook.xml.rels",
               '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
               + "".join('<Relationship Id="rId{0}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{0}.xml"/>'.format(i + 1) for i in range(n)) +
               '<Relationship Id="rId{}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'.format(n + 1) +
               '</Relationships>')
    z.writestr("xl/styles.xml", STYLES)
    for i, s in enumerate(sheets):
        z.writestr("xl/worksheets/sheet{}.xml".format(i + 1), _sheet_xml(s))
    z.close()
    return bio.getvalue()


# ------------------------------------------------------------ чтение xlsx (импорт)
def read_first_sheet(data):
    """Прочитать первый лист .xlsx в список строк (значения как строки)."""
    import re
    import xml.etree.ElementTree as ET
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    z = zipfile.ZipFile(io.BytesIO(data))
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        root = ET.fromstring(z.read("xl/sharedStrings.xml"))
        for si in root.findall("m:si", ns):
            shared.append("".join(t.text or "" for t in si.iter("{%s}t" % ns["m"])))
    sheet_files = sorted(n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n))
    if not sheet_files:
        return []
    first = "xl/worksheets/sheet1.xml" if "xl/worksheets/sheet1.xml" in sheet_files else sheet_files[0]
    root = ET.fromstring(z.read(first))
    rows = []
    for row in root.iter("{%s}row" % ns["m"]):
        vals = {}
        for c in row.findall("m:c", ns):
            ref = c.get("r", "")
            letters = "".join(ch for ch in ref if ch.isalpha())
            idx = 0
            for ch in letters:
                idx = idx * 26 + (ord(ch) - 64)
            idx -= 1
            t = c.get("t")
            v = c.find("m:v", ns)
            if t == "s" and v is not None:
                val = shared[int(v.text)]
            elif t == "inlineStr":
                val = "".join(x.text or "" for x in c.iter("{%s}t" % ns["m"]))
            else:
                val = v.text if v is not None else ""
                if val and val.endswith(".0"):
                    val = val[:-2]
            vals[idx] = val
        if vals:
            rows.append([vals.get(i, "") for i in range(max(vals) + 1)])
    return rows
