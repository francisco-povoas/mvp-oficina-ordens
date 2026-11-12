from app.modules.ordens_servico.application.use_cases.adicionar_item_na_ordem_servico import (
    AdicionarItemNaOrdemServicoUseCase,
)
from app.modules.ordens_servico.application.use_cases.adicionar_servico_na_ordem_servico import (
    AdicionarServicoNaOrdemServicoUseCase,
)
from app.modules.ordens_servico.application.use_cases.cancelar_reserva_item_saga import (
    CancelarReservaItemSagaUseCase,
)
from app.modules.ordens_servico.application.use_cases.concluir_diagnostico import (
    ConcluirDiagnosticoOrdemServicoUseCase,
)
from app.modules.ordens_servico.application.use_cases.confirmar_recebimento_item import (
    ConfirmarRecebimentoItemDaOrdemServicoUseCase,
)
from app.modules.ordens_servico.application.use_cases.criar_ordem_servico import (
    CriarOrdemServicoUseCase,
)
from app.modules.ordens_servico.application.use_cases.entregar_ordem_servico import (
    EntregarOrdemServicoUseCase,
)
from app.modules.ordens_servico.application.use_cases.finalizar_ordem_servico import (
    FinalizarOrdemServicoUseCase,
)
from app.modules.ordens_servico.application.use_cases.iniciar_diagnostico import (
    IniciarDiagnosticoOrdemServicoUseCase,
)
from app.modules.ordens_servico.application.use_cases.iniciar_execucao_ordem_servico import (
    IniciarExecucaoOrdemServicoUseCase,
)
from app.modules.ordens_servico.application.use_cases.listar_execucoes_servico import (
    ListarExecucoesServicoUseCase,
)
from app.modules.ordens_servico.application.use_cases.listar_ordens_servico import (
    ListarOrdensServicoUseCase,
)
from app.modules.ordens_servico.application.use_cases.obter_estatistica_tempo_execucao_servico import (
    ObterEstatisticaTempoExecucaoServicoUseCase,
)
from app.modules.ordens_servico.application.use_cases.obter_ordem_servico_por_id import (
    ObterOrdemServicoPorIdUseCase,
)
from app.modules.ordens_servico.application.use_cases.obter_reserva_item_saga import (
    ObterReservaItemSagaUseCase,
)
from app.modules.ordens_servico.application.use_cases.registrar_diagnostico import (
    RegistrarDiagnosticoOrdemServicoUseCase,
)
from app.modules.ordens_servico.application.use_cases.registrar_tempo_executado_servico import (
    RegistrarTempoExecutadoServicoUseCase,
)
from app.modules.ordens_servico.application.use_cases.remover_item_da_ordem_servico import (
    RemoverItemDaOrdemServicoUseCase,
)
from app.modules.ordens_servico.application.use_cases.remover_servico_da_ordem_servico import (
    RemoverServicoDaOrdemServicoUseCase,
)

__all__ = [
    "CriarOrdemServicoUseCase",
    "ListarOrdensServicoUseCase",
    "ObterOrdemServicoPorIdUseCase",
    "ObterReservaItemSagaUseCase",
    "IniciarDiagnosticoOrdemServicoUseCase",
    "RegistrarDiagnosticoOrdemServicoUseCase",
    "AdicionarServicoNaOrdemServicoUseCase",
    "RemoverServicoDaOrdemServicoUseCase",
    "AdicionarItemNaOrdemServicoUseCase",
    "CancelarReservaItemSagaUseCase",
    "RemoverItemDaOrdemServicoUseCase",
    "ConcluirDiagnosticoOrdemServicoUseCase",
    "ConfirmarRecebimentoItemDaOrdemServicoUseCase",
    "IniciarExecucaoOrdemServicoUseCase",
    "RegistrarTempoExecutadoServicoUseCase",
    "FinalizarOrdemServicoUseCase",
    "ObterEstatisticaTempoExecucaoServicoUseCase",
    "ListarExecucoesServicoUseCase",
    "EntregarOrdemServicoUseCase",
]
