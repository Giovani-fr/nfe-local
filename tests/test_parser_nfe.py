"""Testes do parser seguro de NF-e (55), NFC-e (65), Resumo e Evento (`parsers/nfe.py`)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from parsers import (
    ErroLayoutDesconhecido,
    ErroParseXML,
    parse_nfe,
    parse_xml_seguro,
)

PASTA_FIXTURES = Path(__file__).resolve().parent / "fixtures"


class TestXmlSeguro:
    """Testes do wrapper anti-XXE e tratamento de erros de sintaxe."""

    def test_rejeita_xml_vazio_ou_tipo_invalido(self) -> None:
        with pytest.raises(ErroParseXML):
            parse_xml_seguro("")
        with pytest.raises(ErroParseXML):
            parse_xml_seguro(b"   ")
        with pytest.raises(ErroParseXML):
            parse_xml_seguro(12345)  # type: ignore[arg-type]

    def test_rejeita_xml_malformado(self) -> None:
        dados = (PASTA_FIXTURES / "xml_malformado.xml").read_bytes()
        with pytest.raises(ErroParseXML, match="malformado"):
            parse_nfe(dados)

    def test_neutraliza_ataque_xxe_sem_expandir_entidade(self) -> None:
        dados = (PASTA_FIXTURES / "xml_xxe_attack.xml").read_bytes()
        doc = parse_nfe(dados)
        assert doc.emitente.documento == "11222333000181"
        assert "root:" not in doc.emitente.razao_social
        assert doc.emitente.razao_social == ""


class TestParseNfe55:
    """Testes de extração completa de NF-e modelo 55."""

    def test_nfe_55_valida_completa(self) -> None:
        dados = (PASTA_FIXTURES / "nfe_55_valida.xml").read_bytes()
        doc = parse_nfe(dados)

        assert doc.tipo == "NFE"
        assert doc.modelo == "55"
        assert doc.serie == "1"
        assert doc.numero == "1001"
        assert doc.chave_acesso == "35260311222333000181550010000010011100010010"
        assert doc.ambiente == 1
        assert doc.situacao == "AUTORIZADA"
        assert doc.protocolo == "135260000001001"
        assert doc.codigo_status == "100"
        assert doc.competencia == date(2026, 3, 10)

        assert doc.emitente.documento == "11222333000181"
        assert doc.emitente.tipo_pessoa == "PJ"
        assert doc.emitente.razao_social == "INDUSTRIA ALFA METALURGICA LTDA"
        assert doc.emitente.nome_fantasia == "ALFA METAL"
        assert doc.emitente.uf == "SP"
        assert doc.emitente.municipio == "SAO PAULO"
        assert doc.emitente.codigo_ibge == "3550308"

        assert doc.destinatario is not None
        assert doc.destinatario.documento == "22333444000181"
        assert doc.destinatario.razao_social == "COMERCIO BETA DISTRIBUIDORA LTDA"
        assert doc.destinatario.email == "fiscal@comerciobeta.exemplo.br"

        assert len(doc.itens) == 2
        item1 = doc.itens[0]
        assert item1.numero_item == 1
        assert item1.codigo == "P001"
        assert item1.ncm == "73269090"
        assert item1.cfop == "5101"
        assert item1.quantidade == Decimal("10.0000")
        assert item1.valor_unitario == Decimal("100.0000")
        assert item1.valor_total == Decimal("1000.00")
        assert item1.valor_frete == Decimal("30.00")
        assert item1.valor_desconto == Decimal("20.00")
        assert item1.imposto.cst_ou_csosn == "000"
        assert item1.imposto.icms_bc == Decimal("1000.00")
        assert item1.imposto.icms_aliquota == Decimal("18.00")
        assert item1.imposto.icms_valor == Decimal("180.00")
        assert item1.imposto.ipi_aliquota == Decimal("5.00")
        assert item1.imposto.ipi_valor == Decimal("50.00")
        assert item1.imposto.pis_valor == Decimal("16.50")
        assert item1.imposto.cofins_valor == Decimal("76.00")
        assert item1.info_adicional == "LOTE FICTICIO 2026-A"

        assert doc.totais.valor_produtos_servicos == Decimal("1500.00")
        assert doc.totais.valor_frete == Decimal("50.00")
        assert doc.totais.valor_desconto == Decimal("30.00")
        assert doc.totais.impostos.icms == Decimal("270.00")
        assert doc.totais.impostos.ipi == Decimal("75.00")
        assert doc.totais.impostos.pis == Decimal("24.75")
        assert doc.totais.impostos.cofins == Decimal("114.00")
        assert doc.totais.impostos.tributos_aproximados == Decimal("483.75")
        assert doc.totais.valor_total_nota == Decimal("1595.00")

        duplicatas = [p for p in doc.pagamentos if p.numero_duplicata]
        assert len(duplicatas) == 2
        assert duplicatas[0].numero_duplicata == "001"
        assert duplicatas[0].vencimento == date(2026, 4, 10)
        assert duplicatas[0].valor == Decimal("800.00")

        assert doc.informacoes_complementares is not None
        assert "FICTICIOS" in doc.informacoes_complementares

    def test_nfe_55_sem_protocolo(self) -> None:
        dados = (PASTA_FIXTURES / "nfe_55_sem_protocolo.xml").read_bytes()
        doc = parse_nfe(dados)

        assert doc.tipo == "NFE"
        assert doc.modelo == "55"
        assert doc.situacao == "SEM_PROTOCOLO"
        assert doc.protocolo is None
        assert doc.chave_acesso == "35260311222333000181550010000010021100010026"
        assert doc.totais.valor_total_nota == Decimal("500.00")

    def test_nfe_55_cnpj_alfanumerico(self) -> None:
        dados = (PASTA_FIXTURES / "nfe_55_cnpj_alfanumerico.xml").read_bytes()
        doc = parse_nfe(dados)

        assert doc.emitente.documento == "12ABC34501DE35"
        assert doc.emitente.tipo_pessoa == "PJ"
        assert doc.chave_acesso == "35260312ABC34501DE35550010000010031100010032"
        assert doc.chave_acesso[6:20] == doc.emitente.documento

    def test_nfe_55_homologacao(self) -> None:
        dados = (PASTA_FIXTURES / "nfe_55_homologacao.xml").read_bytes()
        doc = parse_nfe(dados)

        assert doc.ambiente == 2
        assert doc.situacao == "AUTORIZADA"


class TestParseNfce65EResumoEvento:
    """Testes de NFC-e (65), Resumo (`resNFe`), Evento (`procEventoNFe`) e rejeição de CT-e."""

    def test_nfce_65_valida(self) -> None:
        dados = (PASTA_FIXTURES / "nfce_65_valida.xml").read_bytes()
        doc = parse_nfe(dados)

        assert doc.tipo == "NFCE"
        assert doc.modelo == "65"
        assert doc.numero == "2001"
        assert doc.destinatario is not None
        assert doc.destinatario.tipo_pessoa == "PF"
        assert doc.destinatario.documento == "12345678909"
        assert len(doc.pagamentos) == 2
        assert doc.pagamentos[0].forma_codigo == "17"
        assert "PIX" in doc.pagamentos[0].forma_descricao
        assert doc.pagamentos[0].valor == Decimal("70.00")
        assert doc.pagamentos[1].forma_codigo == "03"
        assert doc.pagamentos[1].valor == Decimal("50.00")

    def test_nfe_resumo(self) -> None:
        dados = (PASTA_FIXTURES / "nfe_resumo.xml").read_bytes()
        doc = parse_nfe(dados)

        assert doc.tipo == "RESUMO"
        assert doc.situacao == "RESUMO"
        assert doc.chave_acesso == "35260311222333000181550010000010071100010073"
        assert doc.serie == "1"
        assert doc.numero == "1007"
        assert doc.emitente.documento == "11222333000181"
        assert doc.emitente.razao_social == "INDUSTRIA ALFA METALURGICA LTDA"
        assert doc.totais.valor_total_nota == Decimal("2450.00")
        assert doc.protocolo == "135260000001007"

    def test_nfe_evento_cancelamento(self) -> None:
        dados = (PASTA_FIXTURES / "nfe_evento_cancelamento.xml").read_bytes()
        doc = parse_nfe(dados)

        assert doc.tipo == "EVENTO"
        assert doc.situacao == "EVENTO"
        assert doc.chave_acesso == "35260311222333000181550010000010011100010010"
        assert doc.tipo_evento == "110111"
        assert doc.descricao_evento is not None
        assert "FICTICIOS" in doc.descricao_evento
        assert doc.codigo_status == "135"
        assert doc.protocolo == "135260000099001"

    def test_rejeita_cte_com_infnfe_aninhado(self) -> None:
        xml_cte = """<?xml version="1.0" encoding="UTF-8"?>
        <cteProc xmlns="http://www.portalfiscal.inf.br/cte" versao="4.00">
          <CTe>
            <infCte Id="CTe35260311222333000181570010000001001000001009">
              <infCTeNorm>
                <infDoc>
                  <infNFe>
                    <chave>35260311222333000181550010000010011100010010</chave>
                  </infNFe>
                </infDoc>
              </infCTeNorm>
            </infCte>
          </CTe>
        </cteProc>
        """
        with pytest.raises(ErroLayoutDesconhecido, match="CT-e"):
            parse_nfe(xml_cte)

    def test_rejeita_layout_desconhecido_abrasf(self) -> None:
        dados = (PASTA_FIXTURES / "nfse_abrasf_legada.xml").read_bytes()
        with pytest.raises(ErroLayoutDesconhecido):
            parse_nfe(dados)
