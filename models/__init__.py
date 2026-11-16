"""Pacote de modelos de dados fiscais (Pydantic v2) e normalizadores."""

from models.fiscal import (
    AlertaValidacao,
    DocumentoFiscal,
    ImpostoItem,
    ImpostoTotal,
    Item,
    ModeloDocumento,
    Pagamento,
    Parte,
    ResultadoAnalise,
    SeveridadeAlerta,
    SituacaoDocumento,
    TipoDocumento,
    TipoPessoa,
    Totais,
)
from models.normalizadores import (
    DECIMAL_ZERO,
    MAPA_FORMAS_PAGAMENTO,
    descrever_forma_pagamento,
    limpar_chave_acesso,
    limpar_documento,
    para_data,
    para_data_hora,
    para_decimal,
)

__version__ = "0.1.0"

__all__ = [
    "DECIMAL_ZERO",
    "MAPA_FORMAS_PAGAMENTO",
    "AlertaValidacao",
    "DocumentoFiscal",
    "ImpostoItem",
    "ImpostoTotal",
    "Item",
    "ModeloDocumento",
    "Pagamento",
    "Parte",
    "ResultadoAnalise",
    "SeveridadeAlerta",
    "SituacaoDocumento",
    "TipoDocumento",
    "TipoPessoa",
    "Totais",
    "__version__",
    "descrever_forma_pagamento",
    "limpar_chave_acesso",
    "limpar_documento",
    "para_data",
    "para_data_hora",
    "para_decimal",
]
