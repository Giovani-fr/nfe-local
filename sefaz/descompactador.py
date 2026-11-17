"""Descompactação segura de blocos `<docZip>` (Base64 + GZIP) e integração com o detector/parser."""

from __future__ import annotations

import base64
import binascii
import gzip
import io
from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, ConfigDict

from models.fiscal import AlertaValidacao, DocumentoFiscal, ResultadoAnalise
from parsers.detector import processar_arquivo_xml
from sefaz.dist_dfe import ItemDocZip, formatar_nsu
from validators.lote import criar_resultado_erro_xml

_GZIP_MAGIC_HEADER = b"\x1f\x8b"
MAX_BYTES_XML_DOC_ZIP_PADRAO = 10 * 1024 * 1024  # 10 MB

CODIGO_ALERTA_MANIFESTACAO_NECESSARIA = "MANIFESTACAO_DESTINATARIO_NECESSARIA"
MENSAGEM_ALERTA_MANIFESTACAO_NECESSARIA = (
    "A SEFAZ entregou apenas o resumo (resNFe). Registre a Manifestação do Destinatário "
    "(ex.: Ciência da Operação 210210) para liberar o XML completo (procNFe)."
)
CODIGO_ERRO_DOCZIP_CORROMPIDO = "DOCZIP_CORROMPIDO"

CategoriaSchemaDocZip = Literal["RESUMO_NFE", "NFE_COMPLETA", "EVENTO_NFE", "OUTRO"]


class ErroDescompactacaoDocZip(Exception):
    """Exceção levantada quando um payload `<docZip>` tem Base64/GZIP inválido ou excede limites."""


