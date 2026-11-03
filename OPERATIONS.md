# Operações

Tarefas do dia a dia de uma instância do AkonMedia em funcionamento.

## Onde ficam os seus dados

Em dois volumes do Docker. Se eles se perderem, a biblioteca se perde junto.

| Volume | Conteúdo |
|---|---|
| `akonmedia_postgres` | contas, registros de mídia, comentários, playlists, legendas |
| `akonmedia_media` | uploads originais, pôsteres, segmentos HLS |

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
docker compose ps                      # cinco serviços, web e worker com status Up
curl -s localhost:8000/health/         # {"status": "ok"}
```

Depois abra a biblioteca e confira três itens por amostragem: pôster, reprodução
e faixa de legenda.

## Cuidados de rotina

- **O armazenamento cresce, e isso é esperado.** Cada vídeo mantém o original e
  mais uma cópia por versão (rendition). Confira o `docker system df` todo mês.
- **Codificações com falha ficam visíveis.** Tudo o que não pôde ser processado
  aparece em `/manage/`, na aba *Com falha*, com o motivo. Nada fica parado na
  fila sem aviso.
- **A chave secreta** do `.env` assina as sessões. Trocá-la desconecta todo
  mundo.

## Uma coisa que não é uma tarefa

Este repositório não contém código de nenhum sistema anterior, e a licença dele
diz isso com precisão. Se isso mudar algum dia — um arquivo copiado de outro
lugar —, a licença tem que mudar junto, no mesmo commit.
