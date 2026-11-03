# QQplay na Oracle Cloud, de graça

Este guia coloca o QQplay no ar numa máquina da camada gratuita (Always Free)
da Oracle Cloud: um servidor ARM Ampere A1 com 2 núcleos e 12 GB de memória.
Ele sai com HTTPS de verdade, lives pelo OBS e pelo navegador, e a gravação de
cada live vira um vídeo da biblioteca. Conte com cerca de uma hora, quase toda
esperando a Oracle e o DNS.

## O que vai rodar

```
alunos e professores ──HTTPS (443)──► Caddy ──► portal (web)
                                          └───► vídeo das lives (live)
OBS ──────────────────RTMP (1935)───► live
```

| Serviço | Função |
|---|---|
| `caddy` | HTTPS com certificado automático (Let's Encrypt); entrega também os arquivos de vídeo |
| `web` | o portal, e a conversão das transmissões feitas pelo navegador |
| `worker` | codificação dos vídeos enviados e das gravações, um por vez, com prioridade menor que a das lives |
| `tasks` | tarefas rápidas: manutenção das lives e limpeza de uploads abandonados |
| `beat` | o relógio dessas tarefas |
| `live` | recebe o OBS e gera o vídeo das lives (HLS) |
| `db`, `redis` | PostgreSQL, e cache e fila |

Quem faz isso: o [`docker-compose.prod.yml`](../../docker-compose.prod.yml), por
cima do `docker-compose.yml`, o [`Caddyfile`](../Caddyfile) e três scripts:
[`setup-host.sh`](setup-host.sh), [`configure.sh`](../configure.sh) e
[`backup.sh`](../backup.sh).

## Antes de começar

- **Uma conta na Oracle Cloud.** O cadastro pede um cartão para verificação; os
  recursos Always Free não são cobrados. A região inicial (home region) não
  muda depois, e é nela que ficam os recursos gratuitos: para alunos no Brasil,
  escolha São Paulo ou Vinhedo.
- **Um domínio ou subdomínio** cujo DNS você controla, como
  `aulas.suaescola.com.br`. Sem domínio, o [DuckDNS](https://www.duckdns.org)
  dá de graça um subdomínio (`suaescola.duckdns.org`) que também recebe
  certificado.
- **Um terminal com `ssh`**: Linux, macOS ou o PowerShell do Windows 10 em
  diante.

## 1. Crie a máquina

No console da Oracle: **Compute → Instances → Create instance** (em português,
Computação → Instâncias → Criar instância).

- **Image**: Canonical Ubuntu 24.04, a comum (não a Minimal).
- **Shape**: Change shape → Ampere → `VM.Standard.A1.Flex`, com **2 OCPUs e
  12 GB** de memória, o máximo gratuito.
- **Networking**: crie uma rede virtual (VCN) nova, com sub-rede pública, e
  deixe marcada a atribuição de um endereço IPv4 público.
- **SSH keys**: deixe a Oracle gerar o par de chaves e baixe a chave privada
  (ou envie a sua chave pública).
- **Boot volume**: marque o tamanho personalizado e use **200 GB**, todo o
  disco gratuito. Os vídeos moram aqui.

Crie a instância e anote o **IP público** que aparece na página dela.

> Erro "Out of capacity" é comum nas máquinas A1 gratuitas. Escolha outro
> Availability Domain na mesma tela ou tente de novo mais tarde.

## 2. Libere as portas na rede da Oracle

A rede da Oracle só deixa passar SSH (porta 22). Na página da instância, abra a
sub-rede (Subnet) e, nela, a **Default Security List**. Em **Add Ingress
Rules**, crie uma regra para cada linha abaixo, todas com origem (Source CIDR)
`0.0.0.0/0`:

| Protocolo | Porta de destino | Para quê |
|---|---|---|
| TCP | 80 | emissão do certificado e redirecionamento para HTTPS |
| TCP | 443 | o site |
| UDP | 443 | HTTP/3, opcional: deixa o site um pouco mais rápido |
| TCP | 1935 | o OBS |

O firewall da própria máquina, que a Oracle também fecha, é aberto pelo script
do passo 4.

## 3. Aponte o domínio para a máquina

No painel de DNS do seu domínio (Registro.br, Cloudflare, o provedor da
hospedagem…), crie o registro:

| Tipo | Nome | Valor |
|---|---|---|
| A | `aulas` (ou o subdomínio que quiser) | o IP público da instância |

Se o DNS estiver na Cloudflare, deixe a nuvem **cinza** (DNS only): o proxy da
Cloudflare não repassa a porta 1935 do OBS.

No seu computador, confira se o nome já responde com o IP:

```bash
nslookup aulas.suaescola.com.br
```

## 4. Prepare o servidor

Entre na máquina. No Linux e no macOS, proteja a chave antes com
`chmod 600 chave.key`.

```bash
ssh -i chave.key ubuntu@IP-PUBLICO
```

Baixe o QQplay e rode o script de preparação. Ele abre as portas 80, 443 e
1935 no firewall da máquina (também nas regras que voltam a cada boot) e
instala o Docker:

```bash
git clone https://github.com/llypeDev/qqplay.git qqplay
cd qqplay
sh deploy/oracle/setup-host.sh
exit
```

Saia e entre de novo, para usar o `docker` sem `sudo`. Se o repositório for
privado, o `git clone` pede seu usuário do GitHub e, no lugar da senha, um
token de acesso (GitHub → Settings → Developer settings → Personal access
tokens).

## 5. Configure

```bash
cd qqplay
sh deploy/configure.sh aulas.suaescola.com.br voce@suaescola.com.br
```

O script escreve o `.env` com o domínio, as senhas geradas na hora (chave do
Django, banco de dados, segredo das lives) e os endereços de HTTPS e RTMP. O
e-mail fica registrado no Let's Encrypt, o emissor do certificado. Se quiser,
ajuste o arquivo com `nano .env`:

- **E-mail, recomendado**: preencha as linhas `EMAIL_*` com o SMTP do seu
  provedor, para a recuperação de senha chegar. Use a porta 587: a Oracle
  bloqueia a 25.
- **Nome do portal**: `PORTAL_NAME`.
- **Fuso horário**: `TIME_ZONE`, que vem como `America/Sao_Paulo`.

Guarde uma cópia do `.env` fora do servidor. Ele nunca vai para o Git, e a
senha do banco que está nele é a que o banco vai usar para sempre.

## 6. Suba

```bash
docker compose up -d --build
```

Na primeira vez, as imagens são construídas na própria máquina, o que leva
alguns minutos. Depois:

```bash
docker compose ps                  # todos "Up"; web aparece como "healthy"
docker compose logs -f caddy       # espere "certificate obtained successfully" e saia com Ctrl+C
docker compose exec web python manage.py createsuperuser
```

Abra `https://aulas.suaescola.com.br` e entre com o usuário que acabou de criar.

## 7. A primeira live

No portal, **Transmitir → Nova live**. Na sala de controle, escolha como
transmitir:

- **Pelo OBS**: em Configurações → Transmissão, serviço Personalizado, com o
  servidor (`rtmp://aulas.suaescola.com.br/live`) e a chave que a sala de
  controle mostra. Em Saída: 720p, de 2.500 a 3.500 Kbps, intervalo de
  quadro-chave de 2 s e áudio AAC. O servidor não recodifica: os alunos
  recebem exatamente o que o OBS envia, então não exagere na taxa.
- **Pelo navegador**: a aba "Por este navegador" transmite a câmera, ou a tela
  com o microfone, sem instalar nada. Ela exige HTTPS, que agora o site tem. A
  conversão roda no servidor: uma transmissão pelo navegador por vez.

## Atualizar

```bash
cd ~/qqplay
git pull
docker compose up -d --build
docker image prune -f
```

Atualize fora do horário das lives: reiniciar os serviços derruba uma
transmissão em andamento. Um vídeo que estava sendo codificado aparece como
falha, com o motivo, e volta com o botão "Tentar de novo".

## Backup

O script `deploy/backup.sh` salva o banco de dados em `~/qqplay-backups` e
guarda 14 dias de cópias. Para rodá-lo toda noite, abra o agendador com
`crontab -e` e acrescente a linha:

```
30 3 * * * sh $HOME/qqplay/deploy/backup.sh >> $HOME/qqplay-backups/backup.log 2>&1
```

Uma cópia no mesmo disco não sobrevive à perda da máquina. De tempos em tempos,
traga o banco e os vídeos para o seu computador (Linux, macOS ou o WSL do
Windows). O `rsync` só copia o que mudou desde a última vez:

```bash
rsync -av -e "ssh -i chave.key" ubuntu@IP-PUBLICO:qqplay-backups/ ./qqplay-backups/
rsync -av -e "ssh -i chave.key" --rsync-path="sudo rsync" \
  ubuntu@IP-PUBLICO:/var/lib/docker/volumes/qqplay_media/_data/ ./qqplay-media/
```

A restauração está no [OPERATIONS.md](../../OPERATIONS.md#restauração).

## O que esperar de 2 núcleos e 12 GB

| | |
|---|---|
| Alunos numa live | o servidor só repassa o vídeo, sem recodificar; o limite tende a ser a banda, já que cada aluno recebe a taxa do OBS. Não houve teste de carga, mas a conta aponta para algumas centenas |
| Transmissões pelo navegador | uma por vez |
| Vídeos enviados | codificados um por vez, em segundo plano. As gravações do OBS, do Zoom e das lives são as mais leves: a versão mais alta é a própria gravação, copiada sem recodificar. Um vídeo de celular em 1080p, com muito movimento, é o caso mais pesado |
| Disco | uns 150 GB livres para vídeos. Uma hora em 1080p, contando o original e as versões, ocupa de uns 2 GB (aula com slides gravada no OBS ou no Zoom) a uns 10 GB (vídeo de celular com muito movimento): de 15 a 70 horas de vídeo |
| Tráfego | a Oracle dá 10 TB de saída por mês, umas 7.000 horas-aluno a 3 Mbps |

Acompanhe o disco com `df -h /` e `docker system df`.

## Cuidados com a camada gratuita

- **Máquina ociosa pode ser recuperada.** A Oracle pode recuperar uma instância
  Always Free que passe 7 dias com o uso de CPU (percentil 95), de rede e de
  memória abaixo de 20%. Um portal com pouco movimento pode cair nesse
  critério. Muitos usuários relatam que converter a conta para Pay As You Go
  evita isso, e os recursos Always Free continuam sem custo nesse modo, mas
  confira a regra atual na documentação da Oracle: na Pay As You Go, o que
  passar dos limites gratuitos é cobrado. Com o backup em dia, perder a máquina
  não é perder as aulas.
- **Fique dentro dos limites**: o shape A1 com 2 OCPUs e 12 GB e os 200 GB de
  disco deste guia já são o máximo gratuito.

## Problemas comuns

| Sintoma | O que verificar |
|---|---|
| O certificado não sai (`docker compose logs caddy` mostra erros de ACME) | o domínio aponta para o IP da instância? As portas 80 e 443 estão na Security List? |
| O OBS não conecta | a porta 1935 está na Security List? O servidor e a chave vieram da sala de controle? |
| Erro 403 de CSRF ao entrar | o endereço no navegador é exatamente o do `PORTAL_DOMAIN`, no `.env`? |
| Erro 502 logo depois de subir | o portal ainda está aplicando as migrações; espere um minuto |
| A aba "Por este navegador" pede HTTPS | abra o site pelo endereço com `https://` |

`docker compose logs <serviço>` mostra o que cada parte está fazendo, por
exemplo `docker compose logs web` ou `docker compose logs live`.

## Segurança

- **O RTMP não é criptografado**, como na maioria dos servidores próprios: a
  chave da live viaja aberta entre o OBS e o servidor. Se ela vazar, gere outra
  na sala de controle. A transmissão pelo navegador vai por HTTPS.
- **Os arquivos de vídeo não pedem login.** Quem tiver o endereço exato de um
  arquivo consegue baixá-lo, mesmo de um vídeo privado. Esses endereços levam
  um código aleatório e só aparecem para quem pode ver o vídeo.
- **O `.env` guarda todas as senhas.** Não o publique em lugar nenhum.
- **O Ubuntu instala sozinho as atualizações de segurança.** De vez em quando,
  rode `sudo apt update && sudo apt upgrade` e reinicie com `sudo reboot`; os
  serviços voltam sozinhos.
- **HSTS**: com tudo funcionando, você pode acrescentar
  `SECURE_HSTS_SECONDS=31536000` ao `.env` e rodar `docker compose up -d`. Os
  navegadores passam a recusar o site sem HTTPS por um ano, então só ligue
  depois de ter certeza de que o domínio fica.
