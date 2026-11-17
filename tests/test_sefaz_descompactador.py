"""Testes unitários (100% offline) de descompactação de `docZip` e integração com o parser XML."""

from __future__ import annotations

import base64
import gzip
from decimal import Decimal
from pathlib import Path

import pytest

from sefaz.descompactador import (
    CODIGO_ALERTA_MANIFESTACAO_NECESSARIA,
    CODIGO_ERRO_DOCZIP_CORROMPIDO,
    DocumentoDistDFeProcessado,
    ErroDescompactacaoDocZip,
    classificar_schema_doc_zip,
    compactar_xml_para_doc_zip_b64,
    descompactar_doc_zip,
    processar_item_doc_zip,
    processar_lote_doc_zip,
)
from sefaz.dist_dfe import ItemDocZip

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class TestDescompactadorDocZip:
    """Testes de compactação, descompactação segura e processamento de `docZip` da SEFAZ."""

    @pytest.mark.parametrize(
        "nome_fixture",
        [
            "nfe_55_valida.xml",
            "nfe_resumo.xml",
            "nfe_evento_cancelamento.xml",
        ],
    )
    def test_compactar_e_descompactar_fixtures_roundtrip(self, nome_fixture: str) -> None:
        xml_original = (FIXTURES_DIR / nome_fixture).read_bytes()
        b64 = compactar_xml_para_doc_zip_b64(xml_original)

        assert isinstance(b64, str)
        assert len(b64) > 0

        xml_recuperado = descompactar_doc_zip(b64)
        assert xml_recuperado == xml_original

    def test_fallback_xml_texto_plano_em_base64_e_rejeicao_de_payload_invalido(self) -> None:
        xml_plano = b"<resNFe><chNFe>35260311222333000181550010000010071100010073</chNFe></resNFe>"
        b64_plano = base64.b64encode(xml_plano).decode("ascii")
        assert descompactar_doc_zip(b64_plano) == xml_plano

        with pytest.raises(ErroDescompactacaoDocZip, match="vazio"):
            descompactar_doc_zip("   ")

        with pytest.raises(ErroDescompactacaoDocZip, match="Base64"):
            descompactar_doc_zip("@@@nao_eh_base64@@@")

        b64_lixo_binario = base64.b64encode(b"\x00\x01\x02\x03lixo").decode("ascii")
        with pytest.raises(ErroDescompactacaoDocZip):
            descompactar_doc_zip(b64_lixo_binario)

    def test_processar_res_nfe_marca_manifestacao_necessaria_e_adiciona_alerta_aviso(
        self,
    ) -> None:
        xml_resumo = (FIXTURES_DIR / "nfe_resumo.xml").read_bytes()
        item = ItemDocZip(
            nsu="000000000000041",
            schema_xml="resNFe_v1.01.xsd",
            conteudo_base64=compactar_xml_para_doc_zip_b64(xml_resumo),
        )

        proc = processar_item_doc_zip(item)

        assert isinstance(proc, DocumentoDistDFeProcessado)
        assert proc.nsu == "000000000000041"
        assert proc.schema_xml == "resNFe_v1.01.xsd"
        assert proc.categoria_schema == "RESUMO_NFE"
        assert proc.manifestacao_necessaria is True
        assert proc.xml_descompactado == xml_resumo
        assert proc.resultado.valido is True
        assert proc.resultado.documento is not None
        assert proc.resultado.documento.tipo == "RESUMO"
        assert proc.resultado.documento.situacao == "RESUMO"
        assert (
            proc.resultado.documento.chave_acesso == "35260311222333000181550010000010071100010073"
        )
        assert proc.resultado.documento.totais.valor_total_nota == Decimal("2450.00")

        codigos_alerta = [a.codigo for a in proc.resultado.alertas]
        assert CODIGO_ALERTA_MANIFESTACAO_NECESSARIA in codigos_alerta
        alerta_manif = next(
            a for a in proc.resultado.alertas if a.codigo == CODIGO_ALERTA_MANIFESTACAO_NECESSARIA
        )
        assert alerta_manif.severidade == "AVISO"
        assert "210210" in alerta_manif.mensagem

    def test_processar_proc_nfe_completa_extrai_itens_impostos_sem_pedir_manifestacao(
        self,
    ) -> None:
        xml_nfe = (FIXTURES_DIR / "nfe_55_valida.xml").read_bytes()
        item = ItemDocZip(
            nsu="000000000000042",
            schema_xml="procNFe_v4.00.xsd",
            conteudo_base64=compactar_xml_para_doc_zip_b64(xml_nfe),
        )

        proc = processar_item_doc_zip(item)

        assert proc.nsu == "000000000000042"
        assert proc.categoria_schema == "NFE_COMPLETA"
        assert proc.manifestacao_necessaria is False
        assert proc.resultado.valido is True
        assert proc.resultado.documento is not None
        doc = proc.resultado.documento
        assert doc.tipo == "NFE"
        assert doc.situacao == "AUTORIZADA"
        assert len(doc.itens) == 2
        assert doc.totais.valor_total_nota == Decimal("1595.00")
        assert doc.totais.impostos.icms == Decimal("270.00")
        assert doc.totais.impostos.ipi == Decimal("75.00")
        assert all(
            a.codigo != CODIGO_ALERTA_MANIFESTACAO_NECESSARIA for a in proc.resultado.alertas
        )

    def test_processar_proc_evento_nfe_classifica_como_evento(self) -> None:
        xml_evento = (FIXTURES_DIR / "nfe_evento_cancelamento.xml").read_bytes()
        item = ItemDocZip(
            nsu="000000000000043",
            schema_xml="procEventoNFe_v1.00.xsd",
            conteudo_base64=compactar_xml_para_doc_zip_b64(xml_evento),
        )

        proc = processar_item_doc_zip(item)

        assert proc.nsu == "000000000000043"
        assert proc.categoria_schema == "EVENTO_NFE"
        assert proc.manifestacao_necessaria is False
        assert proc.resultado.valido is True
        assert proc.resultado.documento is not None
        assert proc.resultado.documento.tipo == "EVENTO"
        assert proc.resultado.documento.tipo_evento == "110111"

    def test_processar_lote_doc_zip_resiliente_a_b64_invalido_gzip_corrompido_e_gzip_bomb(
        self,
    ) -> None:
        xml_resumo = (FIXTURES_DIR / "nfe_resumo.xml").read_bytes()
        xml_nfe = (FIXTURES_DIR / "nfe_55_valida.xml").read_bytes()

        # Cria um GZIP com header válido mas stream truncado/corrompido
        gzip_corrompido_b64 = base64.b64encode(b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\xff").decode(
            "ascii"
        )

        # Cria um payload GZIP grande (ex.: 32 KB) para testar o limite max_bytes_xml
        xml_grande = b"<resNFe>" + (b"A" * 32768) + b"</resNFe>"
        gzip_grande_b64 = base64.b64encode(gzip.compress(xml_grande)).decode("ascii")

        # Passa os itens fora de ordem para garantir que processar_lote_doc_zip ordena por NSU
        lote = [
            ItemDocZip(
                nsu="000000000000014",
                schema_xml="procNFe_v4.00.xsd",
                conteudo_base64=compactar_xml_para_doc_zip_b64(xml_nfe),
            ),
            ItemDocZip(
                nsu="000000000000012",
                schema_xml="procNFe_v4.00.xsd",
                conteudo_base64=gzip_corrompido_b64,
            ),
            ItemDocZip(
                nsu="000000000000011",
                schema_xml="resNFe_v1.01.xsd",
                conteudo_base64="***BASE64_INVALIDO***",
            ),
            ItemDocZip(
                nsu="000000000000013",
                schema_xml="resNFe_v1.01.xsd",
                conteudo_base64=gzip_grande_b64,
            ),
            ItemDocZip(
                nsu="000000000000010",
                schema_xml="resNFe_v1.01.xsd",
                conteudo_base64=compactar_xml_para_doc_zip_b64(xml_resumo),
            ),
        ]

        resultados = processar_lote_doc_zip(lote, max_bytes_xml=16384)

        assert [r.nsu for r in resultados] == [
            "000000000000010",
            "000000000000011",
            "000000000000012",
            "000000000000013",
            "000000000000014",
        ]

        # NSU 10 (resNFe válido)
        assert resultados[0].resultado.valido is True
        assert resultados[0].manifestacao_necessaria is True

        # NSUs 11, 12 e 13 (falhas isoladas com código DOCZIP_CORROMPIDO)
        for idx in (1, 2, 3):
            res_erro = resultados[idx]
            assert res_erro.resultado.valido is False
            assert res_erro.resultado.documento is None
            assert res_erro.xml_descompactado == b""
            assert res_erro.resultado.alertas[0].codigo == CODIGO_ERRO_DOCZIP_CORROMPIDO

        # NSU 14 (procNFe válido)
        assert resultados[4].resultado.valido is True
        assert resultados[4].categoria_schema == "NFE_COMPLETA"

    def test_classificar_schema_doc_zip_variantes(self) -> None:
        assert classificar_schema_doc_zip("resNFe_v1.01.xsd") == "RESUMO_NFE"
        assert classificar_schema_doc_zip("procNFe_v4.00.xsd") == "NFE_COMPLETA"
        assert classificar_schema_doc_zip("nfeProc_v4.00.xsd") == "NFE_COMPLETA"
        assert classificar_schema_doc_zip("procEventoNFe_v1.00.xsd") == "EVENTO_NFE"
        assert classificar_schema_doc_zip("resEvento_v1.01.xsd") == "EVENTO_NFE"
        assert classificar_schema_doc_zip("schemaDesconhecido.xsd") == "OUTRO"
