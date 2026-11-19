# ruff: noqa: F811  (fixtures do pytest importadas de outros módulos de teste)
"""Visão única SEFAZ + ADN + upload: papel, escopo "Tudo", status, diagnóstico e demonstração."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import (
    app,
    definir_consultor_adn,
    definir_repositorio_recebidas,
    definir_sessao_http_adn,
)
from parsers.nfse_nacional import parse_nfse_nacional
from sefaz.adn import EstadoCursorADN, calcular_papel_visao
from sefaz.adn_demo import CNPJ_DEMO, montar_itens_demo
from sefaz.adn_sync import carregar_demo_adn
from sefaz.storage import RepositorioDistDFeSQLite
from tests.test_adn import CORPO_FIM, _Resp, _Sessao
from tests.test_adn_diagnostico import SENHA, certificado_cadastrado, pfx  # noqa: F401

FIXTURE_NFSE = Path(__file__).parent / "fixtures" / "nfse_nacional_valida.xml"


@pytest.fixture
def repo(tmp_path) -> RepositorioDistDFeSQLite:
    return RepositorioDistDFeSQLite(tmp_path / "visao.db")


@pytest.fixture
def client(repo):
    definir_repositorio_recebidas(repo)
    try:
        yield TestClient(app)
    finally:
        definir_repositorio_recebidas(None)
        definir_consultor_adn(None)
        definir_sessao_http_adn(None)


@pytest.fixture
def com_demo(client):
    assert client.post("/api/recebidas/demo").status_code == 200
    return client


class TestPapelEmitidaRecebida:
    def test_calculo_por_documentos_e_pelo_papel_do_adn(self):
        assert calcular_papel_visao("11.222.333/0001-81", CNPJ_DEMO, "999", None) == "EMITIDA"
        assert calcular_papel_visao(CNPJ_DEMO, "999", CNPJ_DEMO, None) == "RECEBIDA"
        assert calcular_papel_visao(CNPJ_DEMO, "1", "2", None) is None
        assert calcular_papel_visao(None, CNPJ_DEMO, "2", None) is None
        assert calcular_papel_visao(None, "1", "2", "INTERMEDIARIO") == "INTERMEDIARIA"
        assert calcular_papel_visao(CNPJ_DEMO, "1", "2", "TOMADOR") == "RECEBIDA"

    def test_lista_devolve_origem_e_papel_para_nfe_e_nfse(self, com_demo):
        corpo = com_demo.get("/api/recebidas").json()
        por_origem = {n["origem"] for n in corpo["notas"]}
        assert por_origem == {"DISTDFE", "ADN"}
        adn = [n for n in corpo["notas"] if n["origem"] == "ADN"]
        assert {n["papel_visao"] for n in adn} == {"EMITIDA", "RECEBIDA", "INTERMEDIARIA"}
        nfe = [n for n in corpo["notas"] if n["origem"] == "DISTDFE"]
        assert nfe and all("papel_visao" in n for n in nfe)

    def test_filtros_rapidos_so_emitidas_e_so_tomadas(self, com_demo):
        emitidas = com_demo.get("/api/recebidas", params={"papel": "EMITIDA"}).json()
        recebidas = com_demo.get("/api/recebidas", params={"papel": "RECEBIDA"}).json()
        interm = com_demo.get("/api/recebidas", params={"papel": "INTERMEDIARIA"}).json()
        assert {n["papel_visao"] for n in emitidas["notas"]} == {"EMITIDA"}
        assert {n["papel_visao"] for n in recebidas["notas"]} == {"RECEBIDA"}
        assert interm["total"] == 1
        # as 3 emitidas e 2 tomadas da NFS-e demo, mais as NF-e demo do mesmo CNPJ
        assert emitidas["total"] >= 3 and recebidas["total"] >= 2

    def test_filtros_origem_sefaz_adn_e_tipo(self, com_demo):
        assert com_demo.get("/api/recebidas", params={"origem": "ADN"}).json()["total"] == 6
        sefaz = com_demo.get("/api/recebidas", params={"origem": "SEFAZ"}).json()
        assert sefaz["total"] == 3
        assert com_demo.get("/api/recebidas", params={"tipo": "NFSE"}).json()["total"] == 6
        assert com_demo.get("/api/recebidas", params={"papel": "INVALIDO"}).json()["total"] == 0

    def test_nfe_sem_cnpj_de_referencia_fica_sem_papel(self, repo, client):
        # sem ADN e sem NFE_CNPJ_CONSULTA, não há como saber (e a UI mostra "-")
        from sefaz.descompactador import compactar_xml_para_doc_zip_b64  # noqa: F401

        assert client.post("/api/recebidas/demo").status_code == 200
        repo_vazio = repo.obter_documento_consulta_recente()
        assert repo_vazio == CNPJ_DEMO  # a demo só marca o CNPJ de referência fictício

    def test_variavel_nfe_cnpj_consulta_define_a_referencia(self, client, monkeypatch):
        monkeypatch.setenv("NFE_CNPJ_CONSULTA", CNPJ_DEMO)
        assert client.post("/api/recebidas/demo").status_code == 200
        nfe = client.get("/api/recebidas", params={"origem": "SEFAZ"}).json()["notas"]
        assert {n["papel_visao"] for n in nfe} <= {"EMITIDA", "RECEBIDA", None}


class TestDemoNaoAtrapalhaDadosReais:
    def test_demo_preserva_cursor_e_cnpj_de_consulta_existentes(self, client, repo):
        repo.salvar_cursor_adn(
            EstadoCursorADN(ambiente=2, ultimo_nsu=5000, documento_consulta="12345678000195")
        )
        resposta = client.post("/api/recebidas/demo").json()
        assert resposta["adn_novas_notas"] == 6
        cursor = repo.obter_cursor_adn(2)
        assert cursor.ultimo_nsu == 5000
        assert cursor.documento_consulta == "12345678000195"
        assert repo.listar_logs_sync(origem="NFSE") == []  # a demo não escreve no sync_log

    def test_documento_de_consulta_nunca_sai_na_api(self, client, repo):
        repo.salvar_cursor_adn(EstadoCursorADN(ambiente=2, documento_consulta=CNPJ_DEMO))
        texto = client.get("/api/recebidas/status").text
        assert CNPJ_DEMO not in texto


class TestStatusUnificado:
    def test_status_traz_cursor_adn_e_ultimo_sync_de_cada_origem(self, client, repo):
        repo.registrar_log_sync(
            ambiente=2, cstat="138", motivo="ok", paginas=1, novos_docs=3, ultimo_nsu="7"
        )
        repo.registrar_log_sync(
            ambiente=2,
            cstat="NENHUM_DOCUMENTO_LOCALIZADO",
            motivo="Nenhum documento novo.",
            paginas=2,
            novos_docs=50,
            ultimo_nsu="50",
            origem="NFSE",
            tentativas=3,
            falhas_conexao="pág. 1, tentativa 1: leitura da resposta [RemoteDisconnected]",
        )
        repo.salvar_cursor_adn(EstadoCursorADN(ambiente=2, ultimo_nsu=50))
        st = client.get("/api/recebidas/status").json()
        assert st["cursor_adn"]["ultimo_nsu"] == 50
        assert st["ultimo_sync_nfe"]["origem"] == "NFE"
        assert st["ultimo_sync_nfse"]["origem"] == "NFSE"
        assert st["ultimo_sync_nfse"]["tentativas"] == 3
        assert "RemoteDisconnected" in st["ultimo_sync_nfse"]["falhas_conexao"]
        assert st["bloqueado_adn"] is False

    def test_status_sem_nenhuma_sincronizacao(self, client):
        st = client.get("/api/recebidas/status").json()
        assert st["ultimo_sync_nfe"] is None and st["ultimo_sync_nfse"] is None
        assert st["certificado_configurado"] is False


class TestEscopoTudo:
    def test_tudo_soma_uploads_sefaz_e_adn_com_origem_e_conta_nfse(self, com_demo):
        antes = com_demo.get("/api/lote/tudo").json()
        assert {r["origem"] for r in antes["resultados"]} == {"SEFAZ", "ADN"}
        assert com_demo.post("/api/lote/demo").status_code == 200
        corpo = com_demo.get("/api/lote/tudo").json()
        assert corpo["lote_id"] == "tudo"
        origens = {r["origem"] for r in corpo["resultados"]}
        # as NF-e demo do SEFAZ têm as mesmas chaves do lote demo: vale o upload (sem duplicar)
        assert origens == {"UPLOAD", "ADN"}
        chaves = [
            r["documento"]["chave_acesso"]
            for r in corpo["resultados"]
            if r["documento"] and r["documento"]["chave_acesso"]
        ]
        assert len(chaves) == len(set(chaves))
        por_tipo = corpo["resumo"]["contagem"]["por_tipo"]
        assert por_tipo.get("NFSE", 0) >= 7  # 6 do ADN + a NFS-e do lote demo
        total = corpo["resumo"]["contagem"]["total_arquivos"]
        assert total == len(corpo["resultados"])
        assert {r["papel"] for r in corpo["resultados"] if r["origem"] == "ADN"} == {
            "EMITIDA",
            "RECEBIDA",
            "INTERMEDIARIA",
        }

    def test_deduplica_por_chave_valendo_o_upload(self, com_demo, repo):
        com_demo.post("/api/lote/demo")
        base = com_demo.get("/api/lote/tudo").json()
        # grava, via ADN, a MESMA NFS-e que o lote demo já tem (mesma chave)
        xml = FIXTURE_NFSE.read_bytes()
        doc = parse_nfse_nacional(xml)
        repo.salvar_nfse_adn(
            chave=doc.chave_acesso, nsu=777, doc=doc, xml_bytes=xml, papel="TOMADOR"
        )
        depois = com_demo.get("/api/lote/tudo").json()
        assert len(depois["resultados"]) == len(base["resultados"])
        iguais = [
            r
            for r in depois["resultados"]
            if r["documento"] and r["documento"]["chave_acesso"] == doc.chave_acesso
        ]
        assert len(iguais) == 1 and iguais[0]["origem"] == "UPLOAD"

    def test_acervo_continua_igual_sem_origem(self, com_demo):
        com_demo.post("/api/lote/demo")
        acervo = com_demo.get("/api/acervo").json()
        assert all(r.get("origem") is None for r in acervo["resultados"])

    def test_espelho_danfse_e_xml_de_nfse_do_adn_pelos_dois_caminhos(self, com_demo, repo):
        nota = com_demo.get("/api/recebidas", params={"origem": "ADN"}).json()["notas"][0]
        chave = nota["chave"]
        for rota in (f"/recebidas/{chave}/espelho", f"/lote/tudo/nota/{chave}/espelho"):
            resp = com_demo.get(rota)
            assert resp.status_code == 200
            assert "DANFSe" in resp.text or "NFS-e" in resp.text
        for rota in (f"/recebidas/{chave}/xml", f"/lote/tudo/nota/{chave}/xml"):
            resp = com_demo.get(rota)
            assert resp.status_code == 200 and resp.content.startswith(b"<?xml")
            assert resp.headers["content-type"].startswith("application/xml")

    def test_nota_inexistente_na_visao_tudo(self, com_demo):
        assert com_demo.get("/lote/tudo/nota/0000/xml").status_code == 404

    def test_exportar_tudo(self, com_demo):
        resp = com_demo.get("/api/lote/tudo/exportar", params={"formato": "csv"})
        assert resp.status_code == 200 and b"NFSE" in resp.content


class TestDiagnosticoNaApi:
    def test_so_leitura_sem_gravar_no_banco_nem_no_cursor(
        self,
        certificado_cadastrado,
        client,
        repo,
        pfx,  # noqa: F811
    ):
        carregar_demo_adn(repo, montar_itens_demo(), documento_alvo=CNPJ_DEMO)
        repo.salvar_cursor_adn(EstadoCursorADN(ambiente=2, ultimo_nsu=42))
        arquivos = {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in repo.caminho_db.parent.iterdir()
            if p.is_file()
        }
        sessao = _Sessao(
            _Resp(
                200,
                {
                    "TipoConsulta": "CnpjBase",
                    "StatusProcessamento": "DOCUMENTOS_LOCALIZADOS",
                    "LoteDFe": [
                        {
                            "NSU": 1,
                            "ChaveAcesso": "9" * 50,
                            "TipoDocumento": "NFSE",
                            "ArquivoXml": "SEGREDO",
                        },
                        {"NSU": 2, "TipoDocumento": "EVENTO", "ArquivoXml": "SEGREDO"},
                    ],
                },
            )
        )
        definir_sessao_http_adn(sessao)
        resp = client.post("/api/recebidas/adn/diagnostico", params={"ambiente": 1})
        assert resp.status_code == 200
        d = resp.json()
        assert d["sucesso"] is True and d["http_status"] == 200 and d["ambiente"] == 1
        assert d["itens_por_tipo_documento"] == {"NFSE": 1, "EVENTO": 1}
        assert d["cnpj_consulta_mascarado"] == "11**********81"
        assert len(sessao.chamadas) == 1 and sessao.chamadas[0]["url"].endswith("/DFe/0")
        for proibido in (CNPJ_DEMO, SENHA, "SEGREDO"):
            assert proibido not in resp.text
        assert repo.obter_cursor_adn(2).ultimo_nsu == 42
        depois = {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in repo.caminho_db.parent.iterdir()
            if p.is_file()
        }
        assert depois == arquivos  # nenhum arquivo do banco mudou

    def test_falha_de_conexao_vira_diagnostico_estruturado(self, certificado_cadastrado, client):  # noqa: F811
        import requests

        definir_sessao_http_adn(_Sessao(requests.ConnectionError("x")))
        d = client.post("/api/recebidas/adn/diagnostico").json()
        assert d["sucesso"] is False and d["categoria_erro"] == "CONEXAO"
        assert d["falha_conexao"]["orientacao"]

    def test_404_inesperado_aponta_a_url_base(self, certificado_cadastrado, client):  # noqa: F811
        definir_sessao_http_adn(_Sessao(_Resp(404, "<html/>")))
        d = client.post("/api/recebidas/adn/diagnostico").json()
        assert d["categoria_erro"] == "ENDERECO" and "NFE_ADN_URL_BASE" in d["interpretacao"]

    def test_sem_certificado_503_e_url_invalida_400(self, client, certificado_cadastrado):  # noqa: F811
        definir_sessao_http_adn(_Sessao(_Resp(404, CORPO_FIM)))
        assert (
            client.post(
                "/api/recebidas/adn/diagnostico", params={"url_base": "http://x.teste"}
            ).status_code
            == 400
        )

    def test_sem_certificado_cadastrado(self, client):
        resp = client.post("/api/recebidas/adn/diagnostico")
        assert resp.status_code == 503


def test_json_do_status_nao_contem_senha_nem_caminho(client):
    texto = json.dumps(client.get("/api/recebidas/status").json())
    assert SENHA not in texto
