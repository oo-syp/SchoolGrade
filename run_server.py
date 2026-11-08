# -*- coding: utf-8 -*-
"""Запуск SchoolGrade из исходников (VS Code: F5) и точка входа для PyInstaller."""
import sys
from schoolgrade.main import main

if __name__ == "__main__":
    sys.exit(main())
