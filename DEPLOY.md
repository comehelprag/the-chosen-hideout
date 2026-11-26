# Deploy — Vercel + GitHub + atualização automática

Do zero até o site no ar se atualizando sozinho. Uns 20 minutos, uma vez.

O desenho é este:

```
Proton Drive  ──(1x por dia)──►  GitHub Action  ──commit──►  GitHub  ──push──►  Vercel
   você sobe o mod               lê a share e reescreve       repo          publica sozinha
                                 data/catalog.json
```

---

## 1. O que você precisa ter

- Conta no **GitHub** (github.com)
- Conta na **Vercel** (vercel.com — entra com o GitHub, é mais fácil)
- **Git** instalado no Windows → https://git-scm.com/download/win
  (instalação padrão, "Next" até o fim. Ele vem com o "Git Bash", que você não
  precisa usar.)

Para conferir se o Git entrou, abra o **PowerShell** e rode:

```powershell
git --version
```

Tem que aparecer algo como `git version 2.47.0`. Se der erro, feche e abra o
PowerShell de novo.

---

## 2. Criar o repositório no GitHub

1. Abra https://github.com/new
2. **Repository name:** `the-chosen-hideout`
3. **Public** (recomendado: Actions ilimitados de graça). O link do Proton já é
   público no site, então o repositório não expõe nada novo.
4. **Não** marque "Add a README" nem `.gitignore` — o projeto já tem os dele.
5. **Create repository**

A página seguinte mostra uns comandos. Ignore, os de baixo já estão certos.

---

## 3. Subir o projeto

No PowerShell, dentro da pasta do site:

```powershell
cd "C:\Users\ragna\Documents\site tch"

git init
git add .
git commit -m "site + catalog sync"
git branch -M main
git remote add origin https://github.com/SEU-USUARIO/the-chosen-hideout.git
git push -u origin main
```

Troque `SEU-USUARIO` pelo seu nome de usuário do GitHub.

Na primeira vez ele abre uma janela do navegador pedindo login no GitHub —
autorize e o push continua sozinho.

> Deu `error: remote origin already exists`? Então o repo já estava ligado:
> `git remote set-url origin https://github.com/SEU-USUARIO/the-chosen-hideout.git`

---

## 4. Publicar na Vercel

1. https://vercel.com/new
2. **Import Git Repository** → escolha `the-chosen-hideout` → **Import**
3. Na tela de configuração, deixe assim:
   - **Framework Preset:** `Other`
   - **Root Directory:** `./` (padrão)
   - **Build Command:** vazio (apague se vier algo)
   - **Output Directory:** vazio
   - **Install Command:** vazio
4. **Deploy**

Em ~20 segundos sai uma URL tipo `the-chosen-hideout.vercel.app`. Abra e confira:
categorias, catálogo, busca, favoritos.

Não tem build porque o site é HTML/CSS/JS puro — a Vercel só serve os arquivos.

---

## 5. Ligar a atualização automática

O workflow já está no repositório (`.github/workflows/sync-catalog.yml`), mas
precisa de uma permissão e de um primeiro teste.

### 5.1 Dar permissão de escrita ao Action

`Settings` (do repo) → **Actions** → **General** → desça até
**Workflow permissions** → marque **Read and write permissions** → **Save**.

Sem isso o job roda mas não consegue commitar.

### 5.2 Rodar uma vez na mão

`Actions` → na lista da esquerda, **Sync Proton catalog** → botão
**Run workflow** → **Run workflow**.

Acompanhe clicando no run. O que deve acontecer:

1. instala Python e Chromium (~2 min)
2. abre a share e lê as 10 pastas (~1 min)
3. commita `data/catalog.json` se algo mudou
4. a Vercel publica sozinha

Se o catálogo já estava igual, ele termina com "Nada mudou no Drive." — normal.

### 5.3 Confirmar o agendamento

O mesmo arquivo tem `cron: "0 5 * * *"` — todo dia às 02:00 de Brasília. Não
precisa fazer nada; o GitHub dispara sozinho.

