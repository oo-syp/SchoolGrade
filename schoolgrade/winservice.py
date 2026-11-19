# -*- coding: utf-8 -*-
"""Запуск SchoolGrade как службы Windows — без pywin32, только ctypes и штатные sc.exe.

  SchoolGrade.exe --service            — точка входа, которую вызывает диспетчер служб (вручную не запускать)
  SchoolGrade.exe --service-install    — зарегистрировать службу «SchoolGrade» (автозапуск) и запустить (нужны права администратора)
  SchoolGrade.exe --service-remove     — остановить и удалить службу
  SchoolGrade.exe --service-start / --service-stop

Из окна программы эти команды выполняются через запрос повышения прав (UAC)."""
import ctypes
import os
import subprocess
import sys
import threading
import time

SERVICE_NAME = "SchoolGrade"
DISPLAY_NAME = "SchoolGrade — сервер электронного журнала"
DESCRIPTION = "Сервер SchoolGrade (учёт успеваемости, расписание, экраны-киоски). Запускается до входа пользователя в систему."

IS_WIN = sys.platform.startswith("win")
CREATE_NO_WINDOW = 0x08000000


def _sc(*args):
    if not IS_WIN:
        return 1, ""
    try:
        r = subprocess.run(["sc.exe"] + list(args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=CREATE_NO_WINDOW)
        return r.returncode, r.stdout.decode("cp866", "replace")
    except OSError as e:
        return 1, str(e)


def status():
    """'none' — не установлена, 'running', 'stopped', 'pending'."""
    if not IS_WIN:
        return "none"
    code, out = _sc("query", SERVICE_NAME)
    if code == 1060 or "1060" in out:
        return "none"
    if "RUNNING" in out:
        return "running"
    if "STOPPED" in out:
        return "stopped"
    if "PENDING" in out:
        return "pending"
    return "none" if code else "stopped"


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _command(data_dir, port):
    if getattr(sys, "frozen", False):
        base = '"%s"' % sys.executable
    else:
        base = '"%s" -m schoolgrade.main' % sys.executable
    return '%s --service --data "%s" --port %d' % (base, data_dir, port)


def install(data_dir, port):
    """Выполняется с правами администратора. Возвращает (ok, сообщение)."""
    if not IS_WIN:
        return False, "Службы Windows доступны только в Windows"
    if status() != "none":
        _sc("stop", SERVICE_NAME)
        time.sleep(1)
        _sc("delete", SERVICE_NAME)
        time.sleep(1)
    code, out = _sc("create", SERVICE_NAME, "binPath=", _command(data_dir, port), "start=", "auto", "DisplayName=", DISPLAY_NAME)
    if code:
        return False, "Не удалось создать службу: " + out.strip()
    _sc("description", SERVICE_NAME, DESCRIPTION)
    _sc("failure", SERVICE_NAME, "reset=", "86400", "actions=", "restart/5000/restart/5000/restart/30000")
    code, out = _sc("start", SERVICE_NAME)
    if code and "1056" not in out:
        return False, "Служба создана, но не запустилась: " + out.strip()
    return True, "Служба установлена и запущена"


def remove():
    if status() == "none":
        return True, "Служба не установлена"
    _sc("stop", SERVICE_NAME)
    for _ in range(20):
        if status() in ("stopped", "none"):
            break
        time.sleep(0.5)
    code, out = _sc("delete", SERVICE_NAME)
    return (code == 0), ("Служба удалена" if code == 0 else out.strip())


def start():
    code, out = _sc("start", SERVICE_NAME)
    return (code == 0 or "1056" in out), out.strip()


def stop():
    code, out = _sc("stop", SERVICE_NAME)
    return (code == 0 or "1062" in out), out.strip()


def run_elevated(args):
    """Запустить этот же exe с правами администратора (окно UAC) и дождаться завершения. Возвращает код выхода или None."""
    if not IS_WIN:
        return None
    from ctypes import wintypes

    class SEI(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", wintypes.ULONG), ("hwnd", wintypes.HWND), ("lpVerb", wintypes.LPCWSTR),
                    ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR), ("lpDirectory", wintypes.LPCWSTR),
                    ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE), ("lpIDList", ctypes.c_void_p),
                    ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY), ("dwHotKey", wintypes.DWORD),
                    ("hIcon", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]
    if getattr(sys, "frozen", False):
        exe, params = sys.executable, args
    else:
        exe, params = sys.executable, "-m schoolgrade.main " + args
    sei = SEI()
    sei.cbSize = ctypes.sizeof(SEI)
    sei.fMask = 0x00000040          # SEE_MASK_NOCLOSEPROCESS
    sei.lpVerb, sei.lpFile, sei.lpParameters, sei.nShow = "runas", exe, params, 0
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei)):
        return None                 # пользователь отказал в UAC
    ctypes.windll.kernel32.WaitForSingleObject(sei.hProcess, 60000)
    code = wintypes.DWORD()
    ctypes.windll.kernel32.GetExitCodeProcess(sei.hProcess, ctypes.byref(code))
    ctypes.windll.kernel32.CloseHandle(sei.hProcess)
    return code.value


