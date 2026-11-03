# QQplay pelo Dokploy

Este guia instala o QQplay por dentro do [Dokploy](https://dokploy.com), numa
máquina que já o roda. É o caminho quando o Traefik do Dokploy já ocupa as
portas 80 e 443 e o guia da [Oracle Cloud](../oracle/README.md), que põe o Caddy
nessas portas, esbarraria nele.

## Como fica

```
alunos e professores ──HTTPS (443)──► Traefik do Dokploy ──► caddy ──► portal (web)
                                                                └───► vídeo das lives (live)
OBS ──────────────────RTMP (1935)───► live
```

- **O Traefik do Dokploy** emite o certificado (Let's Encrypt) e atende em 80 e
  443, como já faz para as outras aplicações da máquina.
- **O Caddy do QQplay** fica só na rede interna, em HTTP. Ele continua mandando
  `/hls` para o servidor das lives e entregando os arquivos de vídeo direto do
  volume, sem ocupar o portal.
- **A porta 1935**, do OBS, é a única que o QQplay publica.

Quem faz isso: o [`docker-compose.dokploy.yml`](../../docker-compose.dokploy.yml),
a mesma stack do `docker-compose.prod.yml` num arquivo só, e o
[`Caddyfile.proxy`](../Caddyfile.proxy). Num arquivo só porque o Dokploy
implanta um único compose, e o relê e regrava para acrescentar as etiquetas do
Traefik.

## Antes de começar

- **O Dokploy instalado e funcionando** na máquina.
- **Um domínio ou subdomínio** com um registro `A` apontando para o IP público
  da máquina. Se o DNS estiver na Cloudflare, deixe a nuvem **cinza** (DNS only).
- **A porta TCP 1935 liberada** na rede do provedor, para o OBS. Na Oracle Cloud
  é na Security List da sub-rede, como no
  [passo 2 do guia da Oracle](../oracle/README.md#2-libere-as-portas-na-rede-da-oracle).
  As portas 80 e 443 já devem estar abertas, porque o Dokploy usa as duas.
- **Acesso ao repositório pelo Dokploy.** Se o repositório for privado, conecte o
  GitHub em **Settings → Git** no Dokploy antes do passo 2.

## 1. Gere as variáveis de ambiente

Entre na máquina por SSH e rode o bloco abaixo, trocando o domínio pelo seu. Ele
só imprime as variáveis na tela, com senhas geradas na hora, e não grava nada:

```bash
D=aulas.suaescola.com.br; cat <<EOF
SECRET_KEY=$(openssl rand -hex 32)
ALLOWED_HOSTS=$D,web,localhost,127.0.0.1
CSRF_TRUSTED_ORIGINS=https://$D
SITE_URL=https://$D
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
SECURE_SSL_REDIRECT=False
MEDIA_ACCEL_REDIRECT=/_media/
TIME_ZONE=America/Sao_Paulo
POSTGRES_PASSWORD=$(openssl rand -hex 24)
LIVE_HOOK_SECRET=$(openssl rand -hex 24)
LIVE_RTMP_URL=rtmp://$D/live
LIVE_HLS_URL=https://$D/hls
EOF
```

Copie o resultado. Ele vai para o Dokploy no passo 3. Guarde também uma cópia
num lugar seguro: a `POSTGRES_PASSWORD` é a senha que o banco vai usar para
sempre.

Se quiser, acrescente:

- **`PORTAL_NAME=...`**: o nome do portal.
- **`EMAIL_HOST`, `EMAIL_PORT=587`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` e
  `DEFAULT_FROM_EMAIL`**: o SMTP para os e-mails de recuperação de senha chegarem.
  A Oracle bloqueia a porta 25, então use a 587.

## 2. Crie o serviço no Dokploy

No painel do Dokploy:

1. **Projects → Create Project**, com o nome `qqplay`. Também dá para usar um
   projeto que já exista.
2. Dentro do projeto, **Create Service → Compose**:
   - **Name**: `qqplay`. Os comandos deste guia procuram os containers por esse
     nome.
   - **Compose Type**: Docker Compose (não Stack).
3. Na aba **General**, em **Provider**:
   - **GitHub**: escolha o repositório e a branch `main`. Para o Dokploy
     implantar sozinho a cada `git push`, ligue **Autodeploy**.
   - **Git**, se o repositório for público: use a URL
     `https://github.com/llypeDev/qqplay.git` e a branch `main`.
   - **Compose Path**: `./docker-compose.dokploy.yml`. Esse campo é o que
     importa: o `docker-compose.yml` padrão não serve aqui.

   Clique em **Save**.

## 3. Cole as variáveis

Na aba **Environment**, cole o que o passo 1 imprimiu e clique em **Save**.

O Dokploy grava essas variáveis num `.env` ao lado do compose, e o QQplay o lê
por cima dos padrões do `.env.docker`. Sem a `POSTGRES_PASSWORD`, a implantação
para com um erro que avisa. Assim, o banco nunca é criado com uma senha padrão.

## 4. Configure o domínio

Na aba **Domains**, clique em **Add Domain**:

| Campo | Valor |
|---|---|
| Service Name | `caddy` |
| Host | `aulas.suaescola.com.br` |
| Path | `/` |
| Container Port | `80` |
| HTTPS | ligado |
| Certificate Provider | Let's Encrypt |

Crie o domínio antes da primeira implantação. Num compose, o Dokploy só aplica
uma mudança de domínio na implantação seguinte.

## 5. Implante

Na aba **General**, clique em **Deploy** e acompanhe em **Deployments**. Na
primeira vez, as imagens são construídas na própria máquina, o que leva alguns
minutos.

Depois, crie o administrador. Por SSH na máquina:

```bash
docker exec -it $(docker ps -q -f label=com.docker.compose.service=web -f name=qqplay) python manage.py createsuperuser
```

Abra `https://aulas.suaescola.com.br` e entre com o usuário que acabou de criar.
A primeira live segue o [passo 7 do guia da Oracle](../oracle/README.md#7-a-primeira-live).

## Atualizar

Com **Autodeploy** ligado, cada `git push` na branch implanta sozinho. Sem ele,
clique em **Deploy** de novo.

Atualize fora do horário das lives: a implantação reinicia os serviços e
derruba uma transmissão em andamento. Um vídeo que estava sendo codificado
aparece como falha, com o motivo, e volta com o botão "Tentar de novo".

## Backup

O Dokploy guarda o código em `/etc/dokploy/compose/<App Name>/code`. O
**App Name** aparece no topo da página do serviço, com um sufixo aleatório,
como `qqplay-a1b2c3`. O `deploy/backup.sh` funciona a partir dali. Teste uma vez
por SSH, trocando o App Name pelo seu:

```bash
COMPOSE_FILE=docker-compose.dokploy.yml sh /etc/dokploy/compose/qqplay-a1b2c3/code/deploy/backup.sh
```

Se a cópia sair em `~/qqplay-backups`, agende o backup para toda noite. Abra o
agendador com `crontab -e` e acrescente a linha:

```
30 3 * * * COMPOSE_FILE=docker-compose.dokploy.yml sh /etc/dokploy/compose/qqplay-a1b2c3/code/deploy/backup.sh >> $HOME/qqplay-backups/backup.log 2>&1
```

Os vídeos ficam no volume `<App Name>_media`. Para trazer o banco e os vídeos
para o seu computador, use os comandos do
[guia da Oracle](../oracle/README.md#backup), trocando `qqplay_media` pelo nome
desse volume. Para conferir o nome, rode `docker volume ls | grep media`.

## Problemas comuns

| Sintoma | O que verificar |
|---|---|
| A implantação para em "set POSTGRES_PASSWORD" | as variáveis do passo 1 estão salvas na aba Environment? |
| "port is already allocated" na porta 1935 | outro serviço da máquina já usa a 1935: `sudo ss -tlnp \| grep 1935` |
| O navegador mostra "404 page not found" | o domínio aponta para o serviço `caddy`, porta 80? Houve uma implantação depois de criar o domínio? O botão **Preview Compose** mostra as etiquetas que o Dokploy acrescentou |
| O certificado não sai | o registro `A` aponta para esta máquina? O que diz `docker logs dokploy-traefik --since 10m`? |
| Erro 502 logo depois de implantar | o portal ainda está aplicando as migrações. Espere um minuto |
| Erro 403 de CSRF ao entrar | o endereço no navegador é exatamente o domínio das variáveis `SITE_URL` e `CSRF_TRUSTED_ORIGINS`? |
| O OBS não conecta | a porta TCP 1935 está liberada na rede do provedor? |

Os logs de cada parte ficam na aba **Logs** do serviço. Por SSH, o comando é
`docker logs <container>`, e `docker ps` mostra os nomes dos containers.

## Segurança

Valem os cuidados do [guia da Oracle](../oracle/README.md#segurança): o RTMP não
é criptografado, os arquivos de vídeo não pedem login a quem tem o endereço
exato, e a aba Environment do Dokploy guarda todas as senhas.
