"""
run.py — Bootstrap local do haus outbound + content engine

Faz tudo numa tacada só:
  1. Confere se as dependências estão instaladas (instala se faltar)
  2. Confere se OPENAI_API_KEY está setada
  3. Inicializa o SQLite (prospects.db) se ainda não existir
  4. Sobe o painel Flask em http://localhost:8000

Uso:
    python run.py
    python run.py --skip-install      # pula o pip install
    python run.py --port 8080         # roda em outra porta
    python run.py --reset-db          # apaga prospects.db e recria
"""

import argparse
import importlib
import os
import subprocess
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).parent
REQUIRED = [
    ("flask", "flask"),
    ("flask_cors", "flask-cors"),
    ("openai", "openai"),
    ("apify_client", "apify-client"),
]


def log(msg: str) -> None:
    print(f"[run] {msg}")


def ensure_deps(skip_install: bool) -> None:
    faltando = []
    for modulo, pacote in REQUIRED:
        try:
            importlib.import_module(modulo)
        except ImportError:
            faltando.append(pacote)

    if not faltando:
        log("dependências OK")
        return

    if skip_install:
        log(f"faltando: {', '.join(faltando)} (--skip-install ativo, abortando)")
        sys.exit(1)

    log(f"instalando: {', '.join(faltando)}")
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "--quiet", *faltando]
    )


def ensure_env() -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        log("ERRO: OPENAI_API_KEY não está setada")
        log('   PowerShell:  $env:OPENAI_API_KEY = "sk-proj-..."')
        log('   bash:        export OPENAI_API_KEY="sk-proj-..."')
        sys.exit(1)
    log("OPENAI_API_KEY detectada")

    if not os.environ.get("APIFY_TOKEN"):
        log("aviso: APIFY_TOKEN não setada — scraper (02_scraper.py) vai falhar, painel roda normal")


def ensure_db(reset: bool) -> None:
    db_path = ROOT / "prospects.db"
    if reset and db_path.exists():
        log(f"removendo {db_path.name}")
        db_path.unlink()

    sys.path.insert(0, str(ROOT))
    from database import init_db

    init_db()
    log(f"banco pronto: {db_path.name}")


def run_painel(port: int, open_browser: bool) -> None:
    sys.path.insert(0, str(ROOT))
    from flask_cors import CORS
    from flask import Flask, jsonify, request, send_from_directory  # noqa: F401

    import importlib.util

    spec = importlib.util.spec_from_file_location("painel_mod", ROOT / "04_painel.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    app = mod.app
    url = f"http://localhost:{port}"
    print()
    print("=" * 60)
    print(" haus content engine rodando")
    print("=" * 60)
    print(f" painel:   {url}")
    print(f" db:       {ROOT / 'prospects.db'}")
    print(" Ctrl+C   pra parar")
    print("=" * 60)
    print()

    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    app.run(host="0.0.0.0", port=port, debug=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap local do haus outbound")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--skip-install", action="store_true")
    parser.add_argument("--reset-db", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    ensure_deps(args.skip_install)
    ensure_env()
    ensure_db(args.reset_db)
    run_painel(args.port, open_browser=not args.no_browser)


if __name__ == "__main__":
    main()
