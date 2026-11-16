# MVP Oficina — Ordens

Microsserviço responsável por identidade e acesso, clientes, veículos, ordens de
serviço (OS), diagnóstico, execução e orquestração das operações distribuídas da
oficina.

## Sumário

- [Responsabilidades](#responsabilidades)
- [Tecnologias e persistência](#tecnologias-e-persistência)
- [Ciclo de vida da OS](#ciclo-de-vida-da-os)
- [Integrações e Saga](#integrações-e-saga)
- [Autenticação e autorização](#autenticação-e-autorização)
- [Execução local](#execução-local)
- [Swagger](#swagger)
- [Testes e cobertura](#testes-e-cobertura)
- [Documentação relacionada](#documentação-relacionada)

## Responsabilidades

| Módulo | Responsabilidade |
|---|---|
| IAM | usuários, perfis, login, emissão de JWT e credenciais internas de serviço |
| Clientes | cadastro, consulta, atualização, ativação e desativação |
| Veículos | cadastro e vínculo com clientes |
| Ordens de serviço | recepção, diagnóstico, itens e serviços aplicados, execução, finalização e entrega |
| Integração distribuída | Saga de reserva, operações de estoque, projeção financeira, Outbox, Inbox e reconciliação |

Ordens não mantém catálogo, saldo de estoque, orçamento ou pagamento. Os dados de
serviços e peças usados por uma OS são snapshots; Recursos e Financeiro continuam
sendo as autoridades de seus próprios agregados.

## Tecnologias e persistência

- Python 3.12, FastAPI, Pydantic e Uvicorn;
- PostgreSQL 16, SQLModel/SQLAlchemy e Alembic;
- boto3 com SQS FIFO em produção ou ElasticMQ no ambiente local;
- JWT HS256 com `python-jose` e hashes de senha com bcrypt;
- JSON Schema para mensagens versionadas;
- OpenTelemetry para traces e métricas;
- Pytest, pytest-cov e Ruff.

O PostgreSQL de Ordens é exclusivo. Nele ficam IAM, clientes, veículos, OS,
snapshots, estados operacionais dos itens, Saga, operações distribuídas, Outbox,
Inbox e a projeção mínima de orçamento/pagamento. Não existem consultas aos bancos
de Recursos ou Financeiro.

## Ciclo de vida da OS

Fluxo principal:

```text
RECEBIDA → EM_DIAGNOSTICO → DIAGNOSTICO_CONCLUIDO
         → AGUARDANDO_APROVACAO → APROVADA → EM_EXECUCAO
         → FINALIZADA → ENTREGUE
```

Variações implementadas:

- aprovação com peça pendente leva a `AGUARDANDO_ITENS`; o recebimento de todas as
  peças pendentes leva a `APROVADA`;
- recusa do orçamento parte de `AGUARDANDO_APROVACAO`, libera reservas quando
  necessário e termina em `ENCERRADA`;
- `ENTREGUE` e `ENCERRADA` são estados terminais;
- iniciar a execução exige ausência de peça `A_RECEBER` e de operação pendente;
- finalizar exige tempo registrado para os serviços ativos;
- entregar exige pagamento confirmado e consumo confirmado de cada reserva em uso.

O diagrama completo está em
[Ciclo de vida da OS](../mvp-oficina-infra/docs/diagrams/ciclo-vida-os.md).

## Integrações e Saga

### Recursos

Consultas de catálogo são REST síncronas, autenticadas com token de serviço. As
alterações de estoque são assíncronas por filas FIFO:

- reserva: `ReserveItem.v1` → `ResourceReserved.v1`, `ItemBackordered.v1` ou
  `ReservationRejected.v1`;
- compensação: `ReleaseReservation.v1` → `ReservationReleased.v1`;
- recebimento: `ReceiveBackorderedItem.v1` → `BackorderedItemReceived.v1`;
- consumo: `ConsumeReservation.v1` → `ReservationConsumed.v1`.

Ordens orquestra e persiste a Saga de inclusão de peça. A criação da operação e o
comando de reserva são gravados na mesma transação local. Cada operação usa
`operation_id`, e Outbox/Inbox suportam entrega *at-least-once* sem repetir o efeito
de negócio. Estoque insuficiente é um resultado válido (`A_RECEBER`), não uma falha.
O cancelamento de uma inclusão pendente libera uma reserva já criada e termina a
Saga como `COMPENSATED`.

### Financeiro

Financeiro consulta por REST os contextos de orçamento e pagamento. Seus fatos
duráveis chegam pela fila Financeiro → Ordens:

- `QuoteCommunicated.v1` leva a OS para `AGUARDANDO_APROVACAO`;
- `QuoteApproved.v1` leva para `APROVADA` ou `AGUARDANDO_ITENS`;
- `QuoteRejected.v1` inicia cancelamentos/liberações e encerra a OS;
- `PaymentConfirmed.v1` atualiza a projeção usada na entrega.

Um worker *one-shot* também reconcilia fatos financeiros por REST sem presumir
sucesso quando Financeiro está indisponível.

## Autenticação e autorização

Ordens autentica usuários e emite JWT HS256. As APIs públicas dos três serviços
validam localmente assinatura, emissor, audiência, expiração e perfis. Os perfis
implementados são `Administrador`, `Atendente` e `Mecanico`; cada rota declara os
perfis aceitos.

As rotas `/internal/v1` não usam o token de usuário. Elas exigem JWT curto de serviço
com `token_use`, audiência e escopo. Segredos, senhas e `SERVICE_CLIENTS_JSON` devem
ser fornecidos por variáveis de ambiente ou Secret e nunca versionados.

## Execução local

O caminho recomendado para executar todo o sistema é o Kubernetes local descrito no
[README de Infra](../mvp-oficina-infra/README.md#subir-o-ambiente-no-minikube).

Para preparar o serviço no host:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
make install
```

As variáveis estão documentadas em `.env.example`. A API requer PostgreSQL migrado,
os dados obrigatórios do primeiro administrador e credenciais internas. O processo
runtime executado pelo container aplica Alembic, realiza o bootstrap idempotente do
IAM e inicia o Uvicorn por `scripts/start.sh`.

Para validar a integração real da Saga entre Ordens e Recursos, mantendo os quatro
repositórios como diretórios irmãos:

```bash
./scripts/test-distributed-saga.sh
```

## Swagger

Com o Checkpoint A ativo, abra um terminal e mantenha o port-forward em execução:

```bash
kubectl --context=minikube -n mvp-oficina port-forward service/ordens-api 8001:8001
```

Swagger UI: <http://127.0.0.1:8001/docs>

OpenAPI JSON: <http://127.0.0.1:8001/openapi.json>

O Kong não publica `/docs`, `/openapi.json` nem `/internal/v1`; use o Service
`ordens-api` diretamente.

## Testes e cobertura

```bash
make lint
make test
```

`make test` executa `pytest` com cobertura de branches sobre `src/app`; o gate
configurado em `pyproject.toml` falha abaixo de 80% e mostra as linhas não cobertas.
Para a suíte isolada com PostgreSQL real e ciclo Alembic:

```bash
docker compose -f compose.test.yaml run --rm --build tests
docker compose -f compose.test.yaml down --volumes --remove-orphans
```

## Documentação relacionada

- [Arquitetura distribuída](../mvp-oficina-infra/docs/diagrams/arquitetura.md)
- [Saga e comunicação](../mvp-oficina-infra/docs/diagrams/saga-comunicacao.md)
- [Contratos de Ordens](contracts/README.md)
- [README principal de Infra](../mvp-oficina-infra/README.md)
