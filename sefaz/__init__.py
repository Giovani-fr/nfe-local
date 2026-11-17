"""Pacote de integração com WebServices da SEFAZ (DistDFe e Certificado A1)."""

from sefaz.cert_a1 import (
    OID_ICP_BRASIL_CNPJ,
    OID_ICP_BRASIL_CPF_RESPONSAVEL,
    ErroCertificadoA1,
    MetadadosCertificado,
    carregar_certificado_de_env,
    carregar_pkcs12_memoria,
    extrair_metadados_certificado,
)
from sefaz.dist_dfe import (
    CONTENT_TYPE_SOAP_DIST_DFE,
    SOAP_ACTION_DIST_DFE,
    URL_DIST_DFE_HOMOLOGACAO,
    URL_DIST_DFE_PRODUCAO,
    ClienteNFeDistribuicaoDFe,
    ErroComunicacaoSEFAZ,
    ItemDocZip,
    RespostaDistDFe,
    formatar_nsu,
    montar_envelope_dist_dfe,
    parse_resposta_dist_dfe,
    resolver_ambiente_sefaz,
)

__version__ = "0.1.0"

__all__ = [
    "CONTENT_TYPE_SOAP_DIST_DFE",
    "OID_ICP_BRASIL_CNPJ",
    "OID_ICP_BRASIL_CPF_RESPONSAVEL",
    "SOAP_ACTION_DIST_DFE",
    "URL_DIST_DFE_HOMOLOGACAO",
    "URL_DIST_DFE_PRODUCAO",
    "ClienteNFeDistribuicaoDFe",
    "ErroCertificadoA1",
    "ErroComunicacaoSEFAZ",
    "ItemDocZip",
    "MetadadosCertificado",
    "RespostaDistDFe",
    "__version__",
    "carregar_certificado_de_env",
    "carregar_pkcs12_memoria",
    "extrair_metadados_certificado",
    "formatar_nsu",
    "montar_envelope_dist_dfe",
    "parse_resposta_dist_dfe",
    "resolver_ambiente_sefaz",
]
