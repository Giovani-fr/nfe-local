"""Somas e KPIs: totais por tipo e papel, sem canceladas nem eventos; ERRO_XML com o motivo."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import app, definir_repositorio_recebidas
from models.fiscal import AlertaValidacao, DocumentoFiscal, Parte, ResultadoAnalise, Totais
from reports.resumo import gerar_quebras, gerar_resumo_lote
from sefaz.storage import RepositorioDistDFeSQLite

ALVO = "11222333000181"
OUTRO = "33444555000181"
FIXTURES = Path(__file__).parent / "fixtures"


def _res(
    tipo: str,
    valor: str,
    emitente: str,
    destinatario: str,
    *,
    chave: str,
    situacao: str = "AUTORIZADA",
) -> ResultadoAnalise:
    v = Decimal(valor)
    doc = DocumentoFiscal(
        tipo=tipo,
        modelo={"NFE": "55", "RESUMO": "55", "NFCE": "65", "NFSE": "NFSE", "EVENTO": "55"}[tipo],
        chave_acesso=chave,
        situacao=situacao,
        emitente=Parte(documento=emitente),
        destinatario=Parte(documento=destinatario),
        totais=Totais(valor_produtos_servicos=v, valor_total_nota=v),
    )
    return ResultadoAnalise(arquivo_origem=f"{chave}.xml", valido=True, documento=doc)


def _lote() -> list[ResultadoAnalise]:
    erro = ResultadoAnalise(
        arquivo_origem="ruim.xml",
        valido=False,
        documento=None,
        alertas=[AlertaValidacao(codigo="XML_MALFORMADO", severidade="ERRO", mensagem="x")],
        erro_fatal="XML malformado: tag não fechada",
    )
    return [
        _res("NFE", "100.00", ALVO, OUTRO, chave="1" * 44),  # NF-e emitida
        _res("NFE", "200.00", OUTRO, ALVO, chave="2" * 44),  # NF-e recebida
        _res("NFE", "50.00", ALVO, OUTRO, chave="3" * 44, situacao="CANCELADA"),
        _res("RESUMO", "70.00", OUTRO, ALVO, chave="4" * 44, situacao="RESUMO"),
        _res("NFSE", "300.00", ALVO, OUTRO, chave="5" * 50),  # NFS-e emitida
        _res("NFSE", "400.00", OUTRO, ALVO, chave="6" * 50),  # NFS-e tomada
        _res("NFSE", "999.00", OUTRO, ALVO, chave="7" * 50, situacao="CANCELADA"),
        _res("NFCE", "10.00", OUTRO, "", chave="8" * 44),  # consumidor, sem papel
        _res("NFE", "100.00", ALVO, OUTRO, chave="1" * 44),  # chave repetida: ignorada
        _res("EVENTO", "0", ALVO, OUTRO, chave="9" * 44),
        erro,
    ]


class TestQuebras:
    def test_totais_por_tipo_e_papel_sem_canceladas_nem_eventos(self):
        q = gerar_quebras(_lote(), ALVO)
        nfe, nfse, nfce = q.por_tipo["NFE"], q.por_tipo["NFSE"], q.por_tipo["NFCE"]
        # NF-e: emitida 100 + recebida 200 + resumo 70 (conta na NF-e); a cancelada fica de fora
        assert (nfe.quantidade, nfe.valor) == (3, Decimal("370.00"))
        assert nfe.papeis["EMITIDA"].valor == Decimal("100.00")
        assert nfe.papeis["RECEBIDA"].valor == Decimal("270.00")
        assert (nfse.quantidade, nfse.valor) == (2, Decimal("700.00"))
        assert nfse.papeis["EMITIDA"].valor == Decimal("300.00")
        assert nfse.papeis["RECEBIDA"].valor == Decimal("400.00")
        assert (nfce.quantidade, nfce.valor) == (1, Decimal("10.00"))
        assert nfce.papeis["SEM_PAPEL"].quantidade == 1
        assert q.quantidade_total == 6 and q.valor_total == Decimal("1080.00")
        assert q.por_papel["EMITIDA"].valor == Decimal("400.00")
        assert q.por_papel["RECEBIDA"].valor == Decimal("670.00")

    def test_canceladas_eventos_resumos_e_duplicadas_ficam_a_parte(self):
        q = gerar_quebras(_lote(), ALVO)
        assert q.canceladas.quantidade == 2 and q.canceladas.valor == Decimal("1049.00")
        assert q.canceladas_por_tipo == {"NFE": 1, "NFSE": 1}
        assert q.eventos == 1 and q.resumos_nfe == 1 and q.duplicadas_ignoradas == 1
        assert "CANCELADAS" in q.criterio and "eventos" in q.criterio

    def test_sem_cnpj_de_referencia_tudo_fica_sem_papel(self):
        q = gerar_quebras(_lote(), None)
        assert q.por_papel["SEM_PAPEL"].quantidade == q.quantidade_total
        assert q.por_papel["EMITIDA"].quantidade == 0

    def test_xml_ilegivel_lista_arquivo_codigo_e_motivo(self):
        q = gerar_quebras(_lote(), ALVO)
        assert len(q.erros_xml) == 1
        e = q.erros_xml[0]
        assert (e.arquivo, e.codigo) == ("ruim.xml", "XML_MALFORMADO")
        assert "tag não fechada" in e.motivo


class TestResumoLoteSemCanceladas:
    def test_valor_total_nao_soma_cancelada_e_conta_a_parte(self):
        resumo = gerar_resumo_lote(_lote())
        # 100 + 200 + 70 + 300 + 400 + 10 = 1080 (sem as canceladas e sem a chave repetida)
        assert resumo.valor_total_notas == Decimal("1080.00")
        assert resumo.contagem.canceladas_fora_dos_totais == 2
        assert resumo.valor_canceladas_fora_dos_totais == Decimal("1049.00")
        assert resumo.contagem.por_tipo["EVENTO"] == 1  # evento é contado, nunca somado
        assert resumo.contagem.por_tipo["ERRO_XML"] == 1


@pytest.fixture
def client(tmp_path):
    definir_repositorio_recebidas(RepositorioDistDFeSQLite(tmp_path / "somas.db"))
    try:
        yield TestClient(app)
    finally:
        definir_repositorio_recebidas(None)


class TestApi:
    def test_recebidas_conta_por_tipo_papel_e_deixa_cancelada_fora(self, client):
        assert client.post("/api/recebidas/demo").status_code == 200
        corpo = client.get("/api/recebidas").json()
        qt = corpo["quebras_todos_tipos"]
        nfse = qt["por_tipo"]["NFSE"]
        # NFS-e demo: 2 emitidas (1500 + 2300,50), 2 tomadas (2000 + 800) e 1 intermediária (450);
        # a sexta (999, emitida) está cancelada e não soma
        assert nfse["quantidade"] == 5 and Decimal(str(nfse["valor"])) == Decimal("7050.50")
        assert Decimal(str(nfse["papeis"]["EMITIDA"]["valor"])) == Decimal("3800.50")
        assert Decimal(str(nfse["papeis"]["RECEBIDA"]["valor"])) == Decimal("2800")
        assert Decimal(str(nfse["papeis"]["INTERMEDIARIA"]["valor"])) == Decimal("450")
        assert qt["canceladas_por_tipo"].get("NFSE") == 1
        assert corpo["total"] == 9  # a lista mostra todas, mas a soma exclui as canceladas
        assert Decimal(str(corpo["valor_total_filtrado"])) == Decimal(str(qt["valor_total"]))

    def test_filtro_de_tipo_nao_muda_os_botoes_e_nfe_inclui_o_resumo(self, client):
        client.post("/api/recebidas/demo")
        todas = client.get("/api/recebidas").json()
        so_nfse = client.get("/api/recebidas", params={"tipo": "NFSE"}).json()
        so_nfe = client.get("/api/recebidas", params={"tipo": "NFE"}).json()
        assert so_nfse["total"] == 6 and so_nfe["total"] == 3  # 3 NF-e (inclui o resNFe)
        assert so_nfse["quebras_todos_tipos"] == todas["quebras_todos_tipos"]
        assert so_nfse["quebras"]["por_tipo"]["NFE"]["quantidade"] == 0
        assert so_nfse["quebras"]["por_tipo"]["NFSE"]["quantidade"] == 5
        assert so_nfe["quebras"]["por_tipo"]["NFSE"]["quantidade"] == 0

    def test_lote_demo_traz_quebras_e_kpi_coerente(self, client):
        corpo = client.post("/api/lote/demo").json()
        q = corpo["quebras"]
        assert q is not None and q["quantidade_total"] > 0
        soma_tipos = sum(Decimal(str(b["valor"])) for b in q["por_tipo"].values())
        assert soma_tipos == Decimal(str(q["valor_total"]))
        assert Decimal(str(q["valor_total"])) == Decimal(str(corpo["resumo"]["valor_total_notas"]))
        assert "criterio" in q and isinstance(q["erros_xml"], list)

    def test_upload_com_xml_ilegivel_lista_o_motivo(self, client):
        ok = (FIXTURES / "nfe_55_valida.xml").read_bytes()
        resp = client.post(
            "/api/lote",
            files=[
                ("files", ("ok.xml", ok, "application/xml")),
                ("files", ("quebrado.xml", b"<a><b></a>", "application/xml")),
            ],
        )
        assert resp.status_code == 200
        q = resp.json()["quebras"]
        assert [e["arquivo"] for e in q["erros_xml"]] == ["quebrado.xml"]
        assert q["erros_xml"][0]["codigo"] and q["erros_xml"][0]["motivo"]
        assert resp.json()["resumo"]["contagem"]["erros_fatais_xml"] == 1

    def test_escopo_tudo_tem_quebras_com_nfse_e_origem(self, client):
        client.post("/api/recebidas/demo")
        client.post("/api/lote/demo")
        corpo = client.get("/api/lote/tudo").json()
        assert corpo["quebras"]["por_tipo"]["NFSE"]["quantidade"] >= 6
        assert {r["origem"] for r in corpo["resultados"]} >= {"ADN", "UPLOAD"}
