"""Detector automático de documentos fiscais (NF-e 55, NFC-e 65, NFS-e Nacional, Resumo, Evento)."""

from __future__ import annotations

import io
import zipfile
from collections.abc import Iterable, Iterator
from decimal import Decimal
from pathlib import PurePosixPath

from models.fiscal import DocumentoFiscal, ResultadoAnalise
from models.normalizadores import DECIMAL_ZERO
from parsers.nfe import parse_nfe_element
from parsers.nfse_nacional import (
    ErroLayoutNFSeMunicipalLegado,
    eh_xml_nfse_legada,
    eh_xml_nfse_nacional,
    parse_nfse_nacional_element,
)
from parsers.xml_seguro import (
    ErroLayoutDesconhecido,
    ErroParseXML,
    parse_xml_seguro,
)
from validators.lote import (
    analisar_documento,
    criar_resultado_erro_xml,
    validar_lote,
)

_ZIP_MAGIC_PREFIX = b"PK\x03\x04"
_MAX_ARQUIVOS_ZIP_PADRAO = 2000
_MAX_BYTES_TOTAL_ZIP_PADRAO = 200 * 1024 * 1024  # 200 MB
_MAX_BYTES_ARQUIVO_ZIP_PADRAO = 25 * 1024 * 1024  # 25 MB


def parse_documento_xml(dados: bytes | str) -> DocumentoFiscal:
    """Detecta automaticamente o tipo do documento XML e retorna `DocumentoFiscal`."""
    raiz = parse_xml_seguro(dados)

    if eh_xml_nfse_legada(raiz):
        raise ErroLayoutNFSeMunicipalLegado(
            "Layout municipal legado de NFS-e (ex.: ABRASF) não suportado. "
            "Apenas o Padrão Nacional da NFS-e (sped.fazenda.gov.br/nfse) é aceito."
        )

    if eh_xml_nfse_nacional(raiz):
        return parse_nfse_nacional_element(raiz)

    return parse_nfe_element(raiz)


def processar_arquivo_xml(
    dados: bytes | str,
    nome_arquivo: str = "documento.xml",
    *,
    tolerancia_financeira: Decimal = DECIMAL_ZERO,
) -> ResultadoAnalise:
    """Faz o parse e a validação completa de um XML, capturando erros fatais sem quebrar o lote."""
    try:
        doc = parse_documento_xml(dados)
        return analisar_documento(
            nome_arquivo,
            doc,
            tolerancia_financeira=tolerancia_financeira,
        )
    except ErroLayoutNFSeMunicipalLegado as exc:
        return criar_resultado_erro_xml(
            nome_arquivo,
            str(exc),
            codigo="LAYOUT_NFSE_MUNICIPAL_DESCONTINUADO",
        )
    except ErroLayoutDesconhecido as exc:
        return criar_resultado_erro_xml(
            nome_arquivo,
            str(exc),
            codigo="LAYOUT_DESCONHECIDO",
        )
    except ErroParseXML as exc:
        return criar_resultado_erro_xml(
            nome_arquivo,
            str(exc),
            codigo="XML_MALFORMADO",
        )


def _caminho_zip_seguro(nome_membro: str) -> str:
    """Valida nome de membro dentro do `.zip` contra *path traversal*."""
    normalizado = nome_membro.replace("\\", "/")
    caminho = PurePosixPath(normalizado)
    if caminho.is_absolute() or ".." in caminho.parts:
        raise ErroParseXML(
            f"Arquivo ZIP contém caminho inseguro (path traversal): '{nome_membro}'."
        )
    return str(caminho)


