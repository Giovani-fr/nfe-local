"""Isolamento global dos testes: nenhum teste pode tocar o banco nem o certificado reais.

- `NFE_DB_PATH` e `NFE_DATA_DIR` apontam, por teste, para pastas temporárias (`tmp_path`).
- As variáveis `NFE_CERT_*` são removidas e a pasta do cadastro antigo é redirecionada.
- Uma verificação de sessão garante que `data/nfe_recebidas.db`, `data/certificado/` e a pasta
  padrão do certificado (`NFE_DATA_DIR` real) não foram criados nem modificados pela suíte.
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


def _assinatura_pasta(pasta: Path) -> tuple:
    """Nomes, tamanhos e datas dos arquivos da pasta (nunca lê o conteúdo)."""
    if not pasta.exists():
        return ()
    return tuple(
        sorted(
            (str(p.relative_to(pasta)), *(_assinatura(p) or (0, 0)))
            for p in pasta.rglob("*")
            if p.is_file()
        )
    )


def _pastas_reais_de_certificado() -> list[Path]:
    from sefaz import segredos

    return [RAIZ / "data" / "certificado", segredos.diretorio_certificado_padrao()]


@pytest.fixture(autouse=True)
def _db_temporario(tmp_path, monkeypatch):
    """Redireciona o banco SQLite de cada teste para um arquivo temporário."""
    destino = tmp_path / "nfe_teste.db"
    monkeypatch.setenv("NFE_DB_PATH", str(destino))
    monkeypatch.setenv("NFE_DATA_DIR", str(tmp_path / "dados_nfe"))
    for var in ("NFE_CERT_DIR", "NFE_CERT_PATH", "NFE_CERT_SENHA"):
        monkeypatch.delenv(var, raising=False)
    import sefaz.cert_a1 as cert_a1

    monkeypatch.setattr(cert_a1, "DIRETORIO_CERTIFICADO_LEGADO", tmp_path / "legado_inexistente")
    yield destino


@pytest.fixture
def dpapi_falso(monkeypatch):
    """Simula a DPAPI de forma reversível (só para testes; em Windows use a real)."""
    import base64

    from sefaz import segredos

    monkeypatch.setattr(segredos, "dpapi_disponivel", lambda: True)
    monkeypatch.setattr(segredos, "proteger_bytes", lambda b: b"FAKE:" + base64.b64encode(b[::-1]))
    monkeypatch.setattr(
        segredos, "desproteger_bytes", lambda b: base64.b64decode(b[len(b"FAKE:") :])[::-1]
    )


@pytest.fixture(autouse=True, scope="session")
def _guarda_banco_real():
    """Falha a suíte se o banco padrão for criado ou modificado por algum teste."""
    antes = _assinatura(DB_PADRAO)
    pastas_antes = {p: _assinatura_pasta(p) for p in _pastas_reais_de_certificado()}
    yield
    depois = _assinatura(DB_PADRAO)
    assert depois == antes, (
        "Algum teste tocou data/nfe_recebidas.db (criado ou modificado). Use NFE_DB_PATH/tmp_path."
    )
    for pasta, assinatura in pastas_antes.items():
        assert _assinatura_pasta(pasta) == assinatura, (
            f"Algum teste tocou a pasta real do certificado ({pasta.name}). Use tmp_path."
        )
