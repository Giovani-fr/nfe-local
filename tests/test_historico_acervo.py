"""Testes de persistência de lotes e Acervo Unificado no SQLite (`Task 20`)."""

from __future__ import annotations

import json
from collections.abc import Generator
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api import app, cli
from api.app import (
    definir_repositorio_recebidas,
    definir_servico_assistente,
    obter_repositorio_lotes,
    obter_repositorio_recebidas,
    obter_servico_assistente,
)
from parsers.detector import MAX_ARQUIVOS_ZIP_PADRAO, MAX_BYTES_TOTAL_ZIP_PADRAO
from reports.assistente_lote import ServicoAssistenteLote
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

    def test_post_sistema_resetar_banco_limpa_lotes_recebidas_lru_e_cache_llm(
        self, client: TestClient
    ) -> None:
        servico_orig = obter_servico_assistente()
        chamadas_llm = 0

        def fake_llm(
            _mensagens: list[dict[str, str]],
            _modelo: str,
            _base_url: str,
            _api_key: str,
        ) -> str:
            nonlocal chamadas_llm
            chamadas_llm += 1
            return f"Resposta simulada #{chamadas_llm}"

        definir_servico_assistente(
            ServicoAssistenteLote(
                api_key="chave-fake",
                modelo="modelo-teste",
                cliente_completion=fake_llm,
            )
        )
        try:
            # Popula Aba 1 (lote demo) + Aba 2 (recebidas demo) + cache do assistente LLM
            lote_id = client.post("/api/lote/demo").json()["lote_id"]
            resp_rec_demo = client.post("/api/recebidas/demo")
            assert resp_rec_demo.status_code == 200
            assert client.get("/api/lotes").json()["total"] == 1
            assert client.get("/api/recebidas").json()["total"] == 3

            resp_p1 = client.post(
                f"/api/lote/{lote_id}/perguntar",
                json={"pergunta": "Quais notas possuem erros?"},
            )
            assert resp_p1.status_code == 200
            assert resp_p1.json()["em_cache"] is False
            assert chamadas_llm == 1

            # Executa reset completo
            resp_reset = client.post("/api/sistema/resetar-banco")
            assert resp_reset.status_code == 200
            corpo_reset = resp_reset.json()
            assert corpo_reset == {
                "status": "ok",
                "lotes_removidos": 1,
                "recebidas_removidas": 3,
            }

            # Confirma tudo zerado (Aba 1, Aba 2, cursor NSU, logs e cache LRU)
            assert client.get("/api/lotes").json()["total"] == 0
            assert client.get("/api/acervo").json()["resultados"] == []
            assert client.get("/api/recebidas").json()["total"] == 0
            status_sefaz = client.get("/api/recebidas/status").json()
            assert status_sefaz["cursor"]["ultimo_nsu"] == "000000000000000"
            assert status_sefaz["ultimos_logs"] == []
            assert obter_repositorio_lotes().obter(lote_id) is None

            # Novo lote idêntico + mesma pergunta não deve vir do cache antigo
            novo_lote_id = client.post("/api/lote/demo").json()["lote_id"]
            resp_p2 = client.post(
                f"/api/lote/{novo_lote_id}/perguntar",
                json={"pergunta": "Quais notas possuem erros?"},
            )
            assert resp_p2.status_code == 200
            assert resp_p2.json()["em_cache"] is False
            assert chamadas_llm == 2
        finally:
            definir_servico_assistente(servico_orig)

    def test_cli_resetar_banco_json_e_texto(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        caminho_db = tmp_path / "cli_reset.db"
        repo = RepositorioDistDFeSQLite(caminho_db)
        definir_repositorio_recebidas(repo)
        try:
            with TestClient(app) as tc:
                assert tc.post("/api/lote/demo").status_code == 200
                assert tc.post("/api/recebidas/demo").status_code == 200
        finally:
            definir_repositorio_recebidas(None)

        # Executa CLI com --db e --json
        codigo = cli.main(["resetar-banco", "--db", str(caminho_db), "--json"])
        assert codigo == 0
        saida_json = json.loads(capsys.readouterr().out)
        assert saida_json["status"] == "ok"
        assert saida_json["lotes_removidos"] == 1
        assert saida_json["recebidas_removidas"] == 3
        assert repo.listar_lotes_upload() == []
        assert repo.listar_notas_recebidas() == []
        assert repo.listar_logs_sync() == []

        # Segunda execução em modo texto (banco já vazio)
        codigo_txt = cli.main(["resetar-banco", "--db", str(caminho_db)])
        assert codigo_txt == 0
        saida_txt = capsys.readouterr().out
        assert "Reset Completo do Banco SQLite Local" in saida_txt
        assert "Lotes removidos     : 0" in saida_txt
        assert "Recebidas removidas : 0" in saida_txt

    def test_lazy_load_acervo_e_busca_cirurgica_de_uma_nota_no_sqlite(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Confirma ampliação dos limites padrão de ZIP para grandes acervos
        assert MAX_ARQUIVOS_ZIP_PADRAO == 50_000
        assert MAX_BYTES_TOTAL_ZIP_PADRAO == 1024 * 1024 * 1024

        # Sobe 2 lotes com notas distintas + 1 nota repetida entre eles
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
            ],
        )
        assert resp_l1.status_code == 200
        lote_id_1 = resp_l1.json()["lote_id"]

        resp_l2 = client.post(
            "/api/lote",
            files=[
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

        # Limpa cache RAM para forçar leitura do SQLite
        obter_repositorio_lotes().limpar()
        repo = obter_repositorio_recebidas()

        # 1. obter_acervo_unificado() padrão (incluir_xmls_brutos=False) NÃO descompacta xml_gzip
        _, _, resultados_lazy, xmls_lazy = repo.obter_acervo_unificado()
        assert len(resultados_lazy) == 2
        assert xmls_lazy == {}

        # 2. Com incluir_xmls_brutos=True, descompacta sob demanda
        _, _, _, xmls_full = repo.obter_acervo_unificado(incluir_xmls_brutos=True)
        assert CHAVE_NFE_1001 in xmls_full

        # 3. Busca cirúrgica de 1 nota no escopo 'acervo' e em lote específico (por chave e índice)
        achado_chave = repo.obter_nota_e_xml_no_escopo("acervo", CHAVE_NFE_1001)
        assert achado_chave is not None
        res_chave, xml_chave = achado_chave
        assert res_chave.documento is not None
        assert res_chave.documento.chave_acesso == CHAVE_NFE_1001
        assert b"<nfeProc" in xml_chave

        achado_idx0 = repo.obter_nota_e_xml_no_escopo("acervo", "0")
        assert achado_idx0 is not None
        assert achado_idx0[0].documento is not None
        assert achado_idx0[0].documento.tipo == "NFSE"
        assert b"<NFSe" in achado_idx0[1]

        achado_lote = repo.obter_nota_e_xml_no_escopo(lote_id_1, "0")
        assert achado_lote is not None
        assert b"<nfeProc" in achado_lote[1]

        # 4. Garante que rotas de detalhe, espelho e XML do acervo NÃO chamam obter_acervo_unificado
        def _proibido(*_args: object, **_kwargs: object) -> object:
            raise AssertionError("Não deve carregar o acervo inteiro para abrir 1 única nota!")

        monkeypatch.setattr(repo, "obter_acervo_unificado", _proibido)

        resp_nota = client.get(f"/api/lote/acervo/nota/{CHAVE_NFE_1001}")
        assert resp_nota.status_code == 200
        assert resp_nota.json()["documento"]["chave_acesso"] == CHAVE_NFE_1001

        resp_espelho = client.get(f"/lote/acervo/nota/{CHAVE_NFE_1001}/espelho")
        assert resp_espelho.status_code == 200
        assert "DANFE" in resp_espelho.text

        resp_xml = client.get(f"/lote/acervo/nota/{CHAVE_NFE_1001}/xml")
        assert resp_xml.status_code == 200
        assert b"<nfeProc" in resp_xml.content

        resp_espelho_idx = client.get("/lote/acervo/nota/0/espelho")
        assert resp_espelho_idx.status_code == 200
        assert "DANFSe v2.0" in resp_espelho_idx.text
        assert '<div class="qr-svg"><svg ' in resp_espelho_idx.text
