"""Pacote de parsers de documentos fiscais eletrônicos."""

from parsers.nfe import parse_nfe, parse_nfe_element
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
    "ErroLayoutDesconhecido",
    "ErroParseXML",
    "__version__",
    "criar_parser_xml_seguro",
    "nome_local",
    "parse_nfe",
    "parse_nfe_element",
    "parse_xml_seguro",
    "primeiro",
    "primeiro_com_filho",
    "texto",
    "todos",
]
