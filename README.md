# MVP Oficina — Ordens

Microsserviço da Fase 4 responsável por IAM, clientes, veículos e ordens de serviço,
incluindo diagnóstico, execução, máquina de estados da OS e estados operacionais dos
itens. O serviço não é dono de catálogo, estoque, orçamento ou pagamento.

## Estado da Etapa 1

Os quatro módulos (`iam`, `clientes`, `veiculos` e `ordens_servico`) estão extraídos e
organizados por `domain`, `application`, `infrastructure` e `presentation`, com ports e
adapters locais. O serviço usa PostgreSQL próprio, SQLModel e migrations Alembic; não
consulta bancos ou modelos dos outros microsserviços.

A Outbox transacional, o publisher e os consumers técnicos independentes estão ativos
na Etapa 2. O publisher reivindica lotes com lease e `FOR UPDATE SKIP LOCKED`, valida o
envelope contra o JSON Schema v1 e publica os quatro comandos de Ordens na fila FIFO de
Recursos. Os consumers validam e recebem os resultados de Recursos e os eventos de
Financeiro com Inbox persistente. Os resultados da Saga de reserva e compensação já
possuem handler funcional; mensagens de estoque e eventos financeiros ainda não
implementados permanecem *fail-closed* e não recebem ACK.

## APIs, autenticação e contratos

- Rotas públicas usam JWT de usuário emitido pelo próprio módulo IAM e aplicam os
  perfis da oficina.
- Rotas `/internal/v1` distinguem tokens de serviço por `token_use`, audiência e
  escopo. Ordens também fornece o endpoint interno de emissão dessas credenciais.
- Os contratos versionados em `contracts/` descrevem contextos internos de orçamento e
  pagamento, envelope/erros comuns e mensagens da futura Saga com Recursos.
- A dependência de Recursos está expressa por ports. Os adapters atuais são
  deliberadamente *fail-closed* nos fluxos ainda não conectados a consumers: operações
  que exigem reserva, recebimento, liberação ou consumo não simulam integração.
- A reconciliação com Financeiro e os eventos financeiros são contratos planejados;
  não há comunicação distribuída funcional na Etapa 1.

Assim, fluxos puramente locais funcionam, mas operações que atravessam Recursos ou
Financeiro só serão integradas na Etapa 2. Os arquivos de contrato não significam que
REST distribuído ou mensageria já estejam ativos.

## Estrutura

| Caminho | Responsabilidade |
|---|---|
| `src/app/modules/` | IAM, clientes, veículos e ordens em Clean Architecture modular |
| `src/app/integrations/` | ports, comandos e adapters fail-closed para Recursos |
| `alembic/` | baseline e tabelas de Outbox/Inbox no PostgreSQL de Ordens |
| `contracts/` | OpenAPI, JSON Schemas próprios e cópia vendorizada do contrato financeiro consumido |
| `tests/` | testes unitários, de contratos e de integração PostgreSQL |
| `.github/workflows/ci.yml` | Ruff, suítes com cobertura mínima de 80% e build da imagem |

Os manifests Kubernetes específicos serão materializados somente na Etapa 4, junto da
preparação do deployment AWS. A Etapa 1 não contém placeholders Kubernetes.

## Execução e validação

Requer Python 3.12. Para desenvolvimento local, copie as variáveis de exemplo sem
versionar segredos. Na inicialização, a API aplica as migrations e executa um bootstrap
idempotente que garante os perfis `Administrador`, `Atendente` e `Mecanico`. Se ainda
não houver administrador ativo, `ADMIN_NOME`, `ADMIN_EMAIL` e `ADMIN_PASSWORD` são
obrigatórios; reinicializações preservam usuário, perfis e hash existentes.

```bash
make install
make lint
make test
make build
docker compose up --build
```

Para a suíte isolada com PostgreSQL e ciclo Alembic:

```bash
docker compose -f compose.test.yaml run --rm --build tests
docker compose -f compose.test.yaml down --volumes --remove-orphans
```

Para validar a Saga real entre Ordens e Recursos com duas bases PostgreSQL, ElasticMQ
e os quatro workers, mantenha os repositórios como diretórios irmãos e execute:

```bash
./scripts/test-distributed-saga.sh
```

O runner remove containers e volumes temporários ao terminar, inclusive em caso de
falha.

O health check fica em `GET /health` na porta `8001`. O CI executa lint, testes
unitários, contratuais e de integração com PostgreSQL/ElasticMQ, gate global de
cobertura e build sem publicar a imagem.

## Publisher da Outbox

O publisher é outro processo da mesma imagem e não depende do FastAPI. Ele exige as
variáveis `DATABASE_URL`, `AWS_REGION`, `SQS_ORDENS_TO_RECURSOS_URL` e, somente em
desenvolvimento/testes, `SQS_ENDPOINT_URL` e credenciais fictícias do ElasticMQ.
Timeouts, tamanho do lote, lease, polling e backoff estão documentados em
`.env.example`. Depois das migrations, execute:

```bash
python -m app.publisher
# ou, dentro da imagem runtime:
./scripts/start-publisher.sh
```

SIGTERM e SIGINT interrompem novos polls e permitem concluir a mensagem em andamento.
A publicação é at-least-once: se o broker aceitar a mensagem e a confirmação local
falhar, o lease expira e uma nova instância pode reenviar o mesmo `message_id`.

## Consumers e Inbox

O processo `app.consumer` hospeda dois consumers lógicos: resultados de Recursos e
eventos financeiros. Cada mensagem é validada pelo contrato da origem antes de entrar
na transação PostgreSQL. A mesma transação grava a Inbox, o marcador idempotente do
efeito e o efeito executado pelo handler. A deduplicação cobre tanto reentrega do mesmo
`message_id` quanto uma nova emissão do mesmo efeito de negócio; payload divergente
para a mesma chave é rejeitado como conflito.

O transporte usa entrega at-least-once, concorrência limitada, renovação de
visibilidade durante processamento e ACK somente depois do commit. Falhas permanecem
disponíveis para retry e seguem a redrive policy da fila até a DLQ. Para executar:

```bash
python -m app.consumer
# ou, dentro da imagem runtime:
./scripts/start-consumer.sh
```

Por segurança, a composição padrão ativa somente o handler da Saga implementada.
Tipos ainda sem handler falham sem ACK e permanecem disponíveis para retry/DLQ.
SIGTERM e SIGINT encerram novos polls e aguardam o trabalho em andamento.

## Reconciliação financeira

A reconciliação é um processo one-shot independente do FastAPI. Ela seleciona apenas
OS elegíveis e vencidas, usa paginação por cursor, lote e concorrência limitados,
consulta `financial-status` com JWT de serviço e registra o resultado persistente por
OS. Timeout ou indisponibilidade não criam decisão financeira; a próxima execução
externa tenta novamente depois do intervalo configurado.

```bash
python -m app.financial_reconciliation_worker
```

As variáveis `RECONCILIATION_*` em `.env.example` controlam lote, número máximo de
páginas, concorrência, timeout, idade mínima e intervalo de nova verificação. O
processo aceita SIGTERM/SIGINT, conclui o trabalho já iniciado e não contém scheduler
próprio nem requer CronJob Kubernetes.

## Próxima etapa funcional

A Etapa 2 permanece em aceite; a Etapa 3 não é iniciada automaticamente.
