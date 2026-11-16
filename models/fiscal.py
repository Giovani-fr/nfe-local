"""Modelos de dados fiscais em Pydantic v2."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from models.normalizadores import DECIMAL_ZERO

TipoDocumento = Literal["NFE", "NFCE", "NFSE", "RESUMO", "EVENTO"]
ModeloDocumento = Literal["55", "65", "NFSE"]
TipoPessoa = Literal["PJ", "PF", "ESTRANGEIRO", "NAO_IDENTIFICADO"]
SituacaoDocumento = Literal[
    "AUTORIZADA",
    "CANCELADA",
    "REJEITADA",
    "SEM_PROTOCOLO",
    "RESUMO",
    "EVENTO",
]
SeveridadeAlerta = Literal["ERRO", "AVISO"]


class Parte(BaseModel):
    """Representa o emitente/prestador ou destinatário/tomador do documento fiscal."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    documento: str = ""
    tipo_pessoa: TipoPessoa = "NAO_IDENTIFICADO"
    razao_social: str = ""
    nome_fantasia: str | None = None
    inscricao_estadual: str | None = None
    inscricao_municipal: str | None = None
    uf: str | None = None
    municipio: str | None = None
    codigo_ibge: str | None = None
    endereco: str | None = None
    cep: str | None = None
    telefone: str | None = None
    email: str | None = None


class ImpostoItem(BaseModel):
    """Tributos incidentes sobre um item de produto ou serviço."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    cst_ou_csosn: str | None = None
    icms_bc: Decimal = DECIMAL_ZERO
    icms_aliquota: Decimal = DECIMAL_ZERO
    icms_valor: Decimal = DECIMAL_ZERO
    icms_st_valor: Decimal = DECIMAL_ZERO
    ipi_aliquota: Decimal = DECIMAL_ZERO
    ipi_valor: Decimal = DECIMAL_ZERO
    pis_aliquota: Decimal = DECIMAL_ZERO
    pis_valor: Decimal = DECIMAL_ZERO
    cofins_aliquota: Decimal = DECIMAL_ZERO
    cofins_valor: Decimal = DECIMAL_ZERO
    iss_aliquota: Decimal = DECIMAL_ZERO
    iss_valor: Decimal = DECIMAL_ZERO
    iss_retido: bool = False


class Item(BaseModel):
    """Item de produto (NF-e/NFC-e) ou serviço prestado (NFS-e)."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    numero_item: int = 1
    codigo: str = ""
    descricao: str = ""
    ncm: str | None = None
    cest: str | None = None
    cfop: str | None = None
    codigo_tributacao_nacional: str | None = None
    codigo_nbs: str | None = None
    unidade: str = "UN"
    quantidade: Decimal = Decimal("1.0000")
    valor_unitario: Decimal = DECIMAL_ZERO
    valor_desconto: Decimal = DECIMAL_ZERO
    valor_frete: Decimal = DECIMAL_ZERO
    valor_seguro: Decimal = DECIMAL_ZERO
    valor_outros: Decimal = DECIMAL_ZERO
    valor_total: Decimal = DECIMAL_ZERO
    imposto: ImpostoItem = Field(default_factory=ImpostoItem)
    info_adicional: str | None = None


class ImpostoTotal(BaseModel):
    """Totais consolidados de impostos do documento fiscal."""

    model_config = ConfigDict(extra="ignore")

    icms_bc: Decimal = DECIMAL_ZERO
    icms: Decimal = DECIMAL_ZERO
    icms_st_bc: Decimal = DECIMAL_ZERO
    icms_st: Decimal = DECIMAL_ZERO
    ipi: Decimal = DECIMAL_ZERO
    pis: Decimal = DECIMAL_ZERO
    cofins: Decimal = DECIMAL_ZERO
    iss: Decimal = DECIMAL_ZERO
    retencoes_federais: Decimal = DECIMAL_ZERO
    ibs_cbs: Decimal = DECIMAL_ZERO
    tributos_aproximados: Decimal = DECIMAL_ZERO


class Totais(BaseModel):
    """Totais financeiros e tributários da nota fiscal."""

    model_config = ConfigDict(extra="ignore")

    valor_produtos_servicos: Decimal = DECIMAL_ZERO
    valor_frete: Decimal = DECIMAL_ZERO
    valor_seguro: Decimal = DECIMAL_ZERO
    valor_desconto: Decimal = DECIMAL_ZERO
    valor_outras_despesas: Decimal = DECIMAL_ZERO
    impostos: ImpostoTotal = Field(default_factory=ImpostoTotal)
    valor_liquido: Decimal = DECIMAL_ZERO
    valor_total_nota: Decimal = DECIMAL_ZERO


class Pagamento(BaseModel):
    """Forma de pagamento ou parcela/duplicata de cobrança."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    forma_codigo: str = "99"
    forma_descricao: str = "Outros"
    valor: Decimal = DECIMAL_ZERO
    numero_duplicata: str | None = None
    vencimento: date | None = None


class DocumentoFiscal(BaseModel):
    """Representação canônica de NF-e (55), NFC-e (65), NFS-e Nacional, Resumo e Evento."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    tipo: TipoDocumento
    modelo: ModeloDocumento
    serie: str = ""
    numero: str = ""
    chave_acesso: str = ""
    data_emissao: datetime | None = None
    competencia: date | None = None
    ambiente: Literal[1, 2] = 1
    protocolo: str | None = None
    codigo_status: str | None = None
    motivo_status: str | None = None
    situacao: SituacaoDocumento = "SEM_PROTOCOLO"
    natureza_operacao: str | None = None
    emitente: Parte = Field(default_factory=Parte)
    destinatario: Parte | None = None
    itens: list[Item] = Field(default_factory=list)
    totais: Totais = Field(default_factory=Totais)
    pagamentos: list[Pagamento] = Field(default_factory=list)
    chaves_referenciadas: list[str] = Field(default_factory=list)
    tipo_evento: str | None = None
    descricao_evento: str | None = None
    informacoes_complementares: str | None = None


class AlertaValidacao(BaseModel):
    """Alerta de validação fiscal ou financeira (ERRO ou AVISO)."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    codigo: str
    severidade: SeveridadeAlerta
    mensagem: str
    campo: str | None = None


class ResultadoAnalise(BaseModel):
    """Resultado completo do processamento e validação de um arquivo XML."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    arquivo_origem: str
    valido: bool
    documento: DocumentoFiscal | None = None
    alertas: list[AlertaValidacao] = Field(default_factory=list)
    erro_fatal: str | None = None