def extrair_xmls_de_zip(
    dados_zip: bytes,
    *,
    max_arquivos: int = _MAX_ARQUIVOS_ZIP_PADRAO,
    max_bytes_total: int = _MAX_BYTES_TOTAL_ZIP_PADRAO,
    max_bytes_arquivo: int = _MAX_BYTES_ARQUIVO_ZIP_PADRAO,
) -> Iterator[tuple[str, bytes]]:
    """Extrai arquivos `.xml` de um `.zip` em memória com proteção contra zip bomb."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(dados_zip))
    except (zipfile.BadZipFile, OSError) as exc:
        raise ErroParseXML(f"Arquivo ZIP inválido ou corrompido: {exc}") from exc

    with zf:
        infos = zf.infolist()
        if len(infos) > max_arquivos:
            raise ErroParseXML(
                f"Arquivo ZIP excede o limite máximo de entradas ({len(infos)} > {max_arquivos})."
            )

        bytes_acumulados = 0
        for info in infos:
            if info.is_dir():
                continue

            caminho_limpo = _caminho_zip_seguro(info.filename)
            partes = PurePosixPath(caminho_limpo).parts
            if any(p.startswith("__MACOSX") or p.startswith(".") for p in partes):
                continue
            if not caminho_limpo.lower().endswith(".xml"):
                continue

            if info.file_size > max_bytes_arquivo:
                raise ErroParseXML(
                    f"Arquivo '{caminho_limpo}' dentro do ZIP excede o tamanho máximo permitido "
                    f"({info.file_size} bytes > {max_bytes_arquivo} bytes)."
                )

            bytes_acumulados += info.file_size
            if bytes_acumulados > max_bytes_total:
                raise ErroParseXML(
                    f"Conteúdo descompactado do ZIP excede o limite total de segurança "
                    f"({max_bytes_total} bytes)."
                )

            with zf.open(info, "r") as fp:
                conteudo = fp.read(max_bytes_arquivo + 1)
                if len(conteudo) > max_bytes_arquivo:
                    raise ErroParseXML(
                        f"Leitura real de '{caminho_limpo}' excedeu o limite de "
                        f"{max_bytes_arquivo} bytes."
                    )
            yield caminho_limpo, conteudo


def _eh_arquivo_zip(nome_arquivo: str, conteudo: bytes | str) -> bool:
    if nome_arquivo.lower().endswith(".zip"):
        return True
    return isinstance(conteudo, (bytes, bytearray)) and bytes(conteudo).startswith(
        _ZIP_MAGIC_PREFIX
    )


def iterar_resultados_lote(
    entradas: Iterable[tuple[str, bytes | str]],
    *,
    tolerancia_financeira: Decimal = DECIMAL_ZERO,
) -> Iterator[ResultadoAnalise]:
    """Itera sobre pares `(nome_arquivo, conteudo)` (XML ou ZIP) gerando `ResultadoAnalise`."""
    for nome_arquivo, conteudo in entradas:
        if _eh_arquivo_zip(nome_arquivo, conteudo):
            dados_bytes = conteudo.encode("utf-8") if isinstance(conteudo, str) else bytes(conteudo)
            try:
                for nome_interno, xml_bytes in extrair_xmls_de_zip(dados_bytes):
                    origem = f"{nome_arquivo}:{nome_interno}"
                    yield processar_arquivo_xml(
                        xml_bytes,
                        origem,
                        tolerancia_financeira=tolerancia_financeira,
                    )
            except ErroParseXML as exc:
                yield criar_resultado_erro_xml(
                    nome_arquivo,
                    str(exc),
                    codigo="ZIP_INVALIDO",
                )
        else:
            yield processar_arquivo_xml(
                conteudo,
                nome_arquivo,
                tolerancia_financeira=tolerancia_financeira,
            )


def processar_lote(
    entradas: Iterable[tuple[str, bytes | str]],
    *,
    tolerancia_financeira: Decimal = DECIMAL_ZERO,
) -> list[ResultadoAnalise]:
    """Processa um lote de arquivos XML e/ou ZIP e aplica a validação cruzada de lote."""
    resultados = list(iterar_resultados_lote(entradas, tolerancia_financeira=tolerancia_financeira))
    return validar_lote(resultados)