# ------------------------------------------------------------------ сама служба (диспетчер служб Windows)
def run_service(start_server, stop_server):
    """Вызывается в процессе, запущенном диспетчером служб. start_server() запускает сервер, stop_server() останавливает."""
    if not IS_WIN:
        raise RuntimeError("Только для Windows")
    from ctypes import wintypes
    adv = ctypes.WinDLL("advapi32", use_last_error=True)

    SERVICE_WIN32_OWN_PROCESS = 0x10
    START_PENDING, RUNNING, STOP_PENDING, STOPPED = 2, 4, 3, 1
    ACCEPT_STOP, ACCEPT_SHUTDOWN = 0x1, 0x4
    CTRL_STOP, CTRL_SHUTDOWN = 1, 5

    class STATUS(ctypes.Structure):
        _fields_ = [("dwServiceType", wintypes.DWORD), ("dwCurrentState", wintypes.DWORD), ("dwControlsAccepted", wintypes.DWORD),
                    ("dwWin32ExitCode", wintypes.DWORD), ("dwServiceSpecificExitCode", wintypes.DWORD),
                    ("dwCheckPoint", wintypes.DWORD), ("dwWaitHint", wintypes.DWORD)]

    HANDLER = ctypes.WINFUNCTYPE(None, wintypes.DWORD)
    MAIN = ctypes.WINFUNCTYPE(None, wintypes.DWORD, ctypes.POINTER(wintypes.LPWSTR))

    class ENTRY(ctypes.Structure):
        _fields_ = [("lpServiceName", wintypes.LPWSTR), ("lpServiceProc", MAIN)]

    adv.RegisterServiceCtrlHandlerW.restype = wintypes.HANDLE
    adv.RegisterServiceCtrlHandlerW.argtypes = [wintypes.LPCWSTR, HANDLER]
    adv.SetServiceStatus.argtypes = [wintypes.HANDLE, ctypes.POINTER(STATUS)]
    stop_evt = threading.Event()
    state = {"h": None, "checkpoint": 0}

    def report(cur, accepted=0, hint=0, code=0):
        st = STATUS(SERVICE_WIN32_OWN_PROCESS, cur, accepted, code, 0, state["checkpoint"], hint)
        state["checkpoint"] = state["checkpoint"] + 1 if cur in (START_PENDING, STOP_PENDING) else 0
        adv.SetServiceStatus(state["h"], ctypes.byref(st))

    def handler(ctrl):
        if ctrl in (CTRL_STOP, CTRL_SHUTDOWN):
            report(STOP_PENDING, 0, 15000)
            stop_evt.set()

    h_cb = HANDLER(handler)

    def svc_main(argc, argv):
        state["h"] = adv.RegisterServiceCtrlHandlerW(SERVICE_NAME, h_cb)
        report(START_PENDING, 0, 20000)
        try:
            start_server()
        except Exception as e:  # noqa
            report(STOPPED, 0, 0, 1)
            sys.stderr.write("Ошибка запуска службы: %s\n" % e)
            return
        report(RUNNING, ACCEPT_STOP | ACCEPT_SHUTDOWN)
        stop_evt.wait()
        try:
            stop_server()
        finally:
            report(STOPPED)

    m_cb = MAIN(svc_main)
    table = (ENTRY * 2)(ENTRY(SERVICE_NAME, m_cb), ENTRY(None, None))
    adv.StartServiceCtrlDispatcherW.argtypes = [ctypes.POINTER(ENTRY)]
    if not adv.StartServiceCtrlDispatcherW(table):
        raise RuntimeError("Не удалось подключиться к диспетчеру служб (запуск возможен только как служба): код %d" % ctypes.get_last_error())
