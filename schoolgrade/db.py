# -*- coding: utf-8 -*-
"""Хранилище данных SchoolGrade (SQLite, один файл schoolgrade.db).

Один процесс-сервер владеет базой; клиенты в локальной сети работают
через браузер, поэтому блокировок файла по сети нет."""
import json
import os
import shutil
import sqlite3
import threading
import time

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS staff (
    id INTEGER PRIMARY KEY,
    last_name TEXT NOT NULL, first_name TEXT NOT NULL DEFAULT '', middle_name TEXT DEFAULT '',
    position TEXT DEFAULT '', education TEXT DEFAULT '', category TEXT DEFAULT '',
    degree TEXT DEFAULT '', title TEXT DEFAULT '', gender TEXT DEFAULT '',
    birth_date TEXT DEFAULT '', hire_date TEXT DEFAULT '', phone TEXT DEFAULT '', email TEXT DEFAULT '',
    pc_skill TEXT DEFAULT '', subjects_text TEXT DEFAULT '', note TEXT DEFAULT '',
    extra TEXT DEFAULT '{}', active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    login TEXT NOT NULL UNIQUE COLLATE NOCASE,
    pw_hash TEXT NOT NULL, pw_salt TEXT NOT NULL,
    full_name TEXT DEFAULT '', role TEXT NOT NULL,
    staff_id INTEGER REFERENCES staff(id) ON DELETE SET NULL,
    must_change INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created REAL, last_seen REAL, ip TEXT
);

CREATE TABLE IF NOT EXISTS years (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
    start_date TEXT, end_date TEXT, is_current INTEGER NOT NULL DEFAULT 0,
    scheme TEXT NOT NULL DEFAULT 'quarters'
);

CREATE TABLE IF NOT EXISTS periods (
    id INTEGER PRIMARY KEY, year_id INTEGER NOT NULL REFERENCES years(id) ON DELETE CASCADE,
    kind TEXT NOT NULL, name TEXT NOT NULL, start_date TEXT, end_date TEXT,
    locked INTEGER NOT NULL DEFAULT 0,
    UNIQUE(year_id, kind)
);

