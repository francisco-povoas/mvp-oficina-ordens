# Instruções — mvp-oficina-ordens

- Preserve o monólito original; use-o somente como fonte de regras e testes legados.
- Este repositório é dono de IAM, clientes, veículos, ordens, diagnóstico, execução e orquestração da Saga.
- Preserve a máquina de estados pública da OS e os estados operacionais dos itens.
- Não inclua catálogo, saldo de estoque, orçamento ou pagamento como agregados locais.
- Não compartilhe domínio, ORM, migrations ou banco com outros serviços.
- Integrações externas devem usar contratos versionados e transações locais.
- Nunca versione segredos, tokens, credenciais ou arquivos `.env`.
- Use o fluxo `feature/* -> main`; não faça push adicional sem autorização da atividade em curso.
