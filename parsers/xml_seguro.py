"""Wrapper `lxml` seguro (anti-XXE) e helpers de navegação agnósticos de namespace."""

from __future__ import annotations

from lxml import etree


class ErroParseXML(Exception):
    """Exceção levantada quando o arquivo XML é malformado, vazio ou ilegível."""


class ErroLayoutDesconhecido(ErroParseXML):
    """Exceção levantada quando o XML é bem-formado, mas não corresponde ao layout esperado."""


def criar_parser_xml_seguro() -> etree.XMLParser:
    """Cria uma instância de `lxml.etree.XMLParser` blindada contra XXE e acessos de rede."""
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        dtd_validation=False,
        load_dtd=False,
        recover=False,
        remove_comments=True,
        remove_pis=True,
    )


def parse_xml_seguro(dados: bytes | str) -> etree._Element:
    """Analisa `bytes` ou `str` XML de forma segura e retorna o elemento raiz."""
    if isinstance(dados, str):
        dados_bytes = dados.strip().encode("utf-8")
    elif isinstance(dados, (bytes, bytearray)):
        dados_bytes = bytes(dados).strip()
    else:
        raise ErroParseXML(f"Tipo de entrada inválido para parse XML: {type(dados).__name__}")

    if not dados_bytes:
        raise ErroParseXML("O conteúdo XML informado está vazio.")

    try:
        raiz = etree.fromstring(dados_bytes, parser=criar_parser_xml_seguro())  # noqa: S320
    except (etree.XMLSyntaxError, ValueError) as exc:
        raise ErroParseXML(f"XML malformado ou inválido: {exc}") from exc

    if raiz is None or not isinstance(raiz.tag, str):
        raise ErroParseXML("Não foi possível identificar o elemento raiz do XML.")

    return raiz


def nome_local(el_ou_tag: etree._Element | str | None) -> str:
    """Retorna o nome local da tag sem o prefixo `{namespace}`."""
    if el_ou_tag is None:
        return ""
    tag = el_ou_tag if isinstance(el_ou_tag, str) else el_ou_tag.tag
    if not isinstance(tag, str):
        return ""
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def primeiro(el: etree._Element | None, nome: str) -> etree._Element | None:
    """Retorna o primeiro elemento (incluindo o próprio `el`) cujo `nome_local` seja `nome`."""
    if el is None:
        return None
    for nodo in el.iter():
        if nome_local(nodo) == nome:
            return nodo
    return None


def primeiro_com_filho(
    el: etree._Element | None,
    nome: str,
    filho: str,
) -> etree._Element | None:
    """Retorna o primeiro descendente `nome` que possua um filho direto chamado `filho`."""
    if el is None:
        return None
    for nodo in el.iter():
        if nome_local(nodo) == nome:
            for sub in nodo:
                if nome_local(sub) == filho:
                    return nodo
    return None


def todos(el: etree._Element | None, nome: str) -> list[etree._Element]:
    """Retorna todos os elementos na subárvore de `el` cujo `nome_local` seja `nome`."""
    if el is None:
        return []
    return [nodo for nodo in el.iter() if nome_local(nodo) == nome]


def texto(el: etree._Element | None, nome: str | None = None) -> str:
    """Extrai o texto limpo (`.strip()`) de `el` ou do primeiro descendente `nome`."""
    if el is None:
        return ""
    alvo = el if nome is None else primeiro(el, nome)
    if alvo is None or alvo.text is None:
        return ""
    return alvo.text.strip()