CREATE TABLE IF NOT EXISTS subjects (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, short TEXT DEFAULT '',
    sort INTEGER NOT NULL DEFAULT 100, eval_type TEXT NOT NULL DEFAULT 'points',
    category TEXT NOT NULL DEFAULT 'subject',
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS user_staff (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    staff_id INTEGER NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
    PRIMARY KEY(user_id, staff_id)
);

CREATE TABLE IF NOT EXISTS classes (
    id INTEGER PRIMARY KEY, year_id INTEGER NOT NULL REFERENCES years(id) ON DELETE CASCADE,
    grade INTEGER NOT NULL, letter TEXT NOT NULL DEFAULT '',
    teacher_id INTEGER REFERENCES staff(id) ON DELETE SET NULL,
    profile TEXT DEFAULT '', shift INTEGER DEFAULT 1, room TEXT DEFAULT '',
    UNIQUE(year_id, grade, letter)
);

CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY,
    last_name TEXT NOT NULL, first_name TEXT NOT NULL DEFAULT '', middle_name TEXT DEFAULT '',
    gender TEXT DEFAULT '', birth_date TEXT DEFAULT '', personal_no TEXT DEFAULT '',
    address TEXT DEFAULT '', phone TEXT DEFAULT '',
    arrived_from TEXT DEFAULT '', study_form TEXT DEFAULT 'в учреждении образования',
    health_group TEXT DEFAULT '', pe_group TEXT DEFAULT '',
    language1 TEXT DEFAULT '', foreign1 TEXT DEFAULT '', foreign2 TEXT DEFAULT '',
    family_type TEXT DEFAULT '', parents TEXT DEFAULT '', parents_phone TEXT DEFAULT '',
    social TEXT DEFAULT '', hobbies TEXT DEFAULT '', note TEXT DEFAULT '',
    extra TEXT DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'active', left_date TEXT DEFAULT '', left_reason TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS class_students (
    id INTEGER PRIMARY KEY,
    class_id INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    date_in TEXT DEFAULT '', date_out TEXT DEFAULT '', note TEXT DEFAULT '',
    year_result TEXT DEFAULT '',
    UNIQUE(class_id, student_id)
);

CREATE TABLE IF NOT EXISTS assignments (
    id INTEGER PRIMARY KEY,
    class_id INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    teacher_id INTEGER REFERENCES staff(id) ON DELETE SET NULL,
    group_name TEXT NOT NULL DEFAULT '',
    hours REAL DEFAULT 1, eval_type TEXT NOT NULL DEFAULT 'points',
    UNIQUE(class_id, subject_id, group_name)
);

CREATE TABLE IF NOT EXISTS assignment_students (
    assignment_id INTEGER NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    PRIMARY KEY(assignment_id, student_id)
);

CREATE TABLE IF NOT EXISTS lessons (
    id INTEGER PRIMARY KEY,
    assignment_id INTEGER NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
    date TEXT NOT NULL, num INTEGER DEFAULT 1,
    topic TEXT DEFAULT '', homework TEXT DEFAULT '', work_type TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_lessons_a ON lessons(assignment_id, date);

CREATE TABLE IF NOT EXISTS marks (
    lesson_id INTEGER NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    value TEXT NOT NULL, comment TEXT DEFAULT '',
    updated_by INTEGER, updated_at TEXT,
    PRIMARY KEY(lesson_id, student_id)
);

CREATE TABLE IF NOT EXISTS final_marks (
    assignment_id INTEGER NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    kind TEXT NOT NULL, value TEXT NOT NULL, excused INTEGER NOT NULL DEFAULT 0,
    updated_by INTEGER, updated_at TEXT,
    PRIMARY KEY(assignment_id, student_id, kind)
);

CREATE TABLE IF NOT EXISTS behavior (
    class_id INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    kind TEXT NOT NULL, value TEXT NOT NULL,
    updated_by INTEGER, updated_at TEXT,
    PRIMARY KEY(class_id, student_id, kind)
);

CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY, ts TEXT DEFAULT (datetime('now','localtime')),
    user_id INTEGER, login TEXT, ip TEXT, action TEXT, details TEXT
);
CREATE INDEX IF NOT EXISTS ix_audit_ts ON audit(ts);

CREATE TABLE IF NOT EXISTS dep_packages (
    id INTEGER PRIMARY KEY, school TEXT DEFAULT '', year TEXT DEFAULT '',
    filename TEXT DEFAULT '', data TEXT NOT NULL,
    uploaded_at TEXT DEFAULT (datetime('now','localtime'))
);
"""

DEFAULT_SUBJECTS = [
    "Белорусский язык", "Белорусская литература", "Русский язык", "Русская литература",
    "Иностранный язык", "Математика", "Алгебра", "Геометрия", "Информатика",
    "Человек и мир", "Всемирная история", "История Беларуси", "Обществоведение",
    "География", "Биология", "Физика", "Астрономия", "Химия",
    "Трудовое обучение", "Изобразительное искусство", "Музыка", "Искусство (отечественная и мировая художественная культура)",
    "Черчение", "Физическая культура и здоровье", "Допризывная и медицинская подготовка",
    "Основы безопасности жизнедеятельности",
]


SUBJECT_SHORT = {
    "Белорусский язык": "Бел. яз.", "Белорусская литература": "Бел. лит.", "Русский язык": "Рус. яз.",
    "Русская литература": "Рус. лит.", "Иностранный язык": "Ин. яз.", "Математика": "Матем.", "Алгебра": "Алгебра",
    "Геометрия": "Геометр.", "Информатика": "Информ.", "Человек и мир": "Чел. и мир", "Всемирная история": "Всем. ист.",
    "История Беларуси": "Ист. Бел.", "Обществоведение": "Общество.", "География": "Географ.", "Биология": "Биолог.",
    "Физика": "Физика", "Астрономия": "Астроном.", "Химия": "Химия", "Трудовое обучение": "Труд. обуч.",
    "Изобразительное искусство": "ИЗО", "Музыка": "Музыка", "Искусство (отечественная и мировая художественная культура)": "Искусство",
    "Черчение": "Черчение", "Физическая культура и здоровье": "ФКиЗ", "Допризывная и медицинская подготовка": "ДП/МП",
    "Основы безопасности жизнедеятельности": "ОБЖ",
}


class Database(object):
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        new = not os.path.exists(path)
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA synchronous = NORMAL")
        with self.lock:
            meta_exists = self.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone() is not None
            had_schema = meta_exists and self.conn.execute(
                "SELECT 1 FROM meta WHERE key='schema'").fetchone() is not None
            self.conn.executescript(SCHEMA)
            if not had_schema:
                self.conn.execute("INSERT OR IGNORE INTO meta(key,value) VALUES('schema',?)", (str(SCHEMA_VERSION),))
            self._migrate()
        self.is_new = new

    def _migrate(self):
        """Автоматически привести базу прежней версии к текущей схеме,
        сохранив все данные. Запускается при каждом открытии базы."""
        cur = int(self.conn.execute("SELECT value FROM meta WHERE key='schema'").fetchone()[0])
        if cur >= SCHEMA_VERSION:
            return

        def has_col(table, col):
            return any(r[1] == col for r in self.conn.execute("PRAGMA table_info(%s)" % table))

        # --- версия 1 -> 2 ---
        if cur < 2:
            if not has_col("subjects", "category"):
                self.conn.execute("ALTER TABLE subjects ADD COLUMN category TEXT NOT NULL DEFAULT 'subject'")
            self.conn.execute("CREATE TABLE IF NOT EXISTS user_staff ("
                              "user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, "
                              "staff_id INTEGER NOT NULL REFERENCES staff(id) ON DELETE CASCADE, "
                              "PRIMARY KEY(user_id, staff_id))")
            # перенести привязку staff_id из users в user_staff
            for u in self.conn.execute("SELECT id, staff_id FROM users WHERE staff_id IS NOT NULL").fetchall():
                self.conn.execute("INSERT OR IGNORE INTO user_staff(user_id,staff_id) VALUES(?,?)", (u[0], u[1]))
            # обфускация существующих персональных данных
            try:
                from . import crypto
                for table in ("students", "staff"):
                    cols = crypto.SENSITIVE[table]
                    rows = self.conn.execute("SELECT id, %s FROM %s" % (",".join(cols), table)).fetchall()
                    for r in rows:
                        upd = {}
                        for i, c in enumerate(cols):
                            v = r[i + 1]
                            if v not in (None, "") and not crypto.is_encrypted(v):
                                upd[c] = crypto.encrypt(v)
                        if upd:
                            self.conn.execute("UPDATE %s SET %s WHERE id=?" % (table, ",".join(k + "=?" for k in upd)),
                                              list(upd.values()) + [r[0]])
            except Exception as e:  # noqa
                import sys
                sys.stderr.write("Обфускация при миграции пропущена: %s\n" % e)
            self.conn.execute("UPDATE meta SET value='2' WHERE key='schema'")

    # ------------------------------------------------------------ helpers
    def q(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        with self.lock:
            r = self.conn.execute(sql, args).fetchone()
            return dict(r) if r else None

    def val(self, sql, args=()):
        with self.lock:
            r = self.conn.execute(sql, args).fetchone()
            return r[0] if r else None

    def ex(self, sql, args=()):
        with self.lock:
            cur = self.conn.execute(sql, args)
            return cur.lastrowid

    def tx(self):
        return _Tx(self)

    # ------------------------------------------------------------ settings
    def get_setting(self, key, default=None):
        v = self.val("SELECT value FROM settings WHERE key=?", (key,))
        if v is None:
            return default
        try:
            return json.loads(v)
        except ValueError:
            return v

    def set_setting(self, key, value):
        self.ex("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value, ensure_ascii=False)))

    def audit(self, user, ip, action, details=""):
        try:
            if not isinstance(details, str):
                details = json.dumps(details, ensure_ascii=False)
            self.ex("INSERT INTO audit(user_id,login,ip,action,details) VALUES(?,?,?,?,?)",
                    (user and user.get("id"), user and user.get("login"), ip, action, details[:4000]))
        except Exception:
            pass

    # ------------------------------------------------------------ backups
    def backup(self, dest_dir, label="manual"):
        if not os.path.isdir(dest_dir):
            os.makedirs(dest_dir)
        name = "schoolgrade_{}_{}.db".format(time.strftime("%Y-%m-%d_%H-%M-%S"), label)
        dest = os.path.join(dest_dir, name)
        with self.lock:
            target = sqlite3.connect(dest)
            try:
                self.conn.backup(target)
            finally:
                target.close()
        return dest

    def prune_backups(self, dest_dir, keep=30):
        try:
            files = sorted(f for f in os.listdir(dest_dir) if f.startswith("schoolgrade_") and f.endswith(".db"))
            for f in files[:-keep]:
                os.remove(os.path.join(dest_dir, f))
        except OSError:
            pass

    def restore(self, src):
        """Восстановить базу из файла резервной копии (заменяет текущие данные)."""
        with self.lock:
            check = sqlite3.connect(src)
            try:
                tables = [r[0] for r in check.execute("SELECT name FROM sqlite_master WHERE type='table'")]
                if "students" not in tables or "final_marks" not in tables:
                    raise ValueError("Файл не является базой SchoolGrade")
                check.backup(self.conn)
            finally:
                check.close()

    def close(self):
        with self.lock:
            self.conn.close()


class _Tx(object):
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        self.db.lock.acquire()
        self.db.conn.execute("BEGIN")
        return self.db

    def __exit__(self, et, ev, tb):
        try:
            if et is None:
                self.db.conn.execute("COMMIT")
            else:
                self.db.conn.execute("ROLLBACK")
        finally:
            self.db.lock.release()
        return False


def copy_file(src, dst):
    shutil.copyfile(src, dst)
