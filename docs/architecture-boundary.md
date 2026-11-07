# Limite arquitetural de Ordens

Ordens é dono de IAM, clientes, veículos, ordens de serviço, diagnóstico, execução, estados operacionais dos itens e coordenação dos fluxos distribuídos.

O serviço não é dono de catálogo, saldo de estoque, orçamento ou pagamento. Referências a `resource_id` e `service_id` não criam foreign keys externas; os valores históricos usados pela OS serão snapshots locais.

Nesta etapa, integrações ainda não implementadas são ports explícitos. Publicação de eventos, Inbox, workers, SQS/ElasticMQ e Saga executável permanecem na Etapa 2.
