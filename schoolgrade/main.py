# -*- coding: utf-8 -*-
"""Точка входа SchoolGrade: запуск сервера и окна управления.

  SchoolGrade.exe                 — окно управления сервером (по умолчанию)
  SchoolGrade.exe --nogui         — без окна (служба / консоль)
  SchoolGrade.exe --port 8090     — другой порт
  SchoolGrade.exe --data D:\\SG    — папка с данными
  SchoolGrade.exe --local         — доступ только с этого компьютера
  SchoolGrade.exe --service       — запуск диспетчером служб Windows (вручную не использовать)
  SchoolGrade.exe --service-install / --service-remove / --service-start / --service-stop  (права администратора)
"""
import argparse
import json
import os
import sys
import threading
import time
import webbrowser

from . import APP_NAME, APP_VERSION, APP_ORG, APP_AUTHOR, APP_CITY_YEAR
from . import api, auth, winservice
from .db import Database, DEFAULT_SUBJECTS, SUBJECT_SHORT
from .server import ServerThread, local_addresses


def resource_dir():
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, "static")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


def default_data_dir():
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cand = os.path.join(base, "SchoolGrade_data")
    try:
        if not os.path.isdir(cand):
            os.makedirs(cand)
        test = os.path.join(cand, ".write_test")
        with open(test, "w") as f:
            f.write("1")
        os.remove(test)
        return cand
    except OSError:
        appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
        cand = os.path.join(appdata, "SchoolGrade")
        if not os.path.isdir(cand):
            os.makedirs(cand)
        return cand


