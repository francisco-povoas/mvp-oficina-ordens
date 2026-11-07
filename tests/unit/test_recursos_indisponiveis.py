"""O boundary de Recursos permanece fail-closed até a Etapa 2."""

import pytest

from app.integrations.resources import (
    RecursosIndisponiveisAdapter,
    ResourceDependencyUnavailable,
)
from app.shared.value_objects.id import ID


async def test_adapter_de_catalogo_falha_explicitamente() -> None:
    with pytest.raises(ResourceDependencyUnavailable, match="Etapa 2"):
        await RecursosIndisponiveisAdapter().obter_por_id(ID.generate())


async def test_adapter_de_estoque_falha_explicitamente() -> None:
    adapter = RecursosIndisponiveisAdapter()
    with pytest.raises(ResourceDependencyUnavailable, match="Etapa 2"):
        await adapter.obter_por_id_com_lock(ID.generate())
    with pytest.raises(ResourceDependencyUnavailable, match="Etapa 2"):
        await adapter.atualizar(None)  # type: ignore[arg-type]
