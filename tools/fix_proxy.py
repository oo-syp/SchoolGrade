# -*- coding: utf-8 -*-
"""Обход ошибки pip «ValueError: check_hostname requires server_hostname».

Старый pip (в комплекте с Python 3.8) не умеет работать с прокси, адрес которого
задан со схемой https:// (так Windows часто отдаёт системный прокси, VPN или
антивирус). Скрипт берёт системные настройки прокси и печатает команды set
с адресом через http:// — build.bat выполняет их перед вызовом pip."""
import sys

try:
    from urllib.request import getproxies
except ImportError:
    sys.exit(0)

proxies = getproxies()
lines = []
for scheme in ("http", "https"):
    url = proxies.get(scheme)
    if not url:
        continue
    if url.startswith("https://"):
        url = "http://" + url[len("https://"):]
    elif "://" not in url:
        url = "http://" + url
    lines.append("set {}_PROXY={}".format(scheme.upper(), url))
if lines:
    print("@echo off")
    print("echo System proxy detected: {}".format(proxies.get("https") or proxies.get("http")))
    for l in lines:
        print(l)
