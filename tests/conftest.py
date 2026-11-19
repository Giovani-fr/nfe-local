"""Isolamento global dos testes: nenhum teste pode tocar o banco real.

- `NFE_DB_PATH` aponta, por teste, para um arquivo temporário (`tmp_path`).
- Uma verificação de sessão garante que `data/nfe_recebidas.db` não foi criado
  nem modificado durante a suíte.
"""

from __future__ import annotations

from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
DB_PADRAO = RAIZ / "data" / "nfe_recebidas.db"


def _assinatura(caminho: Path) -> tuple[int, int] | None:
    try:
        st = caminho.stat()
    except FileNotFoundError:
        return None
    return (st.st_size, st.st_mtime_ns)


@pytest.fixture(autouse=True)
def _db_temporario(tmp_path, monkeypatch):
    """Redireciona o banco SQLite de cada teste para um arquivo temporário."""
    destino = tmp_path / "nfe_teste.db"
    monkeypatch.setenv("NFE_DB_PATH", str(destino))
    yield destino


@pytest.fixture(autouse=True, scope="session")
def _guarda_banco_real():
    """Falha a suíte se o banco padrão for criado ou modificado por algum teste."""
    antes = _assinatura(DB_PADRAO)
    yield
    depois = _assinatura(DB_PADRAO)
    assert depois == antes, (
        "Algum teste tocou data/nfe_recebidas.db (criado ou modificado). Use NFE_DB_PATH/tmp_path."
    )
