#!/usr/bin/env python3
"""
sync_proton.py — gera data/catalog.json a partir do seu Proton Drive.

O Proton Drive é criptografado ponta a ponta e não tem API pública de listagem,
então não existe "o site puxar sozinho": alguém com a sua sessão precisa listar
as pastas. Este script faz essa parte. Três formas de alimentar ele:

  1. CLI oficial (recomendado)
       proton-drive auth login          # uma vez, abre o navegador
       python tools/sync_proton.py --root /my-files/TCH

  2. JSON salvo antes
       proton-drive filesystem list /my-files/TCH --json > dump.json
       python tools/sync_proton.py --input dump.json --root /my-files/TCH

  3. Pasta local sincronizada (app do Proton Drive no Windows)
       python tools/sync_proton.py --local "C:\\Users\\voce\\Proton Drive\\TCH"

O que ele preserva do manifesto atual: descrição, tags, versão, badge e link de
cada item — e o nome/descrição/cor/link de cada categoria. Ou seja, você edita
o JSON na mão uma vez e o sync nunca sobrescreve o seu texto.

Uso:
    python tools/sync_proton.py --root /my-files/TCH --dry-run
    python tools/sync_proton.py --root /my-files/TCH
    python tools/sync_proton.py --root /my-files/TCH --links

--links tenta criar um link público por pasta de categoria usando
`proton-drive sharing set-url`. Se a sua versão do CLI ainda não tiver esse
comando, ele avisa e segue: aí um único link da raiz em site.protonRoot já
resolve todos os cards.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import unicodedata
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "data" / "catalog.json"
DEFAULT_ROOT = "/my-files"

# paleta usada nos cards (alterna azul / laranja)
ACCENTS = [
    "#6f6bff", "#ff7a2f", "#5c7cff", "#ffa15c",
    "#8f8bff", "#ff9466", "#7d7bff", "#ffb066",
]

SITE_DEFAULTS = {
    "name": "The Chosen Hideout",
    "tagline": "Arma 3 community archive",
    "discord": "https://discord.gg/F4JHDDYCf6",
    "protonRoot": "https://drive.proton.me/urls/SUBSTITUA-PELO-LINK-RAIZ",
}

# pastas que não viram categoria
IGNORED = {"$recycle.bin", ".git", ".sync", "node_modules", ".DS_Store"}

# nomes de campo que o CLI/SDK pode usar — a gente tenta todos
NAME_KEYS = ("name", "displayname", "filename", "basename")
PATH_KEYS = ("path", "fullpath", "relativepath", "remotepath")
SIZE_KEYS = ("size", "sizebytes", "totalsize", "bytes", "displaysize")
TIME_KEYS = (
    "modificationtime", "modifiedtime", "modtime", "mtime",
    "updated", "updatedat", "createdat", "creationtime",
)
DIR_KEYS = ("isfolder", "isdirectory", "isdir")
TYPE_KEYS = ("type", "kind", "nodetype")
CHILD_KEYS = ("children", "items", "entries", "nodes", "files")
URL_KEYS = ("url", "link", "publiclink", "shareurl")


# ----------------------------------------------------------------- helpers --

def slug(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text or "folder"


def item_id(path: str) -> str:
    return hashlib.sha1(path.encode("utf-8")).hexdigest()[:10]


def fmt_size(n: int) -> str:
    value = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            digits = 0 if value >= 100 or unit == "B" else 1
            return f"{value:.{digits}f} {unit}"
        value /= 1024
    return "—"


def first(mapping: dict, keys: tuple[str, ...]):
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return None


def to_size(value) -> int:
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    digits = re.sub(r"[^\d.]", "", str(value))
    try:
        return int(float(digits)) if digits else 0
    except ValueError:
        return 0


def to_date(value) -> str:
    if not value:
        return ""
    text = str(value).strip()
    match = re.match(r"(\d{4}-\d{2}-\d{2})", text)
    if match:
        return match.group(1)
    try:
        from datetime import datetime
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return ""


def strip_root(path: str, root: str) -> str:
    """Remove o prefixo da raiz (/my-files/TCH) do caminho vindo do Proton."""
    path = path.replace("\\", "/").strip("/")
    root = (root or "").replace("\\", "/").strip("/")
    if not root:
        return path
    if path.lower() == root.lower():
        return ""
    if path.lower().startswith(root.lower() + "/"):
        return path[len(root) + 1:]
    # raiz parcial: tira os segmentos que casarem, um por um
    parts = path.split("/")
    root_parts = root.split("/")
    if parts[:len(root_parts)] and [p.lower() for p in parts[:len(root_parts)]] == [p.lower() for p in root_parts]:
        return "/".join(parts[len(root_parts):])
    return path


# ------------------------------------------------------------- listagem --

def flatten(payload, parent: str = "") -> list[dict]:
    """Aceita lista plana, {items: [...]} ou árvore com children/items."""
    if isinstance(payload, dict):
        for key in CHILD_KEYS:
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
        else:
            payload = [payload]
    if not isinstance(payload, list):
        return []

    out: list[dict] = []

    def walk(node, prefix: str) -> None:
        if not isinstance(node, dict):
            return
        m = {str(k).lower(): v for k, v in node.items()}
        name = first(m, NAME_KEYS)
        path = first(m, PATH_KEYS)
        if not path:
            path = f"{prefix}/{name}" if prefix and name else (name or "")
        path = str(path)
        kids = None
        for key in CHILD_KEYS:
            if isinstance(m.get(key), list) and m[key]:
                kids = m[key]
                break
        if kids:
            for kid in kids:
                walk(kid, path)
            return
        out.append({"path": path, "meta": m})

    for node in payload:
        walk(node, parent)
    return out


def is_dir(meta: dict) -> bool:
    for key in DIR_KEYS:
        if key in meta:
            return bool(meta[key])
    for key in TYPE_KEYS:
        value = str(meta.get(key) or "").lower()
        if value:
            return value in ("folder", "directory", "dir")
    return False


def from_cli(binary: str, root: str) -> list[dict]:
    base = [binary, "filesystem", "list", root, "--json"]

    for extra in (["--recursive"], []):
        cmd = base + extra
        print(f"[>] {' '.join(cmd)}")
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            continue  # flag não existe nessa versão? tenta sem
        try:
            return flatten(json.loads(proc.stdout or "[]"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"o CLI devolveu algo que não é JSON: {exc}\n"
                f"--- primeiras linhas ---\n{(proc.stdout or '')[:400]}"
            ) from exc

    raise RuntimeError(
        "não consegui listar com `proton-drive filesystem list`. "
        "Rode o comando na mão pra ver o erro:\n"
        f"  {binary} filesystem list {root} --json"
    )


def from_local(folder: Path) -> list[dict]:
    out: list[dict] = []
    for path in folder.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(folder).as_posix()
        if any(part.lower() in IGNORED for part in rel.split("/")):
            continue
        stat = path.stat()
        out.append({
            "path": rel,
            "meta": {
                "name": path.name,
                "size": stat.st_size,
                "modificationtime": date.fromtimestamp(stat.st_mtime).isoformat(),
            },
        })
    return out


# ---------------------------------------------------------------- montagem --

def build(entries: list[dict], manifest: dict, root: str) -> tuple[list[dict], list[dict], dict[str, str]]:
    old_cats = {c["id"]: c for c in manifest.get("categories", [])}
    old_items = {i.get("protonPath"): i for i in manifest.get("items", [])}

    # agrupa por categoria (1º nível) e por pacote (2º nível)
    groups: dict[str, dict[str, list[dict]]] = {}
    loose = 0
    for entry in entries:
        rel = strip_root(entry["path"], root)
        if not rel:
            continue
        parts = rel.split("/")
        if len(parts) < 2:
            loose += 1
            continue
        folder = parts[0]
        if folder.lower() in IGNORED:
            continue
        package = parts[1]
        groups.setdefault(folder, {}).setdefault(package, []).append({
            "path": rel,
            "depth": len(parts),
            "meta": entry["meta"],
        })

    if loose:
        print(f"[!] {loose} arquivo(s) solto(s) na raiz foram ignorados "
              f"(o site indexa o que está dentro das pastas).")

    categories: list[dict] = []
    items: list[dict] = []
    folders: dict[str, str] = {}

    for index, folder in enumerate(sorted(groups, key=str.lower)):
        cat_id = slug(folder)
        known = old_cats.get(cat_id, {})
        folders[cat_id] = folder
        categories.append({
            "id": cat_id,
            "name": known.get("name") or folder,
            "desc": known.get("desc") or f"Files from the {folder} folder.",
            "accent": known.get("accent") or ACCENTS[index % len(ACCENTS)],
            "protonUrl": known.get("protonUrl", ""),
        })

        for package in sorted(groups[folder], key=str.lower):
            files = groups[folder][package]
            rel_path = f"{folder}/{package}"
            previous = old_items.get(rel_path, {})

            # pacote = pasta (tem arquivos dentro) ou arquivo solto na categoria
            nested = any(f["depth"] > 2 for f in files)
            if nested:
                display = package
                size = sum(to_size(first(f["meta"], SIZE_KEYS)) for f in files)
                dates = [to_date(first(f["meta"], TIME_KEYS)) for f in files]
                updated = max([d for d in dates if d], default="")
            else:
                display = Path(package).stem
                meta = files[0]["meta"]
                size = to_size(first(meta, SIZE_KEYS))
                updated = to_date(first(meta, TIME_KEYS))

            items.append({
                "id": previous.get("id") or item_id(rel_path),
                "name": previous.get("name") or display,
                "category": cat_id,
                "desc": previous.get("desc") or "",
                "tags": previous.get("tags") or [],
                "version": previous.get("version") or "—",
                "sizeBytes": size,
                "updated": updated or previous.get("updated") or "",
                **({"badge": previous["badge"]} if previous.get("badge") else {}),
                "protonPath": rel_path,
                **({"protonUrl": previous["protonUrl"]} if previous.get("protonUrl") else {}),
            })

    return categories, items, folders


def create_links(binary: str, root: str, categories: list[dict], folders: dict[str, str]) -> None:
    """Tenta criar um link público por pasta. Falha alto e claro se não der."""
    for cat in categories:
        current = cat.get("protonUrl") or ""
        if current and "SUBSTITUA" not in current and "COLE-O-LINK" not in current:
            continue
        target = f"{root.rstrip('/')}/{folders.get(cat['id'], cat['name'])}"
        cmd = [binary, "sharing", "set-url", "--role", "viewer", "--json", target]
        print(f"[>] {' '.join(cmd)}")
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        url = ""
        for blob in (proc.stdout, proc.stderr):
            for match in re.findall(r"https://drive\.proton\.me/urls/[A-Za-z0-9#_-]+", blob or ""):
                url = match
        if proc.returncode == 0 and url:
            cat["protonUrl"] = url
            print(f"    ok {url}")
        else:
            print("    [x] esse CLI não criou link público.")
            print("        Cria no app web (Share → Public link) e cola em "
                  "categories[].protonUrl, ou preenche site.protonRoot com um "
                  "link único da raiz — os cards caem nele por fallback.")
            return


# ------------------------------------------------------------------- main --

def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Indexa o Proton Drive no catálogo do site.")
    parser.add_argument("--root", default=DEFAULT_ROOT,
                        help=f"pasta raiz dentro do Drive (default: {DEFAULT_ROOT})")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="arquivo de saída")
    parser.add_argument("--cli", default="proton-drive", help="executável do Proton Drive CLI")
    parser.add_argument("--input", type=Path, help="JSON já salvo do CLI (pula a chamada)")
    parser.add_argument("--local", type=Path, help="pasta local sincronizada pelo app")
    parser.add_argument("--links", action="store_true", help="tenta criar link público por pasta")
    parser.add_argument("--dry-run", action="store_true", help="não grava nada, só relata")
    args = parser.parse_args()

    if args.out.exists():
        with args.out.open("r", encoding="utf-8") as fh:
            manifest = json.load(fh)
    else:
        print(f"[i] {args.out} não existe ainda, criando do zero.")
        manifest = {"site": dict(SITE_DEFAULTS), "categories": [], "items": []}
    manifest.setdefault("site", dict(SITE_DEFAULTS))
    manifest.setdefault("categories", [])
    manifest.setdefault("items", [])

    try:
        if args.input:
            entries = flatten(json.loads(args.input.read_text(encoding="utf-8-sig")))
        elif args.local:
            if not args.local.is_dir():
                print(f"[x] pasta não encontrada: {args.local}")
                return 1
            entries = from_local(args.local)
        else:
            entries = from_cli(args.cli, args.root)
    except (RuntimeError, OSError, json.JSONDecodeError) as exc:
        print(f"[x] {exc}")
        return 1

    print(f"[i] {len(entries)} arquivo(s) listados")

    categories, items, folders = build(entries, manifest, args.root)
    if not items:
        print("[x] nada indexado. Confira --root (o caminho tem que ser o da pasta "
              "que contém as categorias) e se o CLI está logado.")
        return 1

    manifest["categories"] = categories
    manifest["items"] = items
    manifest["site"]["updated"] = date.today().isoformat()

    if args.links:
        create_links(args.cli, args.root, categories, folders)

    print()
    print(f"{'CATEGORIA':<28} {'PACOTES':>8}  TAMANHO")
    print("-" * 52)
    for cat in categories:
        bucket = [i for i in items if i["category"] == cat["id"]]
        total = sum(i["sizeBytes"] for i in bucket)
        print(f"{cat['name'][:27]:<28} {len(bucket):>8}  {fmt_size(total)}")
    print("-" * 52)
    print(f"{'TOTAL':<28} {len(items):>8}  {fmt_size(sum(i['sizeBytes'] for i in items))}")

    if args.dry_run:
        print("\n[i] --dry-run: nada foi gravado.")
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    print(f"\n[ok] gravado em {args.out}")
    if not any(c.get("protonUrl") for c in categories):
        print("     Nenhuma categoria tem protonUrl: os cards vão usar site.protonRoot.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
