#!/usr/bin/env python3
"""
sync_share.py — reescreve data/catalog.json lendo a share pública do Proton Drive.

Por que isso funciona: o link de compartilhamento carrega a chave de
descriptografia no fragmento da URL (#...). Quem abre o link, abre a pasta. Então
um navegador headless consegue entrar em cada pasta e ler os nomes — sem conta
Proton, sem senha, sem CLI, sem sessão.

    python tools/sync_share.py --dry-run     # mostra a tabela, não grava
    python tools/sync_share.py               # grava data/catalog.json

Regras de mapeamento:
    pasta de 1º nível (AFGHANISTAN, USA, ...)  -> categoria
    entrada de 2º nível (pasta ou arquivo)     -> item

O que NUNCA é sobrescrito: descrição, tags, versão, badge e link de cada item,
mais nome/descrição/cor de cada categoria. Tudo casado por caminho (`protonPath`)
ou por `id`. Você escreve uma vez, o sync respeita pra sempre.

Rede de segurança: se a coleta vier vazia ou muito menor que o catálogo atual, o
script aborta sem tocar no arquivo. Share quebrada não derruba o site.

Requisitos: pip install playwright && python -m playwright install chromium
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "data" / "catalog.json"
DEBUG_DIR = ROOT / "tools" / ".debug"

ACCENTS = [
    "#6f6bff", "#ff7a2f", "#5c7cff", "#ffa15c",
    "#8f8bff", "#ff9466", "#7d7bff", "#ffb066",
]

ARCHIVE_SUFFIXES = {".rar", ".zip", ".7z", ".tar", ".gz", ".pkg"}

SITE_DEFAULTS = {
    "name": "The Chosen Hideout",
    "tagline": "Arma 3 community archive",
    "discord": "https://discord.gg/F4JHDDYCf6",
    "protonRoot": "",
}

# --------------------------------------------------------------- seletores --
# O front do Proton é React e não promete estabilidade. Tudo aqui está num lugar
# só: se um dia quebrar, é isso que você ajusta (e o job falha alto, não calado).
ROW_SEL = 'tr[data-testid="drive-explorer-row"]'
NAME_SEL = 'td[data-cell-id="name"] span[aria-label]'
SIZE_SEL = 'td[data-cell-id="size"]'
ACTIVATOR_SEL = "button[data-item-activator]"
CRUMB_SEL = "ul.collapsing-breadcrumbs"

READ_ROWS_JS = """
(sel) => {
  const rows = [...document.querySelectorAll(sel)];
  return rows.map((r) => {
    const nameEl = r.querySelector('td[data-cell-id="name"] span[aria-label]');
    const sizeEl = r.querySelector('td[data-cell-id="size"]');
    const fallback = r.querySelector('td[data-cell-id="name"]');
    const name = nameEl
      ? nameEl.getAttribute('aria-label')
      : (fallback ? fallback.innerText.trim().split('\\n')[0] : '');
    const rawIndex = r.getAttribute('data-index');
    return {
      name: (name || '').trim(),
      size: sizeEl ? sizeEl.innerText.trim() : '',
      index: rawIndex === null ? null : Number(rawIndex),
    };
  }).filter((r) => r.name);
}
"""

# A lista do Proton é virtualizada: só existe no DOM o que está na tela. Rolar
# até o fim e voltar é o que garante que nenhum mod fique de fora.
SCROLL_JS = """
() => {
  const row = document.querySelector('tr[data-testid="drive-explorer-row"]');
  if (!row) return { moved: false };
  let el = row.parentElement;
  while (el) {
    const s = getComputedStyle(el);
    if (/(auto|scroll)/.test(s.overflowY) && el.scrollHeight > el.clientHeight + 20) {
      const before = el.scrollTop;
      el.scrollTop = Math.min(el.scrollTop + el.clientHeight * 0.85, el.scrollHeight);
      return { moved: el.scrollTop !== before };
    }
    el = el.parentElement;
  }
  const before = window.scrollY;
  window.scrollBy(0, window.innerHeight * 0.85);
  return { moved: window.scrollY !== before };
}
"""

SCROLL_TOP_JS = """
() => {
  const row = document.querySelector('tr[data-testid="drive-explorer-row"]');
  let el = row ? row.parentElement : null;
  while (el) {
    const s = getComputedStyle(el);
    if (/(auto|scroll)/.test(s.overflowY)) { el.scrollTop = 0; break; }
    el = el.parentElement;
  }
  window.scrollTo(0, 0);
}
"""

ENTER_JS = """
([sel, activator, name]) => {
  const rows = [...document.querySelectorAll(sel)];
  const row = rows.find((r) => {
    const el = r.querySelector('td[data-cell-id="name"] span[aria-label]');
    return el && el.getAttribute('aria-label') === name;
  });
  if (!row) return false;
  const btn = row.querySelector(activator);
  if (!btn) return false;
  btn.focus();
  btn.click();
  return true;
}
"""


# ----------------------------------------------------------------- helpers --

def slug(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text or "folder"


def item_id(path: str) -> str:
    return hashlib.sha1(path.encode("utf-8")).hexdigest()[:10]


def display_name(raw: str) -> str:
    name = raw.lstrip("@").strip()
    suffix = Path(name).suffix.lower()
    if suffix in ARCHIVE_SUFFIXES:
        name = name[: -len(suffix)]
    return name.strip() or raw


def parse_size(text: str) -> int:
    match = re.match(r"([\d.,]+)\s*(B|KB|MB|GB|TB)", (text or "").strip(), re.I)
    if not match:
        return 0
    number = float(match.group(1).replace(",", "."))
    unit = match.group(2).upper()
    factor = {"B": 1, "KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3, "TB": 1024 ** 4}[unit]
    return int(number * factor)


def fmt_size(n: int) -> str:
    value = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            digits = 0 if value >= 100 or unit == "B" else 1
            return f"{value:.{digits}f} {unit}"
        value /= 1024
    return "—"


def is_folder(row: dict) -> bool:
    """O Proton mostra '--' em pasta e um tamanho real em arquivo."""
    return row.get("size", "").strip() in ("", "--", "—", "-")


# ------------------------------------------------------------------ scrape --

class Scraper:
    def __init__(self, page, timeout: int = 30000):
        self.page = page
        self.timeout = timeout

    def rows(self) -> list[dict]:
        return self.page.evaluate(READ_ROWS_JS, ROW_SEL)

    def load_all(self) -> list[dict]:
        """Rola a lista até o fim juntando as linhas — a tabela é virtualizada."""
        seen: dict[object, dict] = {}
        for _ in range(200):
            for row in self.rows():
                key = row["index"] if row["index"] is not None else row["name"]
                seen[key] = row
            if not self.page.evaluate(SCROLL_JS).get("moved"):
                break
            self.page.wait_for_timeout(180)
        self.page.evaluate(SCROLL_TOP_JS)
        return sorted(
            seen.values(),
            key=lambda r: (r["index"] if r["index"] is not None else 10**6, r["name"].lower()),
        )

    def wait_rows(self) -> list[dict]:
        """Espera a lista renderizar e parar de mudar (o React monta em etapas)."""
        previous: list[str] | None = None
        for _ in range(int(self.timeout / 350)):
            rows = self.load_all()
            names = [r["name"] for r in rows]
            if names and previous == names:
                return rows
            previous = names
            self.page.wait_for_timeout(350)
        raise RuntimeError("a listagem do Proton nunca estabilizou (timeout)")

    def wait_changed(self, previous_names: list[str]) -> list[dict]:
        """Espera a lista virar outra coisa (entramos numa pasta)."""
        for _ in range(int(self.timeout / 350)):
            rows = self.load_all()
            names = [r["name"] for r in rows]
            if names and names != previous_names:
                return self.wait_rows()
            self.page.wait_for_timeout(350)
        raise RuntimeError("clicar na pasta não abriu nada (timeout)")

    def enter(self, name: str, previous_names: list[str]) -> list[dict]:
        ok = self.page.evaluate(ENTER_JS, [ROW_SEL, ACTIVATOR_SEL, name])
        if not ok:
            raise RuntimeError(f"não achei a linha '{name}' na listagem")
        self.page.keyboard.press("Enter")
        return self.wait_changed(previous_names)

    def back_to_root(self, root_names: list[str]) -> None:
        clicked = self.page.evaluate(
            """(sel) => {
                const btn = document.querySelector(sel + ' li button');
                if (!btn) return false;
                btn.click();
                return true;
            }""",
            CRUMB_SEL,
        )
        if not clicked:
            raise RuntimeError("não achei o botão de voltar (breadcrumb)")
        for _ in range(int(self.timeout / 350)):
            if [r["name"] for r in self.load_all()] == root_names:
                return
            self.page.wait_for_timeout(350)
        raise RuntimeError("o breadcrumb não voltou para a raiz (timeout)")

    def scrape(self) -> list[tuple[str, list[dict]]]:
        root = self.wait_rows()
        root_names = [r["name"] for r in root]
        print(f"[i] raiz: {len(root)} pasta(s)")

        result: list[tuple[str, list[dict]]] = []
        for index, row in enumerate(root, 1):
            name = row["name"]
            if not is_folder(row):
                print(f"[!] '{name}' na raiz é arquivo, ignorado")
                continue
            print(f"[>] ({index}/{len(root)}) {name}")
            entries = self.enter(name, root_names)
            result.append((name, entries))
            self.back_to_root(root_names)

        return result


# ------------------------------------------------------------------ montagem --

def build(scraped, manifest: dict) -> tuple[list[dict], list[dict], int]:
    old_cats = {c["id"]: c for c in manifest.get("categories", [])}
    old_items = {i.get("protonPath"): i for i in manifest.get("items", [])}

    categories: list[dict] = []
    items: list[dict] = []
    folders_without_size = 0

    for index, (folder, entries) in enumerate(sorted(scraped, key=lambda x: x[0].lower())):
        cat_id = slug(folder)
        known = old_cats.get(cat_id, {})

        categories.append({
            "id": cat_id,
            "name": known.get("name") or folder,
            "desc": known.get("desc") or f"Files from the {folder} folder.",
            "accent": known.get("accent") or ACCENTS[index % len(ACCENTS)],
            "protonUrl": known.get("protonUrl", ""),
        })

        for entry in entries:
            raw = entry["name"]
            path = f"{folder}/{raw}"
            previous = old_items.get(path, {})
            folder_entry = is_folder(entry)

            if folder_entry:
                folders_without_size += 1

            items.append({
                "id": previous.get("id") or item_id(path),
                "name": previous.get("name") or display_name(raw),
                "category": cat_id,
                "desc": previous.get("desc") or "",
                "tags": previous.get("tags") or [],
                "version": previous.get("version") or "—",
                "sizeBytes": 0 if folder_entry else parse_size(entry["size"]),
                "updated": previous.get("updated") or "",
                **( {"badge": previous["badge"]} if previous.get("badge") else {} ),
                "protonPath": path,
                **( {"protonUrl": previous["protonUrl"]} if previous.get("protonUrl") else {} ),
            })

    return categories, items, folders_without_size


# --------------------------------------------------------------------- main --

def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Indexa a share pública do Proton no catálogo.")
    parser.add_argument("--share", help="link público (default: site.protonRoot do catalog.json)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="arquivo de saída")
    parser.add_argument("--dry-run", action="store_true", help="não grava nada")
    parser.add_argument("--headed", action="store_true", help="abre o navegador (debug)")
    parser.add_argument("--timeout", type=int, default=30000, help="timeout por etapa, em ms")
    parser.add_argument("--min-ratio", type=float, default=0.7,
                        help="aborta se o total cair abaixo disso vs. o catálogo atual")
    parser.add_argument("--force", action="store_true", help="grava mesmo caindo abaixo do ratio")
    args = parser.parse_args()

    if args.out.exists():
        with args.out.open(encoding="utf-8-sig") as fh:
            manifest = json.load(fh)
    else:
        print(f"[i] {args.out} não existe, criando do zero")
        manifest = {"site": dict(SITE_DEFAULTS), "categories": [], "items": []}
    manifest.setdefault("site", dict(SITE_DEFAULTS))
    manifest.setdefault("categories", [])
    manifest.setdefault("items", [])

    share = args.share or manifest["site"].get("protonRoot", "")
    if not re.match(r"^https?://", share or ""):
        print("[x] sem link válido. Passe --share https://drive.proton.me/urls/... "
              "ou preencha site.protonRoot no catalog.json.")
        return 1

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[x] Playwright não instalado. Rode:\n"
              "      pip install playwright\n"
              "      python -m playwright install chromium")
        return 1

    print(f"[i] share: {share}")
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=not args.headed)
            context = browser.new_context(
                locale="en-US",
                viewport={"width": 1440, "height": 1000},
                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/131.0.0.0 Safari/537.36"),
            )
            page = context.new_page()
            page.goto(share, wait_until="domcontentloaded", timeout=args.timeout)
            scraper = Scraper(page, timeout=args.timeout)
            try:
                scraped = scraper.scrape()
            except Exception:
                DEBUG_DIR.mkdir(parents=True, exist_ok=True)
                shot = DEBUG_DIR / "failure.png"
                html = DEBUG_DIR / "failure.html"
                try:
                    page.screenshot(path=str(shot), full_page=True)
                    html.write_text(page.content(), encoding="utf-8")
                    print(f"[i] diagnóstico em {shot} e {html}")
                except Exception:
                    pass
                raise
            browser.close()
    except Exception as exc:  # noqa: BLE001 — queremos a mensagem crua no CI
        print(f"[x] falhou: {exc}")
        return 1

    categories, items, folders_without_size = build(scraped, manifest)

    if not items:
        print("[x] nenhum item coletado. Catálogo intacto.")
        return 1

    old_total = len(manifest["items"])
    if old_total and not args.force and len(items) < old_total * args.min_ratio:
        print(f"[x] coleta suspeita: {len(items)} itens contra {old_total} no catálogo "
              f"({len(items) / old_total:.0%}). Nada foi gravado.\n"
              f"    Se a queda é real, rode com --force.")
        return 1

    manifest["categories"] = categories
    manifest["items"] = items
    manifest["site"]["protonRoot"] = share
    manifest["site"]["updated"] = date.today().isoformat()

    print()
    print(f"{'CATEGORY':<28} {'ITEMS':>6}  SIZE")
    print("-" * 48)
    for cat in categories:
        bucket = [i for i in items if i["category"] == cat["id"]]
        total = sum(i["sizeBytes"] for i in bucket)
        print(f"{cat['name'][:27]:<28} {len(bucket):>6}  {fmt_size(total) if total else '—'}")
    print("-" * 48)
    print(f"{'TOTAL':<28} {len(items):>6}")

    if folders_without_size:
        print(f"\n[i] {folders_without_size} item(ns) são pasta — o Proton não informa "
              f"tamanho de pasta no share; o card mostra o caminho no Drive.")

    if args.dry_run:
        print("\n[i] --dry-run: nada foi gravado")
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")

    print(f"\n[ok] gravado em {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
