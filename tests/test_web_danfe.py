"""Testes de formatação pt-BR, Code 128 SVG, espelhos DANFE/DANFSe e interface Web (`Task 10`)."""

from __future__ import annotations

from collections.abc import Generator
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api import app
from api.app import definir_repositorio_recebidas, obter_repositorio_lotes
from parsers.detector import processar_arquivo_xml
from reports.danfe_svg import (
    barcode_svg,
    formatar_cep,
    formatar_chave,
    formatar_cnpj_cpf,
    formatar_moeda,
    formatar_numero_nf,
    montar_contexto_danfe,
    montar_contexto_danfse,
)
from sefaz.storage import RepositorioDistDFeSQLite

PASTA_FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture()
def client(tmp_path: Path) -> Generator[TestClient, None, None]:
    """Retorna cliente de teste do FastAPI com repositório em memória limpo e SQLite isolado."""
    obter_repositorio_lotes().limpar()
    definir_repositorio_recebidas(RepositorioDistDFeSQLite(tmp_path / "test_web_danfe.db"))
    try:
        yield TestClient(app)
    finally:
        obter_repositorio_lotes().limpar()
        definir_repositorio_recebidas(None)


class TestFormatadoresEBarcodeSvg:
    """Testes unitários dos formatadores pt-BR e do gerador Code 128 em SVG puro."""

    def test_formatar_moeda(self) -> None:
        assert formatar_moeda(Decimal("1234.5")) == "1.234,50"
        assert formatar_moeda(Decimal("0")) == "0,00"
        assert formatar_moeda(1500000.75, casas=2) == "1.500.000,75"
        assert formatar_moeda(Decimal("2.5"), casas=4) == "2,5000"
        assert formatar_moeda(None) == "0,00"

    def test_formatar_cnpj_cpf_numerico_e_alfanumerico(self) -> None:
        assert formatar_cnpj_cpf("11222333000181") == "11.222.333/0001-81"
        assert formatar_cnpj_cpf("12ABC34501DE35") == "12.ABC.345/01DE-35"
        assert formatar_cnpj_cpf("12345678909") == "123.456.789-09"
        assert formatar_cnpj_cpf("") == ""
        assert formatar_cnpj_cpf(None) == ""

    def test_formatar_chave_cep_e_numero_nf(self) -> None:
        chave = "35260311222333000181550010000010011100010010"
        chave_fmt = formatar_chave(chave)
        assert chave_fmt == "3526 0311 2223 3300 0181 5500 1000 0010 0111 0001 0010"
        assert formatar_cep("01310100") == "01310-100"
        assert formatar_numero_nf("1001") == "000.001.001"

    def test_barcode_svg_code128_c_numerico(self) -> None:
        chave_num = "35260311222333000181550010000010011100010010"
        svg = barcode_svg(chave_num)
        assert svg.startswith("<svg ")
        assert svg.endswith("</svg>")
        assert 'data-subset="C"' in svg
        assert "<rect " in svg

    def test_barcode_svg_code128_b_alfanumerico(self) -> None:
        chave_alnum = "35260312ABC34501DE35550010000010031100010032"
        svg = barcode_svg(chave_alnum)
        assert svg.startswith("<svg ")
        assert svg.endswith("</svg>")
        assert 'data-subset="B"' in svg
        assert "<rect " in svg

    def test_barcode_svg_chave_invalida_retorna_vazio(self) -> None:
        assert barcode_svg("") == ""
        assert barcode_svg(None) == ""
        assert barcode_svg("12345") == ""
        assert barcode_svg("3526031122233300018155001000001001110001001@") == ""

    def test_montar_contextos_danfe_e_danfse(self) -> None:
        res_nfe = processar_arquivo_xml(
            (PASTA_FIXTURES / "nfe_55_cnpj_alfanumerico.xml").read_bytes(),
            "nfe_55_cnpj_alfanumerico.xml",
        )
        ctx_danfe = montar_contexto_danfe(res_nfe)
        assert ctx_danfe["emitente"]["documento_fmt"] == "12.ABC.345/01DE-35"
        assert "<svg " in ctx_danfe["barcode_svg"]

        res_nfse = processar_arquivo_xml(
            (PASTA_FIXTURES / "nfse_nacional_valida.xml").read_bytes(),
            "nfse_nacional_valida.xml",
        )
        ctx_danfse = montar_contexto_danfse(res_nfse)
        assert len(ctx_danfse["chave_acesso"]) == 50
        assert ctx_danfse["totais"]["iss_fmt"] != "0,00"

    def test_montar_contextos_danfe_e_danfse_com_xml_bytes_oficial(self) -> None:
        xml_nfe = (PASTA_FIXTURES / "nfe_55_valida.xml").read_bytes()
        res_nfe = processar_arquivo_xml(xml_nfe, "nfe_55_valida.xml")
        ctx_nfe = montar_contexto_danfe(res_nfe, xml_bytes=xml_nfe)
        d_nfe = ctx_nfe["d"]
        assert d_nfe["tp_nf"] == "1"
        assert d_nfe["serie"] == "001"
        assert d_nfe["emit"]["bairro"] == "DISTRITO INDUSTRIAL"
        assert d_nfe["dest"]["bairro"] == "CENTRO"
        assert d_nfe["transp"]["frete"] == "0-Emitente"
        assert d_nfe["prot"]["num"] == "135260000001001"
        assert "10/03/2026" in d_nfe["prot"]["data"]
        assert len(d_nfe["dups"]) == 2
        assert len(d_nfe["itens"]) == 2
        assert d_nfe["itens"][0]["cst"] == "000"
        assert d_nfe["itens"][0]["bc_icms"] == "1.000,00"
        assert d_nfe["itens"][0]["p_icms"] == "18,00"
        assert d_nfe["itens"][0]["p_ipi"] == "5,00"

        xml_nfse = (PASTA_FIXTURES / "nfse_nacional_valida.xml").read_bytes()
        res_nfse = processar_arquivo_xml(xml_nfse, "nfse_nacional_valida.xml")
        ctx_nfse = montar_contexto_danfse(res_nfse, xml_bytes=xml_nfse)
        d_nfse = ctx_nfse["d"]
        assert d_nfse["numero_dps"] == "501"
        assert d_nfse["serie_dps"] == "1"
        assert d_nfse["prestador_simples"] == "Não Optante"
        assert d_nfse["amb_ger"] == "Sistema Nacional NFS-e"
        assert d_nfse["tp_amb"] == "Produção"
        assert d_nfse["iss_tipo"] == "Operação Tributável"
        assert d_nfse["iss_aliq_fmt"] == "5,00%"
        assert d_nfse["fed_irrf_fmt"] == "30,00"
        assert d_nfse["fed_csll_fmt"] == "20,00"
        assert d_nfse["ibs"]["presente"] is True
        assert d_nfse["ibs"]["v_ibs_tot_fmt"] == "20,00"
        assert d_nfse["ibs"]["v_cbs_fmt"] == "18,00"
        assert d_nfse["aprox_fed_fmt"] == "123,00"
        assert d_nfse["aprox_mun_fmt"] == "100,00"