> O GitHub desativa agendamento em repositório parado há 60 dias. Como este job
> commita, ele se mantém ativo. Se um dia parar, `Actions` → **Enable workflow**.

---

## 6. Domínio próprio (opcional)

`Settings` → **Domains** → adicione `tch.seudominio.com` e siga o que ele pedir
(um registro CNAME no seu provedor de domínio). O HTTPS é automático.

---

## 7. Links diretos por mod (o card abre a pasta do mod)

Sem isso, todo card abre a raiz do `A3`. Com um link por pasta, o clique cai
direto no mod.

O CLI oficial faz isso (`sharing set-url`). Se ainda não tem o binário:

```
https://proton.me/download/drive/cli/index.html   → windows/x64
```

```powershell
$cli = "$env:LOCALAPPDATA\proton-drive\proton-drive.exe"

& $cli auth login                     # abre o navegador, uma vez
& $cli filesystem list /my-files      # confirme o nome da pasta (A3?)

.\.venv\Scripts\python.exe tools\make_links.py --cli $cli --root /my-files/A3 --dry-run
.\.venv\Scripts\python.exe tools\make_links.py --cli $cli --root /my-files/A3
```

Ele cria 1 link por mod (55 hoje), grava em `items[].protonUrl` e salva depois de
cada um. Se cair no meio, roda de novo: pula o que já tem link.

Depois, publique:

```powershell
git add data/catalog.json
git commit -m "catalog: links diretos"
git push
```

> Cada link é uma share separada no Proton — revogar um mod é revogar aquela
> share. Mod novo? Roda o `make_links.py` de novo e só ele ganha link.

Se 55 shares for demais, `--categories` cria só 10 (um por nação): o card cai na
pasta da nação, um clique antes do mod.

---

## 8. Como fica o dia a dia

| Você faz | O sistema faz |
| --- | --- |
| sobe um mod numa pasta existente | o card aparece na próxima madrugada |
| cria uma pasta de nação nova | vira categoria nova sozinho |
| renomeia uma pasta | vira item novo (favorito antigo se perde) |
| edita `desc`/`tags` no JSON | o sync nunca sobrescreve |
| roda `make_links.py` | o card passa a abrir a pasta do mod |

Quer que apareça na hora, sem esperar a madrugada? `Actions` → **Sync Proton
catalog** → **Run workflow**. Publica em ~3 minutos.

---

## 9. Se algo quebrar

**O Action falhou e o site continua com o catálogo antigo.**
Isso é de propósito: o script aborta sem gravar quando a coleta vem estranha.
Abra o run que falhou, leia a última linha vermelha e baixe o artifact
`sync-debug` (tem um print da tela do Proton no momento da falha).

Causa mais provável: o Proton mudou o HTML da share. Aí você tem duas saídas:

**a) Rodar local e commitar na mão** (5 min, sempre funciona):

```powershell
cd "C:\Users\ragna\Documents\site tch"
.venv\Scripts\python tools\sync_share.py --dry-run
.venv\Scripts\python tools\sync_share.py
git add data/catalog.json
git commit -m "catalog: sync manual"
git push
```

**b) Caminho oficial, sem navegador:** instale o CLI do Proton
(`proton.me/drive/download`, seção CLI), rode `proton-drive auth login` e depois
`python tools/sync_proton.py --root /my-files/TCH`. Mesmo resultado, sem
depender do HTML.

Se o erro for "nenhum item coletado", quase sempre é o link da share mudou ou foi
revogado: confira `site.protonRoot` no `data/catalog.json`.

**O site abriu vazio.**
`catalog.json` não carregou. Confira se o arquivo está no repo e se é JSON válido
(uma vírgula sobrando derruba tudo). O console do navegador (F12) diz o motivo.

**Publiquei e o site não mudou.**
A Vercel guarda cache do `catalog.json`? Não mais: o `vercel.json` manda
`no-store`. Se persistir, `Deployments` → veja se o último deploy foi depois do
commit. Se não, `Redeploy`.
