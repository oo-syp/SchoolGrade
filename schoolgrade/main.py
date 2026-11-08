# -*- coding: utf-8 -*-
"""Точка входа SchoolGrade: запуск сервера и окна управления.

  SchoolGrade.exe                 — окно управления сервером (по умолчанию)
  SchoolGrade.exe --nogui         — без окна (служба / консоль)
  SchoolGrade.exe --port 8090     — другой порт
  SchoolGrade.exe --data D:\\SG    — папка с данными
  SchoolGrade.exe --local         — доступ только с этого компьютера
"""
import argparse
import json
import os
import sys
import threading
import time
import webbrowser

from . import APP_NAME, APP_VERSION, APP_ORG, APP_AUTHOR, APP_CITY_YEAR
from . import api, auth
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
    if db.val("SELECT COUNT(*) FROM subjects") == 0:
        for i, name in enumerate(DEFAULT_SUBJECTS):
            db.ex("INSERT INTO subjects(name,short,sort) VALUES(?,?,?)", (name, SUBJECT_SHORT.get(name, ""), (i + 1) * 10))
    app = api.App(db, data_dir, resource_dir(), verbose)
    api.APP["app"] = app
    return app


def backup_loop(app, keep):
    while True:
        try:
            last = app.db.get_setting("last_auto_backup", 0) or 0
            if time.time() - float(last) > 24 * 3600:
                app.db.backup(app.backup_dir, "auto")
                app.db.prune_backups(app.backup_dir, keep)
                app.db.set_setting("last_auto_backup", time.time())
        except Exception as e:  # noqa
            sys.stderr.write("Ошибка резервного копирования: %s\n" % e)
        time.sleep(3600)


def urls(port, host):
    if host in ("127.0.0.1", "localhost"):
        return ["http://127.0.0.1:%d" % port]
    return ["http://127.0.0.1:%d" % port] + ["http://%s:%d" % (ip, port) for ip in local_addresses()]


def run_gui(app, srv, cfg):
    import tkinter as tk
    from tkinter import messagebox, filedialog

    root = tk.Tk()
    root.title("%s %s — сервер" % (APP_NAME, APP_VERSION))
    root.geometry("560x420")
    root.minsize(480, 380)
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
    tk.Label(root, text="Адреса для входа (откройте в браузере на любом компьютере сети):", bg=bg,
             font=("Segoe UI", 9)).pack()
    box = tk.Text(root, height=5, width=60, font=("Consolas", 10), relief="flat", bd=6)
    box.insert("1.0", "\n".join(urls(cfg["port"], cfg["host"])))
    box.configure(state="disabled")
    box.pack(padx=16, pady=6, fill="x")
    btns = tk.Frame(root, bg=bg)
    btns.pack(pady=6)

    def open_browser():
        webbrowser.open("http://127.0.0.1:%d" % cfg["port"])

    def backup_now():
        try:
            p = app.db.backup(app.backup_dir, "manual")
            messagebox.showinfo(APP_NAME, "Резервная копия создана:\n" + p)
        except Exception as e:
            messagebox.showerror(APP_NAME, str(e))

    def open_folder():
        try:
            os.startfile(app.data_dir)  # Windows
        except AttributeError:
            webbrowser.open("file://" + app.data_dir)

    def quit_app():
        if messagebox.askyesno(APP_NAME, "Остановить сервер? Пользователи в сети потеряют доступ."):
            srv.stop()
            app.db.close()
            root.destroy()

    for text, cmd in [("Открыть программу", open_browser), ("Резервная копия", backup_now),
                      ("Папка с данными", open_folder), ("Остановить", quit_app)]:
        tk.Button(btns, text=text, command=cmd, width=17, font=("Segoe UI", 9)).pack(side="left", padx=3)
    tk.Label(root, text="Данные: " + app.data_dir, bg=bg, fg="#555", font=("Segoe UI", 8), wraplength=520).pack(pady=(8, 0))
    tk.Label(root, text="Не закрывайте это окно, пока с программой работают пользователи.", bg=bg, fg="#a33",
             font=("Segoe UI", 8)).pack()
    tk.Label(root, text="%s\nРазработчик: %s · %s" % (APP_ORG, APP_AUTHOR, APP_CITY_YEAR), bg=bg, fg="#666",
             font=("Segoe UI", 8)).pack(side="bottom", pady=8)
    root.protocol("WM_DELETE_WINDOW", quit_app)
    if cfg.get("open_browser", True):
        root.after(800, open_browser)
    root.mainloop()


def main(argv=None):
    ap = argparse.ArgumentParser(prog=APP_NAME)
    ap.add_argument("--port", type=int)
    ap.add_argument("--data")
    ap.add_argument("--local", action="store_true", help="доступ только с этого компьютера")
    ap.add_argument("--nogui", action="store_true")
    ap.add_argument("--nobrowser", action="store_true")
    ap.add_argument("--verbose", action="store_true")
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
    save_config(data_dir, cfg)

    app = init_app(data_dir, args.verbose)
    try:
        srv = ServerThread(app, cfg["host"], int(cfg["port"]))
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
    srv.start()
    threading.Thread(target=backup_loop, args=(app, int(cfg.get("backup_keep", 30))), daemon=True).start()

    if args.nogui:
        print("%s %s запущен. Данные: %s" % (APP_NAME, APP_VERSION, data_dir))
        for u in urls(cfg["port"], cfg["host"]):
            print("  " + u)
        print("Ctrl+C — остановить")
        if cfg.get("open_browser") and not args.nobrowser:
            pass
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            srv.stop()
        return 0
    try:
        run_gui(app, srv, cfg)
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
