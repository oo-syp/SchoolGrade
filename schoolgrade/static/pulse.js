/* SchoolPulse («Активность») — интерфейс модуля. Подключается из app.js через window.SG_PULSE(api). ES2017, без библиотек. */
"use strict";
window.SG_PULSE = function (A) {
  var h = A.h, S = A.S, GET = A.GET, POST = A.POST, PUT = A.PUT, DEL = A.DEL, toast = A.toast, clear = A.clear, safe = A.safe;
  var INPUT_ROLES = ["admin", "workspace", "teacher", "classteacher"];

  function canInput() { return INPUT_ROLES.indexOf(S.me.user.role) >= 0; }
  function vib(ms) { try { if (navigator.vibrate) navigator.vibrate(ms || 25); } catch (e) { /* нет вибрации */ } }
  function ico(svg) { var s = h("span", { class: "pl-ico" }); s.innerHTML = svg || ""; return s; }
  function hm(t) { return (t || "").slice(11, 16); }
  function dialog(title, text, buttons, extra) {
    var bg = h("div", { class: "modal-bg" });
    var close = function () { bg.remove(); };
    var foot = h("footer");
    buttons.forEach(function (b) {
      foot.appendChild(h("button", { class: "pl-btn " + (b.cls || ""), onclick: safe(async function () { if ((await b.fn(close)) !== false) close(); }) }, b.text));
    });
    bg.appendChild(h("div", { class: "modal" }, h("header", null, title), h("div", { class: "body" }, h("p", null, text), extra || null), foot));
    document.body.appendChild(bg);
    return close;
  }

  // ------------------------------------------------------------------ ввод событий (мобильный экран)
  function InputScreen(box) {
    var st = { screen: "today", types: [], lessons: [], lesson: null, data: null, sel: null, mass: false, last: null, extra: null };

    async function loadTypes() { st.types = (await GET("/api/pulse/event-types")).types; }

    async function today() {
      st.screen = "today"; st.lesson = null; st.extra = null;
      var r = await GET("/api/pulse/lessons/today");
      st.lessons = r.lessons; st.bells = r.bells;
      draw();
    }
    async function openLesson(l) {
      var id = l.lesson_id;
      if (!id) id = (await POST("/api/pulse/lessons/open", { assignment_id: l.assignment_id, num: l.num })).lesson_id;
      st.lesson = id; st.screen = "lesson"; st.sel = null; st.mass = false; st.last = null;
      await refresh();
    }
    async function refresh() {
      st.data = await GET("/api/pulse/lesson/" + st.lesson + "/events");
      draw();
    }
    function lessonTypes() { return st.types.filter(function (t) { return !t.is_extracurricular && t.role !== "return"; }); }
    function extraTypes() { return st.types.filter(function (t) { return t.is_extracurricular; }); }
    function returnType() { return st.types.filter(function (t) { return t.role === "return"; })[0]; }

    async function send(code, ids, isExtra) {
      var body = { type_code: code, student_ids: ids };
      if (isExtra) body.class_id = st.extra.class_id; else body.lesson_id = st.lesson;
      var r = await POST("/api/pulse/events", body);      // одно нажатие — одно событие, без подтверждений
      vib(r.created.length ? 25 : [40, 40, 40]);
      if (r.created.length) st.last = r.batch_id;
      if (r.skipped.length && !r.created.length) toast(r.skipped[0].reason, true);
      else if (r.skipped.length) toast("Не записано: " + r.skipped.length + " (" + r.skipped[0].reason + ")", true);
      if (isExtra) { toast("Записано: " + r.created.length); draw(); } else await refresh();
    }
    async function undo() {
      if (!st.last) { toast("Нечего отменять"); return; }
      var b = st.last; st.last = null;
      await DEL("/api/pulse/batches/" + b); vib(15);
      if (st.screen === "lesson") await refresh(); else toast("Отменено");
    }

    function tile(s, selected, onTap) {
      var out = s.state === "out";
      var t = h("button", { type: "button", class: "pl-tile" + (selected ? " sel" : "") + (out ? " out" : ""), onclick: onTap },
        out ? h("span", { class: "door" }, "🚪") : null, s.short,
        (s.pos || s.neg) ? h("span", { class: "cnt" }, s.pos ? h("span", { class: "p" }, "+" + s.pos) : "", " ", s.neg ? h("span", { class: "n" }, "−" + s.neg) : "") : null);
      return t;
    }
    function typeBtn(t, onTap) {
      var b = h("button", { type: "button", class: "pl-btn", style: { background: t.color }, onclick: safe(onTap) }, ico(t.icon), t.name);
      return b;
    }

    function drawToday() {
      var wrap = h("div", { class: "pl-lessons" });
      wrap.appendChild(h("h2", { style: { marginTop: 0 } }, "Сегодня"));
      if (!st.lessons.length) wrap.appendChild(h("div", { class: "card" }, "По расписанию у вас сегодня уроков нет. Выберите «Другой урок», если ведёте урок вне расписания."));
      st.lessons.forEach(function (l) {
        wrap.appendChild(h("button", { class: "pl-btn", onclick: safe(function () { return openLesson(l); }) },
          l.num + " урок · " + l.subject + " · " + l.class, h("small", null, [l.time, l.group, l.room ? "каб. " + l.room : ""].filter(Boolean).join(" · "))));
      });
      wrap.appendChild(h("button", { class: "pl-btn", onclick: safe(otherLesson) }, "Другой урок"));
      wrap.appendChild(h("button", { class: "pl-btn", style: { marginLeft: "8px" }, onclick: safe(offLesson) }, "Вне урока"));
      wrap.appendChild(h("button", { class: "pl-btn", style: { marginLeft: "8px" }, onclick: safe(typeSettings) }, "⚙ Типы событий"));
      return wrap;
    }

    async function chooseClass(extraOnly) {
      var r = await GET("/api/pulse/choices");
      return r.classes.filter(function (c) { return !extraOnly || c.extra; });
    }
    async function otherLesson() {
      var cls = await chooseClass(false);
      var cs = h("select", { style: { width: "100%", minHeight: "44px", fontSize: "16px" } }, cls.map(function (c) { return h("option", { value: c.class_id }, c.class); }));
      var as = h("select", { style: { width: "100%", minHeight: "44px", fontSize: "16px", marginTop: "8px" } });
      function fill() {
        clear(as);
        var c = cls.filter(function (x) { return String(x.class_id) === cs.value; })[0];
        ((c && c.assignments) || []).forEach(function (a) { as.appendChild(h("option", { value: a.id }, a.subject + (a.group ? " (" + a.group + ")" : ""))); });
      }
      cs.onchange = fill; fill();
      dialog("Другой урок", "Выберите класс и предмет. Событиям будет сопоставлена запись урока в журнале на сегодня.", [
        { text: "Открыть", cls: "pri", fn: async function () { if (!as.value) { toast("Нет доступных предметов", true); return false; } await openLesson({ assignment_id: +as.value }); } },
        { text: "Отмена", fn: function () { } }], h("div", null, cs, as));
    }
    async function offLesson() {
      var cls = await chooseClass(true);
      if (!cls.length) { toast("Внеурочные события вводит классный руководитель", true); return; }
      var cs = h("select", { style: { width: "100%", minHeight: "44px", fontSize: "16px" } }, cls.map(function (c) { return h("option", { value: c.class_id }, c.class); }));
      dialog("Вне урока", "Выберите класс для внеурочного события (дежурство, столовая и т. п.).", [
        { text: "Открыть", cls: "pri", fn: async function () {
          var ro = await GET("/api/pulse/class/" + cs.value + "/roster");
          st.extra = { class_id: +cs.value, students: ro.students, name: cs.options[cs.selectedIndex].text }; st.screen = "extra"; st.sel = null; st.last = null; draw(); } },
        { text: "Отмена", fn: function () { } }], cs);
    }
    async function typeSettings() {
      var all = (await GET("/api/pulse/event-types"));
      var cur = all.visible;
      var boxes = all.all_active.map(function (t) {
        var cb = h("input", { type: "checkbox", checked: !cur || cur.indexOf(t.code) >= 0, style: { width: "22px", height: "22px" } });
        return { code: t.code, cb: cb, row: h("label", { style: { display: "flex", gap: "10px", alignItems: "center", minHeight: "44px" } }, cb, t.name + "  ", h("span", { class: "pl-sub" }, t.code)) };
      });
      dialog("Какие кнопки показывать", "Отключённые типы скрываются с экрана ввода (в справочнике они остаются).", [
        { text: "Сохранить", cls: "pri", fn: async function () {
          await PUT("/api/pulse/visible-types", { codes: boxes.filter(function (b) { return b.cb.checked; }).map(function (b) { return b.code; }) });
          await loadTypes(); draw(); } },
        { text: "Отмена", fn: function () { } }], h("div", null, boxes.map(function (b) { return b.row; })));
    }

    function drawGrid(students, types, onType, extra) {
      var frag = h("div");
      var grid = h("div", { class: "pl-grid" });
      students.forEach(function (s) {
        grid.appendChild(tile(s, st.sel === s.id && !st.mass, function () { st.mass = false; st.sel = st.sel === s.id ? null : s.id; vib(10); draw(); }));
      });
      frag.appendChild(grid);
      var sel = students.filter(function (s) { return s.id === st.sel; })[0];
      var panel = null;
      if (st.mass) {
        var ids = students.filter(function (s) { return s.state !== "out"; }).map(function (s) { return s.id; });
        panel = h("div", { class: "pl-panel" }, types.map(function (t) { return typeBtn(t, function () { return onType(t, ids); }); }));
      } else if (sel && sel.state === "out") {
        var rt = returnType();
        panel = h("div", { class: "pl-panel" }, h("button", { class: "pl-btn big one", style: { background: rt ? rt.color : "#1a7f37", color: "#fff", border: 0 }, onclick: safe(function () { return send(rt.code, [sel.id]); }) },
          ico(rt && rt.icon), "Вернулся в класс · " + sel.short));
      } else if (sel) {
        var tt = types.filter(function (t) { return true; });
        panel = h("div", { class: "pl-panel" }, tt.map(function (t) { return typeBtn(t, function () { return onType(t, [sel.id]); }); }));
      }
      if (panel) frag.appendChild(panel);
      return frag;
    }

    function drawLesson() {
      var d = st.data, l = d.lesson;
      var w = h("div");
      w.appendChild(h("div", { class: "pl-head" },
        h("div", null, h("b", { style: { fontSize: "18px" } }, l.num + " урок · " + l.subject), h("div", { class: "pl-sub" }, l.class + (l.group ? " · " + l.group : "") + (l.ended_at ? " · завершён " + hm(l.ended_at) : ""))),
        h("button", { class: "pl-btn", onclick: safe(today) }, "← Назад")));
      w.appendChild(h("div", { class: "pl-head" },
        h("button", { class: "pl-btn" + (st.mass ? " pri" : ""), onclick: function () { st.mass = !st.mass; st.sel = null; draw(); } }, "Весь класс"),
        h("button", { class: "pl-btn", onclick: safe(undo), "aria-label": "Отменить последнее действие" }, "↶"),
        h("button", { class: "pl-btn", onclick: safe(endLesson) }, "Завершить урок")));
      w.appendChild(drawGrid(d.students, lessonTypes(), function (t, ids) { return send(t.code, ids); }));
      var lastEv = d.events.slice(-4).reverse();
      if (lastEv.length) w.appendChild(h("div", { class: "pl-sub" }, "Последние: ", lastEv.map(function (e) { return hm(e.time) + " " + e.student.split(" ")[0] + " — " + e.code; }).join(" · ")));
      return w;
    }

    function drawExtra() {
      var w = h("div");
      w.appendChild(h("div", { class: "pl-head" }, h("div", null, h("b", { style: { fontSize: "18px" } }, "Вне урока · " + st.extra.name), h("div", { class: "pl-sub" }, "Внеурочные события")),
        h("button", { class: "pl-btn", onclick: safe(today) }, "← Назад")));
      w.appendChild(h("div", { class: "pl-head" }, h("button", { class: "pl-btn" + (st.mass ? " pri" : ""), onclick: function () { st.mass = !st.mass; st.sel = null; draw(); } }, "Весь класс"),
        h("button", { class: "pl-btn", onclick: safe(undo) }, "↶")));
      var ty = extraTypes();
      if (!ty.length) w.appendChild(h("div", { class: "card" }, "Внеурочные типы отключены. Администратор включает их в «Активность → Справочник»."));
      w.appendChild(drawGrid(st.extra.students, ty, function (t, ids) { return send(t.code, ids, true); }));
      return w;
    }

    function endLesson() {
      return POST("/api/pulse/lesson/" + st.lesson + "/end", {}).then(function () { toast("Урок завершён"); return today(); }, function (e) {
        if (e.status !== 409) throw e;
        var open = st.data.students.filter(function (s) { return s.state === "out"; }).length;
        var tm = h("input", { type: "time", style: { minHeight: "44px", fontSize: "16px" } });
        dialog("Завершить урок", "У " + open + " учащихся не отмечено возвращение в класс. Зафиксировать их отсутствие до конца урока?", [
          { text: "Да, зафиксировать", cls: "pri", fn: async function () {
            await POST("/api/pulse/lesson/" + st.lesson + "/end", { fix_open: true, return_time: tm.value || undefined });
            toast("Урок завершён"); await today(); } },
          { text: "Отмена, я исправлю", fn: function () { } }],
          h("label", { class: "pl-sub" }, "Примерное время возвращения (необязательно): ", tm));
      });
    }

    function draw() {
      clear(box);
      var w = st.screen === "lesson" ? drawLesson() : st.screen === "extra" ? drawExtra() : drawToday();
      box.appendChild(w);
    }
    return { start: safe(async function () { await loadTypes(); await today(); }) };
  }

  // ------------------------------------------------------------------ вспомогательное для таблиц
  function sortable(headers, rows, opts) {
    opts = opts || {};
    var sortCol = opts.col === undefined ? -1 : opts.col, dir = opts.dir || 1;
    var tw = h("div", { class: "tw" });
    function draw() {
      var rs = rows.slice();
      if (sortCol >= 0) rs.sort(function (a, b) {
        var x = a.cells[sortCol], y = b.cells[sortCol];
        if (typeof x === "number" && typeof y === "number") return (x - y) * dir;
        return String(x).localeCompare(String(y), "ru") * dir;
      });
      var tb = h("tbody");
      rs.forEach(function (r) {
        var tr = h("tr", { onclick: r.onclick || null, style: r.onclick ? { cursor: "pointer" } : null });
        r.cells.forEach(function (c, i) { tr.appendChild(h("td", { class: typeof c === "number" ? "n" : "" }, r.render && r.render[i] ? r.render[i] : String(c))); });
        tb.appendChild(tr);
      });
      var th = h("tr");
      headers.forEach(function (t, i) {
        th.appendChild(h("th", { class: "sortable" + (typeof (rows[0] && rows[0].cells[i]) === "number" ? " n" : ""), onclick: function () { if (sortCol === i) dir = -dir; else { sortCol = i; dir = 1; } draw(); } },
          t + (sortCol === i ? (dir > 0 ? " ▲" : " ▼") : "")));
      });
      clear(tw).appendChild(h("table", { class: "t" }, h("thead", null, th), tb));
    }
    draw();
    return tw;
  }

  function svgBars(items, w) {
    // items: [{label, pos, neg}] — горизонтальные столбики позитива (зелёный) и негатива (красный)
    var rowH = 22, lab = 150, W = w || 640, max = 1;
    items.forEach(function (i) { max = Math.max(max, i.pos, i.neg); });
    var ns = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 " + W + " " + (items.length * rowH + 6)); svg.setAttribute("width", "100%");
    function el(n, a, t) { var e = document.createElementNS(ns, n); for (var k in a) e.setAttribute(k, a[k]); if (t !== undefined) e.textContent = t; svg.appendChild(e); }
    var half = (W - lab - 40) / 2;
    items.forEach(function (it, i) {
      var y = i * rowH + 3;
      el("text", { x: lab - 6, y: y + 14, "text-anchor": "end", "font-size": 12, fill: "#223" }, it.label);
      var pw = it.pos / max * half, nw = it.neg / max * half;
      el("rect", { x: lab, y: y + 2, width: Math.max(pw, 0), height: 14, fill: "#1a7f37" });
      el("rect", { x: lab + half + 30, y: y + 2, width: Math.max(nw, 0), height: 14, fill: "#cf222e" });
      el("text", { x: lab + pw + 4, y: y + 14, "font-size": 11, fill: "#445" }, it.pos ? "+" + it.pos : "");
      el("text", { x: lab + half + 30 + nw + 4, y: y + 14, "font-size": 11, fill: "#445" }, it.neg ? "−" + it.neg : "");
    });
    return svg;
  }

  var PERIODS = [["quarter", "Текущая четверть"], ["week", "7 дней"], ["month", "30 дней"], ["today", "Сегодня"], ["year", "Учебный год"]];

  // ------------------------------------------------------------------ сводка по классу
  async function SummaryTab(box, params) {
    var classes = await GET("/api/classes", { year_id: S.year && S.year.id });
    if (!classes.length) { box.appendChild(h("div", { class: "card" }, "Нет доступных классов.")); return; }
    var own = (S.me.user.own_classes || [])[0];
    var cs = A.sel(classes.map(function (c) { return [c.id, c.name]; }), params.class_id || own || classes[0].id);
    var ps = A.sel(PERIODS, params.period || "quarter");
    var scope = A.sel([["", "Все события класса"], ["mine", "Только мой предмет"]], params.scope || "");
    var out = h("div");
    var xl = h("a", { class: "pl-btn", href: "javascript:void 0", style: { textDecoration: "none", display: "inline-flex", alignItems: "center" } }, "Excel");
    box.appendChild(h("div", { class: "pl-head" }, h("div", { style: { display: "flex", gap: "8px", flexWrap: "wrap" } }, cs, ps, scope), xl));
    box.appendChild(out);
    var load = safe(async function () {
      var q = { period: ps.value, scope: scope.value };
      var r = await GET("/api/pulse/class/" + cs.value + "/summary", q);
      xl.href = "/api/pulse/class/" + cs.value + "/summary?period=" + ps.value + "&scope=" + scope.value + "&format=xlsx";
      clear(out);
      out.appendChild(h("div", { class: "pl-sub", style: { marginBottom: "6px" } }, r.class.name + " класс · " + r.period_label + " (" + r.from + " — " + r.to + ") · событий: " + r.total_events));
      var heads = ["Учащийся"].concat(r.types.map(function (t) { return t.code; })).concat(["Позитив", "Негатив", "Отсутствие, мин", "Рекомендация"]);
      var rows = r.students.map(function (s) {
        var cells = [s.name].concat(r.types.map(function (t) { return s.counts[t.id] || 0; })).concat([s.pos, s.neg, s.absence_min, s.recommendation]);
        return { cells: cells, onclick: function () { timeline(s, cs.value, ps.value); } };
      });
      out.appendChild(sortable(heads, rows));
      if (r.students.length) {
        out.appendChild(h("h3", null, "Позитив и негатив по учащимся"));
        out.appendChild(h("div", { class: "card" }, svgBars(r.students.map(function (s) { return { label: s.name, pos: s.pos, neg: s.neg }; }))));
      }
      if (r.subjects.length) {
        out.appendChild(h("h3", null, "По предметам"));
        out.appendChild(sortable(["Предмет", "Событий", "Позитив", "Негатив"], r.subjects.map(function (x) { return { cells: [x.subject, x.events, +x.pos.toFixed(1), +x.neg.toFixed(1)] }; })));
      }
      out.appendChild(h("div", { class: "pl-sub", style: { marginTop: "8px" } }, "Рекомендация носит справочный характер: отметку по поведению выставляет педагог. Нажмите на строку, чтобы открыть ленту учащегося."));
    });
    cs.onchange = ps.onchange = scope.onchange = load;
    await load();
  }

  async function timeline(s, classId, period) {
    var r = await GET("/api/pulse/student/" + s.id + "/timeline", { period: period, class_id: classId });
    var body = h("div");
    body.appendChild(h("div", { class: "pl-sub" }, r.period_label + " · суммарное отсутствие: " + r.absence_total_min + " мин"));
    var lastDate = "";
    r.items.forEach(function (i) {
      if (i.date !== lastDate) { lastDate = i.date; body.appendChild(h("div", { style: { fontWeight: "600", marginTop: "8px" } }, i.date)); }
      body.appendChild(h("div", { style: { borderLeft: "5px solid " + i.color, padding: "3px 8px", marginTop: "3px" } }, hm(i.time) + " · " + i.text, h("span", { class: "pl-sub" }, " · " + i.subject)));
    });
    if (!r.items.length) body.appendChild(h("p", null, "Событий за период нет."));
    var rec = h("div", { style: { marginTop: "12px" } });
    body.appendChild(rec);
    var u = S.me.user;
    if (u.can_manage || u.role === "workspace" || (u.own_classes || []).indexOf(+classId) >= 0) {
      try {
        var b = await GET("/api/pulse/behavior-recommendation/" + s.id, { period: period, class_id: classId });
        rec.appendChild(h("div", { class: "card" }, h("b", null, "Рекомендация по поведению: «" + b.recommended + "»"), h("div", { class: "pl-sub" }, b.explain),
          h("div", { class: "pl-sub" }, "В журнале сейчас: " + (b.current || "—") + (b.kind ? " (период " + b.kind + ")" : "")),
          b.can_apply && b.kind ? h("button", { class: "pl-btn pri", style: { marginTop: "8px" }, onclick: safe(async function () {
            if (!A.confirmBox("Записать в журнал отметку «" + b.recommended + "» по поведению для " + s.name + "?")) return;
            await POST("/api/pulse/behavior-recommendation/" + s.id + "/apply", { kind: b.kind, value: b.recommended, confirm: true, class_id: +classId });
            toast("Отметка записана в журнал"); }) }, "Записать в журнал (после подтверждения)") : null));
      } catch (e) { /* нет права на рекомендацию */ }
    }
    A.modal(s.name, body, []);
  }

  // ------------------------------------------------------------------ красные флаги
  async function FlagsTab(box) {
    var r = await GET("/api/pulse/flags/week");
    box.appendChild(h("div", { class: "pl-sub", style: { marginBottom: "8px" } }, "За период " + r.from + " — " + r.to + ". Порог: " + r.config.flag_negatives + " и более негативных событий или отсутствие более " + r.config.flag_absence_min + " мин."));
    if (!r.flags.length) box.appendChild(h("div", { class: "card" }, "Красных флагов за неделю нет."));
    r.flags.forEach(function (f) {
      box.appendChild(h("div", { class: "pl-flag" }, h("b", null, f.student), " · " + f.class, h("div", null, f.reasons.join("; "))));
    });
  }

  // ------------------------------------------------------------------ сводка по школе
  async function OverviewTab(box, params) {
    var ps = A.sel(PERIODS, params.period || "quarter");
    var out = h("div");
    box.appendChild(h("div", { class: "pl-head" }, ps)); box.appendChild(out);
    var load = safe(async function () {
      var r = await GET("/api/pulse/overview", { period: ps.value });
      clear(out);
      out.appendChild(h("div", { class: "pl-sub" }, r.period_label + " · событий: " + r.total_events));
      [["parallels", "По параллелям"], ["classes", "По классам"], ["teachers", "По учителям"], ["subjects", "По предметам"]].forEach(function (p) {
        out.appendChild(h("h3", null, p[1]));
        out.appendChild(sortable(["Название", "Событий", "Позитив", "Негатив", "Отсутствие, мин"], r[p[0]].map(function (x) { return { cells: [x.name, x.events, x.pos, x.neg, x.absence_min] }; })));
        if (r[p[0]].length) out.appendChild(h("div", { class: "card" }, svgBars(r[p[0]].map(function (x) { return { label: x.name, pos: x.pos, neg: x.neg }; }))));
      });
    });
    ps.onchange = load; await load();
  }

  // ------------------------------------------------------------------ справочник (администратор)
  async function SettingsTab(box) {
    var r = await GET("/api/pulse/event-types", { all: 1 });
    var cfg = r.config;
    box.appendChild(h("h3", { style: { marginTop: 0 } }, "Справочник типов событий"));
    var rows = r.types.map(function (t) {
      return { cells: [t.sort_order, t.code, t.name, r.categories[t.category], t.behavior_weight, t.max_per_lesson || "—", t.is_active ? "да" : "нет"],
        render: { 1: h("span", null, h("span", { class: "pl-swatch", style: { background: t.color } }), t.code) }, onclick: function () { editType(t); } };
    });
    box.appendChild(sortable(["№", "Код", "Название", "Категория", "Вес", "Макс. за урок", "Активен"], rows, { col: 0 }));
    box.appendChild(h("button", { class: "pl-btn pri", style: { marginTop: "8px" }, onclick: function () { editType(null); } }, "+ Новый тип"));

    box.appendChild(h("h3", null, "Формула рекомендации по поведению"));
    var f = {};
    function num(k, label) { f[k] = h("input", { type: "number", min: "1", step: "1", value: cfg[k], style: { width: "90px" } }); return A.field(label, f[k]); }
    f.base = A.sel(["прим.", "уд.", "неуд."], cfg.base);
    box.appendChild(h("div", { class: "card" }, A.field("Базовая отметка", f.base), num("pos_step", "+1 ступень за каждые N позитивных (с учётом веса)"), num("neg_step", "−1 ступень за каждые N негативных"),
      num("flag_negatives", "«Красный флаг»: негативных за неделю, от"), num("flag_absence_min", "«Красный флаг»: отсутствие за неделю, мин, более"),
      h("button", { class: "pl-btn pri", onclick: safe(async function () {
        await PUT("/api/pulse/config", { base: f.base.value, pos_step: f.pos_step.value, neg_step: f.neg_step.value, flag_negatives: f.flag_negatives.value, flag_absence_min: f.flag_absence_min.value });
        toast("Сохранено"); }) }, "Сохранить")));

    var ar = await GET("/api/pulse/archive");
    box.appendChild(h("h3", null, "Архив"));
    box.appendChild(h("div", { class: "card" }, h("p", null, "Событий в журнале: " + ar.events + "; в архиве: " + ar.archived + ". События старше " + ar.years + " лет переносятся в архив автоматически раз в сутки."),
      h("button", { class: "pl-btn", onclick: safe(async function () { var m = await POST("/api/pulse/archive", {}); toast("Перенесено в архив: " + m.moved); }) }, "Архивировать сейчас"), " ",
      h("button", { class: "pl-btn", onclick: safe(async function () {
        if (!A.confirmBox("Безвозвратно удалить весь архив событий?")) return;
        var m = await DEL("/api/pulse/archive"); toast("Удалено: " + m.deleted); }) }, "Очистить архив")));
    box.appendChild(h("div", { class: "card" }, h("b", null, "Публичный экран класса: "), "/kiosk/pulse/class/<номер класса> — открывается без входа, имена показываются только у позитивных событий.",
      h("div", { class: "pl-sub" }, "Внимание: Закон № 99-З и постановление № 184 — отметки автоматически не выставляются; рекомендуем закрепить порядок использования модуля внутренним локальным актом учреждения.")));

    function editType(t) {
      var d = t || { code: "", name: "", category: "positive", role: "", polarity: "pos", color: "#1a7f37", icon: "", is_active: 1, is_extracurricular: 0, affects_behavior_recommendation: 1, behavior_weight: 1, max_per_lesson: 0, sort_order: 100 };
      var i = {
        code: h("input", { value: d.code }), name: h("input", { value: d.name }), category: A.sel(Object.keys(r.categories).map(function (k) { return [k, r.categories[k]]; }), d.category),
        polarity: A.sel([["pos", "позитив"], ["neg", "негатив"], ["none", "не влияет"]], d.polarity), color: h("input", { type: "color", value: d.color }),
        icon: h("textarea", { rows: 3, style: { width: "100%" } }, d.icon), weight: h("input", { type: "number", step: "0.1", min: "0", value: d.behavior_weight, style: { width: "80px" } }),
        max: h("input", { type: "number", min: "0", value: d.max_per_lesson, style: { width: "80px" } }), sort: h("input", { type: "number", value: d.sort_order, style: { width: "80px" } }),
        active: h("input", { type: "checkbox", checked: !!d.is_active }), extra: h("input", { type: "checkbox", checked: !!d.is_extracurricular }), aff: h("input", { type: "checkbox", checked: !!d.affects_behavior_recommendation })
      };
      var body = h("div", null, A.field("Код", i.code), A.field("Название", i.name), A.field("Категория", i.category), A.field("Влияние на поведение", i.polarity), A.field("Цвет", i.color),
        A.field("Вес (0 — не влияет)", i.weight), A.field("Максимум на ученика за урок (0 — без ограничения)", i.max), A.field("Порядок", i.sort),
        A.field("Иконка (SVG)", i.icon), A.field("Активен", i.active), A.field("Внеурочный (вводится вне урока)", i.extra), A.field("Учитывать в рекомендации", i.aff));
      var btns = [{ text: "Сохранить", cls: "primary", fn: async function () {
        var data = { code: i.code.value, name: i.name.value, category: i.category.value, polarity: i.polarity.value, color: i.color.value, icon: i.icon.value,
          behavior_weight: i.weight.value, max_per_lesson: i.max.value, sort_order: i.sort.value, is_active: i.active.checked, is_extracurricular: i.extra.checked, affects_behavior_recommendation: i.aff.checked };
        if (t) await PUT("/api/pulse/event-types/" + t.id, data); else await POST("/api/pulse/event-types", data);
        route(); } }];
      if (t) btns.push({ text: "Удалить", cls: "danger", fn: async function () { if (!A.confirmBox("Удалить тип «" + t.code + "»? Если есть события, тип будет только отключён.")) return false; await DEL("/api/pulse/event-types/" + t.id); route(); } });
      A.modal(t ? "Тип события" : "Новый тип события", body, btns);
    }
  }
  function route() { return safe(A.ROUTES.pulse)({ tab: "settings" }); }

  // ------------------------------------------------------------------ страница
  A.ROUTES.pulse = async function (params) {
    var u = S.me.user;
    var tabs = [];
    if (canInput()) tabs.push(["input", "Ввод"]);
    tabs.push(["summary", "Сводка класса"], ["flags", "Красные флаги"]);
    if (u.can_manage || u.role === "workspace" || u.role === "viewer") tabs.push(["overview", "По школе"]);
    if (u.can_admin) tabs.push(["settings", "Справочник"]);
    var tab = tabs.filter(function (t) { return t[0] === params.tab; })[0] ? params.tab : tabs[0][0];
    var main = A.getMain();
    clear(main);
    A.head("Активность", "События на уроке: ответы, замечания, выходы из класса");
    var wrap = h("div", { class: "pl-wrap" });
    var nav = h("div", { class: "pl-tabs" }, tabs.map(function (t) {
      return h("a", { href: "javascript:void 0", class: t[0] === tab ? "on" : "", onclick: function () { params.tab = t[0]; A.setParams(params); safe(A.ROUTES.pulse)(params); } }, t[1]);
    }));
    var box = h("div");
    wrap.appendChild(nav); wrap.appendChild(box); main.appendChild(wrap);
    if (tab === "input") InputScreen(box).start();
    else if (tab === "summary") await SummaryTab(box, params);
    else if (tab === "flags") await FlagsTab(box);
    else if (tab === "overview") await OverviewTab(box, params);
    else if (tab === "settings") await SettingsTab(box);
  };
};
