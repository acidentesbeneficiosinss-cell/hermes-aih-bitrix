"""`hermes-install`: gera a skill do agente Hermes e o script do cron sem agente, com os caminhos absolutos desta instalação."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from aih_bitrix.cli import main
from aih_bitrix.hermes_install import (
    CRON_SCRIPT_NAME,
    SKILL_NAME,
    HermesInstallError,
    default_app_dir,
    default_hermes_home,
    install,
    render_cron_wrapper,
    render_skill,
)

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def hermes_home(tmp_path):
    home = tmp_path / "hermes"
    (home / "skills" / "productivity").mkdir(parents=True)
    return home


@pytest.fixture
def app_dir(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    (app / "run.py").write_text("# stub", encoding="utf-8")
    return app


def skill_path(home: Path) -> Path:
    return home / "skills" / "productivity" / SKILL_NAME / "SKILL.md"


def script_path(home: Path) -> Path:
    return home / "scripts" / CRON_SCRIPT_NAME


# ---------------------------------------------------------------- onde fica o Hermes

def test_hermes_home_comes_from_the_environment_then_from_the_platform_default():
    assert default_hermes_home({"HERMES_HOME": "/dados/hermes"}, platform="linux", home=Path("/h")) == Path("/dados/hermes")
    assert default_hermes_home({"LOCALAPPDATA": "/x/Local"}, platform="win32", home=Path("/h")) == Path("/x/Local") / "hermes"
    assert default_hermes_home({}, platform="linux", home=Path("/home/u")) == Path("/home/u") / ".hermes"


def test_default_app_dir_is_this_checkout():
    assert default_app_dir() == ROOT


# ---------------------------------------------------------------- arquivos gerados

def test_install_writes_the_skill_and_the_cron_script_with_this_installations_paths(hermes_home, app_dir):
    written = install(hermes_home, app_dir, "C:/venv/Scripts/python.exe")
    assert set(written) == {skill_path(hermes_home), script_path(hermes_home)}
    skill = skill_path(hermes_home).read_text(encoding="utf-8")
    script = script_path(hermes_home).read_text(encoding="utf-8")
    assert str(app_dir) in skill and "C:/venv/Scripts/python.exe" in skill
    assert "@@" not in skill and "@@" not in script
    compile(script, "aih_poll.py", "exec")  # sintaxe válida


def test_the_skill_has_hermes_frontmatter_and_the_rules_an_agent_must_follow(hermes_home, app_dir):
    text = render_skill(app_dir, "py")
    assert text.startswith("---\nname: aih-bitrix\ndescription: ")
    head = text.split("---")[1]
    assert head.count("\ndescription:") == 1 and all(len(line) < 400 for line in head.splitlines())
    for must_say in ("extract --show", "selfcheck", "--dry-run", "doctor", "nunca apaga", "poll --once", "poll --init-only",
                     "LGPD", "needs_review", "AMOSTRA-FICTICIA"):
        assert must_say in text, must_say


def test_install_refuses_to_overwrite_without_force_and_leaves_no_half_install(hermes_home, app_dir):
    (hermes_home / "scripts").mkdir()
    script_path(hermes_home).write_text("antigo", encoding="utf-8")
    with pytest.raises(HermesInstallError, match="--force"):
        install(hermes_home, app_dir, "py")
    assert not skill_path(hermes_home).exists() and script_path(hermes_home).read_text(encoding="utf-8") == "antigo"
    install(hermes_home, app_dir, "py", force=True)
    assert skill_path(hermes_home).exists() and script_path(hermes_home).read_text(encoding="utf-8") != "antigo"


def test_install_needs_an_existing_hermes_home_and_an_app_folder_with_run_py(tmp_path, app_dir):
    with pytest.raises(HermesInstallError, match="Hermes"):
        install(tmp_path / "nao-existe", app_dir, "py")
    with pytest.raises(HermesInstallError, match="run.py"):
        install(tmp_path, tmp_path / "vazia", "py")


# ---------------------------------------------------------------- o script que o cron do Hermes executa

STUB = """\
import os, sys
mode = open("mode.txt", encoding="utf-8").read().strip()
print("ARGS=" + " ".join(sys.argv[1:]) + ";CWD=" + os.path.basename(os.getcwd()), file=open("seen.txt", "w", encoding="utf-8"))
if mode == "news":
    print("[ok] arquivo | contato: created #1 | card: created #2")
elif mode == "fail":
    print("[error] arquivo | contato: - | card: -")
    print("Traceback: Marcos 123.456.789-09", file=sys.stderr)
    sys.exit(1)
"""


def run_wrapper(tmp_path: Path, app: Path, mode: str, python: str | None = None) -> subprocess.CompletedProcess:
    (app / "run.py").write_text(STUB, encoding="utf-8")
    (app / "mode.txt").write_text(mode, encoding="utf-8")
    script = tmp_path / "aih_poll.py"
    script.write_text(render_cron_wrapper(app, python or sys.executable), encoding="utf-8")
    return subprocess.run([sys.executable, str(script)], capture_output=True, text=True, cwd=tmp_path, timeout=60)


def test_cron_script_is_silent_when_nothing_happened(tmp_path, app_dir):
    done = run_wrapper(tmp_path, app_dir, "silent")
    assert done.returncode == 0 and done.stdout == "" and done.stderr == ""
    assert (app_dir / "seen.txt").read_text(encoding="utf-8").strip() == "ARGS=poll --once --quiet;CWD=app"  # roda na pasta do app


def test_cron_script_passes_the_apps_output_through_when_there_is_news(tmp_path, app_dir):
    done = run_wrapper(tmp_path, app_dir, "news")
    assert done.returncode == 0 and done.stdout.strip() == "[ok] arquivo | contato: created #1 | card: created #2"


def test_cron_script_alerts_on_failure_without_forwarding_stderr(tmp_path, app_dir):
    done = run_wrapper(tmp_path, app_dir, "fail")
    assert done.returncode == 1 and "[error] arquivo" in done.stdout and "[falha]" in done.stdout
    assert "Marcos" not in done.stdout + done.stderr and "123.456" not in done.stdout + done.stderr  # stderr nunca é repassado


def test_cron_script_reports_an_unusable_python_without_a_traceback(tmp_path, app_dir):
    done = run_wrapper(tmp_path, app_dir, "news", python=str(tmp_path / "nao" / "existe" / "python"))
    assert done.returncode == 2 and "[falha]" in done.stdout and "Traceback" not in done.stdout + done.stderr


# ---------------------------------------------------------------- comando

def test_hermes_install_command_writes_the_files_and_prints_the_cron_command(hermes_home, app_dir, capsys):
    code = main(["hermes-install", "--hermes-home", str(hermes_home), "--app-dir", str(app_dir)], environ={})
    out = capsys.readouterr()
    assert code == 0 and skill_path(hermes_home).exists() and script_path(hermes_home).exists()
    assert "hermes cron create" in out.out and "--no-agent" in out.out and CRON_SCRIPT_NAME in out.out
    again = main(["hermes-install", "--hermes-home", str(hermes_home), "--app-dir", str(app_dir)], environ={})
    assert again == 2 and "--force" in capsys.readouterr().err
    assert main(["hermes-install", "--hermes-home", str(hermes_home), "--app-dir", str(app_dir), "--force"], environ={}) == 0


def test_hermes_install_command_fails_clearly_when_hermes_is_not_where_expected(tmp_path, capsys):
    code = main(["hermes-install", "--hermes-home", str(tmp_path / "nada")], environ={})
    assert code == 2 and "Hermes" in capsys.readouterr().err
