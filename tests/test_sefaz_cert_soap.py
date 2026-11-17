"""Testes unitários (100% offline) do leitor de Certificado A1 e cliente SOAP DistDFe."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

from parsers.xml_seguro import parse_xml_seguro, primeiro, texto
from sefaz.cert_a1 import (
    OID_ICP_BRASIL_CNPJ,
    ErroCertificadoA1,
    MetadadosCertificado,
    carregar_certificado_de_env,
    carregar_pkcs12_memoria,
    extrair_metadados_certificado,
)
from sefaz.dist_dfe import (
    CONTENT_TYPE_SOAP_DIST_DFE,
    URL_DIST_DFE_HOMOLOGACAO,
    URL_DIST_DFE_PRODUCAO,
    ClienteNFeDistribuicaoDFe,
    ErroComunicacaoSEFAZ,
    formatar_nsu,
    montar_envelope_dist_dfe,
    parse_resposta_dist_dfe,
)


def _encode_asn1_utf8_string(valor: str) -> bytes:
    """Codifica uma string em DER UTF8String (tag 0x0C) para `x509.OtherName`."""
    dados = valor.encode("utf-8")
    assert len(dados) < 128
    return bytes([0x0C, len(dados)]) + dados


def _gerar_pfx_sintetico(
    *,
    common_name: str = "EMPRESA SINTETICA TESTE LTDA",
    cnpj_oid: str | None = "11222333000181",
    senha: str = "senha-teste-123",
    dias_inicio: int = -10,
    dias_fim: int = 180,
) -> bytes:
    """Gera em memória um arquivo PKCS#12 (`.pfx`) sintético autoassinado para testes."""
    chave_privada = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    agora = datetime.now(UTC)

    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "BR"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "ICP-Brasil Sintetico"),
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        ]
    )
    issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "BR"),
            x509.NameAttribute(NameOID.COMMON_NAME, "AC SINTETICA HOMOLOGACAO"),
        ]
    )

    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(chave_privada.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(agora + timedelta(days=dias_inicio))
        .not_valid_after(agora + timedelta(days=dias_fim))
    )

    if cnpj_oid is not None:
        other_name = x509.OtherName(
            x509.ObjectIdentifier(OID_ICP_BRASIL_CNPJ),
            _encode_asn1_utf8_string(cnpj_oid),
        )
        builder = builder.add_extension(
            x509.SubjectAlternativeName([other_name]),
            critical=False,
        )

    certificado = builder.sign(private_key=chave_privada, algorithm=hashes.SHA256())

    return pkcs12.serialize_key_and_certificates(
        name=b"cert-sintetico",
        key=chave_privada,
        cert=certificado,
        cas=None,
        encryption_algorithm=serialization.BestAvailableEncryption(senha.encode("utf-8")),
    )


