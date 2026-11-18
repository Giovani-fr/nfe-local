"""Testes de persistência de lotes e Acervo Unificado no SQLite (`Task 20`)."""

from __future__ import annotations

from collections.abc import Generator
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api import app
from api.app import (
    definir_repositorio_recebidas,
    obter_repositorio_lotes,
)
from sefaz.storage import RepositorioDistDFeSQLite

PASTA_FIXTURES = Path(__file__).resolve().parent / "fixtures"
CHAVE_NFE_1001 = "35260311222333000181550010000010011100010010"


@pytest.fixture()
def client(tmp_path: Path) -> Generator[TestClient, None, None]:
    """Configura `TestClient` com banco SQLite isolado em `tmp_path` e cache limpo."""
    obter_repositorio_lotes().limpar()
    repo = RepositorioDistDFeSQLite(tmp_path / "teste_historico_acervo.db")
    definir_repositorio_recebidas(repo)
    try:
        yield TestClient(app)
    finally:
        obter_repositorio_lotes().limpar()
        definir_repositorio_recebidas(None)


class TestHistoricoPersistenteEAcervoUnificado:
    """Valida a persistência SQLite de lotes, hidratação pós-reinício e Acervo Unificado."""

    def test_lote_upload_e_demo_persistem_apos_limpar_cache_em_memoria(
        self, client: TestClient
    ) -> None:
        arquivos = [
            (
                "files",
                (
                    "nfe_55_valida.xml",
                    (PASTA_FIXTURES / "nfe_55_valida.xml").read_bytes(),
                    "application/xml",
                ),
            ),
            (
                "files",
                (
                    "nfse_nacional_valida.xml",
                    (PASTA_FIXTURES / "nfse_nacional_valida.xml").read_bytes(),
                    "application/xml",
                ),
            ),
        ]
        resp_up = client.post("/api/lote", files=arquivos)
        assert resp_up.status_code == 200
        lote_id_upload = resp_up.json()["lote_id"]

        resp_demo = client.post("/api/lote/demo")
        assert resp_demo.status_code == 200
        lote_id_demo = resp_demo.json()["lote_id"]

        # Simula reinício do servidor limpando o cache LRU em memória
        obter_repositorio_lotes().limpar()
        assert obter_repositorio_lotes().obter(lote_id_upload) is None
        assert obter_repositorio_lotes().obter(lote_id_demo) is None

        # Os lotes continuam acessíveis a partir do SQLite e hidratam o cache
        resp_get_up = client.get(f"/api/lote/{lote_id_upload}")
        assert resp_get_up.status_code == 200
        assert resp_get_up.json()["resumo"]["contagem"]["total_arquivos"] == 2
        assert obter_repositorio_lotes().obter(lote_id_upload) is not None

        # Consulta de nota individual, espelho DANFE e XML bruto pós-reinício
        resp_nota = client.get(f"/api/lote/{lote_id_upload}/nota/{CHAVE_NFE_1001}")
        assert resp_nota.status_code == 200
        assert resp_nota.json()["documento"]["chave_acesso"] == CHAVE_NFE_1001

        resp_espelho = client.get(f"/lote/{lote_id_upload}/nota/{CHAVE_NFE_1001}/espelho")
        assert resp_espelho.status_code == 200
        assert "DANFE" in resp_espelho.text

        resp_xml = client.get(f"/lote/{lote_id_upload}/nota/{CHAVE_NFE_1001}/xml")
        assert resp_xml.status_code == 200
        assert b"<nfeProc" in resp_xml.content

        # Lote demo também permanece acessível pós-reinício
        resp_get_demo = client.get(f"/api/lote/{lote_id_demo}")
        assert resp_get_demo.status_code == 200
        assert resp_get_demo.json()["resumo"]["contagem"]["total_arquivos"] >= 6

    def test_listar_lotes_historico_com_nome_rotulo_e_origem(self, client: TestClient) -> None:
        resp_vazio = client.get("/api/lotes")
        assert resp_vazio.status_code == 200
        assert resp_vazio.json() == {"total": 0, "lotes": []}

        # 1. Envia lote upload com 3 arquivos
        arquivos = [
            (
                "files",
                (
                    "nfe_55_valida.xml",
                    (PASTA_FIXTURES / "nfe_55_valida.xml").read_bytes(),
                    "application/xml",
                ),
            ),
            (
                "files",
                (
                    "nfce_65_valida.xml",
                    (PASTA_FIXTURES / "nfce_65_valida.xml").read_bytes(),
                    "application/xml",
                ),
            ),
            (
                "files",
                (
                    "nfse_nacional_valida.xml",
                    (PASTA_FIXTURES / "nfse_nacional_valida.xml").read_bytes(),
                    "application/xml",
                ),
            ),
        ]
        lote_upload = client.post("/api/lote", files=arquivos).json()["lote_id"]

        # 2. Carrega lote de demonstração
        lote_demo = client.post("/api/lote/demo").json()["lote_id"]

        resp_lista = client.get("/api/lotes")
        assert resp_lista.status_code == 200
        dados = resp_lista.json()
        assert dados["total"] == 2
        lotes = dados["lotes"]
        assert len(lotes) == 2

        # Ordenado do mais recente (demo) para o mais antigo (upload)
        assert lotes[0]["lote_id"] == lote_demo
        assert lotes[0]["origem"] == "demo"
        assert lotes[0]["nome_rotulo"] == "Lote de Demonstração"

        assert lotes[1]["lote_id"] == lote_upload
        assert lotes[1]["origem"] == "upload"
        assert "3 arquivos" in lotes[1]["nome_rotulo"]
        assert "nfe_55_valida.xml" in lotes[1]["nome_rotulo"]
        assert lotes[1]["total_arquivos"] == 3
        assert lotes[1]["validos"] == 3

    def test_acervo_unificado_consolida_multiplos_lotes_e_deduplica_mesma_nota(
        self, client: TestClient
    ) -> None:
        # Lote 1: NF-e 55 válida (R$ 1595.00) + NFC-e 65 válida (R$ 250.00)
        resp_l1 = client.post(
            "/api/lote",
            files=[
                (
                    "files",
                    (
                        "nfe_55_valida.xml",
                        (PASTA_FIXTURES / "nfe_55_valida.xml").read_bytes(),
                        "application/xml",
                    ),
                ),
                (
                    "files",
                    (
                        "nfce_65_valida.xml",
                        (PASTA_FIXTURES / "nfce_65_valida.xml").read_bytes(),
                        "application/xml",
                    ),
                ),
            ],
        )
        assert resp_l1.status_code == 200
        valor_l1 = Decimal(str(resp_l1.json()["resumo"]["valor_total_notas"]))

        # Lote 2: Mesma NF-e 55 válida (duplicada entre lotes!) + NFS-e Nacional válida
        resp_l2 = client.post(
            "/api/lote",
            files=[
                (
                    "files",
                    (
                        "nfe_55_valida.xml",
                        (PASTA_FIXTURES / "nfe_55_valida.xml").read_bytes(),
                        "application/xml",
                    ),
                ),
                (
                    "files",
                    (
                        "nfse_nacional_valida.xml",
                        (PASTA_FIXTURES / "nfse_nacional_valida.xml").read_bytes(),
                        "application/xml",
                    ),
                ),
            ],
        )
        assert resp_l2.status_code == 200

        # Limpa cache em memória para garantir que o acervo lê do SQLite
        obter_repositorio_lotes().limpar()

        resp_acervo = client.get("/api/acervo")
        assert resp_acervo.status_code == 200
        acervo = resp_acervo.json()
        assert acervo["lote_id"] == "acervo"

        # 3 documentos únicos no acervo (NF-e 55 não foi duplicada!)
        assert acervo["resumo"]["contagem"]["total_arquivos"] == 3
        assert len(acervo["resultados"]) == 3
        valor_acervo = Decimal(str(acervo["resumo"]["valor_total_notas"]))
        assert valor_acervo > valor_l1

        # Filtros no /api/acervo funcionam normalmente
        resp_acervo_nfse = client.get("/api/acervo", params={"tipo": "NFSE"})
        assert resp_acervo_nfse.status_code == 200
        assert len(resp_acervo_nfse.json()["resultados"]) == 1

        # Exportação XLSX/CSV e espelho DANFE via lote_id='acervo'
        resp_xlsx = client.get("/api/lote/acervo/exportar", params={"formato": "xlsx"})
        assert resp_xlsx.status_code == 200
        assert resp_xlsx.content.startswith(b"PK\x03\x04")
        assert "lote_acervo.xlsx" in resp_xlsx.headers["content-disposition"]

        resp_espelho = client.get(f"/lote/acervo/nota/{CHAVE_NFE_1001}/espelho")
        assert resp_espelho.status_code == 200
        assert "DANFE" in resp_espelho.text

        resp_xml = client.get(f"/lote/acervo/nota/{CHAVE_NFE_1001}/xml")
        assert resp_xml.status_code == 200
        assert b"<nfeProc" in resp_xml.content

    def test_delete_lote_individual_e_limpar_apenas_demo(self, client: TestClient) -> None:
        # Cria 1 lote real (upload) e 2 lotes demo
        lote_real = client.post(
            "/api/lote",
            files=[
                (
                    "files",
                    (
                        "nfe_55_valida.xml",
                        (PASTA_FIXTURES / "nfe_55_valida.xml").read_bytes(),
                        "application/xml",
                    ),
                )
            ],
        ).json()["lote_id"]
        lote_demo_1 = client.post("/api/lote/demo").json()["lote_id"]
        lote_demo_2 = client.post("/api/lote/demo").json()["lote_id"]

        assert client.get("/api/lotes").json()["total"] == 3

        # Exclui apenas lote_demo_1
        resp_del_1 = client.delete(f"/api/lote/{lote_demo_1}")
        assert resp_del_1.status_code == 200
        assert resp_del_1.json()["removido"] is True
        assert client.get(f"/api/lote/{lote_demo_1}").status_code == 404
        assert client.delete(f"/api/lote/{lote_demo_1}").status_code == 404

        # Limpa apenas lotes de demonstração (mantendo lote_real intacto)
        resp_limpar_demo = client.delete("/api/lotes", params={"apenas_demo": "true"})
        assert resp_limpar_demo.status_code == 200
        assert resp_limpar_demo.json() == {"removidos": 1}

        assert client.get(f"/api/lote/{lote_demo_2}").status_code == 404
        assert client.get(f"/api/lote/{lote_real}").status_code == 200

        lista_restante = client.get("/api/lotes").json()
        assert lista_restante["total"] == 1
        assert lista_restante["lotes"][0]["lote_id"] == lote_real

        # Limpa todo o histórico restante
        resp_limpar_tudo = client.delete("/api/lotes")
        assert resp_limpar_tudo.status_code == 200
        assert resp_limpar_tudo.json() == {"removidos": 1}
        assert client.get("/api/lotes").json()["total"] == 0
