# -*- coding: utf-8 -*-
"""Чтение книг Excel 97–2003 (.xls, BIFF8) без сторонних библиотек.

Нужен для импорта из «Старт ШКОЛА». Читает значения ячеек: числа, строки,
логические значения и сохранённые результаты формул. Форматирование,
защита листов и макросы игнорируются.

    book = open_workbook("Параллель_5.xls")
    sh = book.sheet("1ч")
    sh.cell(3, 3)  ->  '5 "А"'
"""
import struct

_SIG = b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1"
_ENDOFCHAIN = 0xFFFFFFFE
_FREESECT = 0xFFFFFFFF


class XlsError(Exception):
    pass


# ---------------------------------------------------------------- OLE2 (CFB)

class _Cfb(object):
    def __init__(self, data):
        if data[:8] != _SIG:
            raise XlsError("Файл не является книгой Excel 97–2003 (.xls)")
        self.d = data
        self.ss = 1 << struct.unpack_from("<H", data, 0x1E)[0]
        self.mss = 1 << struct.unpack_from("<H", data, 0x20)[0]
        (nfat, dir_start, _tx, self.cutoff, minifat_start, nminifat,
         difat_start, ndifat) = struct.unpack_from("<IIIIIIII", data, 0x2C)
        # DIFAT: первые 109 записей в заголовке, остальные — цепочкой секторов
        difat = list(struct.unpack_from("<109I", data, 0x4C))
        sec, per = difat_start, self.ss // 4 - 1
        guard = 0
        while sec not in (_ENDOFCHAIN, _FREESECT) and guard < ndifat + 1:
            vals = struct.unpack_from("<%dI" % (per + 1), data, self._off(sec))
            difat.extend(vals[:per])
            sec = vals[per]
            guard += 1
        self.fat = []
        n = self.ss // 4
        for s in difat[:nfat]:
            if s in (_ENDOFCHAIN, _FREESECT):
                continue
            self.fat.extend(struct.unpack_from("<%dI" % n, data, self._off(s)))
        dirdata = self._chain(dir_start)
        self.entries = []
        for i in range(len(dirdata) // 128):
            e = dirdata[i * 128:(i + 1) * 128]
            nlen = struct.unpack_from("<H", e, 0x40)[0]
            name = e[:max(nlen - 2, 0)].decode("utf-16-le", "replace")
            etype = e[0x42]
            start, size = struct.unpack_from("<II", e, 0x74)
            self.entries.append((name, etype, start, size))
        root = self.entries[0]
        self.ministream = self._chain(root[2])[:root[3]] if root[3] else b""
        self.minifat = []
        if nminifat:
            mf = self._chain(minifat_start)
            self.minifat = list(struct.unpack_from("<%dI" % (len(mf) // 4), mf))

    def _off(self, sec):
        return (sec + 1) * self.ss

    def _chain(self, start):
        out, sec, seen = [], start, 0
        limit = len(self.fat) + 1
        while sec not in (_ENDOFCHAIN, _FREESECT) and seen < limit:
            o = self._off(sec)
            out.append(self.d[o:o + self.ss])
            sec = self.fat[sec] if sec < len(self.fat) else _ENDOFCHAIN
            seen += 1
        return b"".join(out)

    def _minichain(self, start):
        out, sec, seen = [], start, 0
        while sec not in (_ENDOFCHAIN, _FREESECT) and seen < len(self.minifat) + 1:
            o = sec * self.mss
            out.append(self.ministream[o:o + self.mss])
            sec = self.minifat[sec] if sec < len(self.minifat) else _ENDOFCHAIN
            seen += 1
        return b"".join(out)

    def stream(self, *names):
        want = [n.lower() for n in names]
        for name, etype, start, size in self.entries:
            if etype == 2 and name.lower() in want:
                data = self._chain(start) if size >= self.cutoff else self._minichain(start)
                return data[:size]
        return None


# ---------------------------------------------------------------- BIFF8

def _records(data, pos):
    n = len(data)
    while pos + 4 <= n:
        rt, ln = struct.unpack_from("<HH", data, pos)
        yield rt, data[pos + 4:pos + 4 + ln], pos
        pos += 4 + ln


def _rk(v):
    if v & 2:
        x = v >> 2
        if x & 0x20000000:
            x -= 0x40000000
        x = float(x)
    else:
        x = struct.unpack("<d", struct.pack("<Q", (v & 0xFFFFFFFC) << 32))[0]
    return x / 100.0 if v & 1 else x


def _ustr(buf, pos, lenbytes=2):
    """XLUnicodeString внутри одной записи. Возвращает (строка, новая позиция)."""
    if lenbytes == 1:
        cch = buf[pos]
        pos += 1
    else:
        cch = struct.unpack_from("<H", buf, pos)[0]
        pos += 2
    flags = buf[pos]
    pos += 1
    if flags & 0x08:
        pos += 2
    if flags & 0x04:
        pos += 4
    if flags & 1:
        s = buf[pos:pos + 2 * cch].decode("utf-16-le", "replace")
        pos += 2 * cch
    else:
        s = buf[pos:pos + cch].decode("latin-1")
        pos += cch
    return s, pos


class _Chunks(object):
    """Последовательное чтение SST, разбитой на записи CONTINUE."""

    def __init__(self, chunks):
        self.c, self.i, self.p = chunks, 0, 0

    def _next(self):
        self.i += 1
        self.p = 0
        if self.i >= len(self.c):
            raise XlsError("Повреждена таблица строк (SST)")

    def read(self, n):
        out = b""
        while n > 0:
            if self.p >= len(self.c[self.i]):
                self._next()
            take = min(n, len(self.c[self.i]) - self.p)
            out += self.c[self.i][self.p:self.p + take]
            self.p += take
            n -= take
        return out

    def string(self):
        cch, flags = struct.unpack("<HB", self.read(3))
        runs = struct.unpack("<H", self.read(2))[0] if flags & 0x08 else 0
        ext = struct.unpack("<i", self.read(4))[0] if flags & 0x04 else 0
        wide = flags & 1
        parts = []
        left = cch
        while left > 0:
            if self.p >= len(self.c[self.i]):
                self._next()
                wide = self.c[self.i][0] & 1   # байт параметров в начале CONTINUE
                self.p = 1
            avail = len(self.c[self.i]) - self.p
            size = 2 if wide else 1
            take = min(left, avail // size)
            if take == 0:   # не помещается даже один символ — переход
                self.p = len(self.c[self.i])
                continue
            raw = self.c[self.i][self.p:self.p + take * size]
            parts.append(raw.decode("utf-16-le", "replace") if wide else raw.decode("latin-1"))
            self.p += take * size
            left -= take
        if runs:
            self.read(4 * runs)
        if ext > 0:
            self.read(ext)
        return "".join(parts)


class Sheet(object):
    def __init__(self, name):
        self.name = name
        self.cells = {}
        self.nrows = 0
        self.ncols = 0

    def _set(self, r, c, v):
        self.cells[(r, c)] = v
        if r + 1 > self.nrows:
            self.nrows = r + 1
        if c + 1 > self.ncols:
            self.ncols = c + 1

    def cell(self, r, c, default=""):
        return self.cells.get((r, c), default)

    def text(self, r, c):
        """Значение как строка без пробелов по краям; целые числа без «.0»."""
        v = self.cells.get((r, c), "")
        if isinstance(v, float):
            return str(int(v)) if v.is_integer() else str(v)
        if isinstance(v, bool):
            return "1" if v else "0"
        return str(v).strip()

    def row_values(self, r, start=0, end=None):
        end = self.ncols if end is None else end
        return [self.cells.get((r, c), "") for c in range(start, end)]


class Book(object):
    def __init__(self, data):
        cfb = _Cfb(data)
        wb = cfb.stream("Workbook", "Book")
        if wb is None:
            raise XlsError("В файле нет книги Excel")
        self.data = wb
        self.sst = []
        self._bounds = []      # (имя, позиция BOF листа, тип)
        self._sheets = {}
        self._globals()

    def _globals(self):
        sst_chunks = None
        last = None
        first = True
        for rt, d, _pos in _records(self.data, 0):
            if first:
                if rt != 0x0809:
                    raise XlsError("Неизвестный формат книги")
                ver = struct.unpack_from("<H", d, 0)[0]
                if ver != 0x0600:
                    raise XlsError("Поддерживаются книги Excel 97–2003 (BIFF8); сохраните файл в этом формате")
                first = False
                continue
            if rt == 0x002F:
                raise XlsError("Книга защищена паролем на открытие. Снимите пароль в Excel и повторите импорт")
            if rt == 0x0085:
                pos = struct.unpack_from("<I", d, 0)[0]
                stype = d[5]
                name, _ = _ustr(d, 6, 1)
                self._bounds.append((name, pos, stype))
            elif rt == 0x00FC:
                sst_chunks = [d[8:]]
                last = "sst"
                continue
            elif rt == 0x003C and last == "sst":
                sst_chunks.append(d)
                continue
            elif rt == 0x000A:
                break
            last = None
        if sst_chunks:
            total = struct.unpack_from("<I", self._first_sst(), 4)[0]
            rd = _Chunks(sst_chunks)
            try:
                for _ in range(total):
                    self.sst.append(rd.string())
            except XlsError:
                pass   # неполная таблица строк — используем прочитанное

    def _first_sst(self):
        for rt, d, _pos in _records(self.data, 0):
            if rt == 0x00FC:
                return d
        return b"\0" * 8

    def sheet_names(self):
        return [b[0] for b in self._bounds if b[2] == 0]

    def has_sheet(self, name):
        return name in self.sheet_names()

    def sheet(self, name):
        if name in self._sheets:
            return self._sheets[name]
        for nm, pos, stype in self._bounds:
            if nm == name:
                sh = self._parse_sheet(nm, pos)
                self._sheets[name] = sh
                return sh
        raise XlsError("Нет листа «%s»" % name)

    def _parse_sheet(self, name, pos):
        sh = Sheet(name)
        pending = None   # ячейка формулы, ожидающая запись STRING
        depth = 0
        for rt, d, _p in _records(self.data, pos):
            if rt == 0x0809:
                depth += 1
                continue
            if rt == 0x000A:
                depth -= 1
                if depth <= 0:
                    break
                continue
            if depth > 1:          # вложенные потоки (диаграммы)
                continue
            if rt == 0x00FD and len(d) >= 10:                 # LABELSST
                r, c, _xf, i = struct.unpack_from("<HHHI", d, 0)
                sh._set(r, c, self.sst[i] if i < len(self.sst) else "")
            elif rt == 0x0203 and len(d) >= 14:               # NUMBER
                r, c, _xf, v = struct.unpack_from("<HHHd", d, 0)
                sh._set(r, c, v)
            elif rt == 0x027E and len(d) >= 10:               # RK
                r, c, _xf, v = struct.unpack_from("<HHHI", d, 0)
                sh._set(r, c, _rk(v))
            elif rt == 0x00BD and len(d) >= 6:                # MULRK
                r, c0 = struct.unpack_from("<HH", d, 0)
                n = (len(d) - 6) // 6
                for k in range(n):
                    _xf, v = struct.unpack_from("<HI", d, 4 + 6 * k)
                    sh._set(r, c0 + k, _rk(v))
            elif rt == 0x0204 and len(d) >= 9:                # LABEL
                r, c = struct.unpack_from("<HH", d, 0)
                s, _ = _ustr(d, 6)
                sh._set(r, c, s)
            elif rt == 0x0205 and len(d) >= 8:                # BOOLERR
                r, c, _xf, v, err = struct.unpack_from("<HHHBB", d, 0)
                if not err:
                    sh._set(r, c, bool(v))
            elif rt == 0x0006 and len(d) >= 20:               # FORMULA
                r, c = struct.unpack_from("<HH", d, 0)
                res = d[6:14]
                if res[6:8] == b"\xff\xff":
                    t = res[0]
                    if t == 0:
                        pending = (r, c)
                        continue
                    if t == 1:
                        sh._set(r, c, bool(res[2]))
                    elif t == 3:
                        sh._set(r, c, "")
                else:
                    sh._set(r, c, struct.unpack("<d", res)[0])
            elif rt == 0x0207 and pending is not None:        # STRING
                try:
                    s, _ = _ustr(d, 0)
                except Exception:
                    s = ""
                sh._set(pending[0], pending[1], s)
            if rt not in (0x0006, 0x04BC, 0x0221, 0x0236):   # SHRFMLA/ARRAY/TABLE могут стоять перед STRING
                pending = None
        return sh


def open_workbook(src):
    """src — путь к файлу или содержимое (bytes)."""
    if isinstance(src, (bytes, bytearray)):
        data = bytes(src)
    else:
        with open(src, "rb") as f:
            data = f.read()
    return Book(data)