class TestCertificadoA1:
    """Testes de leitura em memória e extração de metadados do Certificado Digital A1."""

    def test_extrair_metadados_via_oid_icp_brasil_numerico_e_alfanumerico(self) -> None:
        pfx_num = _gerar_pfx_sintetico(
            common_name="METALURGICA TESTE S.A.",
            cnpj_oid="11222333000181",
            senha="segredo",
        )
        meta_num = extrair_metadados_certificado(pfx_num, "segredo")
        assert isinstance(meta_num, MetadadosCertificado)
        assert meta_num.titular == "METALURGICA TESTE S.A."
        assert meta_num.documento == "11222333000181"
        assert "AC SINTETICA HOMOLOGACAO" in meta_num.emissor
        assert meta_num.vencido is False
        assert meta_num.ainda_nao_valido is False
        assert meta_num.dias_para_vencer >= 170
        assert len(meta_num.thumbprint_sha1) == 40

        # CNPJ alfanumérico (NT 2014.002) no OID ICP-Brasil
        pfx_alfa = _gerar_pfx_sintetico(
            common_name="INDUSTRIA ALFA LTDA",
            cnpj_oid="12ABC34501DE35",
            senha="segredo",
        )
        meta_alfa = extrair_metadados_certificado(pfx_alfa, "segredo")
        assert meta_alfa.documento == "12ABC34501DE35"

    def test_extrair_cnpj_fallback_common_name(self) -> None:
        pfx_cn = _gerar_pfx_sintetico(
            common_name="COMERCIO EXEMPLO LTDA:11.222.333/0001-81",
            cnpj_oid=None,
            senha="123",
        )
        meta = extrair_metadados_certificado(pfx_cn, "123")
        assert meta.documento == "11222333000181"
        assert "COMERCIO EXEMPLO LTDA" in meta.titular

    def test_detecta_certificado_vencido_e_ainda_nao_valido(self) -> None:
        pfx_vencido = _gerar_pfx_sintetico(dias_inicio=-400, dias_fim=-5, senha="123")
        meta_vencido = extrair_metadados_certificado(pfx_vencido, "123")
        assert meta_vencido.vencido is True
        assert meta_vencido.dias_para_vencer < 0

        pfx_futuro = _gerar_pfx_sintetico(dias_inicio=5, dias_fim=365, senha="123")
        meta_futuro = extrair_metadados_certificado(pfx_futuro, "123")
        assert meta_futuro.ainda_nao_valido is True
        assert meta_futuro.vencido is False

    def test_senha_incorreta_ou_bytes_invalidos_levantam_erro_certificado(self) -> None:
        pfx = _gerar_pfx_sintetico(senha="senha-correta")
        with pytest.raises(ErroCertificadoA1):
            carregar_pkcs12_memoria(pfx, "senha-errada")

        with pytest.raises(ErroCertificadoA1):
            carregar_pkcs12_memoria(b"nao-eh-um-pfx", "qualquer")

    def test_carregar_certificado_de_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("NFE_CERT_PATH", raising=False)
        monkeypatch.delenv("NFE_CERT_SENHA", raising=False)
        with pytest.raises(ErroCertificadoA1) as exc_sem_env:
            carregar_certificado_de_env()
        assert "NFE_CERT_PATH" in str(exc_sem_env.value)

        caminho_pfx = tmp_path / "cert_teste.pfx"
        caminho_pfx.write_bytes(_gerar_pfx_sintetico(cnpj_oid="11222333000181", senha="abc"))

        monkeypatch.setenv("NFE_CERT_PATH", str(caminho_pfx))
        monkeypatch.setenv("NFE_CERT_SENHA", "abc")

        pfx_lido, senha_lida, meta = carregar_certificado_de_env()
        assert pfx_lido == caminho_pfx.read_bytes()
        assert senha_lida == "abc"
        assert meta.documento == "11222333000181"


