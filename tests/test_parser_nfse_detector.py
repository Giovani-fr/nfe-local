"""Testes do parser de NFS-e Padrão Nacional e do detector automático (`parsers/detector.py`)."""

from __future__ import annotations

import io
import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from parsers import (
    ErroLayoutNFSeMunicipalLegado,
    ErroParseXML,
    extrair_xmls_de_zip,
    parse_documento_xml,
    parse_nfse_nacional,
    processar_arquivo_xml,
    processar_lote,
)

PASTA_FIXTURES = Path(__file__).resolve().parent / "fixtures"


class TestParseNfseNacional:
    """Testes de extração da NFS-e Padrão Nacional (`sped.fazenda.gov.br/nfse`)."""

    def test_nfse_nacional_valida_completa(self) -> None:
        dados = (PASTA_FIXTURES / "nfse_nacional_valida.xml").read_bytes()
        doc = parse_nfse_nacional(dados)

        assert doc.tipo == "NFSE"
        assert doc.modelo == "NFSE"
        assert doc.numero == "501"
        assert doc.serie == "1"
        assert doc.chave_acesso == "35503082233444555000181000000000050126030000001005"
        assert len(doc.chave_acesso) == 50
        assert doc.ambiente == 1
        assert doc.situacao == "AUTORIZADA"
        assert doc.protocolo == "9000501"
        assert doc.competencia == date(2026, 3, 16)

        assert doc.emitente.documento == "33444555000181"
        assert doc.emitente.tipo_pessoa == "PJ"
        assert doc.emitente.razao_social == "SERVICOS TECNOLOGIA GAMA SA"
        assert doc.emitente.nome_fantasia == "GAMA TECH"
        assert doc.emitente.inscricao_municipal == "99887766"
        assert doc.emitente.uf == "SP"
        assert doc.emitente.municipio == "SAO PAULO"
        assert doc.emitente.codigo_ibge == "3550308"
        assert doc.emitente.email == "faturamento@gamatech.exemplo.br"

        assert doc.destinatario is not None
        assert doc.destinatario.documento == "11222333000181"
        assert doc.destinatario.razao_social == "INDUSTRIA ALFA METALURGICA LTDA"
        assert doc.destinatario.inscricao_municipal == "11223344"

        assert len(doc.itens) == 1
        item = doc.itens[0]
        assert item.codigo_tributacao_nacional == "010101"
        assert item.codigo_nbs == "115021000"
        assert "DESENVOLVIMENTO" in item.descricao
        assert item.valor_total == Decimal("2000.00")
        assert item.imposto.iss_aliquota == Decimal("5.00")
        assert item.imposto.iss_valor == Decimal("100.00")
        assert item.imposto.iss_retido is False
        assert item.imposto.pis_valor == Decimal("13.00")
        assert item.imposto.cofins_valor == Decimal("60.00")

        assert doc.totais.valor_produtos_servicos == Decimal("2000.00")
        assert doc.totais.valor_liquido == Decimal("1877.00")
        assert doc.totais.valor_total_nota == Decimal("2000.00")
        assert doc.totais.impostos.iss == Decimal("100.00")
        assert doc.totais.impostos.pis == Decimal("13.00")
        assert doc.totais.impostos.cofins == Decimal("60.00")
        assert doc.totais.impostos.retencoes_federais == Decimal("123.00")
        assert doc.totais.impostos.ibs_cbs == Decimal("38.00")
        assert doc.totais.impostos.tributos_aproximados == Decimal("223.00")

        res = processar_arquivo_xml(dados, "nfse_nacional_valida.xml")
        assert res.valido is True
        assert res.alertas == []

    def test_rejeita_nfse_municipal_legada_abrasf(self) -> None:
        dados = (PASTA_FIXTURES / "nfse_abrasf_legada.xml").read_bytes()
        with pytest.raises(ErroLayoutNFSeMunicipalLegado, match="Padrão Nacional"):
            parse_nfse_nacional(dados)

        res = processar_arquivo_xml(dados, "nfse_abrasf_legada.xml")
        assert res.valido is False
        assert res.documento is None
        assert res.erro_fatal is not None
        assert res.alertas[0].codigo == "LAYOUT_NFSE_MUNICIPAL_DESCONTINUADO"


class TestDetectorAutomaticoELoteZip:
    """Testes de detecção automática de documentos, arquivos ZIP e segurança anti-zip-bomb."""

    @pytest.mark.parametrize(
        ("arquivo", "tipo_esperado", "modelo_esperado"),
        [
            ("nfe_55_valida.xml", "NFE", "55"),
            ("nfe_55_sem_protocolo.xml", "NFE", "55"),
            ("nfe_55_cnpj_alfanumerico.xml", "NFE", "55"),
            ("nfce_65_valida.xml", "NFCE", "65"),
            ("nfse_nacional_valida.xml", "NFSE", "NFSE"),
            ("nfe_resumo.xml", "RESUMO", "55"),
            ("nfe_evento_cancelamento.xml", "EVENTO", "55"),
        ],
    )
    def test_detector_identifica_tipo_e_modelo(
        self,
        arquivo: str,
        tipo_esperado: str,
        modelo_esperado: str,
    ) -> None:
        dados = (PASTA_FIXTURES / arquivo).read_bytes()
        doc = parse_documento_xml(dados)
        assert doc.tipo == tipo_esperado
        assert doc.modelo == modelo_esperado

    def test_processar_lote_com_zip_em_memoria(self) -> None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for nome in (
                "nfe_55_valida.xml",
                "nfce_65_valida.xml",
                "nfse_nacional_valida.xml",
                "nfe_resumo.xml",
            ):
                zf.writestr(f"subpasta/{nome}", (PASTA_FIXTURES / nome).read_bytes())
            zf.writestr("__MACOSX/._nfe_55_valida.xml", b"lixo")
            zf.writestr("leia-me.txt", b"ignorar txt")

        resultados = processar_lote([("lote_teste.zip", buf.getvalue())])
        assert len(resultados) == 4
        assert all(r.valido for r in resultados)
        tipos = [r.documento.tipo for r in resultados if r.documento]
        assert tipos == ["NFE", "NFCE", "NFSE", "RESUMO"]

    def test_protecao_contra_path_traversal_e_limites_zip(self) -> None:
        buf_traversal = io.BytesIO()
        with zipfile.ZipFile(buf_traversal, "w") as zf:
            zf.writestr("../escape.xml", b"<NFe/>")

        with pytest.raises(ErroParseXML, match="path traversal"):
            list(extrair_xmls_de_zip(buf_traversal.getvalue()))

        buf_grande = io.BytesIO()
        with zipfile.ZipFile(buf_grande, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("grande.xml", b"A" * 5000)

        with pytest.raises(ErroParseXML, match="excede o tamanho"):
            list(extrair_xmls_de_zip(buf_grande.getvalue(), max_bytes_arquivo=1000))
