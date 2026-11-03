# AkonMedia — núcleo

O núcleo do AkonMedia, desenvolvido em clean room: uma plataforma de mídia
auto-hospedada construída do zero, sem nenhum código aproveitado de
implementações anteriores. O código da aplicação é trabalho original sob a
licença MIT (veja `LICENSE`); os recursos de terceiros estão listados em
[Licenças](#licenças).

![Biblioteca, tema escuro](docs/screenshots/home-dark.jpg)

| | |
|---|---|
| ![Página de vídeo: player adaptativo com marcadores de capítulo e prévia em storyboard na barra de progresso](docs/screenshots/watch.jpg) | ![Estúdio do criador](docs/screenshots/studio.jpg) |
| ![Gerenciar: filas, estatísticas e papéis das contas](docs/screenshots/manage.jpg) | ![Biblioteca, tema claro](docs/screenshots/home-light.jpg) |

<p align="center"><img src="docs/screenshots/phone.jpg" alt="Página de vídeo no celular" width="300"></p>

## Situação

Fase 5 de 6 — a curadoria e as legendas estão prontas, e a interface foi
reconstruída de ponta a ponta. Falta a virada (cut-over) em si.

| Área | Estado |
|---|---|
| Projeto Django, configurações por ambiente, integração com o Celery | pronto |
| Contas baseadas em e-mail (cadastro, login, redefinição de senha, gestão de e-mails) via `django-allauth`, com estilo próprio | pronto |
| Escada de papéis com cinco níveis e um único módulo de política | pronto |
| Regras de visibilidade em um só módulo: pública, não listada (só com o link), privada | pronto |
| API de sessão (`/api/v1/whoami/`), verificações de liveness e readiness | pronto |
| Modelos do domínio de mídia: mídia, categoria, playlist, comentário, legenda, capítulo | pronto |
| Upload em partes retomável: novas tentativas, retomada depois de recarregar a página, cancelamento, limpeza de sessões abandonadas | pronto |
| Pipeline ffprobe/ffmpeg: pôster, escada HLS com `CODECS`, storyboard para a prévia na barra de progresso | pronto |
| Áudio: capa embutida ou um pôster com a forma de onda | pronto |
| Imagens: miniaturas que respeitam o EXIF; PDFs: prévia da primeira página (poppler) | pronto |
| API REST de mídia com regras de visibilidade e permissões por objeto | pronto |
| Biblioteca com destaque, seção "Em alta", filtros por tipo e categoria, ordenação | pronto |
| Página de vídeo: player próprio, capítulos, comentários com respostas fixados em momentos do vídeo, lista "A seguir" | pronto |
| Player: menu de qualidade, velocidade, legendas, PiP, modo teatro, painel de estatísticas, atalhos de teclado, prévias na barra de progresso | pronto |
| Playlists: criar, salvar a partir de qualquer vídeo, reordenar, compartilhar, fila "reproduzir tudo" com autoplay | pronto |
| Página de embed com opções de tempo inicial, autoplay, título e controles | pronto |
| Estúdio do criador: métricas, filtros, edição de detalhes, miniatura, capítulos, reprocessar, excluir | pronto |
| Páginas de canal e configurações da conta | pronto |
| Curadoria: fila de codificação, falhas com nova tentativa, moderação de comentários, papéis das contas | pronto |
| Upload de legendas (SRT/VTT), editor de falas sincronizado com uma prévia, download em WebVTT e SRT | pronto |
| Temas escuro e claro, layout para celular, páginas de erro com a marca | pronto |
| Interface em português do Brasil (padrão) e em inglês, com seletor de idioma | pronto |
| Guia de backup e restauração | `OPERATIONS.md` |

## Requisitos

- Python 3.13
- `uv` (recomendado) ou `pip`
- ffmpeg e ffprobe para vídeo e áudio (o `pdftoppm` do poppler para prévias de PDF, opcional)

## Rodando com Docker

```bash
cp .env.docker .env     # troque o SECRET_KEY antes de expor isto na rede
docker compose up -d
docker compose exec web python manage.py createsuperuser
```

Abra <http://localhost:8000>. A primeira inicialização aplica as migrações e
coleta os arquivos estáticos; os workers esperam o banco de dados ficar pronto
em vez de disputar com ele.

| Serviço | Função |
|---|---|
| `web` | gunicorn, serve o site e a API; healthcheck em `/health/` |
| `worker` | worker do Celery: transcodificação, miniaturas, storyboards |
| `beat` | agendador do Celery: limpeza, de hora em hora, dos uploads abandonados |
| `db` | PostgreSQL 17 |
| `redis` | cache e broker |

A imagem já traz ffmpeg e poppler, então a codificação funciona sem configuração
extra. A mídia enviada fica no volume `media`; para tráfego real, coloque nginx
ou um armazenamento de objetos na frente e defina `SERVE_MEDIA=False`.

## Rodando sem Docker

```bash
uv venv .venv --python 3.13
uv pip install -r requirements.txt

python manage.py migrate
python manage.py createsuperuser

python manage.py runserver
```

Sem `POSTGRES_HOST`, o projeto roda em SQLite, então não é preciso mais nada
para começar. Defina `POSTGRES_HOST` (e as variáveis relacionadas) para usar
PostgreSQL. Em desenvolvimento, o Celery executa as tarefas inline, então um
upload é codificado antes de a requisição "complete" responder.

### Conteúdo de demonstração

```bash
python manage.py seed_demo           # cerca de um minuto
python manage.py seed_demo --quick   # clipes mais curtos
```

Gera seis vídeos (1080p e 720p, para você ver a escada inteira), três áudios,
três imagens e um PDF com ffmpeg e Pillow, passa tudo pelo pipeline real e
acrescenta capítulos, legendas em inglês e em português, comentários com
respostas e três playlists. O conteúdo de demonstração é escrito em português,
como a interface padrão. O comando cria quatro contas com uma senha conhecida
(`demo-pass-123`), por isso se recusa a rodar com `DEBUG` desligado, a menos que
você passe `--force`.

## Testes

```bash
python manage.py test apps
```

Os testes do pipeline só rodam onde o ffmpeg está instalado.

### Teste de ponta a ponta no navegador

`tools/e2e/smoke.mjs` conduz um navegador de verdade pelo produto: cadastro,
upload de um vídeo em partes com uma delas derrubada de propósito, espera da
codificação, reprodução do HLS e troca de qualidade, comentário e resposta,
criação e reprodução de uma playlist, edição de detalhes, capítulos e miniatura,
inclusão e edição de legendas, estúdio, busca, temas, troca do idioma da
interface, upload e exclusão de uma imagem, embed, layouts de celular e um
gestor mudando um papel. São 42 verificações, e o teste falha diante de qualquer
erro de JavaScript. Os seletores seguem o idioma padrão da interface, o
português do Brasil.

```bash
npm i -D playwright && npx playwright install chromium   # só uma vez
python manage.py seed_demo && python manage.py runserver &
BASE_URL=http://127.0.0.1:8000 \
E2E_MANAGER_EMAIL=kai@example.com E2E_MANAGER_PASSWORD=demo-pass-123 \
node tools/e2e/smoke.mjs
```

## Estrutura

```
config/
  settings/base.py      tudo lido do ambiente
  settings/dev.py       padrões locais permissivos
  settings/prod.py      falha de forma explícita se faltar um segredo
  formats/              formatos de data por idioma (en, pt_BR)
  urls.py               admin, contas, API, páginas públicas, favicon, robots
  context.py            valores de que todo template precisa
  celery.py             descoberta de tarefas
apps/
  core/                 páginas: biblioteca, vídeo, busca, canal, estúdio,
                        edição, playlists, legendas, gerenciar; template tags;
                        health
  rbac/                 escada de papéis e o único lugar que a interpreta
  users/                modelo de conta, admin, API de sessão
  media/                modelos, regras de acesso, API REST, pipeline ffmpeg,
                        tarefas, entrega com byte ranges, gerador da demo
  uploader/             sessões de upload em partes retomáveis e sua limpeza
templates/              HTML escrito só para este projeto (allauth incluído)
locale/pt_BR/           catálogos em português (fontes .po e .mo compilados)
static/css/app.css      o design system: tokens, temas, layout, componentes
static/css/player.css   a aparência do player
static/js/              app.js (interface), player.js, upload.js
static/brand/           símbolo, logotipos, favicons, ícones de app, imagem OG
static/icons/sprite.svg sprite de ícones (Lucide, veja Licenças)
static/vendor/          builds e fontes de terceiros, com a licença ao lado
tools/build_logo.py     regenera o kit da marca, as fontes e o sprite de ícones
tools/e2e/smoke.mjs     teste de ponta a ponta no navegador
```

## Notas de design

**Streaming é uma escada, não um arquivo.** Cada vídeo é codificado em até três
versões (1080/720/480 ou 720/480/360, conforme a origem) e em um `master.m3u8`
que as lista com resolução, largura de banda e os `CODECS` da RFC 6381, lidos de
volta dos segmentos codificados. A origem nunca é ampliada. O player usa hls.js
em todo lugar, exceto em aparelhos Apple, então o menu de qualidade e o painel
de estatísticas funcionam em qualquer navegador, e ele volta para o arquivo
original se o stream falhar.

**A prévia da barra de progresso é uma requisição só.** O pipeline também grava
um storyboard — uma única folha de sprites com até 100 miniaturas — e os
metadados da grade. Passar o mouse sobre a barra de progresso mostra o quadro
certo sem buscar mais nada.

**Nada na biblioteca parece quebrado.** O áudio ganha a capa embutida ou, na
falta dela, uma forma de onda desenhada nas cores da marca; as imagens ganham
uma miniatura na orientação correta; os PDFs ganham uma prévia da primeira
página. Um `.mp4` que só tem som é reclassificado como áudio. Os pôsteres são
uma conveniência: se não for possível gerar um, o item é publicado mesmo assim.

**A mídia é servida com byte ranges, com segurança.** `apps/media/serving.py`
responde `206` a requisições com `Range` (e `416` às impossíveis). Toda resposta
leva `X-Content-Type-Options: nosniff` e uma CSP de sandbox; o que não for
áudio, vídeo, imagem ou PDF é enviado como download, então um HTML ou SVG
enviado nunca consegue rodar script com os cookies do portal. Para tráfego real,
coloque nginx ou um armazenamento de objetos na frente.

**Visibilidade é dado, lido em um só lugar.** `apps/media/access.py` define quem
pode ver o quê: itens públicos aparecem nas listagens; itens não listados abrem
para quem tem o link (página, embed, legendas, detalhe na API), mas nunca
aparecem em listagens; itens privados são do dono e dos curadores. As páginas de
playlist filtram as entradas com as mesmas regras, então uma playlist pública
não vaza o vídeo privado de ninguém.

**Papéis são dados, não condicionais.** `apps/rbac/roles.py` define a escada e
`apps/rbac/permissions.py` é o único módulo que a lê. Views e templates
perguntam `can_curate_others(user)`, nunca `user.role == "editor"`. Um gestor
pode conceder papéis até o próprio nível, nunca acima dele.

**Uploads sobrevivem a redes ruins.** O navegador envia partes de 5 MB com
XMLHttpRequest (para mostrar progresso, velocidade e tempo restante reais),
tenta cada uma de novo com backoff exponencial, ressincroniza ao receber `409` e
lembra a sessão de cada arquivo, então escolher o mesmo arquivo depois de
recarregar a página continua de onde parou. Os bytes são lidos do stream bruto,
então partes de até 32 MB funcionam independentemente de
`DATA_UPLOAD_MAX_MEMORY_SIZE`. O arquivo parcial é apagado assim que o item de
mídia passa a existir, e o beat remove as sessões abandonadas há mais de
`UPLOAD_SESSION_TTL_HOURS` (ou rode `manage.py purge_uploads`).

**Excluir uma mídia libera o disco.** Remover um item remove a pasta dele —
original, pôster, escada HLS e storyboard — depois que a transação é confirmada.

**Endereços de e-mail são dados pessoais.** As páginas públicas mostram nomes de
exibição; a API só devolve o endereço de um dono ou autor à própria pessoa e aos
curadores.

**O logotipo é feito de curvas, não de texto.** `static/brand/` é gerado por
`tools/build_logo.py`, que converte a fonte Space Grotesk em curvas. O símbolo é
um botão de play cortado em três degraus — a escada de bitrates que a plataforma
codifica. O mesmo script recorta as fontes da interface só com os caracteres
usados, guarda-as no repositório e monta o sprite de ícones, então o site não
depende de nenhuma CDN para funcionar.

**O e-mail é o identificador.** Não existe coluna de nome de usuário em lugar
nenhum. Os endereços são normalizados para minúsculas ao salvar, para que uma
pessoa não consiga ter duas contas só trocando maiúsculas por minúsculas.

**As configurações falham de forma segura.** `config/settings/prod.py` gera um
erro já na importação quando falta `SECRET_KEY` ou `ALLOWED_HOSTS`, em vez de
subir em produção com um valor padrão de desenvolvimento.

## Idiomas

A interface abre em português do Brasil por padrão e também está disponível em
inglês. Todo visitante recebe o `LANGUAGE_CODE` (`pt-br`, a menos que o ambiente
defina outro); o seletor no rodapé e no menu da conta grava outra escolha no
cookie de idioma do Django. O cabeçalho `Accept-Language` do navegador é
ignorado de propósito, para que a interface nunca mude de idioma sem a pessoa
pedir.

Templates, código Python e os três scripts são todos traduzíveis: os scripts
leem os textos do catálogo JavaScript do Django (`/jsi18n/`). Números e datas
também seguem o idioma ("9,4 mil", "20 de out. de 2026"); os formatos compactos
de data ficam em `config/formats/`. Onde o próprio catálogo em português do
django-allauth deixa algo em inglês, `apps/users/translations.py` registra a
mensagem para que o catálogo do projeto possa corrigi-la. Os arquivos `.mo`
compilados ficam no repositório, então rodar o portal não exige nada a mais.
Depois de mudar ou acrescentar textos, gere os catálogos de novo (isso exige o
GNU gettext, por exemplo `apt install gettext`), traduza as entradas novas em
`locale/pt_BR/LC_MESSAGES/*.po` e compile:

```bash
python manage.py makemessages -l pt_BR --ignore=.venv --ignore="static/vendor/*"
python manage.py makemessages -d djangojs -l pt_BR --ignore=.venv --ignore="static/vendor/*"
python manage.py compilemessages -l pt_BR --ignore=.venv
```

## Regenerando o kit da marca

```bash
uv pip install -r requirements-dev.txt     # fonttools, brotli, cairosvg…
python tools/build_logo.py                 # ou: brand | fonts | icons
```

## Ambiente

Veja o `.env.example`. Nada é obrigatório para desenvolvimento local; em
produção são necessários, no mínimo, `SECRET_KEY`, `ALLOWED_HOSTS` e um banco de
dados.

## Licenças

O código da aplicação é MIT. Os arquivos de terceiros mantêm as próprias
licenças, guardadas ao lado deles em `static/vendor/` (veja
`static/vendor/README.md`): hls.js (Apache-2.0), Inter, Space Grotesk e
JetBrains Mono (SIL Open Font License 1.1) e os ícones Lucide usados para montar
`static/icons/sprite.svg` (ISC).
