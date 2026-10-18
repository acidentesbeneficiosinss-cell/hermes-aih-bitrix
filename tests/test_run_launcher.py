"""`python run.py <comando>` roda direto da pasta do projeto, sem instalar o pacote (o jeito mais simples para o Hermes)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run_py(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    return subprocess.run([sys.executable, str(ROOT / "run.py"), *args], cwd=cwd, capture_output=True, env=env, timeout=180)


def test_run_py_works_from_any_folder(tmp_path):
    done = run_py("--version", cwd=tmp_path)
    assert done.returncode == 0 and done.stdout.decode("utf-8").startswith("aih-bitrix")


def test_run_py_sample_then_extract_roundtrip(tmp_path):
    made = run_py("sample", "--out", "amostra.pdf", cwd=tmp_path)
    assert made.returncode == 0 and (tmp_path / "amostra.pdf").read_bytes().startswith(b"%PDF")
    extracted = run_py("extract", "amostra.pdf", cwd=tmp_path)
    assert extracted.returncode == 0
    assert json.loads(extracted.stdout.decode("utf-8"))["rotulos_nao_reconhecidos"] == []


def test_run_py_doctor_offline_reports_a_healthy_install(tmp_path):
    done = run_py("doctor", "--offline", cwd=tmp_path)
    text = done.stdout.decode("utf-8")
    assert done.returncode == 0, text
    assert "[PASS] Extrator de PDF" in text and "Resultado:" in text
