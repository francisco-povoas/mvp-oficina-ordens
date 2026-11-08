# MVP Oficina — Ordens

Microsserviço da Fase 4 responsável por IAM, clientes, veículos e ordens de serviço,
incluindo diagnóstico, execução, máquina de estados da OS e estados operacionais dos
itens. O serviço não é dono de catálogo, estoque, orçamento ou pagamento.

## Estado da Etapa 1

Os quatro módulos (`iam`, `clientes`, `veiculos` e `ordens_servico`) estão extraídos e
organizados por `domain`, `application`, `infrastructure` e `presentation`, com ports e
adapters locais. O serviço usa PostgreSQL próprio, SQLModel e migrations Alembic; não
consulta bancos ou modelos dos outros microsserviços.

A Outbox possui somente tabela/modelo estrutural. Publicação, polling, retry, Inbox,
workers, SQS/ElasticMQ e a Saga executável ainda não existem e pertencem à Etapa 2.

## APIs, autenticação e contratos

- Rotas públicas usam JWT de usuário emitido pelo próprio módulo IAM e aplicam os
  perfis da oficina.
- Rotas `/internal/v1` distinguem tokens de serviço por `token_use`, audiência e
  escopo. Ordens também fornece o endpoint interno de emissão dessas credenciais.
- Os contratos versionados em `contracts/` descrevem contextos internos de orçamento e
  pagamento, envelope/erros comuns e mensagens da futura Saga com Recursos.
- A dependência de Recursos está expressa por ports. Os adapters atuais são
  deliberadamente *fail-closed*: operações que exigem catálogo, reserva, recebimento,
  liberação ou consumo retornam indisponibilidade em vez de simular integração.
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
| `alembic/` | baseline exclusivo do PostgreSQL de Ordens |
| `contracts/` | OpenAPI e JSON Schemas versionados para integrações futuras |
| `tests/` | testes unitários, de contratos e de integração PostgreSQL |
| `.github/workflows/ci.yml` | Ruff, suítes com cobertura mínima de 80% e build da imagem |

Os manifests Kubernetes específicos serão materializados somente na Etapa 4, junto da
preparação do deployment AWS. A Etapa 1 não contém placeholders Kubernetes.

## Execução e validação

Requer Python 3.12. Para desenvolvimento local, copie as variáveis de exemplo sem
versionar segredos.

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

O health check fica em `GET /health` na porta `8001`. O CI executa lint, testes
unitários, contratuais e de integração, gate global de cobertura e build sem publicar a
imagem.

## Próxima etapa funcional

A Etapa 2 implementará REST entre serviços, Outbox/Inbox funcionais, workers,
SQS/ElasticMQ e a Saga de reserva/compensação. Nenhum desses componentes está sendo
apresentado aqui como integração concluída.
