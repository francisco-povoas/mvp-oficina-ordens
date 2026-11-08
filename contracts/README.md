# Contratos versionados de Ordens

Este diretório é fonte de contrato da Etapa 1, não uma integração distribuída ativa.

- `messages/saga-stock.v1.schema.json`: os seis comandos/eventos da Saga e os quatro contratos das operações distribuídas de estoque.
- `messages/saga-stock.v1.examples.json`: um exemplo válido por tipo.
- `openapi/internal-v1.openapi.json`: emissão de token interno e consultas funcionais `quote-context`/`payment-context` da Etapa 2.
- `common/`: envelope e erro HTTP v1.

Compatibilidade v1 aceita apenas campos já definidos; campos adicionais são rejeitados. Alteração incompatível exige v2. `message_id` deduplica uma emissão, `operation_id` identifica o efeito idempotente, `correlation_id` propaga a requisição e `causation_id` liga mensagens. `saga_id` é obrigatório na Saga e nulo nas coordenações leves. Publicação, consumo, Inbox, workers e lógica distribuída pertencem à Etapa 2.

## OpenAPI 1.1.0

- `Problem` foi alinhado ao schema comum, incluindo os limites de `message` e os campos opcionais `operation_id` e `details`.
- respostas de sucesso e erro declaram `X-Correlation-ID`; as rotas internas exigem um UUID nesse header.
- respostas 422 e 500 passaram a ser declaradas explicitamente, sempre no formato `Problem`.
- `quote-context` e `payment-context` estão funcionais; o segundo valida o total aprovado informado pelo Financeiro contra os snapshots da OS finalizada.

