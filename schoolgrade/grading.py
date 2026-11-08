# -*- coding: utf-8 -*-
"""Правила оценивания и расчёт показателей успеваемости.

Нормативная основа (проверяйте актуальную редакцию на pravo.by):
  * Кодекс Республики Беларусь об образовании;
  * Правила проведения аттестации учащихся при освоении содержания
    образовательных программ общего среднего образования
    (постановление Министерства образования РБ от 11.07.2022 № 184,
    с изменениями и дополнениями);
  * Нормы оценки результатов учебной деятельности учащихся по учебным предметам.

Все числовые параметры (минимум отметок, порог «качества знаний» и т.п.)
вынесены в настройки программы, чтобы их можно было привести в соответствие
с новой редакцией документов без изменения кода.
"""
from decimal import Decimal, ROUND_HALF_UP

# ---------------------------------------------------------------- отметки
POINTS = [str(i) for i in range(1, 11)]

PASS = "зач."         # зачтено
FAIL = "незач."       # не зачтено
EXEMPT = "осв."       # освобождён
NOT_ATT = "н/а"       # не аттестован
NOT_STUDIED = "н/и"   # не изучал
ABSENT = "н"          # отсутствовал на уроке (только в текущем журнале)

SPECIAL_FINAL = [PASS, FAIL, EXEMPT, NOT_ATT, NOT_STUDIED]
SPECIAL_CURRENT = [ABSENT, PASS, FAIL, EXEMPT]

BEHAVIOR = ["прим.", "уд.", "неуд."]
BEHAVIOR_FULL = {"прим.": "примерное", "уд.": "удовлетворительное",
                 "неуд.": "неудовлетворительное"}

# Синонимы, которые учитель может ввести с клавиатуры
ALIASES = {
    "зач": PASS, "зачт": PASS, "зачтено": PASS, "з": PASS,
    "незач": FAIL, "не зач": FAIL, "незачтено": FAIL, "нз": FAIL,
    "осв": EXEMPT, "освобожден": EXEMPT, "освобождён": EXEMPT, "о": EXEMPT,
    "н/а": NOT_ATT, "на": NOT_ATT, "н.а": NOT_ATT, "н/а.": NOT_ATT, "неаттестован": NOT_ATT,
    "н/и": NOT_STUDIED, "ни": NOT_STUDIED, "н.и": NOT_STUDIED, "не изучал": NOT_STUDIED,
    "н": ABSENT, "y": ABSENT, "n": ABSENT,
    "прим": "прим.", "примерное": "прим.", "п": "прим.",
    "уд": "уд.", "удовлетворительное": "уд.",
    "неуд": "неуд.", "неудовлетворительное": "неуд.",
}

# Пять уровней результатов учебной деятельности (10-балльная шкала)
LEVELS = [
    ("low", "Низкий", (1, 2)),
    ("sat", "Удовлетворительный", (3, 4)),
    ("mid", "Средний", (5, 6)),
    ("suf", "Достаточный", (7, 8)),
    ("high", "Высокий", (9, 10)),
]

# Весовые коэффициенты для СОУ (степень обученности учащихся),
# как в программе «Старт ШКОЛА» (методика В.П. Симонова для 10-балльной шкалы)
SOU_WEIGHTS = {10: 100, 9: 96, 8: 90, 7: 74, 6: 55, 5: 45, 4: 40, 3: 32, 2: 20, 1: 12}

DEFAULT_RULES = {
    "min_marks_quarter": 3,          # минимум текущих отметок для четвертной
    "min_marks_quarter_1h": 1,       # ... если предмет 1 час в неделю
    "kz_min_point": 6,               # «качество знаний»: доля отметок не ниже этого балла
    "unexcused_na_as_zero": True,    # н/а без уважительной причины = 0 при расчёте годовой
    "period_scheme": "quarters",     # quarters | halves
}


def normalize(value, kind="final"):
    """Привести введённое значение к каноническому виду.
    kind: current (текущая), final (итоговая), behavior (поведение).
    Возвращает строку или '' (очистить). Бросает ValueError при ошибке."""
    if value is None:
        return ""
    v = str(value).strip().lower().replace(",", ".")
    if v == "":
        return ""
    if kind == "behavior":
        v = ALIASES.get(v.rstrip("."), v)
        if v in BEHAVIOR:
            return v
        raise ValueError("Поведение: допустимо «прим.», «уд.», «неуд.»")
    if v.isdigit() and 1 <= int(v) <= 10:
        return str(int(v))
    canon = ALIASES.get(v, ALIASES.get(v.rstrip("."), v))
    allowed = SPECIAL_CURRENT if kind == "current" else SPECIAL_FINAL
    if canon in allowed:
        return canon
    if kind == "current":
        raise ValueError("Отметка: 1–10, «н», «зач.», «незач.», «осв.»")
    raise ValueError("Отметка: 1–10, «зач.», «незач.», «осв.», «н/а», «н/и»")


def is_point(v):
    return isinstance(v, str) and v.isdigit() and 1 <= int(v) <= 10


