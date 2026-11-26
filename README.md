# The Chosen Hideout — site

Catálogo estático da comunidade: categorias espelhando as pastas do Proton Drive,
busca, filtros e favoritos. HTML + CSS + JS puro, sem build, sem dependência.

```
index.html                     página única
assets/css/style.css           visual (VHS / glitch)
assets/js/app.js               render, busca, filtros, favoritos
data/catalog.json              ← o índice que o site lê (gerado ou editado à mão)
tools/sync_share.py            gera o catalog.json lendo a share pública
tools/make_links.py            cria link público por pasta e grava em items[].protonUrl
tools/sync_proton.py           alternativa via CLI oficial do Proton
.github/workflows/             Action que roda o sync todo dia e commita
vercel.json                    cache do catálogo (sempre fresco)
DEPLOY.md                      tutorial de publicação (Vercel + GitHub)
```

## Rodar local

`fetch` não funciona abrindo o arquivo com duplo clique. Use um servidor:

```bash
cd "site tch"
python -m http.server 8080
# abre http://localhost:8080
```

## Editar o catálogo

Tudo vive em `data/catalog.json`:

| Campo | O que é |
| --- | --- |
| `site.discord` | convite do Discord |
| `site.protonRoot` | link raiz do Proton (fallback dos cards) |
| `site.updated` | data mostrada no hero |
| `categories[].protonUrl` | link **de compartilhamento da pasta** no Proton |
| `categories[].accent` | cor do card |
| `items[].tags` | viram as hashtags e entram na busca |
| `items[].version` / `sizeBytes` | aparecem no rodapé do card |
| `items[].badge` | opcional, ex. `"new"` |

Categoria sem `protonUrl` cai no `site.protonRoot`. Item sem `protonUrl` cai na
URL da própria categoria — ou seja, o link de pasta já resolve o download.

O que você escrever à mão (`desc`, `tags`, `version`, `badge`, `protonUrl`) é
**preservado** por qualquer sync. O sync só mexe em nome, caminho e tamanho.

## Como o Proton entra

O Proton é criptografado ponta a ponta e não tem API pública de listagem. O que
existe é o link da share: ele carrega a chave de descriptografia no fragmento da
URL (`#EzmwH5aYWpLP`). Quem abre o link, abre a pasta — inclusive um navegador
headless.

É nisso que a automação se apoia: um Chromium invisível abre a share, entra em
cada pasta de nação e lê os nomes. **Sem conta Proton, sem senha, sem sessão.**

### O mínimo: um link só

`Compartilhar → Qualquer pessoa com o link` na pasta raiz, e cola em
`site.protonRoot`. Todos os cards caem nele por fallback.

Um link basta porque link do Proton não tem deep link: a URL aponta pra pasta
compartilhada e a navegação pra dentro acontece no cliente.

### Catálogo automático

```bash
# uma vez na máquina
python -m venv .venv
.venv\Scripts\pip install playwright
.venv\Scripts\python -m playwright install chromium

# sempre que quiser atualizar
.venv\Scripts\python tools\sync_share.py --dry-run
.venv\Scripts\python tools\sync_share.py
```

Pasta de 1º nível vira categoria, entrada de 2º nível vira item. Nome de exibição
perde o `@` e a extensão de arquivo (`.rar`, `.zip`) — o caminho real continua
intacto em `protonPath`.

O Proton não informa tamanho de pasta no share (`--`), então item que é pasta
fica com `sizeBytes: 0` e o card mostra o caminho no Drive. Arquivo solto vem
com tamanho real.

**Rede de segurança:** se a coleta vier vazia ou cair mais de 30% em relação ao
catálogo atual, o script aborta sem gravar. Share quebrada não derruba o site.
Se a queda for real, `--force`.

### Link direto por mod

Um link do Proton abre **uma pasta**, nunca um arquivo dentro dela — não existe
deep link. Então, para o card abrir a pasta exata do mod, cada pasta precisa do
seu próprio link público. O `tools/make_links.py` cria esses links pelo CLI
oficial e grava em `items[].protonUrl`:

```bash
# binário em proton.me/download/drive/cli/index.html (0.9.0+)
proton-drive auth login                                   # uma vez, no navegador
python tools/make_links.py --root /my-files/A3 --dry-run
python tools/make_links.py --root /my-files/A3
```

É idempotente: item que já tem link é pulado, e ele grava depois de cada link
criado (se cair no meio, é só rodar de novo). `--limit 5` faz em lotes;
`--categories` cria também um link por nação, que serve de fallback para mod
novo que ainda não tem link.

Precedência do destino de um card: `items[].protonUrl` → `categories[].protonUrl`
→ `site.protonRoot`. O sync diário preserva os dois primeiros pelo `protonPath`.

### Alternativa: CLI oficial

Se um dia o front do Proton mudar e o scraper quebrar, o caminho robusto é o CLI
oficial (não use rclone: o backend `protondrive` foi descontinuado em 2026).

```bash
proton-drive auth login                  # abre o navegador, uma vez
proton-drive filesystem list /my-files   # sanity check
python tools/sync_proton.py --root /my-files/TCH --dry-run
python tools/sync_proton.py --root /my-files/TCH
```

| Comando | Quando usar |
| --- | --- |
| `--root /my-files/TCH` | chama o CLI na hora (precisa de sessão) |
| `--input dump.json` | já rodou `... list ... --json > dump.json` |
| `--local "C:\...\Proton Drive\TCH"` | pasta do app do Windows, sem CLI |

`--links` tenta criar um link público por pasta via `sharing set-url`. Se a sua
versão do CLI ainda não tiver o comando, ele avisa e você cria na mão — o
`site.protonRoot` cobre o resto.

> Fair use: o CLI segue a política do Proton. Listar é barato; não saia
> re-baixando o acervo inteiro a cada hora.

### Atualização automática

`.github/workflows/sync-catalog.yml` roda o `sync_share.py` todo dia às 05:00 UTC
e commita o `catalog.json` se algo mudou. A Vercel detecta o commit e publica.

Fluxo completo: você sobe o mod no Proton → de madrugada o Action regenera o
índice → a Vercel publica → o membro vê o card novo.

Se o job falhar, o GitHub te manda e-mail e guarda um print da tela do Proton no
artifact `sync-debug` — é o suficiente pra saber se o layout mudou.

## Deploy

Vercel, com deploy automático a cada push. Passo a passo em **[DEPLOY.md](DEPLOY.md)**.

## Notas

- Favoritos ficam em `localStorage` (`tch:favs`), por navegador.
- `?cat=usa` na URL abre o catálogo já filtrado — bom pra linkar categoria no Discord.
- `/` foca a busca, `Esc` limpa.
- O site não hospeda arquivo nenhum: só aponta. O conteúdo é responsabilidade de
  quem publica a pasta.
