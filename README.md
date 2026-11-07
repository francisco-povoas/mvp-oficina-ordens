# MVP Oficina — Ordens

Microsserviço responsável por IAM, clientes, veículos, ordens de serviço, diagnóstico, execução e orquestração dos fluxos distribuídos da oficina.

## Estado atual

O esqueleto executável independente da Etapa 1 está disponível. A extração dos módulos do monólito será incremental e preservará os limites descritos em `docs/architecture-boundary.md`.

## Estrutura inicial

- `src/app/`: aplicação FastAPI e futuras camadas de domínio;
- `tests/`: testes próprios do serviço;
- `docs/`: contratos e documentação próprios;
- `compose.yaml`: API e PostgreSQL de desenvolvimento;
- `compose.test.yaml`: testes isolados com PostgreSQL próprio.

O serviço terá PostgreSQL próprio e não poderá consultar bancos, modelos ORM ou entidades de domínio dos demais serviços.

## Comandos

```bash
make install
make lint
make test
make build
docker compose up --build
```

O health check fica disponível em `GET /health` na porta `8001`.