class DocumentoDistDFeProcessado(BaseModel):
    """Resultado do processamento completo de um item `<docZip>` retornado pelo `DistDFe`."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    nsu: str
    schema_xml: str
    categoria_schema: CategoriaSchemaDocZip
    manifestacao_necessaria: bool = False
    resultado: ResultadoAnalise
    xml_descompactado: bytes = b""


def compactar_xml_para_doc_zip_b64(xml_bytes: bytes | str) -> str:
    """Compacta um XML com GZIP e codifica em Base64 ASCII no formato `<docZip>` da SEFAZ."""
    dados = xml_bytes.encode("utf-8") if isinstance(xml_bytes, str) else bytes(xml_bytes)
    compactado = gzip.compress(dados, compresslevel=6)
    return base64.b64encode(compactado).decode("ascii")


def descompactar_doc_zip(
    conteudo_base64: str | bytes,
    max_bytes_xml: int = MAX_BYTES_XML_DOC_ZIP_PADRAO,
) -> bytes:
    """Decodifica Base64 e descompacta o stream GZIP de um `<docZip>` com proteção anti-bomb."""
    if max_bytes_xml <= 0:
        raise ValueError(f"max_bytes_xml deve ser positivo: {max_bytes_xml}")

    texto_b64 = (
        conteudo_base64.decode("ascii", errors="ignore")
        if isinstance(conteudo_base64, (bytes, bytearray))
        else str(conteudo_base64)
    )
    limpo = "".join(texto_b64.split())
    if not limpo:
        raise ErroDescompactacaoDocZip("Conteúdo Base64 do '<docZip>' está vazio.")

    try:
        decodificado = base64.b64decode(limpo, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ErroDescompactacaoDocZip(
            f"Conteúdo do '<docZip>' não é um Base64 válido: {exc}"
        ) from exc

    if not decodificado:
        raise ErroDescompactacaoDocZip("Payload decodificado do '<docZip>' está vazio.")

    if decodificado.startswith(_GZIP_MAGIC_HEADER):
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(decodificado), mode="rb") as gz:
                xml_bytes = gz.read(max_bytes_xml + 1)
        except (OSError, EOFError, zlib_error_types()) as exc:
            raise ErroDescompactacaoDocZip(
                f"Falha ao descompactar stream GZIP do '<docZip>': {exc}"
            ) from exc

        if len(xml_bytes) > max_bytes_xml:
            raise ErroDescompactacaoDocZip(
                f"XML descompactado do '<docZip>' excede o limite de segurança de "
                f"{max_bytes_xml} bytes (proteção contra gzip bomb)."
            )
        if not xml_bytes.strip():
            raise ErroDescompactacaoDocZip("XML descompactado do '<docZip>' está vazio.")
        return xml_bytes

    # Fallback defensivo: caso algum mock/homologação envie XML puro em Base64 sem GZIP
    recortado = decodificado.lstrip()
    if recortado.startswith(b"<"):
        if len(decodificado) > max_bytes_xml:
            raise ErroDescompactacaoDocZip(
                f"XML decodificado do '<docZip>' excede o limite de segurança de "
                f"{max_bytes_xml} bytes."
            )
        return decodificado

    raise ErroDescompactacaoDocZip(
        "Payload decodificado do '<docZip>' não possui cabeçalho GZIP válido (0x1f8b) "
        "nem corresponde a um documento XML."
    )


def zlib_error_types() -> type[BaseException]:
    """Retorna a classe `zlib.error` para captura segura de erros de descompressão."""
    import zlib

    return zlib.error


def classificar_schema_doc_zip(
    schema_xml: str,
    doc: DocumentoFiscal | None = None,
) -> CategoriaSchemaDocZip:
    """Classifica o item do `DistDFe` pelo atributo `@schema` ou pelo `DocumentoFiscal` parseado."""
    schema_limpo = (schema_xml or "").strip().lower()

    if schema_limpo.startswith("resnfe"):
        return "RESUMO_NFE"
    if schema_limpo.startswith(("procnfe", "nfeproc")):
        return "NFE_COMPLETA"
    if schema_limpo.startswith(("proceventonfe", "resevento", "evento")):
        return "EVENTO_NFE"

    if doc is not None:
        if doc.tipo == "RESUMO":
            return "RESUMO_NFE"
        if doc.tipo in ("NFE", "NFCE"):
            return "NFE_COMPLETA"
        if doc.tipo == "EVENTO":
            return "EVENTO_NFE"

    return "OUTRO"


def _normalizar_nsu_seguro(nsu: str) -> str:
    limpo = (nsu or "").strip()
    if limpo.isdigit() and len(limpo) <= 15:
        return formatar_nsu(limpo)
    return limpo or "000000000000000"


def processar_item_doc_zip(
    item: ItemDocZip,
    max_bytes_xml: int = MAX_BYTES_XML_DOC_ZIP_PADRAO,
) -> DocumentoDistDFeProcessado:
    """Descompacta um `ItemDocZip`, passa pelo parser/validador e sinaliza resumos `resNFe`."""
    nsu_fmt = _normalizar_nsu_seguro(item.nsu)
    schema_str = (item.schema_xml or "").strip()
    nome_origem = f"NSU_{nsu_fmt}_{schema_str or 'docZip.xml'}"

    try:
        xml_bytes = descompactar_doc_zip(item.conteudo_base64, max_bytes_xml=max_bytes_xml)
    except ErroDescompactacaoDocZip as exc:
        categoria_erro = classificar_schema_doc_zip(schema_str, None)
        resultado_erro = criar_resultado_erro_xml(
            nome_origem,
            str(exc),
            codigo=CODIGO_ERRO_DOCZIP_CORROMPIDO,
        )
        return DocumentoDistDFeProcessado(
            nsu=nsu_fmt,
            schema_xml=schema_str,
            categoria_schema=categoria_erro,
            manifestacao_necessaria=False,
            resultado=resultado_erro,
            xml_descompactado=b"",
        )

    resultado = processar_arquivo_xml(xml_bytes, nome_arquivo=nome_origem)
    doc = resultado.documento
    categoria = classificar_schema_doc_zip(schema_str, doc)
    eh_resumo = categoria == "RESUMO_NFE" or (doc is not None and doc.tipo == "RESUMO")

    if eh_resumo and doc is not None:
        ja_tem_alerta = any(
            alerta.codigo == CODIGO_ALERTA_MANIFESTACAO_NECESSARIA for alerta in resultado.alertas
        )
        if not ja_tem_alerta:
            resultado.alertas.append(
                AlertaValidacao(
                    codigo=CODIGO_ALERTA_MANIFESTACAO_NECESSARIA,
                    severidade="AVISO",
                    mensagem=MENSAGEM_ALERTA_MANIFESTACAO_NECESSARIA,
                    campo="situacao",
                )
            )

    return DocumentoDistDFeProcessado(
        nsu=nsu_fmt,
        schema_xml=schema_str,
        categoria_schema=categoria,
        manifestacao_necessaria=eh_resumo,
        resultado=resultado,
        xml_descompactado=xml_bytes,
    )


def _chave_ordenacao_nsu(item: ItemDocZip) -> tuple[int, int | str]:
    limpo = (item.nsu or "").strip()
    if limpo.isdigit():
        return (0, int(limpo))
    return (1, limpo)


def processar_lote_doc_zip(
    itens: Iterable[ItemDocZip],
    max_bytes_xml: int = MAX_BYTES_XML_DOC_ZIP_PADRAO,
) -> list[DocumentoDistDFeProcessado]:
    """Processa uma sequência de `ItemDocZip` em ordem crescente de NSU sem derrubar o lote."""
    itens_ordenados = sorted(itens, key=_chave_ordenacao_nsu)
    return [processar_item_doc_zip(item, max_bytes_xml=max_bytes_xml) for item in itens_ordenados]