class TestDistDFeSoap:
    """Testes de montagem do envelope SOAP 1.2, parser de retorno e cliente DistDFe."""

    def test_formatar_nsu_15_digitos(self) -> None:
        assert formatar_nsu(0) == "000000000000000"
        assert formatar_nsu("42") == "000000000000042"
        assert formatar_nsu("000000000012345") == "000000000012345"

        with pytest.raises(ValueError):
            formatar_nsu(-1)

        with pytest.raises(ValueError):
            formatar_nsu("1" * 16)

    def test_montar_envelope_dist_dfe_estrutura_e_namespaces(self) -> None:
        xml_bytes = montar_envelope_dist_dfe(
            documento="11.222.333/0001-81",
            ult_nsu=125,
            uf_autor="35",
            ambiente=2,
        )
        raiz = parse_xml_seguro(xml_bytes)
        assert "http://www.w3.org/2003/05/soap-envelope" in raiz.tag

        interesse = primeiro(raiz, "nfeDistDFeInteresse")
        assert interesse is not None
        assert "NFeDistribuicaoDFe" in interesse.tag

        dist_int = primeiro(raiz, "distDFeInt")
        assert dist_int is not None
        assert dist_int.attrib.get("versao") == "1.01"
        assert texto(dist_int, "tpAmb") == "2"
        assert texto(dist_int, "cUFAutor") == "35"
        assert texto(dist_int, "CNPJ") == "11222333000181"
        assert texto(dist_int, "ultNSU") == "000000000000125"

        # Suporte a CPF (11 dígitos)
        xml_cpf = montar_envelope_dist_dfe(documento="123.456.789-01", ult_nsu=0)
        raiz_cpf = parse_xml_seguro(xml_cpf)
        assert texto(raiz_cpf, "CPF") == "12345678901"

    def test_parse_resposta_dist_dfe_138_com_multiplos_doczip(self) -> None:
        soap_138 = """<?xml version="1.0" encoding="utf-8"?>
        <soap12:Envelope xmlns:soap12="http://www.w3.org/2003/05/soap-envelope">
          <soap12:Body>
            <nfeDistDFeInteresseResponse xmlns="http://www.portalfiscal.inf.br/nfe/wsdl/NFeDistribuicaoDFe">
              <nfeDistDFeInteresseResult>
                <retDistDFeInt xmlns="http://www.portalfiscal.inf.br/nfe" versao="1.01">
                  <tpAmb>2</tpAmb>
                  <verAplic>1.6.3</verAplic>
                  <cStat>138</cStat>
                  <xMotivo>Documento(s) localizado(s)</xMotivo>
                  <dhResp>2026-03-15T10:20:30-03:00</dhResp>
                  <ultNSU>000000000000102</ultNSU>
                  <maxNSU>000000000000150</maxNSU>
                  <loteDistDFeInt>
                    <docZip NSU="000000000000101" schema="resNFe_v1.01.xsd">H4sIAAAAAAAAA==</docZip>
                    <docZip NSU="000000000000102" schema="procNFe_v4.00.xsd">H4sIBBBBBBBB==</docZip>
                  </loteDistDFeInt>
                </retDistDFeInt>
              </nfeDistDFeInteresseResult>
            </nfeDistDFeInteresseResponse>
          </soap12:Body>
        </soap12:Envelope>
        """
        resp = parse_resposta_dist_dfe(soap_138)
        assert resp.ambiente == 2
        assert resp.ver_aplic == "1.6.3"
        assert resp.cstat == "138"
        assert resp.xmotivo == "Documento(s) localizado(s)"
        assert resp.ult_nsu == "000000000000102"
        assert resp.max_nsu == "000000000000150"
        assert resp.dh_resp is not None
        assert len(resp.lote_doc_zip) == 2
        assert resp.lote_doc_zip[0].nsu == "000000000000101"
        assert resp.lote_doc_zip[0].schema_xml == "resNFe_v1.01.xsd"
        assert resp.lote_doc_zip[0].conteudo_base64 == "H4sIAAAAAAAAA=="
        assert resp.lote_doc_zip[1].nsu == "000000000000102"
        assert resp.lote_doc_zip[1].schema_xml == "procNFe_v4.00.xsd"

    def test_parse_resposta_dist_dfe_137_e_656(self) -> None:
        soap_137 = """<?xml version="1.0" encoding="utf-8"?>
        <soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope">
          <soap:Body>
            <retDistDFeInt xmlns="http://www.portalfiscal.inf.br/nfe" versao="1.01">
              <tpAmb>2</tpAmb>
              <verAplic>1.6.3</verAplic>
              <cStat>137</cStat>
              <xMotivo>Nenhum documento localizado</xMotivo>
              <dhResp>2026-03-15T10:25:00-03:00</dhResp>
              <ultNSU>000000000000150</ultNSU>
              <maxNSU>000000000000150</maxNSU>
            </retDistDFeInt>
          </soap:Body>
        </soap:Envelope>
        """
        r137 = parse_resposta_dist_dfe(soap_137)
        assert r137.cstat == "137"
        assert r137.lote_doc_zip == []

        soap_656 = soap_137.replace("<cStat>137</cStat>", "<cStat>656</cStat>").replace(
            "Nenhum documento localizado",
            "Rejeicao: Consumo Indevido",
        )
        r656 = parse_resposta_dist_dfe(soap_656)
        assert r656.cstat == "656"
        assert "Consumo Indevido" in r656.xmotivo

    def test_parse_resposta_soap_fault_levanta_erro_comunicacao(self) -> None:
        soap_fault = """<?xml version="1.0" encoding="utf-8"?>
        <soap12:Envelope xmlns:soap12="http://www.w3.org/2003/05/soap-envelope">
          <soap12:Body>
            <soap12:Fault>
              <soap12:Code><soap12:Value>soap12:Receiver</soap12:Value></soap12:Code>
              <soap12:Reason>
                <soap12:Text xml:lang="pt">Erro interno no WebService</soap12:Text>
              </soap12:Reason>
            </soap12:Fault>
          </soap12:Body>
        </soap12:Envelope>
        """
        with pytest.raises(ErroComunicacaoSEFAZ) as exc_info:
            parse_resposta_dist_dfe(soap_fault)
        assert "Erro interno no WebService" in str(exc_info.value)

    def test_cliente_consultar_dist_nsu_usa_cnpj_do_certificado_e_sessao_injetada(self) -> None:
        pfx = _gerar_pfx_sintetico(cnpj_oid="11222333000181", senha="123")
        requisicoes_capturadas: list[dict[str, Any]] = []

        class FakeResponse:
            status_code = 200
            content = b"""<?xml version="1.0" encoding="utf-8"?>
            <soap12:Envelope xmlns:soap12="http://www.w3.org/2003/05/soap-envelope">
              <soap12:Body>
                <retDistDFeInt xmlns="http://www.portalfiscal.inf.br/nfe" versao="1.01">
                  <tpAmb>2</tpAmb>
                  <verAplic>1.6.3</verAplic>
                  <cStat>137</cStat>
                  <xMotivo>Nenhum documento localizado</xMotivo>
                  <dhResp>2026-03-15T11:00:00-03:00</dhResp>
                  <ultNSU>000000000000055</ultNSU>
                  <maxNSU>000000000000055</maxNSU>
                </retDistDFeInt>
              </soap12:Body>
            </soap12:Envelope>"""
            text = content.decode("utf-8")

        class FakeSession:
            def post(self, url: str, *, data: bytes, headers: dict[str, str], timeout: float):
                requisicoes_capturadas.append(
                    {"url": url, "data": data, "headers": headers, "timeout": timeout}
                )
                return FakeResponse()

        cliente = ClienteNFeDistribuicaoDFe(
            pfx_bytes=pfx,
            senha="123",
            uf_autor="35",
            ambiente=2,
            sessao_http=FakeSession(),
        )
        assert cliente.url_servico == URL_DIST_DFE_HOMOLOGACAO
        assert cliente.metadados_certificado.documento == "11222333000181"

        # Consulta sem informar `documento`: deve extrair automaticamente do certificado A1
        resposta = cliente.consultar_dist_nsu(ult_nsu=55)
        assert resposta.cstat == "137"
        assert resposta.ult_nsu == "000000000000055"

        assert len(requisicoes_capturadas) == 1
        req = requisicoes_capturadas[0]
        assert req["url"] == URL_DIST_DFE_HOMOLOGACAO
        assert req["headers"]["Content-Type"] == CONTENT_TYPE_SOAP_DIST_DFE

        envelope_enviado = parse_xml_seguro(req["data"])
        assert texto(envelope_enviado, "CNPJ") == "11222333000181"
        assert texto(envelope_enviado, "ultNSU") == "000000000000055"

        # Valida seleção de URL de produção quando ambiente=1
        cliente_prod = ClienteNFeDistribuicaoDFe(
            pfx_bytes=pfx,
            senha="123",
            ambiente=1,
            sessao_http=FakeSession(),
        )
        assert cliente_prod.url_servico == URL_DIST_DFE_PRODUCAO
