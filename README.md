# MVP Oficina — Ordens

Microsserviço responsável por IAM, clientes, veículos, ordens de serviço, diagnóstico, execução e orquestração dos fluxos distribuídos da oficina.

## Estado atual

Este repositório contém somente a estrutura mínima inicial da Etapa 1 da Fase 4. Nenhum módulo do monólito foi segregado ainda.

## Estrutura inicial

- `src/`: código-fonte futuro do serviço;
- `tests/`: testes próprios do serviço;
- `docs/`: contratos e documentação próprios.

O serviço terá PostgreSQL próprio e não poderá consultar bancos, modelos ORM ou entidades de domínio dos demais serviços.