def round_math(x):
    """Математическое округление (0,5 — вверх). round() в Python округляет
    «к чётному», поэтому используем Decimal."""
    return int(Decimal(str(x)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def level_of(point):
    p = int(point)
    for code, name, (a, b) in LEVELS:
        if a <= p <= b:
            return code
    return None


def level_name(code):
    for c, name, _ in LEVELS:
        if c == code:
            return name
    return ""


def suggest_period_mark(current_values, hours_per_week=None, rules=None):
    """Предложить отметку за четверть (полугодие) по текущим отметкам.
    Возвращает dict: value, avg, count, reason."""
    rules = dict(DEFAULT_RULES, **(rules or {}))
    pts = [int(v) for v in current_values if is_point(v)]
    specials = [v for v in current_values if v in (PASS, FAIL, EXEMPT)]
    need = rules["min_marks_quarter_1h"] if (hours_per_week and float(hours_per_week) <= 1) \
        else rules["min_marks_quarter"]
    if not pts:
        if specials:
            last = specials[-1]
            return {"value": last, "avg": None, "count": 0, "reason": "по зачётной системе"}
        return {"value": NOT_ATT if current_values else "", "avg": None, "count": 0,
                "reason": "нет отметок" if current_values else ""}
    avg = sum(pts) / len(pts)
    if len(pts) < need:
        return {"value": NOT_ATT, "avg": round(avg, 2), "count": len(pts),
                "reason": "отметок меньше минимума ({})".format(need)}
    return {"value": str(round_math(avg)), "avg": round(avg, 2), "count": len(pts), "reason": ""}


def suggest_year_mark(period_marks, rules=None):
    """Годовая отметка по отметкам за четверти/полугодия.
    period_marks: список dict {value, excused}.
    н/а по уважительной причине — четверть не учитывается;
    н/а без уважительной причины — приравнивается к 0 (если включено правило)."""
    rules = dict(DEFAULT_RULES, **(rules or {}))
    nums = []
    vals = [m.get("value") or "" for m in period_marks]
    for m in period_marks:
        v = m.get("value") or ""
        if is_point(v):
            nums.append(int(v))
        elif v == NOT_ATT and not m.get("excused") and rules["unexcused_na_as_zero"]:
            nums.append(0)
    if nums:
        avg = sum(nums) / len(nums)
        r = round_math(avg)
        if r == 0:
            return {"value": NOT_ATT, "avg": round(avg, 2), "reason": "н/а без уважительной причины"}
        return {"value": str(r), "avg": round(avg, 2), "reason": ""}
    filled = [v for v in vals if v]
    if not filled:
        return {"value": "", "avg": None, "reason": ""}
    if all(v == EXEMPT for v in filled):
        return {"value": EXEMPT, "avg": None, "reason": ""}
    if all(v == NOT_STUDIED for v in filled):
        return {"value": NOT_STUDIED, "avg": None, "reason": ""}
    passes = [v for v in filled if v in (PASS, FAIL)]
    if passes:
        return {"value": FAIL if FAIL == passes[-1] else PASS, "avg": None, "reason": ""}
    return {"value": NOT_ATT, "avg": None, "reason": "нет отметок в баллах"}


def stats(values, rules=None):
    """Показатели по набору отметок: количество по баллам и уровням,
    средний балл, КЗ %, СОУ %, количество н/а, зач. и т.д."""
    rules = dict(DEFAULT_RULES, **(rules or {}))
    by_point = {i: 0 for i in range(1, 11)}
    other = {PASS: 0, FAIL: 0, EXEMPT: 0, NOT_ATT: 0, NOT_STUDIED: 0}
    for v in values:
        if is_point(v):
            by_point[int(v)] += 1
        elif v in other:
            other[v] += 1
    n = sum(by_point.values())
    levels = {code: sum(by_point[p] for p in range(a, b + 1)) for code, _, (a, b) in LEVELS}
    res = {
        "count": n,
        "by_point": by_point,
        "levels": levels,
        "levels_pct": {k: (round(c * 100.0 / n, 1) if n else None) for k, c in levels.items()},
        "other": other,
        "avg": None, "kz": None, "sou": None,
    }
    if n:
        res["avg"] = round(sum(p * c for p, c in by_point.items()) / n, 2)
        good = sum(c for p, c in by_point.items() if p >= int(rules["kz_min_point"]))
        res["kz"] = round(good * 100.0 / n, 1)
        res["sou"] = round(sum(SOU_WEIGHTS[p] * c for p, c in by_point.items()) / n, 1)
    return res


def student_level(values):
    """Уровень учащегося — по самой низкой отметке (как в «Старт ШКОЛА»)."""
    pts = [int(v) for v in values if is_point(v)]
    if not pts:
        return None
    return level_of(min(pts))


def period_kinds(scheme="quarters"):
    if scheme == "halves":
        return [("h1", "1 полугодие"), ("h2", "2 полугодие")]
    return [("q1", "1 четверть"), ("q2", "2 четверть"), ("q3", "3 четверть"), ("q4", "4 четверть")]


FINAL_KINDS = [("year", "Годовая"), ("exam", "Экзамен"), ("final", "Итоговая")]


def all_kinds(scheme="quarters"):
    return period_kinds(scheme) + FINAL_KINDS


def kind_name(kind):
    for k, n in period_kinds("quarters") + period_kinds("halves") + FINAL_KINDS:
        if k == kind:
            return n
    return kind
