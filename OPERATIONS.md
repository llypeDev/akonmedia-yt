# Operações

Tarefas do dia a dia de uma instância do AkonMedia em funcionamento.

## Onde ficam os seus dados

Em dois volumes do Docker. Se eles se perderem, a biblioteca se perde junto.

| Volume | Conteúdo |
|---|---|
| `akonmedia_postgres` | contas, registros de mídia, comentários, playlists, legendas |
| `akonmedia_media` | uploads originais, pôsteres, segmentos HLS |

Um terceiro volume, `akonmedia_live`, guarda só o que está em andamento: o HLS
das lives no ar e as gravações que ainda não viraram vídeo. Ele não precisa de
backup, mas não o apague durante uma live.

Em produção (`docker-compose.prod.yml`), o `akonmedia_caddy_data` guarda os
certificados HTTPS. Sem ele, o Caddy pede certificados novos, então também
dispensa backup; só não o apague à toa, porque o Let's Encrypt limita quantos
certificados um domínio recebe por semana.

## Backup

Os dois, juntos, com a stack rodando:

```bash
docker compose exec -T db pg_dump -U akonmedia -Fc akonmedia > akonmedia-$(date +%F).dump
docker run --rm -v akonmedia_media:/data -v "$PWD":/backup alpine \
  tar czf /backup/media-$(date +%F).tar.gz -C /data .
```

Guarde os dois arquivos juntos. Um dump do banco sem o volume de mídia restaura
um catálogo de links quebrados; a mídia sem o banco é uma pasta que ninguém
consegue navegar.

O `deploy/backup.sh` faz a parte do banco e guarda 14 dias de cópias em
`~/akonmedia-backups` (`BACKUP_DIR` e `KEEP_DAYS` mudam isso); agende-o no cron
para toda noite. Copiar o volume de mídia inteiro todo dia para o mesmo disco
só ocupa espaço: leve-o para outra máquina com `rsync`, que copia apenas o que
mudou. Os comandos estão no [guia da Oracle](deploy/oracle/README.md#backup).

## Restauração

```bash
docker compose up -d db
docker compose exec -T db pg_restore -U akonmedia -d akonmedia --clean --if-exists < akonmedia-2026-11-03.dump
docker run --rm -v akonmedia_media:/data -v "$PWD":/backup alpine \
  sh -c "rm -rf /data/* && tar xzf /backup/media-2026-11-03.tar.gz -C /data"
docker compose up -d
```

O container web aplica as migrações pendentes ao iniciar, então é seguro
restaurar um dump feito antes de uma atualização.

## Verificação depois da restauração

```bash
docker compose ps                      # todos os serviços com status Up
curl -s localhost:8000/health/         # {"status": "ok"}
```

Em produção, o portal não fica exposto na porta 8000: confira pelo domínio,
com `curl -s https://seu-dominio/health/`.

Depois abra a biblioteca e confira três itens por amostragem: pôster, reprodução
e faixa de legenda.

## Cuidados de rotina

- **O armazenamento cresce, e isso é esperado.** Cada vídeo mantém o original e
  mais uma cópia por versão (rendition). Confira o `docker system df` todo mês.
- **Codificações com falha ficam visíveis.** Tudo o que não pôde ser processado
  aparece em `/manage/`, na aba *Com falha*, com o motivo. Nada fica parado na
  fila sem aviso: uma codificação interrompida por uma reinicialização (uma
  atualização, a máquina caindo) é marcada como falha quando o worker volta, e
  sai com "Tentar de novo".
- **Atualizações**: `git pull` e `docker compose up -d --build`, fora do horário
  das lives, porque reiniciar os serviços derruba uma transmissão em andamento.
  Depois, `docker image prune -f` libera o espaço das imagens antigas.
- **A chave secreta** do `.env` assina as sessões. Trocá-la desconecta todo
  mundo.
- **Gravações de live que falharam ficam guardadas.** Se uma gravação não puder
  ser publicada, a sala de controle da live mostra o motivo, e os arquivos de
  cada conexão continuam em `akonmedia_live`, na pasta `recordings`. Apague-os
  depois de recuperar o que precisar.
- **O segredo das lives** (`LIVE_HOOK_SECRET`) precisa ser o mesmo no portal e
  no serviço `live`. Depois de trocá-lo, reinicie os dois.

## Uma coisa que não é uma tarefa

Este repositório não contém código de nenhum sistema anterior, e a licença dele
diz isso com precisão. Se isso mudar algum dia — um arquivo copiado de outro
lugar —, a licença tem que mudar junto, no mesmo commit.