def load_config(data_dir):
    path = os.path.join(data_dir, "config.json")
    cfg = {"port": 8080, "host": "0.0.0.0", "open_browser": True, "backup_keep": 30}
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(data_dir, cfg):
    try:
        with open(os.path.join(data_dir, "config.json"), "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def init_app(data_dir, verbose=False):
    db = Database(os.path.join(data_dir, "schoolgrade.db"))
    auth.ensure_admin(db)
    if db.val("SELECT COUNT(*) FROM subjects") == 0 and db.get_setting("org_type"):
        # предметы заводятся после выбора типа организации (api_org_type_put),
        # но если тип уже выбран, а предметов нет — наполнить базовым набором
        for i, name in enumerate(DEFAULT_SUBJECTS):
            db.ex("INSERT INTO subjects(name,short,sort,category) VALUES(?,?,?,'subject')",
                  (name, SUBJECT_SHORT.get(name, ""), (i + 1) * 10))
    app = api.App(db, data_dir, resource_dir(), verbose)
    api.APP["app"] = app
    return app


def backup_loop(app, keep):
    while not getattr(app, "stopped", False):
        try:
            last = app.db.get_setting("last_auto_backup", 0) or 0
            if time.time() - float(last) > 24 * 3600:
                app.db.backup(app.backup_dir, "auto")
                app.db.prune_backups(app.backup_dir, keep)
                app.db.set_setting("last_auto_backup", time.time())
        except Exception as e:  # noqa
            sys.stderr.write("Ошибка резервного копирования: %s\n" % e)
        for _ in range(360):
            if getattr(app, "stopped", False):
                return
            time.sleep(10)


def urls(port, host):
    if host in ("127.0.0.1", "localhost"):
        return ["http://127.0.0.1:%d" % port]
    from . import v15
    return v15.addresses(port) + ["http://127.0.0.1:%d" % port]


def start_server(app, cfg):
    """Запустить HTTP/HTTPS-сервер. Бросает OSError, если порт занят."""
    srv = ServerThread(app, cfg["host"], int(cfg["port"]))
    app.port = int(cfg["port"])
    from . import v15
    v15.apply_tls()
    srv.start()
    return srv


def run_gui(app, srv, cfg, data_dir):
    """app/srv равны None, если сервер уже работает как служба Windows — окно тогда только управляет службой."""
    import tkinter as tk
    from tkinter import messagebox

    st = {"app": app, "srv": srv}
    root = tk.Tk()
    root.title("%s %s — сервер" % (APP_NAME, APP_VERSION))
    root.geometry("600x560")
    root.minsize(520, 520)
    try:
        ico = os.path.join(resource_dir(), "icon.ico")
        if os.path.exists(ico):
            root.iconbitmap(ico)
    except Exception:
        pass
    bg = "#f4f6fa"
    root.configure(bg=bg)
    tk.Label(root, text=APP_NAME, font=("Segoe UI", 20, "bold"), fg="#1f3a68", bg=bg).pack(pady=(14, 0))
    tk.Label(root, text="Учёт результатов учебной деятельности учащихся", font=("Segoe UI", 10), bg=bg).pack()
    status = tk.Label(root, text="Сервер работает", font=("Segoe UI", 10, "bold"), fg="#1b7f3b", bg=bg)
    status.pack(pady=(10, 4))
    tk.Label(root, text="Адреса для входа (по имени компьютера — первый в списке):", bg=bg, font=("Segoe UI", 9)).pack()
    box = tk.Text(root, height=6, width=64, font=("Consolas", 10), relief="flat", bd=6)
    box.pack(padx=16, pady=6, fill="x")

    def fill_urls():
        box.configure(state="normal")
        box.delete("1.0", "end")
        box.insert("1.0", "\n".join(urls(cfg["port"], cfg["host"])))
        box.configure(state="disabled")

    def primary_url():
        return urls(cfg["port"], cfg["host"])[0]

    def copy_url():
        root.clipboard_clear()
        root.clipboard_append(primary_url())
        messagebox.showinfo(APP_NAME, "Адрес скопирован:\n" + primary_url())

    fill_urls()
    btns = tk.Frame(root, bg=bg)
    btns.pack(pady=6)

    def open_browser():
        webbrowser.open("http://127.0.0.1:%d" % cfg["port"])

    def backup_now():
        if not st["app"]:
            messagebox.showinfo(APP_NAME, "Сервер работает как служба. Резервную копию можно создать в программе: Администрирование → Резервные копии.")
            return
        try:
            p = st["app"].db.backup(st["app"].backup_dir, "manual")
            messagebox.showinfo(APP_NAME, "Резервная копия создана:\n" + p)
        except Exception as e:
            messagebox.showerror(APP_NAME, str(e))

    def open_folder():
        try:
            os.startfile(data_dir)  # Windows
        except AttributeError:
            webbrowser.open("file://" + data_dir)

    def service_on():
        return winservice.status() != "none"

    def quit_app():
        if service_on():
            messagebox.showinfo(APP_NAME, "Включён запуск как служба Windows, поэтому закрыть программу крестиком нельзя.\n\n"
                                "Остановить сервер можно только в «Диспетчере задач» (от имени администратора) "
                                "или в services.msc (служба «SchoolGrade»).")
            return
        if messagebox.askyesno(APP_NAME, "Остановить сервер? Пользователи в сети потеряют доступ."):
            if st["srv"]:
                st["srv"].stop()
            if st["app"]:
                st["app"].stopped = True
                st["app"].db.close()
            root.destroy()

    for text, cmd in [("Открыть программу", open_browser), ("Копировать адрес", copy_url), ("Резервная копия", backup_now),
                      ("Папка с данными", open_folder), ("Остановить", quit_app)]:
        tk.Button(btns, text=text, command=cmd, width=15, font=("Segoe UI", 9)).pack(side="left", padx=2)

    # --- служба Windows
    svc = tk.LabelFrame(root, text=" Служба Windows ", bg=bg, font=("Segoe UI", 9, "bold"), padx=10, pady=6)
    svc.pack(fill="x", padx=16, pady=8)
    var = tk.BooleanVar(value=service_on())
    chk = tk.Checkbutton(svc, text="Запускать как службу Windows (автозагрузка)", variable=var, bg=bg, font=("Segoe UI", 10))
    chk.pack(anchor="w")
    svc_lbl = tk.Label(svc, text="", bg=bg, fg="#555", font=("Segoe UI", 8), justify="left", wraplength=520)
    svc_lbl.pack(anchor="w")
    tk.Label(svc, text="Сервер запускается при включении компьютера, до входа пользователя в систему. При включённой службе окно "
                       "нельзя закрыть крестиком; остановка — «Диспетчер задач» (администратор) или services.msc.",
             bg=bg, fg="#555", font=("Segoe UI", 8), justify="left", wraplength=540).pack(anchor="w", pady=(2, 4))

    def refresh_svc():
        s = winservice.status()
        names = {"none": "не установлена", "running": "установлена, работает", "stopped": "установлена, остановлена", "pending": "запускается…"}
        svc_lbl.config(text="Состояние службы: " + names.get(s, s) + ("" if winservice.IS_WIN else " (только в Windows)"))
        status.config(text="Сервер работает как служба Windows" if s in ("running", "pending") else
                      ("Служба установлена, но остановлена" if s == "stopped" else "Сервер работает"),
                      fg="#1b7f3b" if s != "stopped" else "#b26a00")

    def restart_local():
        try:
            if st["app"] is None:
                st["app"] = init_app(data_dir)
                threading.Thread(target=backup_loop, args=(st["app"], int(cfg.get("backup_keep", 30))), daemon=True).start()
            st["srv"] = start_server(st["app"], cfg)
        except OSError as e:
            messagebox.showerror(APP_NAME, "Не удалось запустить сервер: %s" % e)

    def stop_local():
        if st["srv"]:
            st["srv"].stop()
            st["srv"] = None
        if st["app"]:
            st["app"].stopped = True
            st["app"].db.close()
            st["app"] = None

    def apply_service():
        want, now = var.get(), service_on()
        if not winservice.IS_WIN:
            messagebox.showinfo(APP_NAME, "Службы Windows доступны только в Windows.")
            var.set(False)
            return
        if want == now:
            messagebox.showinfo(APP_NAME, "Изменений нет.")
            return
        base = '--data "%s" --port %d' % (data_dir, int(cfg["port"]))
        if want:
            stop_local()                       # освободить порт для службы
            code = winservice.run_elevated("--service-install " + base)
            for _ in range(30):
                if winservice.status() == "running":
                    break
                time.sleep(0.5)
            if code == 0 and winservice.status() == "running":
                messagebox.showinfo(APP_NAME, "Служба установлена и запущена. Теперь сервер стартует вместе с компьютером.")
            else:
                var.set(False)
                messagebox.showerror(APP_NAME, "Службу установить не удалось (нужны права администратора — подтвердите запрос Windows).")
                restart_local()
        else:
            code = winservice.run_elevated("--service-remove")
            if winservice.status() == "none":
                messagebox.showinfo(APP_NAME, "Служба удалена. Сервер продолжает работать из этого окна.")
                restart_local()
            else:
                var.set(True)
                messagebox.showerror(APP_NAME, "Службу удалить не удалось (нужны права администратора).")
        refresh_svc()

    tk.Button(svc, text="Применить", command=apply_service, width=14, font=("Segoe UI", 9, "bold")).pack(anchor="w")
    refresh_svc()
    tk.Label(root, text="Данные: " + data_dir, bg=bg, fg="#555", font=("Segoe UI", 8), wraplength=560).pack(pady=(4, 0))
    tk.Label(root, text="%s\nРазработчик: %s · %s" % (APP_ORG, APP_AUTHOR, APP_CITY_YEAR), bg=bg, fg="#666",
             font=("Segoe UI", 8)).pack(side="bottom", pady=8)
    root.protocol("WM_DELETE_WINDOW", quit_app)
    def tick():
        refresh_svc()
        root.after(5000, tick)
    root.after(5000, tick)
    if cfg.get("open_browser", True):
        root.after(800, open_browser)
    root.mainloop()


def _safe_streams():
    """Вывод в консоль не должен ронять сервер: на Windows с некириллической
    кодовой страницей (или при перенаправлении в файл) print() с русским
    текстом вызывал UnicodeEncodeError."""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(errors="replace")
        except Exception:
            pass


def _service_cmd(args, data_dir, cfg):
    """Команды управления службой (выполняются с правами администратора)."""
    if args.service_install:
        ok, msg = winservice.install(data_dir, int(cfg["port"]))
    elif args.service_remove:
        ok, msg = winservice.remove()
    elif args.service_start:
        ok, msg = winservice.start()
    else:
        ok, msg = winservice.stop()
    print(msg)
    return 0 if ok else 1


def run_as_service(data_dir, cfg):
    log = open(os.path.join(data_dir, "service.log"), "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = log
    holder = {}

    def start():
        app = init_app(data_dir)
        holder["app"] = app
        holder["srv"] = start_server(app, cfg)
        threading.Thread(target=backup_loop, args=(app, int(cfg.get("backup_keep", 30))), daemon=True).start()
        print("%s: служба запущена, порт %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), cfg["port"]))

    def stop():
        holder["app"].stopped = True
        holder["srv"].stop()
        holder["app"].db.close()
        print("%s: служба остановлена" % time.strftime("%Y-%m-%d %H:%M:%S"))

    winservice.run_service(start, stop)
    return 0


def main(argv=None):
    _safe_streams()
    ap = argparse.ArgumentParser(prog=APP_NAME)
    ap.add_argument("--port", type=int)
    ap.add_argument("--data")
    ap.add_argument("--local", action="store_true", help="доступ только с этого компьютера")
    ap.add_argument("--nogui", action="store_true")
    ap.add_argument("--nobrowser", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--service", action="store_true", help="запуск диспетчером служб Windows")
    ap.add_argument("--service-install", action="store_true")
    ap.add_argument("--service-remove", action="store_true")
    ap.add_argument("--service-start", action="store_true")
    ap.add_argument("--service-stop", action="store_true")
    args = ap.parse_args(argv)

    data_dir = os.path.abspath(args.data) if args.data else default_data_dir()
    if not os.path.isdir(data_dir):
        os.makedirs(data_dir)
    cfg = load_config(data_dir)
    if args.port:
        cfg["port"] = args.port
    if args.local:
        cfg["host"] = "127.0.0.1"
    if args.nobrowser:
        cfg["open_browser"] = False
    if args.service_install or args.service_remove or args.service_start or args.service_stop:
        return _service_cmd(args, data_dir, cfg)
    if args.service:
        cfg["open_browser"] = False
        return run_as_service(data_dir, cfg)
    save_config(data_dir, cfg)

    if winservice.status() in ("running", "pending"):
        # сервер уже работает как служба — окно только показывает адреса и управляет службой
        if args.nogui:
            print("SchoolGrade уже работает как служба Windows.")
            return 0
        try:
            run_gui(None, None, cfg, data_dir)
        except Exception as e:  # noqa
            print("Окно недоступно (%s). SchoolGrade работает как служба Windows." % e)
        return 0

    app = init_app(data_dir, args.verbose)
    try:
        srv = start_server(app, cfg)
    except OSError as e:
        msg = ("Не удалось запустить сервер на порту %s (%s).\n"
               "Возможно, SchoolGrade уже запущен — откройте http://127.0.0.1:%s в браузере." % (cfg["port"], e, cfg["port"]))
        if not args.nogui:
            try:
                import tkinter as tk
                from tkinter import messagebox
                r = tk.Tk(); r.withdraw()
                messagebox.showerror(APP_NAME, msg)
                webbrowser.open("http://127.0.0.1:%s" % cfg["port"])
            except Exception:
                print(msg)
        else:
            print(msg)
        return 1
    threading.Thread(target=backup_loop, args=(app, int(cfg.get("backup_keep", 30))), daemon=True).start()

    if args.nogui:
        print("%s %s запущен. Данные: %s" % (APP_NAME, APP_VERSION, data_dir))
        for u in urls(cfg["port"], cfg["host"]):
            print("  " + u)
        print("Ctrl+C — остановить")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            srv.stop()
        return 0
    try:
        run_gui(app, srv, cfg, data_dir)
    except Exception as e:  # нет графической среды
        print("Окно недоступно (%s), работа в консольном режиме." % e)
        for u in urls(cfg["port"], cfg["host"]):
            print("  " + u)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            srv.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
