"""Testes de integridade, segurança e dígitos verificadores das fixtures XML."""

from __future__ import annotations

from pathlib import Path

import pytest
from lxml import etree

PASTA_FIXTURES = Path(__file__).resolve().parent / "fixtures"

FIXTURES_ESPERADAS = (
    "nfe_55_valida.xml",
    "nfe_55_sem_protocolo.xml",
    "nfe_55_cnpj_alfanumerico.xml",
    "nfe_55_homologacao.xml",
    "nfe_55_soma_errada.xml",
    "nfe_55_chave_dv_invalido.xml",
    "nfce_65_valida.xml",
    "nfse_nacional_valida.xml",
    "nfse_abrasf_legada.xml",
    "nfe_resumo.xml",
    "nfe_evento_cancelamento.xml",
    "xml_malformado.xml",
    "xml_xxe_attack.xml",
)

FIXTURES_BEM_FORMADAS = tuple(f for f in FIXTURES_ESPERADAS if f != "xml_malformado.xml")


def criar_parser_seguro() -> etree.XMLParser:
    """Retorna instância do XMLParser configurado contra ataques XXE e rede."""
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        dtd_validation=False,
        load_dtd=False,
    )


def calcular_dv_cnpj(base_12: str) -> str:
    """Calcula os 2 dígitos verificadores de CNPJ (numérico ou alfanumérico NT 2014.002 v1.40)."""
    pesos_1 = (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
    soma_1 = sum((ord(c) - 48) * p for c, p in zip(base_12, pesos_1, strict=True))
    resto_1 = soma_1 % 11
    dv_1 = 0 if resto_1 < 2 else 11 - resto_1

    base_13 = f"{base_12}{dv_1}"
    pesos_2 = (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
    soma_2 = sum((ord(c) - 48) * p for c, p in zip(base_13, pesos_2, strict=True))
    resto_2 = soma_2 % 11
    dv_2 = 0 if resto_2 < 2 else 11 - resto_2
    return f"{dv_1}{dv_2}"


def validar_cnpj(cnpj: str) -> bool:
    """Valida CNPJ de 14 posições (numérico ou alfanumérico)."""
    if len(cnpj) != 14 or not cnpj[12:].isdigit():
        return False
    return calcular_dv_cnpj(cnpj[:12]) == cnpj[12:]


def validar_cpf(cpf: str) -> bool:
    """Valida CPF numérico de 11 dígitos."""
    if len(cpf) != 11 or not cpf.isdigit() or len(set(cpf)) == 1:
        return False
    soma_1 = sum(int(c) * p for c, p in zip(cpf[:9], range(10, 1, -1), strict=True))
    r_1 = (soma_1 * 10) % 11
    dv_1 = 0 if r_1 == 10 else r_1

    soma_2 = sum(int(c) * p for c, p in zip(cpf[:10], range(11, 1, -1), strict=True))
    r_2 = (soma_2 * 10) % 11
    dv_2 = 0 if r_2 == 10 else r_2
    return cpf[9:] == f"{dv_1}{dv_2}"


def calcular_dv_chave_44(base_43: str) -> str:
    """Calcula o dígito verificador Módulo 11 de chave de acesso de 44 posições."""
    pesos = (2, 3, 4, 5, 6, 7, 8, 9)
    soma = sum((ord(c) - 48) * pesos[i % 8] for i, c in enumerate(reversed(base_43)))
    resto = soma % 11
    dv = 0 if resto < 2 else 11 - resto
    return str(dv)


def validar_chave_44(chave: str) -> bool:
    """Verifica se a chave de 44 caracteres possui DV Módulo 11 válido."""
    if len(chave) != 44 or not chave[-1].isdigit():
        return False
    return calcular_dv_chave_44(chave[:43]) == chave[-1]


@pytest.mark.parametrize("nome_arquivo", FIXTURES_ESPERADAS)
def test_existencia_e_conteudo_nao_vazio(nome_arquivo: str) -> None:
    """Garante que cada uma das 13 fixtures existe no disco e possui conteúdo."""
    caminho = PASTA_FIXTURES / nome_arquivo
    assert caminho.is_file(), f"Fixture não encontrada: {nome_arquivo}"
    assert caminho.stat().st_size > 0, f"Fixture está vazia: {nome_arquivo}"


@pytest.mark.parametrize("nome_arquivo", FIXTURES_BEM_FORMADAS)
def test_parse_xml_bem_formado_com_parser_seguro(nome_arquivo: str) -> None:
    """Garante que todas as fixtures bem-formadas são lidas sem erro pelo parser anti-XXE."""
    dados = (PASTA_FIXTURES / nome_arquivo).read_bytes()
    raiz = etree.fromstring(dados, parser=criar_parser_seguro())  # noqa: S320
    assert raiz is not None


def test_xml_malformado_levanta_syntax_error() -> None:
    """Garante que `xml_malformado.xml` dispara `lxml.etree.XMLSyntaxError`."""
    dados = (PASTA_FIXTURES / "xml_malformado.xml").read_bytes()
    with pytest.raises(etree.XMLSyntaxError):
        etree.fromstring(dados, parser=criar_parser_seguro())  # noqa: S320


def test_xml_xxe_attack_nao_expande_entidade_externa() -> None:
    """Garante que o parser com `resolve_entities=False` bloqueia a expansão de `&xxe;`."""
    dados = (PASTA_FIXTURES / "xml_xxe_attack.xml").read_bytes()
    raiz = etree.fromstring(dados, parser=criar_parser_seguro())  # noqa: S320
    ns = {"nfe": "http://www.portalfiscal.inf.br/nfe"}
    elem_nome = raiz.find(".//nfe:emit/nfe:xNome", namespaces=ns)
    assert elem_nome is not None
    texto_serializado = etree.tostring(elem_nome, encoding="unicode")
    assert "&xxe;" in texto_serializado
    assert "root:" not in (elem_nome.text or "")


@pytest.mark.parametrize(
    "nome_arquivo",
    (
        "nfe_55_valida.xml",
        "nfe_55_sem_protocolo.xml",
        "nfe_55_cnpj_alfanumerico.xml",
        "nfe_55_homologacao.xml",
        "nfe_55_soma_errada.xml",
        "nfce_65_valida.xml",
    ),
)
def test_chaves_e_documentos_validos_nas_nfe_nfce(nome_arquivo: str) -> None:
    """Verifica matematicamente os DVs das chaves (44 posições), CNPJs e CPFs."""
    dados = (PASTA_FIXTURES / nome_arquivo).read_bytes()
    raiz = etree.fromstring(dados, parser=criar_parser_seguro())  # noqa: S320
    ns = {"nfe": "http://www.portalfiscal.inf.br/nfe"}

    inf_nfe = raiz.find(".//nfe:infNFe", namespaces=ns)
    assert inf_nfe is not None
    chave_id = inf_nfe.attrib["Id"].removeprefix("NFe")
    assert validar_chave_44(chave_id), f"Chave inválida em {nome_arquivo}: {chave_id}"

    for elem_cnpj in raiz.findall(".//nfe:CNPJ", namespaces=ns):
        assert elem_cnpj.text is not None
        assert validar_cnpj(elem_cnpj.text), f"CNPJ inválido em {nome_arquivo}: {elem_cnpj.text}"

    for elem_cpf in raiz.findall(".//nfe:CPF", namespaces=ns):
        assert elem_cpf.text is not None
        assert validar_cpf(elem_cpf.text), f"CPF inválido em {nome_arquivo}: {elem_cpf.text}"


def test_fixture_cnpj_alfanumerico_contem_letras() -> None:
    """Garante que a fixture alfanumérica realmente usa letras no CNPJ e na chave."""
    dados = (PASTA_FIXTURES / "nfe_55_cnpj_alfanumerico.xml").read_bytes()
    raiz = etree.fromstring(dados, parser=criar_parser_seguro())  # noqa: S320
    ns = {"nfe": "http://www.portalfiscal.inf.br/nfe"}

    cnpj_emit = raiz.findtext(".//nfe:emit/nfe:CNPJ", namespaces=ns)
    inf_nfe = raiz.find(".//nfe:infNFe", namespaces=ns)
    assert cnpj_emit is not None and inf_nfe is not None
    chave = inf_nfe.attrib["Id"].removeprefix("NFe")

    assert any(c.isalpha() for c in cnpj_emit[:12])
    assert chave[6:20] == cnpj_emit
    assert validar_cnpj(cnpj_emit)
    assert validar_chave_44(chave)


def test_fixture_chave_dv_invalido_tem_dv_incorreto() -> None:
    """Garante que `nfe_55_chave_dv_invalido.xml` possui dígito verificador incorreto."""
    dados = (PASTA_FIXTURES / "nfe_55_chave_dv_invalido.xml").read_bytes()
    raiz = etree.fromstring(dados, parser=criar_parser_seguro())  # noqa: S320
    ns = {"nfe": "http://www.portalfiscal.inf.br/nfe"}

    inf_nfe = raiz.find(".//nfe:infNFe", namespaces=ns)
    assert inf_nfe is not None
    chave = inf_nfe.attrib["Id"].removeprefix("NFe")
    assert len(chave) == 44
    assert not validar_chave_44(chave)


def test_fixtures_resumo_evento_e_nfse_nacional() -> None:
    """Valida chaves e CNPJs em resumo, evento e NFS-e Nacional."""
    ns_nfe = {"nfe": "http://www.portalfiscal.inf.br/nfe"}
    for arq in ("nfe_resumo.xml", "nfe_evento_cancelamento.xml"):
        raiz = etree.fromstring(  # noqa: S320
            (PASTA_FIXTURES / arq).read_bytes(), parser=criar_parser_seguro()
        )
        chave = raiz.findtext(".//nfe:chNFe", namespaces=ns_nfe)
        cnpj = raiz.findtext(".//nfe:CNPJ", namespaces=ns_nfe)
        assert chave is not None and validar_chave_44(chave)
        assert cnpj is not None and validar_cnpj(cnpj)

    ns_nfse = {"nfse": "http://www.sped.fazenda.gov.br/nfse"}
    raiz_nfse = etree.fromstring(  # noqa: S320
        (PASTA_FIXTURES / "nfse_nacional_valida.xml").read_bytes(),
        parser=criar_parser_seguro(),
    )
    inf_nfse = raiz_nfse.find(".//nfse:infNFSe", namespaces=ns_nfse)
    assert inf_nfse is not None
    chave_nfse = inf_nfse.attrib["Id"].removeprefix("NFS")
    assert len(chave_nfse) == 50 and chave_nfse.isdigit()
    for elem_cnpj in raiz_nfse.findall(".//nfse:CNPJ", namespaces=ns_nfse):
        assert elem_cnpj.text is not None and validar_cnpj(elem_cnpj.text)
