#!/usr/bin/env python3
"""
make_links.py — cria um link público por item do catálogo e grava em items[].protonUrl.

O Proton não tem deep link: um link abre uma pasta e só. Para o card abrir a pasta
exata do mod, cada pasta precisa do seu próprio link público. Este script cria
esses links pelo CLI oficial e escreve as URLs no catalog.json.

    proton-drive auth login                          # uma vez, abre o navegador
    python tools/make_links.py --root /my-files/A3 --dry-run
    python tools/make_links.py --root /my-files/A3

Idempotente: item que já tem protonUrl é pulado. Subiu um mod novo? Roda de novo
e só ele ganha link.

    --categories   cria também um link por categoria (rede de segurança: um mod
                   novo sem link ainda cai na pasta da nação certa)
    --refresh      recria o link dos itens que já têm (a URL antiga deixa de valer)
    --limit N      faz só N por vez (bom pra testar sem criar 55 de uma vez)

Depois de rodar, commite o catalog.json — o sync diário preserva os links pelo
protonPath, mas ele parte do que está no repositório.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "data" / "catalog.json"
URL_RE = re.compile(r"https://drive\.proton\.me/urls/[A-Za-z0-9#_\-]+")


# ----------------------------------------------------------------- helpers --

def cli_parts(value: str) -> list[str]:
    """Aceita 'proton-drive' ou 'C:\\caminho\\proton-drive.exe' ou 'python stub.py'."""
    parts = shlex.split(value, posix=(os.name != "nt"))
    return [p.strip('"') for p in parts if p.strip('"')]


def run(cmd: list[str], timeout: int = 90) -> tuple[int, str, str]:
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def find_url(blob: str) -> str:
    """Procura a URL em qualquer lugar do JSON, ou no texto puro."""
    match = URL_RE.search(blob or "")
    if match:
        return match.group(0)
    try:
        payload = json.loads(blob)
    except (json.JSONDecodeError, TypeError):
        return ""

    stack = [payload]
    while stack:
        node = stack.pop()
        if isinstance(node, str):
            found = URL_RE.search(node)
            if found:
                return found.group(0)
        elif isinstance(node, dict):
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)

    return ""


def save(path: Path, manifest: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(path)


def create_link(cli: list[str], path: str, role: str, timeout: int) -> tuple[str, str]:
    """Devolve (url, erro)."""
    cmd = [*cli, "sharing", "set-url", "--role", role, "--json", path]
    code, out, err = run(cmd, timeout=timeout)
    url = find_url(out) or find_url(err)
    if code == 0 and url:
        return url, ""
    detail = (err or out or "").strip().splitlines()
    return "", f"exit {code}: {detail[-1][:200] if detail else 'sem saída'}"


# -------------------------------------------------------------------- main --

def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Cria link público por item e grava no catálogo.")
    parser.add_argument("--root", default="/my-files/A3",
                        help="pasta raiz no Drive que contém as categorias (default: /my-files/A3)")
    parser.add_argument("--cli", default="proton-drive", help="executável do Proton Drive CLI")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="catalog.json de saída")
    parser.add_argument("--role", default="viewer", choices=["viewer", "editor"],
                        help="permissão do link (default: viewer)")
    parser.add_argument("--delay", type=float, default=1.2,
                        help="segundos entre chamadas (default: 1.2, evita throttle)")
    parser.add_argument("--limit", type=int, default=0, help="máximo de links por execução")
    parser.add_argument("--timeout", type=int, default=90, help="timeout por chamada, em segundos")
    parser.add_argument("--categories", action="store_true",
                        help="cria também um link por categoria (fallback dos cards)")
    parser.add_argument("--refresh", action="store_true",
                        help="recria links que já existem")
    parser.add_argument("--dry-run", action="store_true", help="mostra o plano, não cria nada")
    args = parser.parse_args()

    if not args.out.exists():
        print(f"[x] {args.out} não existe. Rode o sync primeiro.")
        return 1

    with args.out.open(encoding="utf-8-sig") as fh:
        manifest = json.load(fh)

    items = manifest.get("items", [])
    categories = manifest.get("categories", [])
    by_id = {c["id"]: c for c in categories}

    cli = cli_parts(args.cli)
    if not cli:
        print("[x] --cli vazio.")
        return 1

    if not args.dry_run:
        if shutil.which(cli[0]) is None and not Path(cli[0]).exists():
            print(f"[x] não achei o CLI: {cli[0]}\n"
                  f"    Baixe em https://proton.me/download/drive/cli/index.html\n"
                  f"    ou passe o caminho completo em --cli.")
            return 1
        code, out, err = run([*cli, "filesystem", "list", args.root, "--json"], timeout=args.timeout)
        if code != 0:
            detail = (err or out or "").strip().splitlines()
            print(f"[x] não consegui listar {args.root}\n"
                  f"    {detail[-1][:300] if detail else 'sem saída'}\n"
                  f"    Confira o caminho: proton-drive filesystem list /my-files")
            return 1

    todo_items = [i for i in items if args.refresh or not i.get("protonUrl")]
    todo_cats = [c for c in categories if args.refresh or not c.get("protonUrl")] if args.categories else []
    total = len(todo_items) + len(todo_cats)

    print(f"[i] raiz: {args.root}")
    print(f"[i] {len(items)} item(ns), {len(todo_items)} sem link"
          + (f", {len(todo_cats)} categoria(s) sem link" if args.categories else ""))
    if total == 0:
        print("[ok] tudo já tem link. Nada a fazer.")
        return 0

    if args.dry_run:
        for item in todo_items[:args.limit or None]:
            print(f"    would link  {item['protonPath']}")
        for cat in todo_cats[:args.limit or None]:
            print(f"    would link  [categoria] {cat['name']}")
        print(f"\n[i] --dry-run: {total} link(s) seriam criados, nada foi chamado.")
        return 0

    done = failed = 0
    started = time.time()
    limit = args.limit or total

    def attempt(path: str, label: str) -> str:
        nonlocal done, failed
        url, error = create_link(cli, path, args.role, args.timeout)
        if url:
            done += 1
            print(f"[+] {label}  ->  {url}")
            return url
        failed += 1
        print(f"[x] {label}  ({error})")
        return ""

    for item in todo_items:
        if done + failed >= limit:
            break
        path = f"{args.root.rstrip('/')}/{item['protonPath']}"
        url = attempt(path, item["protonPath"])
        if url:
            item["protonUrl"] = url
        time.sleep(args.delay)

    for cat in todo_cats:
        if done + failed >= limit:
            break
        # o caminho real da categoria é o primeiro segmento dos itens dela
        sample = next((i["protonPath"] for i in items if i["category"] == cat["id"]), None)
        if not sample:
            continue
        folder = sample.split("/")[0]
        url = attempt(f"{args.root.rstrip('/')}/{folder}", f"[categoria] {cat['name']}")
        if url:
            cat["protonUrl"] = url
        time.sleep(args.delay)

    # grava o que deu certo
    save(args.out, manifest)

    elapsed = time.time() - started
    print(f"\n[ok] {done} link(s) criado(s), {failed} falha(s), {elapsed:.0f}s")
    print(f"     gravado em {args.out}")
    if failed:
        print("     Rode de novo: o script pula o que já tem link e tenta só o que faltou.")
    print("\n[!] Commite o catalog.json para o site usar os links:\n"
          "      git add data/catalog.json && git commit -m \"catalog: links por pasta\" && git push")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