class TestInterfaceWebEDanfeRoutes:
    """Testes de integração das rotas Web, lote de demonstração e espelhos DANFE/DANFSe."""

    def test_pagina_inicial_e_arquivos_estaticos(self, client: TestClient) -> None:
        resp_index = client.get("/")
        assert resp_index.status_code == 200
        assert "text/html" in resp_index.headers["content-type"]
        html = resp_index.text
        assert "nfe-local" in html
        assert "Carregar Lote de Demonstração" in html
        assert "Todo o Acervo Unificado" in html
        assert "Histórico de Lotes Importados" in html
        assert "btn-subtab-notas" in html
        assert "btn-subtab-resumo" in html
        assert "btn-subtab-ia" in html
        for campo_sort in (
            'data-sort="arquivo"',
            'data-sort="tipo"',
            'data-sort="numero"',
            'data-sort="data_emissao"',
            'data-sort="emitente"',
            'data-sort="destinatario"',
            'data-sort="valor_total"',
            'data-sort="status"',
        ):
            assert campo_sort in html
        assert "/static/styles.css" in html
        assert "/static/app.js" in html

        resp_css = client.get("/static/styles.css")
        assert resp_css.status_code == 200
        assert "text/css" in resp_css.headers["content-type"]
        assert "th.sortable" in resp_css.text

        resp_js = client.get("/static/app.js")
        assert resp_js.status_code == 200
        assert "javascript" in resp_js.headers["content-type"]

    def test_post_lote_demo_e_espelhos_danfe_danfse(self, client: TestClient) -> None:
        resp_demo = client.post("/api/lote/demo")
        assert resp_demo.status_code == 200
        corpo = resp_demo.json()
        lote_id = corpo["lote_id"]
        assert corpo["resumo"]["contagem"]["total_arquivos"] >= 6

        # Localiza índices por arquivo_origem no lote demo
        mapa_indices = {r["arquivo_origem"]: idx for idx, r in enumerate(corpo["resultados"])}

        # 1. DANFE NF-e 55 válida (padrão oficial completo com 14 colunas e transportador)
        idx_nfe = mapa_indices["nfe_55_valida.xml"]
        resp_danfe = client.get(f"/lote/{lote_id}/nota/{idx_nfe}/espelho")
        assert resp_danfe.status_code == 200
        assert "DANFE" in resp_danfe.text
        assert "<svg " in resp_danfe.text
        assert "11.222.333/0001-81" in resp_danfe.text
        assert "0 - ENTRADA" in resp_danfe.text
        assert "1 - SAÍDA" in resp_danfe.text
        assert "Folha 1/1" in resp_danfe.text
        assert "www.nfe.fazenda.gov.br/portal" in resp_danfe.text
        assert "Transportador / Volumes transportados" in resp_danfe.text
        assert "Código ANTT" in resp_danfe.text
        assert "0-Emitente" in resp_danfe.text
        assert "BC ICMS" in resp_danfe.text
        assert "Alíq. ICMS" in resp_danfe.text
        assert "Alíq. IPI" in resp_danfe.text
        assert "Outras desp. acessórias" in resp_danfe.text
        assert "Valor do IPI" in resp_danfe.text

        # 2. DANFE NF-e com CNPJ alfanumérico (Code 128-B)
        idx_alnum = mapa_indices["nfe_55_cnpj_alfanumerico.xml"]
        resp_alnum = client.get(f"/lote/{lote_id}/nota/{idx_alnum}/espelho")
        assert resp_alnum.status_code == 200
        assert "12.ABC.345/01DE-35" in resp_alnum.text
        assert 'data-subset="B"' in resp_alnum.text

        # 3. DANFE NF-e em Homologação (marca d'água SEM VALOR FISCAL)
        idx_homolog = mapa_indices["nfe_55_homologacao.xml"]
        resp_homolog = client.get(f"/lote/{lote_id}/nota/{idx_homolog}/espelho")
        assert resp_homolog.status_code == 200
        assert "SEM VALOR FISCAL" in resp_homolog.text
        assert "AMBIENTE_HOMOLOGACAO" in resp_homolog.text

        # 4. DANFE NFC-e 65
        idx_nfce = mapa_indices["nfce_65_valida.xml"]
        resp_nfce = client.get(f"/lote/{lote_id}/nota/{idx_nfce}/espelho")
        assert resp_nfce.status_code == 200
        assert "65" in resp_nfce.text

        # 5. DANFSe v2.0 NFS-e Padrão Nacional (NT 008/2026)
        idx_nfse = mapa_indices["nfse_nacional_valida.xml"]
        resp_danfse = client.get(f"/lote/{lote_id}/nota/{idx_nfse}/espelho")
        assert resp_danfse.status_code == 200
        assert "DANFSe v2.0" in resp_danfse.text
        assert "Documento Auxiliar da NFS-e" in resp_danfse.text
        assert "NÚMERO DA DPS" in resp_danfse.text
        assert "SÉRIE DA DPS" in resp_danfse.text
        assert "Simples Nacional na Data de Competência" in resp_danfse.text
        assert "Não Optante" in resp_danfse.text
        assert "Tributação Municipal (ISSQN)" in resp_danfse.text
        assert "Tributação Federal (Exceto CBS)" in resp_danfse.text
        assert "Tributação IBS / CBS" in resp_danfse.text
        assert "Lei nº 12.741/2012" in resp_danfse.text
        assert "IBS" in resp_danfse.text

        # 6. Download do XML original por chave e por índice
        chave_nfe = corpo["resultados"][idx_nfe]["documento"]["chave_acesso"]
        resp_xml = client.get(f"/lote/{lote_id}/nota/{chave_nfe}/xml")
        assert resp_xml.status_code == 200
        assert "application/xml" in resp_xml.headers["content-type"]
        assert "attachment" in resp_xml.headers["content-disposition"]
        assert b"<nfeProc" in resp_xml.content

        resp_xml_idx = client.get(f"/lote/{lote_id}/nota/{idx_nfe}/xml")
        assert resp_xml_idx.status_code == 200
        assert resp_xml_idx.content == resp_xml.content

        # 7. Erros 404 para lote/nota inexistentes
        assert client.get(f"/lote/{lote_id}/nota/999/espelho").status_code == 404
        assert client.get("/lote/inexistente/nota/0/espelho").status_code == 404
        assert client.get("/lote/inexistente/nota/0/xml").status_code == 404

    def test_espelho_para_xml_malformado_retorna_400(self, client: TestClient) -> None:
        resp = client.post(
            "/api/lote",
            files=[
                (
                    "files",
                    (
                        "xml_malformado.xml",
                        (PASTA_FIXTURES / "xml_malformado.xml").read_bytes(),
                        "application/xml",
                    ),
                )
            ],
        )
        lote_id = resp.json()["lote_id"]
        resp_espelho = client.get(f"/lote/{lote_id}/nota/0/espelho")
        assert resp_espelho.status_code == 400
