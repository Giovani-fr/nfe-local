"""Pacote de parsers de documentos fiscais eletrônicos."""

from parsers.detector import (
    extrair_xmls_de_zip,
    iterar_lote,
    iterar_resultados_lote,
    parse_documento_xml,
    processar_arquivo_xml,
    processar_lote,
)
from parsers.nfe import parse_nfe, parse_nfe_element
from parsers.nfse_nacional import (
    NAMESPACE_NFSE_NACIONAL,
    ErroLayoutNFSeMunicipalLegado,
    eh_xml_nfse_legada,
    eh_xml_nfse_nacional,
    parse_nfse_nacional,
    parse_nfse_nacional_element,
)
from parsers.xml_seguro import (
    ErroLayoutDesconhecido,
    ErroParseXML,
    criar_parser_xml_seguro,
    nome_local,
    parse_xml_seguro,
    primeiro,
    primeiro_com_filho,
    texto,
    todos,
)

__version__ = "0.1.0"

__all__ = [
    "NAMESPACE_NFSE_NACIONAL",
    "ErroLayoutDesconhecido",
    "ErroLayoutNFSeMunicipalLegado",
    "ErroParseXML",
    "__version__",
    "criar_parser_xml_seguro",
    "eh_xml_nfse_legada",
    "eh_xml_nfse_nacional",
    "extrair_xmls_de_zip",
    "iterar_lote",
    "iterar_resultados_lote",
    "nome_local",
    "parse_documento_xml",
    "parse_nfe",
    "parse_nfe_element",
    "parse_nfse_nacional",
    "parse_nfse_nacional_element",
    "parse_xml_seguro",
    "primeiro",
    "primeiro_com_filho",
    "processar_arquivo_xml",
    "processar_lote",
    "texto",
    "todos",
]
