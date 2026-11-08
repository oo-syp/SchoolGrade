/* SchoolGrade — веб-интерфейс. Без сторонних библиотек, ES2017 (Chrome 109 / Firefox 115 на Windows 7). */
"use strict";
(function () {
  // ===================================================================== утилиты
  var S = { me: null, year: null };
  var $app = document.getElementById("app");

  function h(tag, attrs) {
    var el = document.createElement(tag);
    if (attrs) for (var k in attrs) {
      var v = attrs[k];
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "html") el.innerHTML = v;
      else if (k.slice(0, 2) === "on") el.addEventListener(k.slice(2), v);
      else if (k === "value") el.value = v;
      else if (k === "checked") el.checked = !!v;
      else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (var i = 2; i < arguments.length; i++) add(el, arguments[i]);
    return el;
  }
  function add(el, c) {
    if (c === null || c === undefined || c === false) return;
    if (Array.isArray(c)) { c.forEach(function (x) { add(el, x); }); return; }
    el.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }
  function toast(msg, err) {
    var t = h("div", { class: err ? "err" : "" }, msg);
    document.getElementById("toast").appendChild(t);
    setTimeout(function () { t.remove(); }, err ? 6000 : 2500);
  }
  function qs(o) {
    var p = [];
    for (var k in o) if (o[k] !== undefined && o[k] !== null && o[k] !== "") p.push(encodeURIComponent(k) + "=" + encodeURIComponent(o[k]));
    return p.length ? "?" + p.join("&") : "";
  }
  S.rid = 0; // номер текущего перехода: ответы для уже покинутой страницы отбрасываются
  function StaleError() { this.message = "stale"; this.stale = true; }
  async function api(method, url, data) {
    var rid = S.rid;
    var opt = { method: method, headers: { "X-SchoolGrade": "1" }, credentials: "same-origin" };
    if (data !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(data); }
    var r = await fetch(url, opt);
    var j = null;
    try { j = await r.json(); } catch (e) { j = {}; }
    if (r.status === 401 && url !== "/api/login") { S.me = null; renderLogin(); throw new Error(j.error || "Требуется вход"); }
    if (!r.ok) { var e = new Error(j.error || ("Ошибка " + r.status)); e.status = r.status; throw e; }
    if (method === "GET" && rid !== S.rid) throw new StaleError();
    return j;
  }
  var GET = function (u, p) { return api("GET", u + qs(p || {})); };
  var POST = function (u, d) { return api("POST", u, d || {}); };
  var PUT = function (u, d) { return api("PUT", u, d || {}); };
  var DEL = function (u) { return api("DELETE", u); };
  function safe(fn) {
    return async function () {
      try { return await fn.apply(this, arguments); } catch (e) { if (!e.stale) toast(e.message, true); }
    };
  }
  function download(url, params) { window.location.href = url + qs(params); }
  function fileToB64(file) {
    return new Promise(function (res, rej) {
      var fr = new FileReader();
      fr.onload = function () { res(String(fr.result).split(",")[1]); };
      fr.onerror = rej; fr.readAsDataURL(file);
    });
  }
  function pickFile(accept, multiple) {
    // поле выбора файла должно быть в документе: «висящий» элемент браузер
    // может удалить сборщиком мусора до того, как пользователь выберет файл
    var old = document.getElementById("sg-file-pick");
    if (old) old.remove();
    return new Promise(function (res) {
      var i = h("input", { type: "file", accept: accept, id: "sg-file-pick", style: { display: "none" } });
      if (multiple) i.multiple = true;
      i.onchange = function () { var f = Array.prototype.slice.call(i.files); i.remove(); res(f); };
      document.body.appendChild(i);
      i.click();
    });
  }
  function fmtDate(d) { if (!d) return ""; var p = d.split("-"); return p.length === 3 ? p[2] + "." + p[1] + "." + p[0] : d; }
  function lvClass(v) {
    var n = parseInt(v, 10);
    if (v === "н" || v === "н/а" || v === "незач.") return "v-n";
    if (!(n >= 1)) return "";
    return n <= 2 ? "v-low" : n <= 4 ? "v-sat" : n <= 6 ? "v-mid" : n <= 8 ? "v-suf" : "v-high";
  }
  function sel(options, value, attrs) {
    var s = h("select", attrs || {});
    options.forEach(function (o) {
      var v = Array.isArray(o) ? o[0] : o, t = Array.isArray(o) ? o[1] : o;
      var op = h("option", { value: v }, t);
      if (String(v) === String(value === null || value === undefined ? "" : value)) op.selected = true;
      s.appendChild(op);
    });
    return s;
  }
  function field(label, input, cls) { return h("label", { class: "f " + (cls || "") }, label, input); }
  function modal(title, body, buttons) {
    var bg = h("div", { class: "modal-bg" });
    var close = function () { bg.remove(); };
    var foot = h("footer");
    (buttons || []).forEach(function (b) {
      foot.appendChild(h("button", { class: b.cls || "", onclick: safe(async function () { if ((await b.fn(close)) !== false) close(); }) }, b.text));
    });
    foot.appendChild(h("button", { onclick: close }, "Закрыть"));
    var m = h("div", { class: "modal" }, h("header", null, title, h("a", { href: "javascript:void 0", onclick: close }, "✕")), h("div", { class: "body" }, body), foot);
    bg.appendChild(m);
    bg.addEventListener("mousedown", function (e) { if (e.target === bg) close(); });
    document.body.appendChild(bg);
    return close;
  }
  function confirmBox(text) { return window.confirm(text); }
  // форма по описанию полей: [{k, label, type, options, wide}]
  function buildForm(fields, data) {
    var inputs = {};
    var f = h("div", { class: "form" });
    fields.forEach(function (d) {
      var v = data[d.k];
      var inp;
      if (d.type === "select") inp = sel(d.options, v);
      else if (d.type === "textarea") inp = h("textarea", { value: v || "" });
      else if (d.type === "check") inp = h("input", { type: "checkbox", checked: v === undefined ? d.def : !!v });
      else if (d.type === "list") { inp = h("input", { value: v || "", list: "dl-" + d.k }); f.appendChild(h("datalist", { id: "dl-" + d.k }, (d.options || []).map(function (o) { return h("option", { value: o }); }))); }
      else inp = h("input", { type: d.type || "text", value: v === null || v === undefined ? "" : v });
      inputs[d.k] = inp;
      f.appendChild(field(d.label, inp, d.wide ? "wide" : ""));
    });
    f.inputs = inputs;
    f.values = function () {
      var o = {};
      fields.forEach(function (d) { var i = inputs[d.k]; o[d.k] = d.type === "check" ? (i.checked ? 1 : 0) : i.value; });
      return o;
    };
    return f;
  }
  // универсальная таблица отчёта
  function reportTable(rep) {
    var vf = rep.vertical_from, vt = rep.vertical_to;
    var thead = h("tr", null, rep.headers.map(function (x, i) {
      return h("th", { class: (vf !== undefined && i >= vf && i < vt) ? "vert" : "n" }, x);
    }));
    var body = rep.rows.map(function (r) {
      if (r && !Array.isArray(r)) {
        var gr = [h("tr", { class: "grp" }, h("td", { colspan: rep.headers.length }, r.group || ""))];
        if (rep.repeat_headers) gr.push(h("tr", { class: "hdr" }, rep.headers.map(function (x) { return h("th", { class: "n" }, x); })));
        return gr;
      }
      var first = String(r[0] || "") + String(r[1] || "");
      var cls = /ИТОГО|Итого|Средний балл/.test(first) ? "total" : (rep.group_rows && r[0] === "" && r[1]) ? "grp" : "";
      return h("tr", { class: cls }, r.map(function (c, i) {
        var td = h("td", { class: (typeof c === "number" || /^[\d,.\-–]+$|^(н\/а|осв\.|зач\.|незач\.|н\/и|прим\.|уд\.|неуд\.)$/.test(String(c))) ? "n" : "" }, c === null ? "" : c);
        if (vf !== undefined && i >= vf && i < vt && c) td.className += " " + lvClass(String(c));
        return td;
      }));
    });
    var box = h("div", { class: "card report" },
      h("h2", { style: { marginTop: 0 } }, rep.title),
      rep.subtitle ? h("div", { class: "muted", style: { whiteSpace: "pre-line", marginBottom: "10px" } }, rep.subtitle) : null,
      h("div", { class: "tw" }, h("table", { class: "t" }, rep.repeat_headers ? null : h("thead", null, thead), h("tbody", null, body))),
      (rep.footer || []).map(function (f) { return h("div", { class: "hint" }, f); }));
    return box;
  }

  // ===================================================================== вход
  async function boot() {
    try {
      S.me = await GET("/api/me");
      S.year = S.me.current_year;
      if (S.me.user.must_change) return renderChangePassword(true);
      if (!S.me.org_type && S.me.user.can_manage) return renderOrgChooser();
      renderShell();
      route();
    } catch (e) {
      if (!S.me) renderLogin();
    }
  }

  function renderOrgChooser() {
    var sel2 = h("select", { style: { width: "100%", padding: "8px", marginTop: "12px" } },
      S.me.org_types.map(function (o) { return h("option", { value: o.code }, o.name); }));
    var go = safe(async function (e) {
      e && e.preventDefault();
      await PUT("/api/org_type", { org_type: sel2.value });
      S.me = await GET("/api/me"); S.year = S.me.current_year;
      renderShell(); location.hash = "#/"; route();
    });
    clear($app).appendChild(h("div", { class: "login" }, h("form", { class: "box", onsubmit: go, style: { width: "440px" } },
      h("h1", null, "Тип учреждения"),
      h("div", { class: "muted" }, "Выберите тип организации. От него зависит набор предметов типового учебного плана. Тип можно изменить позже в паспорте учреждения."),
      sel2,
      h("div", { class: "hint", style: { marginTop: "8px" } }, "Для района/города выберите «Отдел (управление) образования» — такой режим только собирает своды по школам."),
      h("button", { class: "pri", type: "submit", style: { width: "100%", marginTop: "14px", padding: "9px" } }, "Продолжить"))));
  }
  function deptMode() { return !!(S.me && S.me.department_mode); }

  async function renderLogin() {
    var about = {};
    try { about = await GET("/api/about"); } catch (e) { }
    var l = h("input", { autofocus: true, autocomplete: "username" }), p = h("input", { type: "password", autocomplete: "current-password" });
    var err = h("div", { class: "bad", style: { minHeight: "20px", marginTop: "8px" } });
    var go = async function (e) {
      e.preventDefault();
      try { await POST("/api/login", { login: l.value, password: p.value }); location.hash = "#/"; boot(); }
      catch (x) { err.textContent = x.message; }
    };
    clear($app).appendChild(h("div", { class: "login" },
      h("form", { class: "box", onsubmit: go },
        h("h1", null, h("img", { src: "icon.svg", alt: "" }), "SchoolGrade"),
        h("div", { class: "muted" }, about.school || "Учёт результатов учебной деятельности"),
        field("Имя пользователя", l), field("Пароль", p), err,
        h("button", { class: "pri", type: "submit", style: { width: "100%", marginTop: "8px", padding: "9px" } }, "Войти"),
        h("div", { class: "foot" }, (about.org || "") , h("br"), "Разработчик: " + (about.author || ""), h("br"), (about.city_year || "") + " · версия " + (about.version || "")))));
  }

  function renderChangePassword(forced) {
    var o = h("input", { type: "password" }), n1 = h("input", { type: "password" }), n2 = h("input", { type: "password" });
    var go = safe(async function (e) {
      e && e.preventDefault();
      if (n1.value !== n2.value) throw new Error("Пароли не совпадают");
      await POST("/api/me/password", { old: o.value, new: n1.value });
      toast("Пароль изменён"); location.hash = "#/"; boot();
    });
    var box = h("form", { class: "box", onsubmit: go }, h("h1", null, "Смена пароля"),
      forced ? h("div", { class: "warn" }, "Перед началом работы задайте собственный пароль (не менее 6 символов).") : null,
      field("Текущий пароль", o), field("Новый пароль", n1), field("Повторите новый пароль", n2),
      h("button", { class: "pri", type: "submit", style: { width: "100%", marginTop: "14px", padding: "9px" } }, "Сохранить"));
    clear($app).appendChild(h("div", { class: "login" }, box));
  }

  // ===================================================================== оболочка
  var NAV_SCHOOL = [
    ["Работа", [["#/", "Главная"], ["#/sheet", "Ведомость класса"], ["#/journal", "Электронный журнал", "journal"], ["#/finals", "Отметки по предмету"], ["#/behavior", "Поведение и итоги года"], ["#/cards", "Табели для родителей"], ["#/ktp", "Тематическое планирование"]]],
    ["Анализ", [["#/control", "Контроль накопляемости"], ["#/reports", "Отчёты и аналитика"], ["#/department", "Передать в отдел образования", "all"]]],
    ["Данные", [["#/classes", "Классы и нагрузка"], ["#/students", "Учащиеся"], ["#/staff", "Сотрудники", "manage"], ["#/subjects", "Предметы", "manage"]]],
    ["Учреждение", [["#/school", "Паспорт учреждения"], ["#/years", "Учебный год", "manage"], ["#/admin", "Администрирование", "admin"], ["#/about", "О программе"]]],
  ];
  var NAV_DEPT = [
    ["Отдел образования", [["#/", "Главная"], ["#/department", "Свод по учреждениям"]]],
    ["Настройки", [["#/school", "Сведения об отделе", "manage"], ["#/admin", "Администрирование", "admin"], ["#/about", "О программе"]]],
  ];
  var $main;
  function renderShell() {
    var u = S.me.user;
    var nav = h("nav", { class: "nav" });
    (deptMode() ? NAV_DEPT : NAV_SCHOOL).forEach(function (g) {
      var items = g[1].filter(function (it) {
        if (it[2] === "admin") return u.can_admin;
        if (it[2] === "manage") return u.can_manage;
        if (it[2] === "all") return u.can_manage || u.role === "viewer";
        if (it[2] === "journal") return journalOn();
        return true;
      });
      if (!items.length) return;
      nav.appendChild(h("div", { class: "grp" }, g[0]));
      items.forEach(function (it) { nav.appendChild(h("a", { href: it[0], "data-r": it[0] }, it[1])); });
    });
    var ysel = sel(S.me.years.map(function (y) { return [y.id, y.name + (y.is_current ? " (текущий)" : "")]; }), S.year && S.year.id,
      { style: { width: "100%", marginTop: "6px", background: "#2b4f8c", color: "#fff", border: "0" } });
    ysel.onchange = function () { S.year = S.me.years.filter(function (y) { return String(y.id) === ysel.value; })[0]; route(); };
    $main = h("main", { class: "main" });
    clear($app).appendChild(h("div", { class: "shell" },
      h("aside", { class: "side" },
        h("div", { class: "brand" }, h("img", { src: "icon.svg", alt: "" }), h("div", null, h("b", null, "SchoolGrade"), h("small", null, S.me.school["Наименование"] || "учёт успеваемости"))),
        nav,
        h("div", { class: "who" }, h("div", null, u.full_name || u.login), h("div", null, u.role_name),
          S.me.years.length ? ysel : null,
          h("div", { style: { marginTop: "8px" } }, h("a", { href: "#/password" }, "Сменить пароль"), " · ",
            h("a", { href: "javascript:void 0", onclick: safe(async function () { await POST("/api/logout"); S.me = null; renderLogin(); }) }, "Выход")))),
      $main));
  }
  window.addEventListener("hashchange", function () { if (S.me) route(); });

  var ROUTES = {};
  function route() {
    if (!$main) return;
    var hash = location.hash || "#/";
    var parts = hash.slice(2).split("?");
    var name = parts[0].split("/")[0];
    var params = {};
    (parts[1] || "").split("&").forEach(function (kv) { if (kv) { var p = kv.split("="); params[decodeURIComponent(p[0])] = decodeURIComponent(p[1] || ""); } });
    Array.prototype.forEach.call(document.querySelectorAll(".nav a"), function (a) {
      var r = a.getAttribute("data-r");
      a.className = (r === "#/" + name || (r === "#/" && name === "")) ? "on" : "";
    });
    clear($main);
    S.rid++;
    var fn = ROUTES[name] || ROUTES[""];
    if (name === "password") return pagePassword();
    if (!deptMode() && !S.year && ["", "about", "years", "school", "admin", "subjects", "staff"].indexOf(name) < 0) {
      $main.appendChild(h("div", { class: "card" }, h("h2", null, "Учебный год не создан"),
        S.me.user.can_manage ? h("a", { href: "#/years" }, "Создайте учебный год") : "Обратитесь к администратору."));
      return;
    }
    safe(fn)(params);
  }
  function setParams(p) {
    var name = (location.hash.slice(2).split("?")[0]);
    history.replaceState(null, "", "#/" + name + qs(p));
  }
  function head(title, sub, right) {
    $main.appendChild(h("div", { class: "pagehead" }, h("div", null, h("h1", null, title), sub ? h("div", { class: "sub" }, sub) : null), right || null));
  }
  function yid() { return S.year ? S.year.id : null; }
  function kindsOf(includeFinal) {
    var sc = (S.year && S.year.scheme) || "quarters";
    var k = sc === "halves" ? [["h1", "1 полугодие"], ["h2", "2 полугодие"]] : [["q1", "1 четверть"], ["q2", "2 четверть"], ["q3", "3 четверть"], ["q4", "4 четверть"]];
    if (includeFinal) k = k.concat([["year", "Годовая"], ["exam", "Экзамен"], ["final", "Итоговая"]]);
    return k;
  }
  function curKind() {
    var today = new Date().toISOString().slice(0, 10);
    return (S._periods || []).reduce(function (acc, p) { return (p.start_date && p.start_date <= today && p.end_date >= today) ? p.kind : acc; }, kindsOf()[0][0]);
  }
  async function loadPeriods() {
    var ys = await GET("/api/years");
    S.me.years = ys;
    var y = ys.filter(function (x) { return S.year && x.id === S.year.id; })[0];
    S._periods = y ? y.periods : [];
    if (y) S.year = y;
  }
  function pagePassword() { renderChangePassword(false); }
  function journalOn() { return !S.me.features || S.me.features.journal !== false; }

  // ===================================================================== главная
  ROUTES[""] = async function () {
    var u = S.me.user;
    head("Здравствуйте, " + (u.full_name || u.login), (S.me.school["Наименование"] || "") + (S.year ? " · " + S.year.name + " учебный год" : ""));
    if (deptMode()) {
      var pk = await GET("/api/department/packages");
      $main.appendChild(h("div", { class: "card" }, h("h2", { style: { marginTop: 0 } }, "Режим «Отдел (управление) образования»"),
        h("p", null, "Загружено пакетов от учреждений: " + pk.length + "."),
        h("div", { class: "bar" }, h("a", { class: "btn pri", href: "#/department" }, "Свод по учреждениям"),
          h("a", { class: "btn", href: "#/school" }, "Сведения об отделе"))));
      return;
    }
    if (!S.year) return;
    await loadPeriods();
    var tiles = h("div", { class: "tiles" });
    $main.appendChild(tiles);
    var classes = await GET("/api/classes", { year_id: yid() });
    var mine = await GET("/api/assignments", { year_id: yid(), mine: 1 });
    var nStud = classes.reduce(function (a, c) { return a + c.n; }, 0);
    [[classes.length, "классов доступно"], [nStud, "учащихся в них"], [mine.length, "моих предметов в классах"],
      [kindsOf().filter(function (k) { return k[0] === curKind(); })[0][1], "текущий учебный период"]].forEach(function (t) {
        tiles.appendChild(h("div", { class: "tile" }, h("b", null, t[0]), h("span", null, t[1])));
      });
    var g = h("div", { class: "grid2" });
    $main.appendChild(g);
    var c1 = h("div", { class: "card" }, h("h2", { style: { marginTop: 0 } }, journalOn() ? "Мои журналы" : "Мои предметы"));
    if (!mine.length) c1.appendChild(h("div", { class: "muted" }, u.staff_id ? "Нагрузка не назначена." : "Учётная запись не связана с сотрудником — обратитесь к администратору."));
    mine.forEach(function (a) {
      c1.appendChild(h("div", { style: { padding: "4px 0" } }, h("a", { href: journalOn() ? "#/journal?aid=" + a.id : "#/finals?aid=" + a.id }, a.class_name + " — " + a.subject + (a.group_name ? " (" + a.group_name + ")" : "")),
        " ", h("a", { class: "muted", href: "#/finals?aid=" + a.id, style: { fontSize: "12px" } }, "отметки за периоды"),
        " ", h("a", { class: "muted", href: "#/sheet?cid=" + a.class_id, style: { fontSize: "12px" } }, "ведомость класса")));
    });
    g.appendChild(c1);
    var own = classes.filter(function (c) { return u.own_classes.indexOf(c.id) >= 0; });
    var c2 = h("div", { class: "card" }, h("h2", { style: { marginTop: 0 } }, own.length ? "Мой класс" : "Классы"));
    (own.length ? own : classes).forEach(function (c) {
      c2.appendChild(h("div", { style: { padding: "4px 0" } }, h("a", { href: "#/classes?cid=" + c.id }, c.name + " класс"), h("span", { class: "muted" }, " · " + c.n + " уч-ся · " + (c.teacher || "кл. рук. не назначен")),
        " ", h("a", { href: "#/sheet?cid=" + c.id, style: { fontSize: "12px" } }, "ведомость")));
    });
    g.appendChild(c2);
    try {
      var acc = await GET("/api/reports/accumulation", { year_id: yid(), kind: curKind() });
      var tot = Object.keys(acc.counts).reduce(function (a, k) { return a + acc.counts[k]; }, 0);
      if (tot) {
        $main.appendChild(h("div", { class: "card warnbox" }, h("b", null, "Контроль накопляемости отметок: замечаний — " + tot),
          h("div", { class: "hint" }, Object.keys(acc.counts).filter(function (k) { return acc.counts[k]; }).map(function (k) { return acc.issue_names[k] + ": " + acc.counts[k]; }).join(" · ")),
          h("a", { class: "btn", href: "#/control", style: { marginTop: "6px", display: "inline-block" } }, "Подробнее")));
      }
    } catch (e) { /* период не задан — пропустить */ }
    var lg = h("div", { class: "card legend" }, h("b", null, "Уровни результатов учебной деятельности: "),
      [["lv-low", "низкий 1–2"], ["lv-sat", "удовлетворительный 3–4"], ["lv-mid", "средний 5–6"], ["lv-suf", "достаточный 7–8"], ["lv-high", "высокий 9–10"]].map(function (x) {
        return h("span", null, h("i", { class: x[0] }), x[1]);
      }), h("div", { class: "hint" }, "Специальные отметки: зач. — зачтено, незач. — не зачтено, осв. — освобождён, н/а — не аттестован, н/и — не изучал; в журнале «н» — отсутствовал. Поведение: прим., уд., неуд."));
    $main.appendChild(lg);
  };

  // ===================================================================== календарно-тематическое планирование
  ROUTES.ktp = async function (params) {
    await loadPeriods();
    head("Календарно-тематическое планирование", "Темы уроков по плану учителя; в журнале тема нового урока подставляется автоматически");
    var tab = params.tab || "plan";
    $main.appendChild(h("div", { class: "tabs" }, [["plan", "План по предмету"], ["progress", "Выполнение программы"]].map(function (t) {
      return h("a", { href: "javascript:void 0", class: tab === t[0] ? "on" : "", onclick: function () { params.tab = t[0]; setParams(params); route(); } }, t[1]);
    })));
    if (tab === "progress") {
      var bar0 = h("div", { class: "bar noprint" }, h("span", { class: "sp" }),
        h("button", { onclick: function () { download("/api/reports/ktp_progress", { year_id: yid(), format: "xlsx" }); } }, "Excel"),
        h("button", { onclick: function () { window.print(); } }, "Печать"));
      $main.appendChild(bar0);
      $main.appendChild(reportTable(await GET("/api/reports/ktp_progress", { year_id: yid() })));
      return;
    }
    var bar = h("div", { class: "bar" }); $main.appendChild(bar);
    var area = h("div"); $main.appendChild(area);
    bar.appendChild(await assignmentPicker(params, function (v) { params.aid = v; setParams(params); load(); }));
    var K = null, items = [], dirty = false;
    window.onbeforeunload = null;

    function markDirty() { dirty = true; saveBtn && (saveBtn.disabled = false); }
    var saveBtn = null;

    var load = safe(async function () {
      clear(area);
      if (!params.aid) { area.appendChild(h("div", { class: "card muted" }, "Выберите класс и предмет.")); return; }
      K = await GET("/api/assignments/" + params.aid + "/ktp");
      items = K.items.map(function (x) { return Object.assign({}, x); });
      dirty = false;
      render();
    });

    function render() {
      clear(area);
      var a = K.assignment, st = K.stats, edit = K.can_edit;
      saveBtn = h("button", { class: "pri", disabled: !dirty, onclick: safe(save) }, "Сохранить");
      area.appendChild(h("div", { class: "card" },
        h("div", { class: "bar" }, h("b", null, a.class_name + " · " + a.subject + (a.group_name ? " (" + a.group_name + ")" : "")), h("span", { class: "muted" }, a.teacher || ""), h("span", { class: "sp" }),
          h("span", { class: "pill " + (st.items ? "ok" : "warn") }, st.items ? "Тем: " + st.items + ", часов: " + String(st.hours).replace(".", ",") : "КТП не загружено"),
          st.items ? h("span", { class: "pill" }, "Проведено по КТП: " + st.lessons_linked + " из " + st.lessons_total + " уроков в журнале") : null),
        edit ? h("div", { class: "bar", style: { marginTop: "8px" } },
          h("button", { onclick: safe(fromFile) }, "Загрузить из файла…"),
          h("button", { onclick: pasteDialog }, "Вставить текст…"),
          h("button", { onclick: safe(copyDialog) }, "Скопировать из другого класса…"),
          h("button", { onclick: function () { items.push({ topic: "", hours: 1, planned_date: "", section: items.length ? items[items.length - 1].section : "", done: [] }); markDirty(); render(); focusLast(); } }, "+ Строка"),
          h("span", { class: "sp" }),
          items.length ? h("button", { onclick: function () { download("/api/assignments/" + params.aid + "/ktp/xlsx"); } }, "Excel") : null,
          saveBtn) : null,
        h("div", { class: "hint" }, "Поддерживаются таблицы Word (.docx), Excel (.xlsx, .xls), CSV и простой список «1. Тема урока». ",
          "Столбцы (№, тема, количество часов, дата) определяются автоматически; строки-разделы («Раздел 1. …») запоминаются как названия разделов.")));
      if (!items.length) { area.appendChild(h("div", { class: "card muted" }, edit ? "Загрузите КТП из файла, вставьте список тем или скопируйте из другого класса." : "КТП для этого предмета не загружено.")); return; }
      var lastSection = null;
      var rows = [];
      items.forEach(function (it, i) {
        if (it.section && it.section !== lastSection) rows.push(h("tr", { class: "grp" }, h("td", { colspan: 7 }, it.section)));
        lastSection = it.section;
        var inp = function (key, attrs) {
          var e = h("input", Object.assign({ value: it[key] === undefined || it[key] === null ? "" : String(it[key]), disabled: !edit }, attrs || {}));
          e.oninput = function () { it[key] = e.value; markDirty(); };
          return e;
        };
        var isNext = K.next_id && it.id === K.next_id;
        rows.push(h("tr", { class: isNext ? "ktp-next" : "" },
          h("td", { class: "n" }, i + 1),
          h("td", null, inp("topic", { class: "ktp-topic", placeholder: "Тема урока" })),
          h("td", null, inp("hours", { class: "ktp-h", inputmode: "decimal" })),
          h("td", null, inp("planned_date", { type: "date", class: "ktp-d" })),
          h("td", { class: "muted", style: { fontSize: "12px" } }, (it.done || []).map(fmtDate).join(", ") + (isNext ? (it.done && it.done.length ? " · " : "") + "следующая" : "")),
          h("td", null, inp("section", { class: "ktp-s", placeholder: "Раздел" })),
          h("td", null, edit ? h("a", { href: "javascript:void 0", title: "Удалить строку", onclick: function () {
            if ((it.done || []).length && !confirmBox("Тема уже использована в журнале (" + it.done.map(fmtDate).join(", ") + "). Удалить строку? Темы уроков в журнале сохранятся.")) return;
            items.splice(i, 1); markDirty(); render();
          } }, "✕") : null)));
      });
      area.appendChild(h("div", { class: "card tw" }, h("table", { class: "t ktp" },
        h("thead", null, h("tr", null, ["№", "Тема урока", "Часов", "Дата по плану", "Проведено (журнал)", "Раздел", ""].map(function (x) { return h("th", null, x); }))),
        h("tbody", null, rows))));
    }
    function focusLast() { var l = area.querySelectorAll("input.ktp-topic"); if (l.length) l[l.length - 1].focus(); }

    async function save() {
      K = await PUT("/api/assignments/" + params.aid + "/ktp", { items: items.map(function (x) { return { id: x.id, topic: x.topic, hours: x.hours, planned_date: x.planned_date, section: x.section, note: x.note || "" }; }) });
      items = K.items.map(function (x) { return Object.assign({}, x); }); dirty = false; render(); toast("КТП сохранено");
    }

    function norm(t) { return String(t || "").toLowerCase().replace(/ё/g, "е").replace(/[^a-zа-я0-9іў]+/g, " ").trim(); }
    function applyParsed(parsed, notes) {
      var hasLinks = items.some(function (x) { return (x.done || []).length; });
      var body = h("div", null,
        h("div", { class: "hint" }, notes.join(". ") + "."),
        h("div", { style: { maxHeight: "50vh", overflow: "auto", marginTop: "8px" } }, h("table", { class: "t" },
          h("thead", null, h("tr", null, ["№", "Раздел", "Тема", "Ч.", "Дата"].map(function (x) { return h("th", null, x); }))),
          h("tbody", null, parsed.slice(0, 400).map(function (x) {
            return h("tr", null, h("td", { class: "n" }, x.num), h("td", { class: "muted" }, x.section || ""), h("td", null, x.topic), h("td", { class: "n" }, String(x.hours).replace(".", ",")), h("td", null, fmtDate(x.planned_date)));
          })))),
        h("p", null, "Найдено уроков: " + parsed.length + "."));
      var btns = [{ text: items.length ? "Заменить текущее КТП" : "Загрузить", cls: "pri", fn: function () {
        // сохранить связи с уроками журнала для совпадающих тем
        var byTopic = {};
        items.forEach(function (x) { if (x.id) byTopic[norm(x.topic)] = byTopic[norm(x.topic)] || x; });
        items = parsed.map(function (p) { var old = byTopic[norm(p.topic)]; delete byTopic[norm(p.topic)]; return Object.assign({ done: old ? old.done : [] }, p, old ? { id: old.id } : {}); });
        markDirty(); render(); toast("Проверьте список и нажмите «Сохранить»");
      } }];
      if (items.length) btns.push({ text: "Добавить в конец", fn: function () { items = items.concat(parsed.map(function (p) { return Object.assign({ done: [] }, p); })); markDirty(); render(); toast("Проверьте список и нажмите «Сохранить»"); } });
      if (hasLinks) body.appendChild(h("div", { class: "hint" }, "При замене связь с уроками журнала сохраняется для тем, которые совпадают по тексту."));
      modal("Загрузка КТП", body, btns);
    }
    async function fromFile() {
      var fs = await pickFile(".docx,.xlsx,.xls,.csv,.txt"); if (!fs.length) return;
      var r = await POST("/api/ktp/parse", { filename: fs[0].name, data: await fileToB64(fs[0]) });
      applyParsed(r.items, r.notes);
    }
    function pasteDialog() {
      var ta = h("textarea", { rows: 14, style: { width: "100%" }, placeholder: "Вставьте сюда таблицу из Word или Excel (Ctrl+V) или список тем, по одной в строке:\n1. Тема первого урока\n2. Тема второго урока (2 ч)" });
      modal("Вставить темы уроков", h("div", null, ta), [{ text: "Распознать", cls: "pri", fn: async function () {
        if (!ta.value.trim()) return false;
        var r = await POST("/api/ktp/parse", { text: ta.value });
        setTimeout(function () { applyParsed(r.items, r.notes); }, 0);
      } }]);
      setTimeout(function () { ta.focus(); }, 50);
    }
    async function copyDialog() {
      var src = await GET("/api/assignments/" + params.aid + "/ktp/sources");
      if (!src.length) { toast("Нет КТП по этому предмету в других классах", true); return; }
      var s = sel(src.map(function (x) { return [x.id, x.title]; }), src[0].id, { style: { width: "100%" } });
      modal("Скопировать КТП", h("div", null, h("p", null, "Источник:"), s, h("div", { class: "hint" }, "Будут скопированы темы, часы, разделы и плановые даты. Даты можно затем поправить.")),
        [{ text: "Скопировать", cls: "pri", fn: async function () {
          var r = await GET("/api/assignments/" + s.value + "/ktp");
          setTimeout(function () { applyParsed(r.items.map(function (x) { return { num: x.num, topic: x.topic, hours: x.hours, planned_date: x.planned_date, section: x.section }; }), ["Источник: " + s.options[s.selectedIndex].text]); }, 0);
        } }]);
    }
    load();
  };

  // ===================================================================== табели успеваемости
  function lsGet(k, d) { try { var v = localStorage.getItem(k); return v === null ? d : v; } catch (e) { return d; } }
  function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* хранилище недоступно */ } }

  ROUTES.cards = async function (params) {
    await loadPeriods();
    head("Табели успеваемости для родителей", "По два табеля на листе A4 (альбомная ориентация) — после печати разрежьте лист пополам");
    var classes = await GET("/api/classes", { year_id: yid() });
    if (!classes.length) { $main.appendChild(h("div", { class: "card muted" }, "Нет доступных классов.")); return; }
    var own = classes.filter(function (c) { return S.me.user.own_classes.indexOf(c.id) >= 0; });
    var cs = sel(classes.map(function (c) { return [c.id, c.name + " класс"]; }), params.cid || (own[0] || classes[0]).id);
    var ctrl = h("div", { class: "card noprint" });
    var area = h("div", { class: "rcards-wrap" });
    $main.appendChild(h("div", { class: "bar noprint" }, cs, h("span", { class: "sp" }),
      h("button", { class: "pri", onclick: function () { window.print(); } }, "Печать")));
    $main.appendChild(ctrl); $main.appendChild(area);
    var data = null, chosenKinds = null, chosenStudents = null;
    var msg = h("textarea", { rows: 2, style: { width: "100%" }, placeholder: "Например: Родительское собрание состоится 20 ноября в 18:00 в кабинете 12." });
    msg.value = lsGet("sg_card_msg", "");
    var showAbs = h("input", { type: "checkbox" }), showAvg = h("input", { type: "checkbox" }), showSign = h("input", { type: "checkbox" });
    showAvg.checked = lsGet("sg_card_avg", "1") === "1"; showSign.checked = lsGet("sg_card_sign", "1") === "1";

    function defaultKinds(all) {
      var cur = curKind(), order = all.map(function (k) { return k.kind; });
      var upto = order.indexOf(cur);
      return all.filter(function (k, i) { return /^(q|h)/.test(k.kind) ? i <= upto : k.kind === "year"; }).map(function (k) { return k.kind; });
    }

    var load = safe(async function () {
      params.cid = cs.value; setParams(params);
      var first = await GET("/api/classes/" + cs.value + "/report_cards");
      chosenKinds = chosenKinds || defaultKinds(first.all_kinds);
      if (!chosenStudents || chosenStudents.cid !== cs.value) chosenStudents = { cid: cs.value, ids: first.students.map(function (s) { return s.id; }) };
      if (showAbs.dataset.init !== "1") { showAbs.checked = first.has_journal; showAbs.dataset.init = "1"; }
      data = first;
      renderCtrl(); renderCards();
    });

    function renderCtrl() {
      clear(ctrl);
      var kb = data.all_kinds.map(function (k) {
        var cb = h("input", { type: "checkbox" }); cb.checked = chosenKinds.indexOf(k.kind) >= 0;
        cb.onchange = function () {
          chosenKinds = data.all_kinds.filter(function (x) { return x.kind === k.kind ? cb.checked : chosenKinds.indexOf(x.kind) >= 0; }).map(function (x) { return x.kind; });
          renderCards();
        };
        return h("label", { class: "chk" }, cb, " " + k.name);
      });
      var sb = data.students.map(function (st) {
        var cb = h("input", { type: "checkbox" }); cb.checked = chosenStudents.ids.indexOf(st.id) >= 0;
        cb.onchange = function () {
          chosenStudents.ids = data.students.filter(function (x) { return x.id === st.id ? cb.checked : chosenStudents.ids.indexOf(x.id) >= 0; }).map(function (x) { return x.id; });
          renderCards();
        };
        return h("label", { class: "chk" }, cb, " " + st.name.split(" ").slice(0, 2).join(" "));
      });
      [showAbs, showAvg, showSign].forEach(function (c) { c.onchange = function () { lsSet("sg_card_avg", showAvg.checked ? "1" : "0"); lsSet("sg_card_sign", showSign.checked ? "1" : "0"); renderCards(); }; });
      msg.oninput = function () { lsSet("sg_card_msg", msg.value); renderCards(); };
      ctrl.appendChild(h("div", null, h("b", null, "Периоды: "), h("span", { class: "chkrow", style: { display: "inline-flex" } }, kb)));
      ctrl.appendChild(h("div", { class: "chkrow" }, h("label", { class: "chk" }, showAvg, " Средний балл"),
        h("label", { class: "chk" }, showAbs, " Пропущено уроков (по журналу)"), h("label", { class: "chk" }, showSign, " Строка для подписи родителей")));
      ctrl.appendChild(h("details", null, h("summary", null, "Учащиеся (" + chosenStudents.ids.length + " из " + data.students.length + ")"),
        h("div", { class: "bar", style: { margin: "6px 0" } },
          h("button", { onclick: function () { chosenStudents.ids = data.students.map(function (x) { return x.id; }); renderCtrl(); renderCards(); } }, "Выбрать всех"),
          h("button", { onclick: function () { chosenStudents.ids = []; renderCtrl(); renderCards(); } }, "Снять выбор")),
        h("div", { class: "chkrow" }, sb)));
      ctrl.appendChild(h("div", { style: { marginTop: "6px" } }, h("b", null, "Сообщение для родителей (печатается во всех табелях):"), msg));
    }

    function card(st) {
      var ks = data.kinds.length ? data.kinds : [];
      ks = data.all_kinds.filter(function (k) { return chosenKinds.indexOf(k.kind) >= 0; });
      var head = h("tr", null, h("th", { class: "subj" }, "Учебный предмет"), ks.map(function (k) { return h("th", null, k.name.replace(" четверть", " четв.").replace(" полугодие", " полуг.")); }));
      var body = st.rows.map(function (r) {
        return h("tr", null, h("td", { class: "subj" }, r.subject), ks.map(function (k) { var v = r.marks[k.kind] || ""; return h("td", { class: "m " + lvClass(v) }, v); }));
      });
      if (showAvg.checked) body.push(h("tr", { class: "sum" }, h("td", { class: "subj" }, "Средний балл"), ks.map(function (k) { return h("td", { class: "m" }, st.avg[k.kind] || ""); })));
      body.push(h("tr", { class: "sum" }, h("td", { class: "subj" }, "Поведение"), ks.map(function (k) { return h("td", { class: "m" }, st.behavior[k.kind] || ""); })));
      if (showAbs.checked) body.push(h("tr", { class: "sum" }, h("td", { class: "subj" }, "Пропущено уроков"), ks.map(function (k) { var v = st.absent[k.kind]; return h("td", { class: "m" }, v === "" || v === undefined ? "" : String(v)); })));
      if (showSign.checked) body.push(h("tr", { class: "sign" }, h("td", { class: "subj" }, "Подпись родителей"), ks.map(function () { return h("td", null, ""); })));
      return h("div", { class: "rcard" },
        h("div", { class: "rc-school" }, data.school),
        h("div", { class: "rc-title" }, "ТАБЕЛЬ УСПЕВАЕМОСТИ"),
        h("div", { class: "rc-who" }, h("b", null, st.name)),
        h("div", { class: "rc-sub" }, (st.gender === "ж" ? "учащаяся " : st.gender === "м" ? "учащийся " : "учащийся(-аяся) ") + data.class + " класса, " + data.year + " учебный год"),
        h("table", { class: "rc-t" }, h("thead", null, head), h("tbody", null, body)),
        msg.value.trim() ? h("div", { class: "rc-msg" }, msg.value.trim()) : null,
        h("div", { class: "rc-foot" }, "Классный руководитель ________________ " + (data.teacher ? data.teacher.split(" ").length > 2 ? data.teacher.split(" ")[0] + " " + data.teacher.split(" ")[1][0] + "." + data.teacher.split(" ")[2][0] + "." : data.teacher : "")));
    }

    function renderCards() {
      clear(area);
      var list = data.students.filter(function (s) { return chosenStudents.ids.indexOf(s.id) >= 0; });
      if (!list.length) { area.appendChild(h("div", { class: "card muted noprint" }, "Не выбрано ни одного учащегося.")); return; }
      if (!chosenKinds.length) { area.appendChild(h("div", { class: "card muted noprint" }, "Выберите хотя бы один период.")); return; }
      for (var i = 0; i < list.length; i += 2) {
        area.appendChild(h("div", { class: "rpage" }, card(list[i]), list[i + 1] ? card(list[i + 1]) : h("div", { class: "rcard empty" })));
      }
    }
    cs.onchange = function () { chosenStudents = null; load(); };
    load();
  };

  // ===================================================================== контроль накопляемости
  ROUTES.control = async function (params) {
    await loadPeriods();
    head("Контроль накопляемости отметок", S.me.user.can_manage || S.me.user.role === "viewer" ? "Все классы и предметы" : "Ваши предметы и ваш класс");
    var qk = kindsOf().filter(function (k) { return /^(q[1-4]|h[12])$/.test(k[0]); });
    var classes = await GET("/api/classes", { year_id: yid() });
    var ks = sel(qk, params.kind || curKind());
    var cs = sel([["", "Все доступные классы"]].concat(classes.map(function (c) { return [c.id, c.name + " класс"]; })), params.cid || "");
    var area = h("div");
    function args() { return { year_id: yid(), kind: ks.value, class_id: cs.value }; }
    var load = safe(async function () {
      params.kind = ks.value; params.cid = cs.value; setParams(params);
      clear(area).appendChild(h("div", { class: "card muted" }, "Проверка…"));
      var r = await GET("/api/reports/accumulation", args());
      clear(area);
      var pills = h("div", { class: "bar" }, Object.keys(r.counts).map(function (k) {
        var n = r.counts[k];
        return h("span", { class: "pill " + (n ? "warn" : "ok") }, r.issue_names[k] + ": " + n);
      }));
      area.appendChild(h("div", { class: "card noprint" }, pills,
        h("div", { class: "hint" }, "«Мало текущих отметок»: пока период идёт, ожидаемое количество растёт пропорционально прошедшим дням; за 10 дней до окончания и позже требуется полный минимум. ",
          "Проверка отметок за период выполняется после окончания периода.")));
      area.appendChild(reportTable(r));
    });
    ks.onchange = load; cs.onchange = load;
    $main.appendChild(h("div", { class: "bar noprint" }, ks, cs, h("span", { class: "sp" }),
      h("button", { onclick: function () { download("/api/reports/accumulation", Object.assign(args(), { format: "xlsx" })); } }, "Excel"),
      h("button", { onclick: function () { window.print(); } }, "Печать")));
    $main.appendChild(area);
    load();
  };

  // ===================================================================== выбор предмета-класса
  async function assignmentPicker(params, onPick) {
    var list = await GET("/api/assignments", { year_id: yid(), mine: (S.me.user.can_manage || S.me.user.role === "viewer") ? "" : 1 });
    var extra = [];
    if (!S.me.user.can_manage && S.me.user.own_classes.length) {
      for (var i = 0; i < S.me.user.own_classes.length; i++) {
        var al = await GET("/api/assignments", { class_id: S.me.user.own_classes[i] });
        al.forEach(function (a) { if (!list.some(function (x) { return x.id === a.id; })) { a.class_name = a.class_name || ""; extra.push(a); } });
      }
    }
    var all = list.concat(extra);
    var opts = [["", "— класс и предмет —"]].concat(all.map(function (a) {
      return [a.id, (a.class_name || "") + " — " + a.subject + (a.group_name ? " (" + a.group_name + ")" : "") + (a.teacher ? " · " + a.teacher : "")];
    }));
    var s = sel(opts, params.aid, { style: { minWidth: "360px" } });
    s.onchange = function () { onPick(s.value); };
    return s;
  }

  // ===================================================================== журнал
  ROUTES.journal = async function (params) {
    if (!journalOn()) {
      head("Электронный журнал");
      $main.appendChild(h("div", { class: "card" }, "Электронный журнал отключён: программа работает в режиме «только ведомости». Отметки за четверти, год, экзамен и итоговые вводятся в ",
        h("a", { href: "#/sheet" }, "ведомости класса"), " или в разделе ", h("a", { href: "#/finals" }, "«Отметки по предмету»"), ". Включить журнал может администратор: «Администрирование → Настройки»."));
      return;
    }
    await loadPeriods();
    head("Электронный журнал", "Сначала выберите класс, затем предмет");
    var kind = params.period || curKind();
    var bar = h("div", { class: "bar" });
    $main.appendChild(bar);
    var area = h("div");
    $main.appendChild(area);
    var classes = await GET("/api/classes", { year_id: yid() });
    var cs = sel([["", "— класс —"]].concat(classes.map(function (c) { return [c.id, c.name + " класс"]; })), params.cid || "");
    var ps = sel(kindsOf(), kind);
    ps.onchange = function () { params.period = ps.value; setParams(params); if (params.aid) load(); };
    bar.appendChild(cs); bar.appendChild(ps);
    var load = safe(async function () {
      clear(area);
      if (params.aid && !params.cid) {   // ссылка вида #/journal?aid=… (с главной страницы)
        var J0 = await GET("/api/journal/" + params.aid, { period: ps.value });
        params.cid = String(J0.assignment.class_id); cs.value = params.cid; setParams(params);
      }
      if (!params.cid) { area.appendChild(h("div", { class: "card muted" }, "Выберите класс.")); return; }
      var asg = await GET("/api/assignments", { class_id: params.cid });
      // предметы класса плиткой
      var grid = h("div", { class: "subjgrid" }, asg.map(function (a) {
        var tag = a.category && a.category !== "subject" ? (a.category === "fz" ? " · ФЗ" : " · ОпИ") : "";
        return h("a", { href: "javascript:void 0", class: "subjcard" + (String(a.id) === String(params.aid) ? " on" : ""),
          onclick: function () { params.aid = a.id; setParams(params); load(); } },
          h("b", null, a.subject + (a.group_name ? " (" + a.group_name + ")" : "")),
          h("small", null, (a.teacher || "—") + tag));
      }));
      area.appendChild(h("div", { class: "card" }, h("h3", { style: { marginTop: 0 } }, "Предметы класса"), asg.length ? grid : h("div", { class: "muted" }, "Классу не назначены предметы.")));
      if (params.aid && asg.some(function (a) { return String(a.id) === String(params.aid); })) {
        var jarea = h("div"); area.appendChild(jarea);
        var J = await GET("/api/journal/" + params.aid, { period: ps.value });
        renderJournal(jarea, J, load);
      }
    });
    cs.onchange = function () { params.cid = cs.value; params.aid = ""; setParams(params); load(); };
    load();
  };

  function renderJournal(area, J, reload) {
    var a = J.assignment, edit = J.can_edit && !J.locked;
    var info = h("div", { class: "bar" },
      h("b", null, a.class_name + " класс · " + a.subject + (a.group_name ? " (" + a.group_name + ")" : "")),
      h("span", { class: "muted" }, "учитель: " + (a.teacher || "—") + " · " + a.hours + " ч/нед · " + ({ points: "10-балльная система", pass: "зачётная система", none: "без отметок" }[a.eval_type] || "")),
      J.period ? h("span", { class: "pill" }, J.period.name + ": " + fmtDate(J.period.start_date) + " – " + fmtDate(J.period.end_date)) : null,
      J.locked ? h("span", { class: "pill bad" }, "период закрыт") : null,
      h("span", { class: "sp" }),
      edit ? h("button", { class: "pri", onclick: function () { lessonDialog(a.id, null, reload); } }, "+ Урок") : null,
      h("button", { onclick: function () { location.hash = "#/finals?aid=" + a.id; } }, "Отметки за периоды"));
    area.appendChild(info);
    if (!J.students.length) { area.appendChild(h("div", { class: "card muted" }, "В классе (группе) нет учащихся.")); return; }
    var lessons = J.lessons;
    var tr = h("tr", null, h("th", { class: "num" }, "№"), h("th", { class: "nm" }, "Учащийся"),
      lessons.map(function (l) {
        var d = l.date.split("-");
        return h("th", { class: "ld" + (l.work_type ? " kr" : ""), title: (l.topic || "") + (l.work_type ? " [" + l.work_type + "]" : "") + (l.homework ? "\nД/з: " + l.homework : ""),
          onclick: function () { if (J.can_edit) lessonDialog(a.id, l, reload); } }, d[2] + "." + d[1], h("small", null, l.work_type ? l.work_type.slice(0, 4) : (l.num > 1 ? "#" + l.num : "")));
      }),
      h("th", { class: "n", title: "Количество отметок в баллах" }, "Кол."), h("th", { class: "n" }, "Ср."), h("th", { class: "n", title: "Пропуски" }, "Пр."),
      h("th", { class: "n", title: "Предварительная (по среднему арифметическому с математическим округлением)" }, "Расч."),
      h("th", { class: "n" }, J.period ? J.period.name.replace(" четверть", " чет.") : "Итог"));
    var rows = [];
    var inputs = [];
    J.students.forEach(function (s, ri) {
      var cells = [];
      var sumCells = { cnt: h("td", { class: "sum" }), avg: h("td", { class: "sum" }), abs: h("td", { class: "sum" }), sug: h("td", { class: "sum" }) };
      var recalc = function () {
        var vals = lessons.map(function (l) { return J.marks[l.id + "_" + s.id]; }).filter(Boolean);
        var pts = vals.filter(function (v) { return /^\d+$/.test(v); }).map(Number);
        sumCells.cnt.textContent = pts.length || "";
        var avg = pts.length ? pts.reduce(function (x, y) { return x + y; }, 0) / pts.length : null;
        sumCells.avg.textContent = avg === null ? "" : avg.toFixed(2).replace(".", ",");
        sumCells.abs.textContent = vals.filter(function (v) { return v === "н"; }).length || "";
        var need = (a.hours <= 1 ? S.me.rules.min_marks_quarter_1h : S.me.rules.min_marks_quarter);
        var sug = avg === null ? "" : (pts.length < need ? "н/а?" : String(Math.floor(avg + 0.5 + 1e-9)));
        sumCells.sug.textContent = sug; sumCells.sug.className = "sum " + lvClass(sug);
        sumCells.sug.title = pts.length < need && avg !== null ? "Отметок меньше минимума (" + need + ")" : "";
      };
      lessons.forEach(function (l, ci) {
        var key = l.id + "_" + s.id;
        var v = J.marks[key] || "";
        var inp = h("input", { value: v, class: lvClass(v), disabled: !edit, maxlength: 6, "data-r": ri, "data-c": ci });
        inp.addEventListener("focus", function () { inp.select(); });
        inp.addEventListener("change", safe(async function () {
          inp.classList.add("saving");
          try {
            var r = await POST("/api/marks", { items: [{ lesson_id: l.id, student_id: s.id, value: inp.value }] });
            var nv = r.saved[0].value; inp.value = nv; J.marks[key] = nv || undefined; if (!nv) delete J.marks[key];
            inp.className = lvClass(nv); recalc();
          } catch (e) { inp.value = J.marks[key] || ""; inp.className = lvClass(inp.value); throw e; }
        }));
        inp.addEventListener("keydown", function (e) { navKey(e, inputs, ri, ci); });
        inputs[ri] = inputs[ri] || [];
        inputs[ri][ci] = inp;
        cells.push(h("td", { class: "c" }, inp));
      });
      var fin = (J.finals[s.id + "_" + (J.period ? J.period.kind : "")] || {}).value || "";
      var finInp = h("input", { value: fin, class: lvClass(fin), disabled: !edit || !J.period, title: "Отметка за период — сохраняется в «Итоговые отметки»" });
      finInp.addEventListener("change", safe(async function () {
        await POST("/api/finals", { items: [{ assignment_id: a.id, student_id: s.id, kind: J.period.kind, value: finInp.value }] });
        toast("Отметка за период сохранена"); finInp.className = lvClass(finInp.value);
      }));
      recalc();
      rows.push(h("tr", null, h("td", { class: "num" }, ri + 1), h("td", { class: "nm", title: s.name + (s.pe_group ? " · физкультура: " + s.pe_group : "") }, s.name), cells,
        sumCells.cnt, sumCells.avg, sumCells.abs, sumCells.sug, h("td", { class: "fin c" }, finInp)));
    });
    area.appendChild(h("div", { class: "tw", style: { maxHeight: "70vh" } }, h("table", { class: "jr" }, h("thead", null, tr), h("tbody", null, rows))));
    area.appendChild(h("div", { class: "hint" }, "Ввод: 1–10, н (отсутствовал), зач, незач, осв. Перемещение — стрелки, Enter — вниз. Щелчок по дате — тема урока, д/з, вид работы. ",
      "«Расч.» — предварительная отметка за период (среднее арифметическое, математическое округление; минимум отметок — " + S.me.rules.min_marks_quarter + ", при 1 ч/нед — " + S.me.rules.min_marks_quarter_1h + ")."));
    if (!lessons.length) area.appendChild(h("div", { class: "card muted", style: { marginTop: "10px" } }, "В этом периоде ещё нет уроков. Нажмите «+ Урок»."));
  }
  function navKey(e, inputs, r, c) {
    var dr = 0, dc = 0;
    if (e.key === "ArrowDown" || e.key === "Enter") dr = 1; else if (e.key === "ArrowUp") dr = -1;
    else if (e.key === "ArrowRight" && e.target.selectionStart === e.target.value.length) dc = 1;
    else if (e.key === "ArrowLeft" && e.target.selectionStart === 0) dc = -1; else return;
    var t = inputs[r + dr] && inputs[r + dr][c + dc];
    if (t) { e.preventDefault(); if (e.key === "Enter") e.target.blur(); t.focus(); }
  }
  async function lessonDialog(aid, l, reload) {
    var data = l || { date: new Date().toISOString().slice(0, 10) };
    var K = null;
    try { K = await GET("/api/assignments/" + aid + "/ktp"); } catch (e) { K = null; }
    var f = buildForm([{ k: "date", label: "Дата", type: "date" }, { k: "work_type", label: "Вид работы", type: "list", options: ["контрольная работа", "самостоятельная работа", "диктант", "изложение", "сочинение", "тест", "лабораторная работа", "практическая работа", "зачёт", "проверочная работа"] },
      { k: "topic", label: "Тема урока", wide: true }, { k: "homework", label: "Домашнее задание", wide: true }], data);
    var ksel = null;
    if (K && K.items.length) {
      var curId = l ? (l.ktp_id || "") : (K.next_id || "");
      ksel = sel([["", "— тема не из КТП —"]].concat(K.items.map(function (it) {
        var used = (it.done || []).filter(function (d) { return !l || d !== l.date || it.id !== l.ktp_id; });
        return [it.id, it.num + ". " + it.topic + (used.length ? "  ✓ " + used.map(fmtDate).join(", ") : "")];
      })), curId, { style: { width: "100%" } });
      var topicInp = f.inputs.topic;
      var syncTopic = function () {
        var it = K.items.filter(function (x) { return String(x.id) === ksel.value; })[0];
        if (it && topicInp) topicInp.value = it.topic;
      };
      ksel.onchange = syncTopic;
      if (!l && curId) syncTopic();
      f.insertBefore(field("Тема из календарно-тематического планирования", ksel, "wide"), f.firstChild);
    }
    var btns = [{ text: "Сохранить", cls: "pri", fn: async function () {
      var v = f.values();
      if (ksel) v.ktp_id = ksel.value ? Number(ksel.value) : null;
      if (l) await PUT("/api/lessons/" + l.id, v); else await POST("/api/journal/" + aid + "/lessons", v);
      reload();
    } }];
    if (l) btns.push({ text: "Удалить урок", cls: "danger", fn: async function () {
      if (!confirmBox("Удалить урок " + fmtDate(l.date) + " вместе со всеми отметками?")) return false;
      await DEL("/api/lessons/" + l.id); reload();
    } });
    modal(l ? "Урок " + fmtDate(l.date) : "Новый урок", f, btns);
  }

  // ===================================================================== ведомость класса (ввод отметок за период)
  var SOU_W = { 10: 100, 9: 96, 8: 90, 7: 74, 6: 55, 5: 45, 4: 40, 3: 32, 2: 20, 1: 12 };
  var LEVEL_NAMES = [[2, "Низкий"], [4, "Удовлетворительный"], [6, "Средний"], [8, "Достаточный"], [10, "Высокий"]];
  function calcStats(vals) {
    var pts = vals.filter(function (v) { return /^(10|[1-9])$/.test(v); }).map(Number);
    if (!pts.length) return { n: 0, avg: "", kz: "", sou: "", level: "" };
    var sum = 0, good = 0, sou = 0, mn = 10;
    pts.forEach(function (p) { sum += p; if (p >= S.me.rules.kz_min_point) good++; sou += SOU_W[p]; if (p < mn) mn = p; });
    var lv = LEVEL_NAMES.filter(function (x) { return mn <= x[0]; })[0][1];
    var f = function (x, d) { return x.toFixed(d).replace(".", ","); };
    return { n: pts.length, avg: f(sum / pts.length, 2), kz: f(good * 100 / pts.length, 1), sou: f(sou / pts.length, 1), level: lv };
  }
  function gridNav(e, grid, r, c) {
    var dr = 0, dc = 0;
    if (e.key === "ArrowDown" || e.key === "Enter") dr = 1; else if (e.key === "ArrowUp") dr = -1;
    else if (e.key === "ArrowRight" && (e.target.tagName === "SELECT" || e.target.selectionStart === e.target.value.length)) dc = 1;
    else if (e.key === "ArrowLeft" && (e.target.tagName === "SELECT" || e.target.selectionStart === 0)) dc = -1;
    else if (e.key === "Tab") return; else return;
    if (e.target.tagName === "SELECT" && dr) return; // стрелки вверх/вниз в списке меняют значение
    var rr = r + dr, cc = c + dc;
    while (grid[rr] && cc >= 0 && cc < grid[rr].length) {
      if (grid[rr][cc]) { e.preventDefault(); if (e.key === "Enter") e.target.blur(); grid[rr][cc].focus(); return; }
      rr += dr; cc += dc;
      if (!dr && !dc) break;
    }
  }

  ROUTES.sheet = async function (params) {
    await loadPeriods();
    head("Ведомость класса", "Отметки за четверть, год, экзамен и итоговые по всем предметам класса");
    var u = S.me.user;
    var classes = await GET("/api/classes", { year_id: yid() });
    var defCls = params.cid || (u.own_classes.filter(function (id) { return classes.some(function (c) { return c.id === id; }); })[0]) || (classes[0] && classes[0].id) || "";
    var cs = sel(classes.map(function (c) { return [c.id, c.name + " класс"]; }), defCls);
    var ks = sel(kindsOf(true), params.kind || curKind());
    var area = h("div");
    var bar = h("div", { class: "bar noprint" }, cs, ks, h("span", { class: "sp" }),
      h("button", { onclick: function () { download("/api/reports/class_sheet", { class_id: cs.value, kind: ks.value, format: "xlsx" }); } }, "Excel"),
      h("button", { onclick: function () { window.print(); } }, "Печать"));
    $main.appendChild(bar); $main.appendChild(area);
    if (!classes.length) { area.appendChild(h("div", { class: "card muted" }, "Нет доступных классов.")); return; }
    var load = safe(async function () {
      params.cid = cs.value; params.kind = ks.value; setParams(params);
      var D = await GET("/api/sheet/" + cs.value, { kind: ks.value });
      renderSheet(clear(area), D);
    });
    cs.onchange = load; ks.onchange = load;
    load();
  };

  function renderSheet(area, D) {
    var subs = D.subjects;
    var c = D.class;
    area.appendChild(h("div", { class: "print-only" }, h("h2", null, "Сводная ведомость: " + c.grade + (c.letter ? " «" + c.letter + "»" : "") + " класс, " + D.kind_name.toLowerCase()),
      h("div", null, D.school + " · " + D.year + " учебный год · Классный руководитель: " + (c.teacher || "—"))));
    var info = h("div", { class: "bar noprint" }, h("b", null, c.grade + (c.letter ? " «" + c.letter + "»" : "") + " класс · " + D.kind_name),
      h("span", { class: "muted" }, "классный руководитель: " + (c.teacher || "—")),
      D.locked ? h("span", { class: "pill bad" }, "период закрыт") : null);
    area.appendChild(info);
    if (!D.students.length) { area.appendChild(h("div", { class: "card muted" }, "В классе нет учащихся.")); return; }
    if (!subs.length) { area.appendChild(h("div", { class: "card muted" }, "Классу не назначены предметы (раздел «Классы и нагрузка → Нагрузка»).")); return; }
    var editableSet = {}; D.editable.forEach(function (id) { editableSet[id] = 1; });
    var thr = h("tr", null, h("th", { class: "n" }, "№"), h("th", { class: "nm2" }, "Фамилия, имя"),
      subs.map(function (sb) {
        return h("th", { class: "vert" + (sb.editable ? " ed" : ""), title: sb.name + (sb.teachers.length ? "\nУчитель: " + sb.teachers.join(", ") : "") + (sb.editable ? "" : "\n(только просмотр)") }, sb.short);
      }),
      D.has_behavior ? h("th", { class: "n" }, "Поведение") : null,
      h("th", { class: "n" }, "Ср. балл"), h("th", { class: "n" }, "КЗ %"), h("th", { class: "n" }, "СОУ %"), h("th", { class: "n" }, "Уровень"));
    var grid = [];
    var avgCells = subs.map(function () { return h("td", { class: "n b" }); });
    var colVals = function (j) { return D.students.map(function (s) { var x = D.cells[s.id + "_" + subs[j].subject_id]; return x ? x.value : ""; }); };
    var recalcCol = function (j) { avgCells[j].textContent = calcStats(colVals(j)).avg; };
    var rows = D.students.map(function (s, ri) {
      grid[ri] = [];
      var stCells = { avg: h("td", { class: "n" }), kz: h("td", { class: "n" }), sou: h("td", { class: "n" }), level: h("td", null) };
      var recalcRow = function () {
        var st = calcStats(subs.map(function (sb) { var x = D.cells[s.id + "_" + sb.subject_id]; return x ? x.value : ""; }));
        stCells.avg.textContent = st.avg; stCells.kz.textContent = st.kz; stCells.sou.textContent = st.sou; stCells.level.textContent = st.level;
      };
      var tds = subs.map(function (sb, j) {
        var key = s.id + "_" + sb.subject_id;
        var aid = D.cellaid[key];
        var cur = D.cells[key] ? D.cells[key].value : "";
        if (!aid) return h("td", { class: "n muted", title: "Учащийся не входит в группу по предмету" }, "—");
        if (!editableSet[aid] || D.locked) { grid[ri][j] = null; return h("td", { class: "n " + lvClass(cur) }, cur); }
        var inp = h("input", { value: cur, class: lvClass(cur), maxlength: 6, title: sb.name });
        inp.addEventListener("focus", function () { inp.select(); });
        inp.addEventListener("keydown", function (e) { gridNav(e, grid, ri, j); });
        inp.addEventListener("change", safe(async function () {
          inp.classList.add("saving");
          try {
            var r = await POST("/api/finals", { items: [{ assignment_id: aid, student_id: s.id, kind: D.kind, value: inp.value }] });
            var v = r.saved[0].value;
            inp.value = v; inp.className = lvClass(v);
            if (v) D.cells[key] = { value: v, excused: 0 }; else delete D.cells[key];
            recalcRow(); recalcCol(j);
          } catch (e) { inp.value = cur; inp.className = lvClass(cur); throw e; }
          cur = inp.value;
        }));
        grid[ri][j] = inp;
        return h("td", { class: "c" }, inp);
      });
      var behTd = null;
      if (D.has_behavior) {
        var bv = D.behavior[String(s.id)] || "";
        if (D.can_behavior && !D.locked) {
          var bs = sel([["", ""], ["прим.", "прим."], ["уд.", "уд."], ["неуд.", "неуд."]], bv);
          bs.onchange = safe(async function () { await POST("/api/behavior", { class_id: c.id, student_id: s.id, kind: D.kind, value: bs.value }); });
          bs.addEventListener("keydown", function (e) { gridNav(e, grid, ri, subs.length); });
          grid[ri][subs.length] = bs;
          behTd = h("td", { class: "c" }, bs);
        } else behTd = h("td", { class: "n" }, bv);
      }
      recalcRow();
      return h("tr", null, h("td", { class: "n" }, ri + 1), h("td", { class: "nm2" }, s.name), tds, behTd, stCells.avg, stCells.kz, stCells.sou, stCells.level);
    });
    subs.forEach(function (_, j) { recalcCol(j); });
    var foot = h("tr", { class: "total" }, h("td"), h("td", null, "Средний балл"), avgCells, D.has_behavior ? h("td") : null, h("td"), h("td"), h("td"), h("td"));
    area.appendChild(h("div", { class: "tw" }, h("table", { class: "t sheet" }, h("thead", null, thr), h("tbody", null, rows, foot))));
    area.appendChild(h("div", { class: "hint noprint" }, "Ввод: 1–10, зач., незач., осв., н/а, н/и (можно коротко: зач, незач, осв, на, ни). Перемещение — стрелки, Enter — вниз. ",
      "Заполнять можно столбцы своих предметов; классный руководитель — все предметы своего класса и поведение. Серые столбцы — только просмотр. ",
      "Отметку «н/а по уважительной причине» отмечайте флажком в разделе «Отметки по предмету»."));
  }

  // ===================================================================== итоговые
  ROUTES.finals = async function (params) {
    head("Отметки по предмету", "Отметки за четверти (полугодия), годовые, экзаменационные и итоговые по одному предмету класса");
    var bar = h("div", { class: "bar" }); $main.appendChild(bar);
    var area = h("div"); $main.appendChild(area);
    bar.appendChild(await assignmentPicker(params, function (v) { params.aid = v; setParams(params); load(); }));
    var load = safe(async function () {
      clear(area);
      if (!params.aid) { area.appendChild(h("div", { class: "card muted" }, "Выберите класс и предмет.")); return; }
      var F = await GET("/api/finals/" + params.aid);
      var a = F.assignment;
      var pkinds = F.kinds.filter(function (k) { return /^[qh]\d$/.test(k.kind); }).map(function (k) { return k.kind; });
      var edit = F.can_edit;
      var tb = h("div", { class: "bar" }, h("b", null, a.class_name + " · " + a.subject + (a.group_name ? " (" + a.group_name + ")" : "")), h("span", { class: "muted" }, a.teacher), h("span", { class: "sp" }));
      if (edit) {
        var ks = sel(F.kinds.filter(function (k) { return /^[qh]\d$|^year$/.test(k.kind); }).map(function (k) { return [k.kind, k.name]; }), pkinds[0]);
        tb.appendChild(ks);
        tb.appendChild(h("button", { onclick: safe(async function () {
          var items = [];
          F.rows.forEach(function (r) {
            var s = r.suggest[ks.value];
            if (s && s.value && !(r.finals[ks.value] || {}).value) items.push({ assignment_id: a.id, student_id: r.id, kind: ks.value, value: s.value });
          });
          if (!items.length) { toast("Нет пустых ячеек для заполнения"); return; }
          if (!confirmBox("Заполнить " + items.length + " пустых отметок «" + ks.options[ks.selectedIndex].text + "» расчётными значениями? Заполненные вручную отметки не изменятся.")) return;
          await POST("/api/finals", { items: items }); toast("Заполнено: " + items.length); load();
        }) }, "Заполнить расчётными"));
      }
      area.appendChild(tb);
      var thr = h("tr", null, h("th", null, "№"), h("th", null, "Учащийся"), F.kinds.map(function (k) { return h("th", { class: "n" }, k.name + (k.locked ? " 🔒" : "")); }));
      var rows = F.rows.map(function (r, i) {
        return h("tr", null, h("td", { class: "n" }, i + 1), h("td", null, r.name), F.kinds.map(function (k) {
          var cur = r.finals[k.kind] || {};
          var sg = r.suggest[k.kind];
          var inp = h("input", { value: cur.value || "", class: lvClass(cur.value || ""), style: { width: "58px", textAlign: "center", fontWeight: "600" },
            disabled: !edit || (k.locked && !F.is_admin), placeholder: sg && sg.value ? sg.value : "" });
          var exc = h("input", { type: "checkbox", checked: !!cur.excused, title: "н/а по уважительной причине (подтверждено документально)", disabled: !edit });
          var save = safe(async function () {
            await POST("/api/finals", { items: [{ assignment_id: a.id, student_id: r.id, kind: k.kind, value: inp.value, excused: exc.checked }] });
            inp.className = lvClass(inp.value); exc.style.display = inp.value.toLowerCase().indexOf("н") === 0 ? "" : "none";
            if (/^[qh]\d$/.test(k.kind)) load();
          });
          inp.onchange = save; exc.onchange = save;
          exc.style.display = (cur.value === "н/а") ? "" : "none";
          var tip = sg ? ((sg.avg !== null && sg.avg !== undefined ? "среднее " + String(sg.avg).replace(".", ",") : "") + (sg.count !== undefined ? ", отметок: " + sg.count : "") + (sg.reason ? " — " + sg.reason : "")) : "";
          return h("td", { class: "n", title: tip }, inp, exc);
        }));
      });
      area.appendChild(h("div", { class: "tw" }, h("table", { class: "t" }, h("thead", null, thr), h("tbody", null, rows))));
      area.appendChild(h("div", { class: "hint" }, "Серым в пустой ячейке показана расчётная отметка (наведите курсор — среднее и количество отметок). ",
        "Годовая: среднее арифметическое отметок за четверти с математическим округлением; н/а по уважительной причине (флажок) не учитывается, ",
        S.me.rules.unexcused_na_as_zero ? "н/а без уважительной причины приравнивается к 0. " : "", "Допустимо: 1–10, зач., незач., осв., н/а, н/и."));
    });
    load();
  };

  // ===================================================================== поведение и итоги года
  ROUTES.behavior = async function (params) {
    head("Поведение и итоги года", "Отметка по поведению за четверть и год, решение педагогического совета");
    var classes = await GET("/api/classes", { year_id: yid() });
    var cs = sel([["", "— класс —"]].concat(classes.map(function (c) { return [c.id, c.name + " класс"]; })), params.cid || (S.me.user.own_classes[0] || ""));
    var bar = h("div", { class: "bar" }, cs); $main.appendChild(bar);
    var area = h("div"); $main.appendChild(area);
    cs.onchange = function () { params.cid = cs.value; setParams(params); load(); };
    var load = safe(async function () {
      clear(area);
      if (!cs.value) return;
      var B = await GET("/api/behavior/" + cs.value);
      var thr = h("tr", null, h("th", null, "№"), h("th", null, "Учащийся"), B.kinds.map(function (k) { return h("th", { class: "n" }, k.name); }), h("th", null, "Итог учебного года"));
      var rows = B.students.map(function (s, i) {
        return h("tr", null, h("td", { class: "n" }, i + 1), h("td", null, s.name), B.kinds.map(function (k) {
          var v = B.values[s.id + "_" + k.kind] || "";
          var se = sel([["", ""], ["прим.", "прим."], ["уд.", "уд."], ["неуд.", "неуд."]], v, { disabled: !B.can_edit });
          se.onchange = safe(async function () { await POST("/api/behavior", { class_id: cs.value, student_id: s.id, kind: k.kind, value: se.value }); toast("Сохранено"); });
          return h("td", { class: "n" }, se);
        }), h("td", null, (function () {
          var se = sel([[""]].concat(S.me.dicts.year_result.map(function (x) { return [x, x]; })), s.year_result, { disabled: !B.can_edit, style: { maxWidth: "300px" } });
          se.onchange = safe(async function () { await POST("/api/classes/" + cs.value + "/result", { student_id: s.id, value: se.value }); toast("Сохранено"); });
          return se;
        })()));
      });
      area.appendChild(h("div", { class: "tw" }, h("table", { class: "t" }, h("thead", null, thr), h("tbody", null, rows))));
      area.appendChild(h("div", { class: "hint" }, "Поведение: «прим.» — примерное, «уд.» — удовлетворительное, «неуд.» — неудовлетворительное. Выставляет классный руководитель. ",
        "«Итог учебного года» используется при переводе учащихся в новый учебный год (раздел «Учебный год»)."));
    });
    load();
  };

  // ===================================================================== отчёты
  var REPORTS = [
    ["class_sheet", "Сводная ведомость класса (учащиеся × предметы)", "class"],
    ["class_subjects", "Анализ класса по предметам (уровни, КЗ, СОУ)", "class"],
    ["class_students", "Рейтинг учащихся класса", "class"],
    ["attendance", "Пропуски уроков по классу", "class_dates"],
    ["students_list", "Список учащихся класса (персональные данные)", "class_only"],
    ["student", "Табель / сведения об учащемся для законных представителей", "student"],
    ["school", "Успеваемость по классам и параллелям учреждения", "all"],
    ["levels_parallel", "Успеваемость по параллелям по уровням (за период или за все периоды)", "levels"],
    ["school_subjects", "Аналитическая информация по предметам (вся школа)", "all"],
    ["parallel", "Аналитика по предметам в параллели", "grade"],
    ["lists", "Списки: на 9–10, 7–10, резерв, низкий уровень, н/а", "all"],
    ["teacher", "Отчёт учителя", "teacher"],
    ["clubs", "Посещаемость факультативов и объединений по интересам", "category"],
  ];
  ROUTES.reports = async function (params) {
    head("Отчёты и аналитика", "Все отчёты можно распечатать или выгрузить в Excel");
    var u = S.me.user;
    var avail = REPORTS.filter(function (r) { return (["all", "grade", "category", "levels"].indexOf(r[2]) < 0 || u.can_manage || u.role === "viewer") && (r[0] !== "attendance" || journalOn()); });
    var type = sel(avail.map(function (r) { return [r[0], r[1]]; }), params.type || avail[0][0]);
    var bar = h("div", { class: "bar" }, type);
    var pbar = h("div", { class: "bar" });
    $main.appendChild(bar); $main.appendChild(pbar);
    var area = h("div"); $main.appendChild(area);
    var classes = await GET("/api/classes", { year_id: yid() });
    await loadPeriods();
    var ctrls = {};
    var build = async function () {
      clear(pbar); clear(area); ctrls = {};
      var waits = [];
      var def = REPORTS.filter(function (r) { return r[0] === type.value; })[0];
      var need = def[2];
      if (/class/.test(need) || need === "student") {
        ctrls.class_id = sel(classes.map(function (c) { return [c.id, c.name + " класс"]; }), params.class_id || (u.own_classes[0] || (classes[0] && classes[0].id)));
        pbar.appendChild(ctrls.class_id);
      }
      if (need === "grade") {
        var grades = []; classes.forEach(function (c) { if (grades.indexOf(c.grade) < 0) grades.push(c.grade); });
        ctrls.grade = sel(grades.map(function (g) { return [g, g + "-е классы"]; }), params.grade); pbar.appendChild(ctrls.grade);
      }
      if (need === "teacher" && (u.can_manage || u.role === "viewer")) {
        ctrls.staff_id = h("select"); pbar.appendChild(ctrls.staff_id);
        GET("/api/staff").then(function (st) { st.filter(function (s) { return s.active; }).forEach(function (s) { ctrls.staff_id.appendChild(h("option", { value: s.id }, s.short)); }); if (params.staff_id) ctrls.staff_id.value = params.staff_id; });
      }
      if (need === "student") {
        ctrls.student_id = h("select"); pbar.appendChild(ctrls.student_id);
        var fill = safe(async function () {
          clear(ctrls.student_id);
          (await GET("/api/classes/" + ctrls.class_id.value + "/students")).forEach(function (s) { ctrls.student_id.appendChild(h("option", { value: s.id }, s.last_name + " " + s.first_name)); });
          if (params.student_id) ctrls.student_id.value = params.student_id;
        });
        ctrls.class_id.onchange = fill; waits.push(fill());
      }
      if (need === "class_dates") {
        var p = S._periods.filter(function (x) { return x.kind === curKind(); })[0] || {};
        ctrls.from = h("input", { type: "date", value: params.from || p.start_date || "" }); ctrls.to = h("input", { type: "date", value: params.to || p.end_date || "" });
        pbar.appendChild(field("с", ctrls.from)); pbar.appendChild(field("по", ctrls.to));
      }
      if (need === "category") {
        ctrls.category = sel([["fz", "Факультативы (ФЗ)"], ["opi", "Объединения по интересам (ОпИ)"]], params.category || "fz");
        pbar.appendChild(ctrls.category);
      }
      if (["class", "all", "grade", "teacher"].indexOf(need) >= 0) { ctrls.kind = sel(kindsOf(true), params.kind || curKind()); pbar.appendChild(ctrls.kind); }
      if (need === "levels") { ctrls.kind = sel(kindsOf(true).concat([["all", "Все периоды (сводно)"]]), params.kind || "all"); pbar.appendChild(ctrls.kind); }
      var args = function () { var o = { year_id: yid() }; for (var k in ctrls) o[k] = ctrls[k].value; return o; };
      pbar.appendChild(h("button", { class: "pri", onclick: safe(async function () {
        var a = args(); params = Object.assign({ type: type.value }, a); setParams(params);
        clear(area).appendChild(reportTable(await GET("/api/reports/" + type.value, a)));
      }) }, "Сформировать"));
      pbar.appendChild(h("button", { onclick: function () { download("/api/reports/" + type.value, Object.assign(args(), { format: "xlsx" })); } }, "Excel"));
      pbar.appendChild(h("button", { onclick: function () { window.print(); } }, "Печать"));
      await Promise.all(waits);
    };
    type.onchange = function () { params = { type: type.value }; build(); };
    await build();
    if (params.type) pbar.querySelector("button.pri").click();
  };

  // ===================================================================== отдел образования
  ROUTES.department = async function () {
    if (!deptMode()) return deptExportPage();
    head("Свод по учреждениям образования", "Загрузите пакеты .sgdep.json, полученные от школ, — программа построит свод");
    var res = h("div");
    var pk = await GET("/api/department/packages");
    var ks = sel(kindsOf(true), "year");
    var bar = h("div", { class: "bar" },
      h("button", { class: "pri", onclick: safe(async function () {
        var fs = await pickFile(".json", true); if (!fs.length) return;
        var files = [];
        for (var i = 0; i < fs.length; i++) files.push({ filename: fs[i].name, data: await fileToB64(fs[i]) });
        var r = await POST("/api/department/packages", { files: files });
        toast("Загружено пакетов: " + r.added); route();
      }) }, "Загрузить пакеты (.sgdep.json)"),
      ks,
      h("button", { onclick: safe(async function () {
        var r = await GET("/api/department/summary", { kind: ks.value });
        clear(res); r.reports.forEach(function (rep) { res.appendChild(reportTable(rep)); });
      }) }, "Построить свод"),
      h("button", { onclick: function () { download("/api/department/summary", { kind: ks.value, format: "xlsx" }); } }, "Свод в Excel"));
    $main.appendChild(bar);
    var tb = h("table", { class: "t" }, h("thead", null, h("tr", null, ["Учреждение", "Уч. год", "Файл", "Загружен", ""].map(function (x) { return h("th", null, x); }))),
      h("tbody", null, pk.map(function (p) {
        return h("tr", null, h("td", null, h("a", { href: "javascript:void 0", onclick: function () { deptPassport(p.id); } }, p.school)),
          h("td", null, p.year), h("td", { class: "muted" }, p.filename), h("td", null, p.uploaded_at),
          h("td", null, h("button", { class: "sm danger", onclick: safe(async function () { if (!confirmBox("Удалить пакет «" + p.school + "»?")) return; await DEL("/api/department/packages/" + p.id); route(); }) }, "удалить")));
      })));
    $main.appendChild(h("div", { class: "card" }, h("h3", { style: { marginTop: 0 } }, "Загруженные учреждения (" + pk.length + ")"),
      pk.length ? h("div", { class: "tw" }, tb) : h("div", { class: "muted" }, "Пакеты не загружены.")));
    $main.appendChild(res);
  };

  var deptPassport = safe(async function (pid) {
    var d = await GET("/api/department/package/" + pid);
    var body = h("div", null, d.fields.map(function (g) {
      return h("div", null, h("h3", null, g[0]), g[1].map(function (k) {
        return (d.school[k]) ? h("div", null, h("b", null, k + ": "), d.school[k]) : null;
      }));
    }));
    modal("Карточка УО: " + (d.school["Наименование"] || ""), body, []);
  });

  function deptExportPage() {
    head("Передача данных в отдел образования", "Сводные данные без персональных сведений учащихся");
    $main.appendChild(h("div", { class: "card" },
      h("p", null, "Файл содержит паспорт учреждения, педагогических работников и сводную аналитику по классам, параллелям и предметам за все периоды ",
        h("b", null, "без персональных данных учащихся"), "."),
      h("div", { class: "bar" },
        h("button", { class: "pri", onclick: function () { download("/api/export/department", { year_id: yid(), format: "xlsx" }); } }, "Скачать Excel (.xlsx)"),
        h("button", { onclick: function () { download("/api/export/department", { year_id: yid(), format: "json" }); } }, "Скачать пакет для свода (.sgdep.json)")),
      h("div", { class: "hint" }, "Пакет .sgdep.json отправьте в отдел образования: там из пакетов всех школ строится свод по району.")));
  }

  // ===================================================================== классы
  ROUTES.classes = async function (params) {
    var u = S.me.user;
    head("Классы и нагрузка", S.year.name + " учебный год", u.can_manage ? h("button", { class: "pri", onclick: function () { classDialog(null); } }, "+ Класс") : null);
    var classes = await GET("/api/classes", { year_id: yid() });
    var staff = u.can_manage ? await GET("/api/staff") : [];
    var staffOpts = [["", "—"]].concat(staff.filter(function (s) { return s.active; }).map(function (s) { return [s.id, s.short]; }));
    function classDialog(c) {
      var f = buildForm([{ k: "grade", label: "Параллель (1–11)", type: "number" }, { k: "letter", label: "Буква" }, { k: "teacher_id", label: "Классный руководитель", type: "select", options: staffOpts },
        { k: "profile", label: "Профиль / особенности" }, { k: "shift", label: "Смена", type: "number" }, { k: "room", label: "Кабинет" }], c || { shift: 1 });
      var b = [{ text: "Сохранить", cls: "pri", fn: async function () {
        var v = f.values(); v.year_id = yid();
        if (c) await PUT("/api/classes/" + c.id, v); else await POST("/api/classes", v);
        route();
      } }];
      if (c && u.can_admin) b.push({ text: "Удалить класс", cls: "danger", fn: async function () {
        if (!confirmBox("Удалить класс " + c.name + " со всей нагрузкой, журналом и отметками? Действие необратимо.")) return false;
        await DEL("/api/classes/" + c.id); params.cid = ""; setParams(params); route();
      } });
      modal(c ? "Класс " + c.name : "Новый класс", f, b);
    }
    var list = h("div", { class: "bar" });
    classes.forEach(function (c) {
      list.appendChild(h("button", { class: String(c.id) === String(params.cid) ? "pri" : "", onclick: function () { params.cid = c.id; setParams(params); route(); } }, c.name + " (" + c.n + ")"));
    });
    $main.appendChild(list);
    if (!classes.length) { $main.appendChild(h("div", { class: "card muted" }, "Классы не созданы.")); return; }
    var c = classes.filter(function (x) { return String(x.id) === String(params.cid); })[0];
    if (!c) { $main.appendChild(h("div", { class: "card muted" }, "Выберите класс.")); return; }
    var tab = params.tab || "students";
    var canEdit = u.can_manage || u.own_classes.indexOf(c.id) >= 0;
    $main.appendChild(h("div", { class: "bar" }, h("h2", { style: { margin: 0 } }, c.name + " класс"), h("span", { class: "muted" }, "Классный руководитель: " + (c.teacher || "не назначен") + (c.profile ? " · " + c.profile : "")),
      h("span", { class: "sp" }), u.can_manage ? h("button", { onclick: function () { classDialog(c); } }, "Свойства класса") : null));
    var tabs = h("div", { class: "tabs" }, [["students", "Учащиеся"], ["load", "Нагрузка (предметы и учителя)"]].map(function (t) {
      return h("a", { href: "javascript:void 0", class: tab === t[0] ? "on" : "", onclick: function () { params.tab = t[0]; setParams(params); route(); } }, t[1]);
    }));
    $main.appendChild(tabs);
    var area = h("div"); $main.appendChild(area);
    if (tab === "students") {
      var studs = await GET("/api/classes/" + c.id + "/students", { all: params.all ? 1 : "" });
      var bar = h("div", { class: "bar" });
      if (canEdit) {
        bar.appendChild(h("button", { class: "pri", onclick: function () { studentDialog(null, c.id, route); } }, "+ Учащийся"));
        bar.appendChild(h("button", { onclick: safe(async function () {
          var all = await GET("/api/students", { year_id: yid(), unassigned: 1 });
          if (!all.length) { toast("Нет учащихся без класса в этом учебном году"); return; }
          var box = h("div", null, all.map(function (s) { return h("label", { style: { display: "block" } }, h("input", { type: "checkbox", value: s.id }), " " + s.last_name + " " + s.first_name + " " + (s.middle_name || "") + " " + (s.birth_date ? "(" + fmtDate(s.birth_date) + ")" : "")); }));
          modal("Зачислить в " + c.name + " класс", box, [{ text: "Зачислить", cls: "pri", fn: async function () {
            var ids = Array.prototype.map.call(box.querySelectorAll("input:checked"), function (i) { return i.value; });
            await POST("/api/classes/" + c.id + "/enroll", { student_ids: ids }); route();
          } }]);
        }) }, "Зачислить из базы"));
        bar.appendChild(h("button", { onclick: function () { pasteDialog(c); } }, "Вставить список ФИО"));
        bar.appendChild(h("button", { onclick: safe(async function () {
          var fs = await pickFile(".xlsx,.csv,.txt");
          if (!fs.length) return;
          var r = await POST("/api/classes/" + c.id + "/import", { filename: fs[0].name, data: await fileToB64(fs[0]) });
          toast("Добавлено учащихся: " + r.added); route();
        }) }, "Импорт из Excel/CSV"));
      }
      bar.appendChild(h("label", null, h("input", { type: "checkbox", checked: !!params.all, onchange: function (e) { params.all = e.target.checked ? 1 : ""; setParams(params); route(); } }), " показать выбывших"));
      area.appendChild(bar);
      var rows = studs.map(function (s, i) {
        return h("tr", { class: s.date_out ? "muted" : "" }, h("td", { class: "n" }, i + 1),
          h("td", null, h("a", { href: "javascript:void 0", onclick: function () { studentDialog(s.id, c.id, route); } }, s.last_name + " " + s.first_name + " " + (s.middle_name || ""))),
          h("td", { class: "n" }, fmtDate(s.birth_date)), h("td", { class: "n" }, s.gender || ""), h("td", null, s.pe_group || ""),
          h("td", null, s.date_out ? "выбыл " + fmtDate(s.date_out) : (s.date_in ? "с " + fmtDate(s.date_in) : "")),
          h("td", null, canEdit && !s.date_out ? h("button", { class: "sm", onclick: function () { leaveDialog(s, c, classes); } }, "Выбытие / перевод") : null,
            " ", h("a", { href: "#/reports?type=student&class_id=" + c.id + "&student_id=" + s.id, style: { fontSize: "12px" } }, "табель")));
      });
      area.appendChild(h("div", { class: "tw" }, h("table", { class: "t" }, h("thead", null, h("tr", null, ["№", "ФИО", "Дата рождения", "Пол", "Группа по физкультуре", "Движение", ""].map(function (x) { return h("th", null, x); }))), h("tbody", null, rows))));
      area.appendChild(h("div", { class: "hint" }, "Импорт: первая строка — заголовки (Фамилия, Имя, Отчество, Дата рождения, Пол, Адрес, Законные представители, Телефон…) либо столбцы по порядку: Фамилия; Имя; Отчество; Дата рождения; Пол."));
    } else {
      var asg = await GET("/api/assignments", { class_id: c.id });
      var subjects = u.can_manage ? await GET("/api/subjects") : [];
      var bar2 = h("div", { class: "bar" });
      if (u.can_manage) bar2.appendChild(h("button", { class: "pri", onclick: function () {
        var box = h("div", { style: { columns: "2" } }, subjects.filter(function (s) { return s.active; }).map(function (s) { return h("label", { style: { display: "block" } }, h("input", { type: "checkbox", value: s.id }), " " + s.name); }));
        var t = sel(staffOpts, ""), hr = h("input", { type: "number", value: 1, step: 0.5, style: { width: "80px" } }), ev = sel([["points", "10 баллов"], ["pass", "зачёт"], ["none", "без отметок"]], "points"), gr = h("input", { placeholder: "напр. 1 подгруппа" });
        modal("Добавить предметы в " + c.name + " класс", h("div", null, h("div", { class: "form" }, field("Учитель", t), field("Часов в неделю", hr), field("Система оценивания", ev), field("Группа (подгруппа)", gr)), h("h3", null, "Предметы"), box),
          [{ text: "Добавить", cls: "pri", fn: async function () {
            var ids = Array.prototype.map.call(box.querySelectorAll("input:checked"), function (i) { return i.value; });
            var r = await POST("/api/assignments", { class_id: c.id, subject_ids: ids, teacher_id: t.value, hours: hr.value, eval_type: ev.value, group_name: gr.value });
            toast("Добавлено: " + r.added); route();
          } }]);
      } }, "+ Предметы"));
      if (u.can_manage) bar2.appendChild(h("button", { onclick: safe(async function () {
        if (!confirmBox("Добавить в " + c.name + " класс предметы типового учебного плана для " + c.grade + " параллели? Уже добавленные предметы не дублируются.")) return;
        var r = await POST("/api/classes/" + c.id + "/autoload", {}); toast("Добавлено предметов: " + r.added); route();
      }) }, "Предметы типового плана"));
      area.appendChild(bar2);
      var evName = { points: "10 баллов", pass: "зачёт", none: "без отметок", attend: "посещение" };
      var rows2 = asg.map(function (a) {
        return h("tr", null, h("td", null, a.subject + (a.category && a.category !== "subject" ? " [" + (a.category === "fz" ? "ФЗ" : "ОпИ") + "]" : "")), h("td", null, a.group_name || ""), h("td", null, a.teacher || "—"), h("td", { class: "n" }, a.hours),
          h("td", null, evName[a.eval_type] || a.eval_type), h("td", { class: "n" }, a.n_sub ? a.n_sub + " уч-ся" : "весь класс"),
          h("td", null, journalOn() ? h("a", { href: "#/journal?aid=" + a.id }, "журнал") : h("a", { href: "#/finals?aid=" + a.id }, "отметки"), " ", u.can_manage ? h("button", { class: "sm", onclick: function () { asgDialog(a); } }, "изменить") : null));
      });
      area.appendChild(h("div", { class: "tw" }, h("table", { class: "t" }, h("thead", null, h("tr", null, ["Предмет", "Группа", "Учитель", "Ч/нед", "Оценивание", "Состав", ""].map(function (x) { return h("th", null, x); }))), h("tbody", null, rows2))));
      var asgDialog = safe(async function (a) {
        var studs = await GET("/api/classes/" + c.id + "/students");
        var curSub = []; // состав подгруппы
        var t = sel(staffOpts, a.teacher_id), hr = h("input", { type: "number", value: a.hours, step: 0.5 }), ev = sel([["points", "10 баллов"], ["pass", "зачёт"], ["none", "без отметок"]], a.eval_type), gr = h("input", { value: a.group_name || "" });
        var box = h("div", { style: { columns: "2" } }, studs.map(function (s) { return h("label", { style: { display: "block" } }, h("input", { type: "checkbox", value: s.id }), " " + s.last_name + " " + s.first_name); }));
        modal(c.name + " · " + a.subject, h("div", null, h("div", { class: "form" }, field("Учитель", t), field("Часов в неделю", hr), field("Система оценивания", ev), field("Группа (подгруппа)", gr)),
          h("h3", null, "Состав группы (если не отмечено никого — весь класс)"), box),
          [{ text: "Сохранить", cls: "pri", fn: async function () {
            var ids = Array.prototype.map.call(box.querySelectorAll("input:checked"), function (i) { return i.value; });
            await PUT("/api/assignments/" + a.id, { teacher_id: t.value, hours: hr.value, eval_type: ev.value, group_name: gr.value, student_ids: (ids.length || a.n_sub) ? ids : undefined }); route();
          } }, { text: "Удалить", cls: "danger", fn: async function () {
            try { await DEL("/api/assignments/" + a.id); }
            catch (e) { if (e.status === 409 && confirmBox(e.message + " Удалить всё равно?")) await api("DELETE", "/api/assignments/" + a.id + "?force=1"); else throw e; }
            route();
          } }]);
        curSub = curSub;
      });
    }
  };

  function pasteDialog(c) {
    var ta = h("textarea", { style: { width: "100%", minHeight: "260px", fontFamily: "Consolas, monospace" },
      placeholder: "Иванов Иван Иванович\nПетрова Анна Сергеевна\nСидоренко Мария" });
    var date = h("input", { type: "date", value: new Date().toISOString().slice(0, 10) });
    var info = h("div", { class: "hint" });
    ta.addEventListener("input", function () {
      var n = ta.value.split(/\r?\n/).filter(function (l) { return l.trim(); }).length;
      info.textContent = n ? "Строк: " + n : "";
    });
    modal("Добавить учащихся в " + c.name + " класс списком", h("div", null,
      h("p", { class: "muted", style: { marginTop: 0 } }, "Вставьте список — по одному учащемуся в строке: Фамилия Имя Отчество (разделитель — пробел). Номера в начале строк («1.», «2)») убираются автоматически. Можно скопировать столбец из Word или Excel."),
      ta, info, h("div", { class: "form", style: { marginTop: "8px" } }, field("Дата зачисления в класс", date))),
      [{ text: "Проверить", fn: async function () {
        var r = await POST("/api/classes/" + c.id + "/import_text", { text: ta.value, preview: 1 });
        var box = h("div", null, h("table", { class: "t" }, h("thead", null, h("tr", null, ["№", "Фамилия", "Имя", "Отчество"].map(function (x) { return h("th", null, x); }))),
          h("tbody", null, r.items.map(function (x, i) { return h("tr", null, h("td", { class: "n" }, i + 1), h("td", null, x.last_name), h("td", null, x.first_name), h("td", null, x.middle_name)); }))));
        modal("Проверка списка (" + r.items.length + ")", box, []);
        return false;
      } }, { text: "Добавить", cls: "pri", fn: async function () {
        var r = await POST("/api/classes/" + c.id + "/import_text", { text: ta.value, date_in: date.value });
        toast("Добавлено учащихся: " + r.added + (r.skipped.length ? ". Пропущены (уже есть в классе): " + r.skipped.join(", ") : ""));
        route();
      } }]);
    setTimeout(function () { ta.focus(); }, 50);
  }

  function leaveDialog(s, c, classes) {
    var date = h("input", { type: "date", value: new Date().toISOString().slice(0, 10) });
    var to = sel([["", "— выбывает из учреждения —"]].concat(classes.filter(function (x) { return x.id !== c.id; }).map(function (x) { return [x.id, "перевод в " + x.name + " класс"]; })), "");
    var reason = h("input", { placeholder: "напр. смена места жительства, приказ № …" });
    modal("Выбытие / перевод: " + s.last_name + " " + s.first_name, h("div", { class: "form" }, field("Дата", date), field("Куда", to), field("Основание", reason, "wide")),
      [{ text: "Провести", cls: "pri", fn: async function () {
        await POST("/api/classes/" + c.id + "/leave", { student_id: s.id, date: date.value, to_class_id: to.value, left_school: !to.value, reason: reason.value }); route();
      } }]);
  }

  var STUDENT_FORM = function () {
    var d = S.me.dicts;
    return [{ k: "last_name", label: "Фамилия" }, { k: "first_name", label: "Имя" }, { k: "middle_name", label: "Отчество" },
      { k: "gender", label: "Пол", type: "select", options: [["", ""], ["м", "мужской"], ["ж", "женский"]] }, { k: "birth_date", label: "Дата рождения", type: "date" },
      { k: "personal_no", label: "Идентификационный номер" }, { k: "address", label: "Адрес проживания", wide: true }, { k: "phone", label: "Телефон учащегося" },
      { k: "parents", label: "Законные представители (ФИО, место работы)", wide: true, type: "textarea" }, { k: "parents_phone", label: "Телефоны законных представителей" },
      { k: "family_type", label: "Семья", type: "list", options: d.family_type }, { k: "arrived_from", label: "Откуда прибыл", type: "list", options: d.arrived_from },
      { k: "study_form", label: "Форма получения образования", type: "list", options: d.study_form }, { k: "health_group", label: "Группа здоровья", type: "list", options: d.health_group },
      { k: "pe_group", label: "Группа по физической культуре", type: "list", options: d.pe_group }, { k: "language1", label: "Язык обучения" },
      { k: "foreign1", label: "Иностранный язык 1" }, { k: "foreign2", label: "Иностранный язык 2" }, { k: "social", label: "Социальный статус / льготы", wide: true },
      { k: "hobbies", label: "Объединения по интересам, увлечения", wide: true }, { k: "note", label: "Примечание", type: "textarea", wide: true }];
  };
  var studentDialog = safe(async function (sid, cid, after) {
    var data = {}, hist = [], canEdit = true;
    if (sid) { var r = await GET("/api/students/" + sid); data = r.student; hist = r.history; canEdit = r.can_edit; }
    var f = buildForm(visibleForm(STUDENT_FORM()), data);
    var body = h("div", null, f, hist.length ? h("div", null, h("h3", null, "Движение по классам"),
      hist.map(function (x) { return h("div", { class: "muted" }, x.year + ": " + x.class_name + " класс" + (x.date_in ? ", с " + fmtDate(x.date_in) : "") + (x.date_out ? " по " + fmtDate(x.date_out) : "") + (x.note ? " (" + x.note + ")" : "") + (x.year_result ? " — " + x.year_result : "")); })) : null,
      h("div", { class: "hint" }, "Обработка персональных данных осуществляется в соответствии с Законом Республики Беларусь от 07.05.2021 № 99-З «О защите персональных данных». Просмотр карточки фиксируется в журнале действий."));
    var btns = canEdit ? [{ text: "Сохранить", cls: "pri", fn: async function () {
      var v = f.values(); if (!v.last_name) throw new Error("Укажите фамилию");
      if (sid) await PUT("/api/students/" + sid, v); else { v.class_id = cid; await POST("/api/students", v); }
      toast("Сохранено"); after && after();
    } }] : [];
    if (sid && S.me.user.can_admin) btns.push({ text: "Удалить из базы", cls: "danger", fn: async function () {
      if (!confirmBox("Удалить учащегося и ВСЕ его отметки из базы? Для выбывших используйте «Выбытие / перевод».")) return false;
      await DEL("/api/students/" + sid); after && after();
    } });
    modal(sid ? "Личная карточка учащегося" : "Новый учащийся", body, btns);
  });

  // ===================================================================== учащиеся (поиск)
  ROUTES.students = async function (params) {
    head("Учащиеся", "Поиск по всем учащимся учреждения");
    var q = h("input", { placeholder: "Фамилия, имя или идентификационный номер", value: params.q || "", style: { minWidth: "320px" } });
    var area = h("div");
    var go = safe(async function () {
      params.q = q.value; setParams(params);
      var rows = await GET("/api/students", { q: q.value, year_id: yid() });
      clear(area).appendChild(h("div", { class: "tw" }, h("table", { class: "t" }, h("thead", null, h("tr", null, ["ФИО", "Класс", "Дата рождения", "Статус"].map(function (x) { return h("th", null, x); }))),
        h("tbody", null, rows.map(function (s) {
          return h("tr", null, h("td", null, h("a", { href: "javascript:void 0", onclick: function () { studentDialog(s.id, s.class_id, go); } }, s.last_name + " " + s.first_name + " " + (s.middle_name || ""))),
            h("td", null, s.class_name || h("span", { class: "muted" }, "без класса")), h("td", { class: "n" }, fmtDate(s.birth_date)), h("td", null, s.status === "left" ? "выбыл" : "обучается"));
        })))));
      if (!rows.length) area.appendChild(h("div", { class: "muted" }, "Ничего не найдено."));
    });
    q.addEventListener("keydown", function (e) { if (e.key === "Enter") go(); });
    $main.appendChild(h("div", { class: "bar" }, q, h("button", { class: "pri", onclick: go }, "Найти"),
      S.me.user.can_manage ? h("button", { onclick: function () { studentDialog(null, null, go); } }, "+ Учащийся без класса") : null));
    $main.appendChild(area);
    go();
  };

  // ===================================================================== сотрудники
  ROUTES.staff = async function () {
    var d = S.me.dicts;
    var FORM = [{ k: "last_name", label: "Фамилия" }, { k: "first_name", label: "Имя" }, { k: "middle_name", label: "Отчество" },
      { k: "position", label: "Должность", type: "list", options: d.position }, { k: "subjects_text", label: "Преподаваемые предметы" },
      { k: "education", label: "Образование", type: "list", options: d.education }, { k: "category", label: "Квалификационная категория", type: "list", options: d.category },
      { k: "degree", label: "Учёная степень", type: "list", options: d.degree }, { k: "title", label: "Почётное звание" },
      { k: "gender", label: "Пол", type: "select", options: [["", ""], ["м", "мужской"], ["ж", "женский"]] }, { k: "birth_date", label: "Дата рождения", type: "date" },
      { k: "hire_date", label: "Дата приёма на работу", type: "date" }, { k: "phone", label: "Телефон" }, { k: "email", label: "Эл. почта" },
      { k: "pc_skill", label: "Владение ПК", type: "list", options: ["базовое", "уверенное", "продвинутое"] }, { k: "active", label: "Работает", type: "check", def: true },
      { k: "note", label: "Примечание", type: "textarea", wide: true }];
    var dlg = function (s) {
      var f = buildForm(visibleForm(FORM), s || {});
      var b = [{ text: "Сохранить", cls: "pri", fn: async function () { if (s) await PUT("/api/staff/" + s.id, f.values()); else await POST("/api/staff", f.values()); route(); } }];
      if (s) b.push({ text: "Удалить", cls: "danger", fn: async function () { if (!confirmBox("Удалить сотрудника? Лучше снять отметку «Работает».")) return false; await DEL("/api/staff/" + s.id); route(); } });
      modal(s ? "Сотрудник" : "Новый сотрудник", f, b);
    };
    head("Сотрудники", "Педагогические и иные работники", h("button", { class: "pri", onclick: function () { dlg(null); } }, "+ Сотрудник"));
    var rows = await GET("/api/staff");
    $main.appendChild(h("div", { class: "tw" }, h("table", { class: "t" }, h("thead", null, h("tr", null, ["ФИО", "Должность", "Предметы", "Категория", "Образование", "Стаж с", "Статус"].map(function (x) { return h("th", null, x); }))),
      h("tbody", null, rows.map(function (s) {
        return h("tr", { class: s.active ? "" : "muted" }, h("td", null, h("a", { href: "javascript:void 0", onclick: function () { dlg(s); } }, s.last_name + " " + s.first_name + " " + (s.middle_name || ""))),
          h("td", null, s.position), h("td", null, s.subjects_text), h("td", null, s.category), h("td", null, s.education), h("td", { class: "n" }, fmtDate(s.hire_date)), h("td", null, s.active ? "работает" : "уволен"));
      })))));
  };

  // ===================================================================== предметы
  var CAT_NAME = function () { return S.me.categories; };
  async function subjectDelete(s) {
    try { await DEL("/api/subjects/" + s.id); route(); }
    catch (e) {
      if (e.status === 409 && e.message.indexOf("confirm_code:") === 0) {
        var code = e.message.split(":")[1];
        var inp = h("input", { style: { fontSize: "20px", letterSpacing: "4px", textAlign: "center", width: "160px" } });
        modal("Подтверждение удаления", h("div", null,
          h("p", null, "По предмету «" + s.name + "» выставлены отметки. Чтобы удалить предмет вместе со всеми отметками, введите код:"),
          h("div", { style: { fontSize: "26px", fontWeight: "700", letterSpacing: "6px", textAlign: "center", margin: "10px 0" } }, code),
          field("Код подтверждения", inp)),
          [{ text: "Удалить", cls: "danger", fn: async function () {
            if (inp.value.trim() !== code) { toast("Код введён неверно", true); return false; }
            await api("DELETE", "/api/subjects/" + s.id + "?code=" + code); route();
          } }]);
        setTimeout(function () { inp.focus(); }, 50);
      } else throw e;
    }
  }
  ROUTES.subjects = async function () {
    var catOpts = Object.keys(CAT_NAME()).map(function (k) { return [k, CAT_NAME()[k]]; });
    var FORM = [{ k: "name", label: "Название", wide: true }, { k: "short", label: "Сокращение (для ведомости)" }, { k: "sort", label: "Порядок", type: "number" },
      { k: "category", label: "Категория", type: "select", options: catOpts },
      { k: "eval_type", label: "Оценивание", type: "select", options: [["points", "10 баллов"], ["pass", "зачёт"], ["none", "без отметок"], ["attend", "только посещение (н / выб)"]] },
      { k: "active", label: "Активен", type: "check", def: true }];
    var dlg = function (s) {
      var f = buildForm(FORM, s || { sort: 500, eval_type: "points", category: "subject" });
      var b = [{ text: "Сохранить", cls: "pri", fn: async function () { if (s) await PUT("/api/subjects/" + s.id, f.values()); else await POST("/api/subjects", f.values()); route(); } }];
      if (s) b.push({ text: "Удалить", cls: "danger", fn: async function () { await subjectDelete(s); return false; } });
      modal(s ? "Предмет" : "Новый предмет", h("div", null, f,
        h("div", { class: "hint" }, "Факультатив (ФЗ) и объединение по интересам (ОпИ) — только учёт посещения (н / выбыл), в отметки и отчёты успеваемости не входят, занятия можно проводить в каникулы.")), b);
    };
    head("Учебные предметы, факультативы и объединения", "Категории: учебные предметы, ФЗ, ОпИ", h("button", { class: "pri", onclick: function () { dlg(null); } }, "+ Предмет"));
    var rows = await GET("/api/subjects");
    $main.appendChild(h("div", { class: "tw" }, h("table", { class: "t" }, h("thead", null, h("tr", null, ["№", "Название", "Сокр.", "Категория", "Оценивание", "Статус"].map(function (x) { return h("th", null, x); }))),
      h("tbody", null, rows.map(function (s, i) {
        return h("tr", { class: s.active ? "" : "muted" }, h("td", { class: "n" }, i + 1), h("td", null, h("a", { href: "javascript:void 0", onclick: function () { dlg(s); } }, s.name)), h("td", null, s.short),
          h("td", null, CAT_NAME()[s.category] || "—"),
          h("td", null, { points: "10 баллов", pass: "зачёт", none: "без отметок", attend: "посещение" }[s.eval_type]), h("td", null, s.active ? "" : "не используется"));
      })))));
  };

  // ===================================================================== паспорт учреждения
  ROUTES.school = async function () {
    var r = await GET("/api/school");
    var can = S.me.user.can_manage;
    head(deptMode() ? "Сведения об отделе (управлении) образования" : "Паспорт учреждения образования", null, can ? h("button", { class: "pri", onclick: safe(async function () {
      var v = {}; Array.prototype.forEach.call($main.querySelectorAll("[data-k]"), function (i) { v[i.getAttribute("data-k")] = i.value; });
      await PUT("/api/school", v); S.me.school = v; toast("Сохранено");
    }) }, "Сохранить") : null);
    r.fields.forEach(function (g) {
      $main.appendChild(h("div", { class: "card" }, h("h2", { style: { marginTop: 0 } }, g[0]), h("div", { class: "form" }, g[1].map(function (k) {
        return field(k, h("input", { "data-k": k, value: r.school[k] || "", disabled: !can }));
      }))));
    });
  };

  // ===================================================================== учебный год
  ROUTES.years = async function () {
    head("Учебный год", "Учебные годы, сроки четвертей, закрытие периодов, перевод учащихся");
    var ys = await GET("/api/years");
    var ny = h("input", { type: "number", value: new Date().getMonth() >= 7 ? new Date().getFullYear() : new Date().getFullYear() - 1, style: { width: "90px" } });
    var sc = sel([["quarters", "четверти"], ["halves", "полугодия"]], "quarters");
    $main.appendChild(h("div", { class: "card" }, h("div", { class: "bar" }, h("b", null, "Новый учебный год, начиная с"), ny, sc,
      h("button", { class: "pri", onclick: safe(async function () { await POST("/api/years", { start_year: ny.value, scheme: sc.value }); S.me = await GET("/api/me"); S.year = S.year || S.me.current_year; renderShell(); route(); }) }, "Создать"))));
    ys.forEach(function (y) {
      var rows = y.periods.map(function (p) {
        var a = h("input", { type: "date", value: p.start_date || "" }), b = h("input", { type: "date", value: p.end_date || "" });
        var lk = h("input", { type: "checkbox", checked: !!p.locked });
        var save = safe(async function () { await PUT("/api/periods/" + p.id, { start_date: a.value, end_date: b.value, locked: lk.checked }); toast("Сохранено"); });
        a.onchange = save; b.onchange = save; lk.onchange = save;
        var dated = /^[qh]\d$/.test(p.kind);
        return h("tr", null, h("td", null, p.name), h("td", null, dated ? a : ""), h("td", null, dated ? b : ""), h("td", null, h("label", null, lk, " закрыт для изменений")));
      });
      var others = ys.filter(function (x) { return x.id !== y.id; });
      var tgt = sel(others.map(function (x) { return [x.id, x.name]; }), others[0] && others[0].id);
      $main.appendChild(h("div", { class: "card" },
        h("div", { class: "bar" }, h("h2", { style: { margin: 0 } }, y.name + " учебный год"), y.is_current ? h("span", { class: "pill" }, "текущий") : h("button", { class: "sm", onclick: safe(async function () { await POST("/api/years/" + y.id + "/current"); S.me = await GET("/api/me"); S.year = S.me.current_year; renderShell(); route(); }) }, "Сделать текущим"), h("span", { class: "muted" }, y.scheme === "halves" ? "полугодия" : "четверти")),
        h("table", { class: "t", style: { maxWidth: "720px" } }, h("thead", null, h("tr", null, ["Период", "Начало", "Окончание", "Блокировка"].map(function (x) { return h("th", null, x); }))), h("tbody", null, rows)),
        others.length ? h("div", { class: "bar", style: { marginTop: "12px" } }, h("span", null, "Перевести учащихся в"), tgt,
          h("button", { onclick: safe(async function () {
            if (!confirmBox("Создать классы и перевести учащихся из " + y.name + " в " + tgt.options[tgt.selectedIndex].text + "? Учащиеся с итогом «оставлен на повторное обучение» останутся в той же параллели, выпускники и выбывшие не переводятся.")) return;
            var r = await POST("/api/years/" + y.id + "/promote", { target_year_id: tgt.value }); toast("Создано классов: " + r.classes + ", переведено учащихся: " + r.students);
          }) }, "Перевод учащихся")) : null));
    });
    $main.appendChild(h("div", { class: "hint" }, "Сроки четвертей устанавливаются ежегодно Министерством образования — проверьте даты. Закрытый период нельзя изменить никому, кроме администратора."));
  };

  // ===================================================================== администрирование
  // ---------------------------------------------------------------- импорт из «Старт ШКОЛА»
  async function startSchoolImport() {
    var st = await GET("/api/import/startschool");
    var box = h("div");
    $main.appendChild(box);
    var opts = { kinds: st.kinds.map(function (k) { return k.kind; }), overwrite: false, passport: true, staff: true, create_teachers: true };
    var previewOk = false;

    function render() {
      clear(box);
      box.appendChild(h("div", { class: "card" },
        h("h2", { style: { marginTop: 0 } }, "Перенос данных из «Старт ШКОЛА»"),
        h("p", null, "Добавьте книги Excel из папки «Старт ШКОЛА»: главную книгу «Старт ШКОЛА.xls» (паспорт учреждения, классы и классные руководители, сотрудники) ",
          "и книги параллелей «Параллель_1.xls» … «Параллель_11.xls» (списки учащихся и отметки за четверти и год). Можно добавить только часть файлов."),
        h("p", { class: "hint" }, "Учащиеся сопоставляются по фамилии и имени, в том числе с небольшими опечатками. Перед импортом можно посмотреть, что изменится; ",
          "перед записью автоматически создаётся резервная копия базы. Отметки переносятся в текущий учебный год."),
        h("div", { class: "bar" },
          h("button", { class: "pri", onclick: safe(addFiles) }, "Добавить файлы…"),
          st.files.length ? h("button", { onclick: safe(async function () { await DEL("/api/import/startschool"); st.files = []; previewOk = false; render(); }) }, "Очистить список") : null),
        st.files.length ? h("table", { class: "t", style: { marginTop: "10px" } },
          h("thead", null, h("tr", null, h("th", null, "Файл"), h("th", null, "Содержимое"))),
          h("tbody", null, st.files.map(function (f) { return h("tr", null, h("td", null, f.file), h("td", null, f.text)); })))
          : h("div", { class: "muted", style: { marginTop: "8px" } }, "Файлы ещё не добавлены.")));
      if (!st.files.length) return;
      var kindBoxes = st.kinds.map(function (k) {
        var cb = h("input", { type: "checkbox" }); cb.checked = opts.kinds.indexOf(k.kind) >= 0;
        cb.onchange = function () { opts.kinds = st.kinds.filter(function (x, i) { return kindBoxes[i].querySelector("input").checked; }).map(function (x) { return x.kind; }); previewOk = false; renderResult(null); };
        return h("label", { class: "chk" }, cb, " " + k.name);
      });
      function flag(key, text) {
        var cb = h("input", { type: "checkbox" }); cb.checked = !!opts[key];
        cb.onchange = function () { opts[key] = cb.checked; previewOk = false; renderResult(null); };
        return h("label", { class: "chk" }, cb, " " + text);
      }
      box.appendChild(h("div", { class: "card" }, h("h3", { style: { marginTop: 0 } }, "Что переносить"),
        h("div", { class: "chkrow" }, kindBoxes),
        h("div", { class: "chkrow" }, flag("overwrite", "Заменять отметки, которые в SchoolGrade уже выставлены и отличаются"),
          flag("passport", "Заполнить пустые поля паспорта учреждения"), flag("staff", "Добавить сотрудников из главной книги"),
          flag("create_teachers", "Добавлять учителей, которых нет среди сотрудников (по фамилии и инициалам)")),
        h("div", { class: "bar", style: { marginTop: "10px" } },
          h("button", { class: "pri", onclick: safe(async function () { renderResult(await POST("/api/import/startschool/preview", opts), true); }) }, "Проверить, что изменится"))));
      resBox = h("div"); box.appendChild(resBox);
    }
    var resBox = null;

    function renderResult(r, isPreview) {
      if (!resBox) return;
      clear(resBox);
      if (!r) return;
      previewOk = !!isPreview;
      var S1 = r.summary;
      var lines = [["Классы", S1.classes_new + " новых, " + S1.classes_existing + " уже есть"],
        ["Учащиеся", S1.students_new + " новых, " + S1.students_matched + " найдено по ФИО" + (S1.students_fuzzy ? ", " + S1.students_fuzzy + " — с исправлением опечаток" : "")],
        ["Сотрудники", S1.staff_new + " новых"], ["Предметы", S1.subjects_new + " новых"],
        ["Нагрузка", S1.assignments_new + " новых предметов в классах" + (S1.groups ? ", " + S1.groups + " групп по иностранному языку" : "")],
        ["Паспорт учреждения", S1.passport_fields + " полей заполнено"]];
      Object.keys(r.marks).forEach(function (k) {
        var m = r.marks[k], kn = (st.kinds.filter(function (x) { return x.kind === k; })[0] || {}).name || k;
        var parts = [m.new + " новых", m.same + " совпадают"];
        if (m.replaced) parts.push(m.replaced + " заменено");
        if (m.diff_kept) parts.push(m.diff_kept + " отличаются (оставлены)");
        if (m.locked) parts.push(m.locked + " в закрытом периоде");
        if (m.new + m.same + m.replaced + m.diff_kept + m.locked) lines.push(["Отметки: " + kn, parts.join(", ")]);
      });
      resBox.appendChild(h("div", { class: "card" },
        h("h3", { style: { marginTop: 0 } }, isPreview ? "Предварительный просмотр (база пока не изменена)" : "Импорт выполнен"),
        r.backup ? h("p", { class: "hint" }, "Резервная копия до импорта: " + r.backup + " (Администрирование → Резервные копии).") : null,
        h("table", { class: "t" }, h("tbody", null, lines.map(function (l) { return h("tr", null, h("th", { style: { textAlign: "left", width: "260px" } }, l[0]), h("td", null, l[1])); }))),
        r.warnings.length ? h("div", { class: "warnbox" }, h("b", null, "Обратите внимание"), h("ul", null, r.warnings.map(function (w) { return h("li", null, w); }))) : null,
        h("h3", null, "По классам"),
        h("table", { class: "t" }, h("thead", null, h("tr", null, ["Класс", "", "Кл. руководитель", "Учащихся в файле", "Новых", "Отметок", "Сопоставлено с исправлением"].map(function (x) { return h("th", null, x); }))),
          h("tbody", null, r.classes.map(function (c) {
            var nm = Object.keys(c.marks).reduce(function (a, k) { return a + c.marks[k]; }, 0);
            return h("tr", null, h("td", null, c.name), h("td", null, h("span", { class: "pill " + (c.status === "новый" ? "warn" : "ok") }, c.status)), h("td", null, c.teacher),
              h("td", { class: "n" }, c.students), h("td", { class: "n" }, c.new_students || ""), h("td", { class: "n" }, nm || ""), h("td", null, c.fuzzy.join("; ")));
          }))),
        r.log.length ? h("details", { style: { marginTop: "10px" } }, h("summary", null, "Подробный протокол (" + r.log.length + ")"),
          h("pre", { class: "log" }, r.log.join("\n"))) : null,
        isPreview ? h("div", { class: "bar", style: { marginTop: "12px" } }, h("button", { class: "pri", onclick: safe(async function () {
          if (!previewOk) { toast("Параметры изменились — проверьте ещё раз"); return; }
          if (!confirmBox("Выполнить импорт? Перед записью будет создана резервная копия базы.")) return;
          var res = await POST("/api/import/startschool/commit", opts);
          st.files = []; render(); renderResult(res, false); toast("Импорт выполнен");
        }) }, "Импортировать")) : null));
      resBox.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    async function addFiles() {
      var fs = await pickFile(".xls", true); if (!fs.length) return;
      var prog = h("div", { class: "pill warn", style: { marginTop: "8px", display: "inline-block" } });
      box.firstChild.appendChild(prog);
      for (var i = 0; i < fs.length; i++) {
        prog.textContent = "Чтение файла " + (i + 1) + " из " + fs.length + ": " + fs[i].name + "…";
        try {
          st = Object.assign(st, await POST("/api/import/startschool/file", { filename: fs[i].name, data: await fileToB64(fs[i]) }));
        } catch (e) { toast(e.message || String(e), true); }
      }
      previewOk = false; render();
    }
    render();
  }

  ROUTES.admin = async function (params) {
    head("Администрирование");
    var tab = params.tab || "users";
    var tabs = [["users", "Пользователи"], ["rules", "Настройки и правила"], ["fields", "Тип и поля"], ["transfer", "Перенос данных"], ["startschool", "Импорт из «Старт ШКОЛА»"], ["backup", "Резервные копии"], ["audit", "Журнал действий"]];
    if (deptMode()) tabs = tabs.filter(function (t) { return t[0] !== "startschool"; });
    $main.appendChild(h("div", { class: "tabs" }, tabs.map(function (t) {
      return h("a", { href: "javascript:void 0", class: tab === t[0] ? "on" : "", onclick: function () { params.tab = t[0]; setParams(params); route(); } }, t[1]);
    })));
    if (tab === "users") {
      var users = await GET("/api/users"), staff = await GET("/api/staff");
      var roleOpts = Object.keys(S.me.roles).map(function (k) { return [k, S.me.roles[k]]; });
      var dlg = function (u) {
        var f = buildForm([{ k: "login", label: "Логин" }, { k: "full_name", label: "Имя для отображения" }, { k: "role", label: "Роль", type: "select", options: roleOpts },
          { k: "password", label: u ? "Новый пароль (если нужно сбросить)" : "Пароль", type: "password" },
          { k: "active", label: "Активен", type: "check", def: true }], u || { role: "teacher" });
        if (u) f.querySelector("input").disabled = true;
        var linked = (u && u.staff_ids) || [];
        var staffBox = h("div", { style: { maxHeight: "180px", overflow: "auto", border: "1px solid #ddd", borderRadius: "5px", padding: "6px", columns: "2" } },
          staff.filter(function (s) { return s.active; }).map(function (s) {
            return h("label", { style: { display: "block" } }, h("input", { type: "checkbox", value: s.id, checked: linked.indexOf(s.id) >= 0 }), " " + s.short);
          }));
        modal(u ? "Пользователь " + u.login : "Новый пользователь", h("div", null, f,
          h("div", { style: { marginTop: "8px" } }, h("b", null, "Сотрудники учётной записи"),
            h("div", { class: "hint" }, "Можно привязать нескольких — например, для общего рабочего места в учительской. По привязанным сотрудникам определяются классное руководство и преподаваемые предметы."), staffBox),
          h("div", { class: "hint" }, "При первом входе пользователь обязан сменить пароль.")),
          [{ text: "Сохранить", cls: "pri", fn: async function () {
            var v = f.values();
            v.staff_ids = Array.prototype.map.call(staffBox.querySelectorAll("input:checked"), function (i) { return +i.value; });
            if (u) await PUT("/api/users/" + u.id, v); else await POST("/api/users", v); route();
          } }].concat(u ? [{ text: "Удалить", cls: "danger", fn: async function () { if (!confirmBox("Удалить пользователя?")) return false; await DEL("/api/users/" + u.id); route(); } }] : []));
      };
      $main.appendChild(h("div", { class: "bar" }, h("button", { class: "pri", onclick: function () { dlg(null); } }, "+ Пользователь"),
        h("button", { onclick: function () {
          var login = h("input", { value: "uchitelskaya" }), pw = h("input", { value: "", placeholder: "задайте пароль" });
          modal("Учётная запись для учительской", h("div", null,
            h("p", null, "Общий аккаунт для одного ПК в учительской: заполняет отметки за четверть, год, экзамен и итоговые и поведение по ", h("b", null, "всем классам"), ", но не может менять списки учащихся и предметы."),
            h("div", { class: "form" }, field("Логин", login), field("Пароль", pw))),
            [{ text: "Создать", cls: "pri", fn: async function () {
              if ((pw.value || "").length < 6) { toast("Пароль не короче 6 символов", true); return false; }
              await POST("/api/users", { login: login.value, password: pw.value, full_name: "Учительская", role: "workspace" });
              toast("Создан аккаунт «Учительская»"); route();
            } }]);
        } }, "+ Аккаунт «Учительская»")));
      $main.appendChild(h("div", { class: "tw" }, h("table", { class: "t" }, h("thead", null, h("tr", null, ["Логин", "Имя", "Роль", "Сотрудники", "Статус"].map(function (x) { return h("th", null, x); }))),
        h("tbody", null, users.map(function (u) {
          var names = (u.staff_ids || []).map(function (id) { var s = staff.filter(function (x) { return x.id === id; })[0]; return s ? s.short : ""; }).filter(Boolean).join(", ");
          return h("tr", { class: u.active ? "" : "muted" }, h("td", null, h("a", { href: "javascript:void 0", onclick: function () { dlg(u); } }, u.login)), h("td", null, u.full_name), h("td", null, S.me.roles[u.role]), h("td", null, names),
            h("td", null, (u.active ? "активен" : "отключён") + (u.must_change ? " · ждёт смены пароля" : "")));
        })))));
      $main.appendChild(h("div", { class: "hint" }, "Роли: администратор — всё; директор/заместитель — все данные, отчёты, закрытие периодов; учительская (общий доступ) — отметки за периоды и поведение по всем классам, без изменения списков и предметов; классный руководитель — свой класс и свои предметы; учитель — свои журналы; только просмотр — отчёты."));
    } else if (tab === "fields") {
      $main.appendChild(h("div", { class: "card" }, h("h2", { style: { marginTop: 0 } }, "Тип организации"),
        h("div", { class: "bar" },
          (function () { var s = sel(S.me.org_types.map(function (o) { return [o.code, o.name]; }), S.me.org_type);
            return h("span", null, s, " ", h("button", { class: "pri", onclick: safe(async function () {
              if (!confirmBox("Изменить тип организации на «" + s.options[s.selectedIndex].text + "»?")) return;
              await PUT("/api/org_type", { org_type: s.value }); S.me = await GET("/api/me"); renderShell(); route();
            }) }, "Сохранить тип")); })()),
        h("div", { class: "hint" }, "От типа зависит набор предметов типового плана и, для отдела образования, особый режим (только своды по школам).")));
      var hf = S.me.hidden_fields, labels = fieldLabels();
      function hideCard(table, title) {
        var box = h("div", { style: { columns: "3" } }, S.me.hideable[table].map(function (fk) {
          return h("label", { style: { display: "block" } }, h("input", { type: "checkbox", value: fk, checked: hf[table].indexOf(fk) >= 0 }), " " + (labels[fk] || fk));
        }));
        return h("div", { class: "card" }, h("h3", { style: { marginTop: 0 } }, title), box,
          h("div", { class: "bar", style: { marginTop: "8px" } }, h("button", { class: "pri", onclick: safe(async function () {
            var sel2 = Array.prototype.map.call(box.querySelectorAll("input:checked"), function (i) { return i.value; });
            var payload = {}; payload[table] = sel2;
            S.me.hidden_fields = await PUT("/api/hidden_fields", payload); toast("Сохранено");
          }) }, "Сохранить")));
      }
      $main.appendChild(h("div", { class: "card" }, h("h2", { style: { marginTop: 0 } }, "Скрытие ненужных полей"),
        h("div", { class: "hint" }, "Отмеченные поля убираются из карточек учащихся и сотрудников: их не видят и не заполняют другие пользователи. Фамилия скрыта быть не может.")));
      $main.appendChild(hideCard("students", "Поля учащихся"));
      $main.appendChild(hideCard("staff", "Поля сотрудников"));
    } else if (tab === "startschool") {
      await startSchoolImport();
    } else if (tab === "transfer") {
      $main.appendChild(h("div", { class: "card" }, h("h2", { style: { marginTop: 0 } }, "Перенос всех настроек и оценок на другой сервер"),
        h("p", null, "Экспорт — один файл .sgdb со всеми данными (пароли — необратимый хэш, персональные данные зашифрованы). Импорт заменяет текущую базу (перед этим создаётся резервная копия)."),
        h("div", { class: "bar" },
          h("button", { class: "pri", onclick: function () { download("/api/export/full"); } }, "Экспорт всех данных (.sgdb)"),
          h("button", { onclick: safe(async function () {
            var fs = await pickFile(".sgdb,.db"); if (!fs.length) return;
            if (!confirmBox("Импортировать данные из файла " + fs[0].name + "? Текущая база будет заменена.")) return;
            await POST("/api/backups/restore", { data: await fileToB64(fs[0]) }); toast("Данные импортированы"); boot();
          }) }, "Импорт данных…")),
        h("div", { class: "hint" }, "Файл .sgdb читается только программой SchoolGrade той же или новой версии.")));
    } else if (tab === "rules") {
      var on = journalOn();
      $main.appendChild(h("div", { class: "card" }, h("h2", { style: { marginTop: 0 } }, "Режим работы"),
        h("div", { class: "bar" }, h("span", { class: "pill " + (on ? "ok" : "warn") }, on ? "Электронный журнал включён" : "Только ведомости (журнал отключён)"),
          h("button", { class: on ? "" : "pri", onclick: safe(async function () {
            var msg = on ? "Отключить электронный журнал? Учителя будут вводить только отметки за четверти, год, экзамен и итоговые в ведомостях. Уже введённые уроки и текущие отметки сохранятся и вернутся при повторном включении."
              : "Включить электронный журнал (уроки, текущие отметки, пропуски)?";
            if (!confirmBox(msg)) return;
            S.me.features = await PUT("/api/features", { journal: !on }); renderShell(); route();
          }) }, on ? "Отключить журнал — только ведомости" : "Включить электронный журнал")),
        h("div", { class: "hint" }, "В режиме «только ведомости» скрыты журнал, ввод уроков и текущих отметок, отчёт о пропусках. Ведомости класса, отметки по предметам, поведение, отчёты и выгрузка в отдел образования работают как обычно.")));
      var r = S.me.rules;
      var f = buildForm([{ k: "min_marks_quarter", label: "Минимум текущих отметок для отметки за четверть", type: "number" },
        { k: "min_marks_quarter_1h", label: "…если предмет 1 час в неделю", type: "number" },
        { k: "kz_min_point", label: "«Качество знаний»: доля отметок не ниже (балл)", type: "number" },
        { k: "unexcused_na_as_zero", label: "н/а без уважительной причины = 0 при расчёте годовой", type: "check" }], r);
      $main.appendChild(h("div", { class: "card" }, f, h("div", { class: "bar", style: { marginTop: "12px" } }, h("button", { class: "pri", onclick: safe(async function () {
        S.me.rules = await PUT("/api/rules", f.values()); toast("Сохранено");
      }) }, "Сохранить")),
        h("div", { class: "hint" }, "Значения по умолчанию соответствуют Правилам проведения аттестации учащихся при освоении содержания образовательных программ общего среднего образования (постановление Министерства образования РБ от 11.07.2022 № 184 с изменениями). При изменении законодательства скорректируйте параметры здесь. ",
          "СОУ рассчитывается по весам: 10–100%, 9–96%, 8–90%, 7–74%, 6–55%, 5–45%, 4–40%, 3–32%, 2–20%, 1–12%.")));
    } else if (tab === "backup") {
      var list = await GET("/api/backups");
      $main.appendChild(h("div", { class: "bar" }, h("button", { class: "pri", onclick: safe(async function () { var b = await POST("/api/backups"); toast("Создана копия " + b.name); route(); }) }, "Создать резервную копию"),
        h("button", { onclick: safe(async function () {
          var fs = await pickFile(".db"); if (!fs.length) return;
          if (!confirmBox("Восстановить базу из файла " + fs[0].name + "? Текущие данные будут заменены (перед этим создаётся копия).")) return;
          await POST("/api/backups/restore", { data: await fileToB64(fs[0]) }); toast("База восстановлена"); boot();
        }) }, "Восстановить из файла…"),
        h("button", { onclick: safe(async function () { if (!confirmBox("Загрузить вымышленные демонстрационные данные?")) return; await POST("/api/demo"); toast("Демо-данные загружены"); boot(); }) }, "Загрузить демо-данные")));
      $main.appendChild(h("div", { class: "tw" }, h("table", { class: "t" }, h("thead", null, h("tr", null, ["Файл", "Дата", "Размер", ""].map(function (x) { return h("th", null, x); }))),
        h("tbody", null, list.map(function (b) {
          return h("tr", null, h("td", null, b.name), h("td", null, b.mtime), h("td", { class: "n" }, Math.round(b.size / 1024) + " КБ"),
            h("td", null, h("button", { class: "sm", onclick: function () { download("/api/backups/download", { name: b.name }); } }, "Скачать"), " ",
              h("button", { class: "sm danger", onclick: safe(async function () { if (!confirmBox("Восстановить базу из " + b.name + "? Текущие данные будут заменены.")) return; await POST("/api/backups/restore", { name: b.name }); toast("База восстановлена"); boot(); }) }, "Восстановить")));
        })))));
      $main.appendChild(h("div", { class: "hint" }, "Автоматическая копия создаётся раз в сутки (хранятся последние 30). Регулярно копируйте папку backup на внешний носитель."));
    } else {
      var rows = await GET("/api/audit", { limit: 500 });
      $main.appendChild(h("div", { class: "bar" }, h("button", { onclick: function () { download("/api/audit", { format: "xlsx", limit: 5000 }); } }, "Выгрузить в Excel")));
      $main.appendChild(h("div", { class: "tw" }, h("table", { class: "t" }, h("thead", null, h("tr", null, ["Дата и время", "Пользователь", "IP", "Действие", "Подробности"].map(function (x) { return h("th", null, x); }))),
        h("tbody", null, rows.map(function (r) { return h("tr", null, h("td", null, r.ts), h("td", null, r.login), h("td", null, r.ip), h("td", null, r.action), h("td", { style: { maxWidth: "520px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }, title: r.details }, r.details)); })))));
    }
  };
  function translit(s) {
    var m = { а: "a", б: "b", в: "v", г: "g", д: "d", е: "e", ё: "e", ж: "zh", з: "z", и: "i", й: "y", к: "k", л: "l", м: "m", н: "n", о: "o", п: "p", р: "r", с: "s", т: "t", у: "u", ф: "f", х: "h", ц: "ts", ч: "ch", ш: "sh", щ: "sch", ъ: "", ы: "y", ь: "", э: "e", ю: "yu", я: "ya", і: "i", ў: "u" };
    return s.toLowerCase().split("").map(function (c) { return m[c] !== undefined ? m[c] : (/[a-z0-9]/.test(c) ? c : ""); }).join("");
  }
  function fieldLabels() {
    return {
      first_name: "Имя", middle_name: "Отчество", gender: "Пол", birth_date: "Дата рождения",
      personal_no: "Идентификационный номер", address: "Адрес проживания", phone: "Телефон",
      arrived_from: "Откуда прибыл", study_form: "Форма получения образования", health_group: "Группа здоровья",
      pe_group: "Группа по физкультуре", language1: "Язык обучения", foreign1: "Иностранный язык 1",
      foreign2: "Иностранный язык 2", family_type: "Семья", parents: "Законные представители",
      parents_phone: "Телефоны законных представителей", social: "Социальный статус", hobbies: "Увлечения", note: "Примечание",
      position: "Должность", education: "Образование", category: "Квалификационная категория", degree: "Учёная степень",
      title: "Почётное звание", hire_date: "Дата приёма", email: "Эл. почта", pc_skill: "Владение ПК", subjects_text: "Преподаваемые предметы"
    };
  }
  function visibleForm(fields) {
    var hidden = (S.me.hidden_fields || {});
    var hs = (hidden.students || []).concat(hidden.staff || []);
    return fields.filter(function (d) { return hs.indexOf(d.k) < 0; });
  }

  // ===================================================================== о программе
  ROUTES.about = async function () {
    var a = S.me.about;
    head("О программе");
    $main.appendChild(h("div", { class: "card about" },
      h("div", { style: { display: "flex", gap: "18px", alignItems: "center" } }, h("img", { src: "icon.svg", style: { width: "72px" } }),
        h("div", null, h("h1", null, a.name + " " + a.version), h("div", { class: "muted" }, "Учёт результатов учебной деятельности учащихся учреждений общего среднего образования Республики Беларусь"))),
      h("h3", null, "Разработчик"),
      h("p", null, a.org), h("p", null, "Разработчик: " + a.author), h("p", null, a.city_year),
      h("h3", null, "Нормативная основа"),
      h("p", null, "Кодекс Республики Беларусь об образовании; Правила проведения аттестации учащихся при освоении содержания образовательных программ общего среднего образования (постановление Министерства образования РБ от 11.07.2022 № 184, с изменениями); нормы оценки результатов учебной деятельности учащихся по учебным предметам; Закон Республики Беларусь «О защите персональных данных» от 07.05.2021 № 99-З."),
      h("h3", null, "Система оценивания"),
      h("p", null, "10-балльная шкала, пять уровней: низкий (1–2), удовлетворительный (3–4), средний (5–6), достаточный (7–8), высокий (9–10). Также: зач., незач., осв., н/а, н/и; поведение — прим., уд., неуд."),
      h("p", { class: "muted" }, "Программа развивает идеи табличного комплекса «Старт ШКОЛА» (ИП Кудласевич В.В., 2005–2006) и не использует его код.")));
  };

  boot();
})();
