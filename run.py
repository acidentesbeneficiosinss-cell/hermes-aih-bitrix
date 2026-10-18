#!/usr/bin/env python3
"""Atalho: `python run.py <comando>` roda direto desta pasta, sem instalar o pacote (usa ./src).

Exemplos:  python run.py doctor   |   python run.py sample   |   python run.py serve --with-poller
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from aih_bitrix.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
