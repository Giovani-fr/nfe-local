"""Diagnóstico somente leitura do ADN: uma única consulta `GET /DFe/0`.

Não grava no banco, não mexe no cursor e nunca devolve XML, senha nem bytes do certificado.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from sefaz.adn import (
    ClienteADN,
    ErroADN,
    MensagemADN,
    _chave_ci,
    _mensagens,
    eh_resposta_fila_zerada,
    mensagem_endereco_nao_encontrado,
    mensagem_rejeicao,
    parse_resposta_adn,
)

_RE_DOC_14 = re.compile(r"(?<![0-9A-Za-z])[0-9A-Za-z]{14}(?![0-9A-Za-z])")
_RE_DOC_11 = re.compile(r"(?<!\d)\d{11}(?!\d)")


def mascarar_documento(documento: str) -> str:
    """Mantém só os 2 primeiros e os 2 últimos caracteres (`11**********81`)."""
    if len(documento) <= 4:
        return "*" * len(documento)
    return documento[:2] + "*" * (len(documento) - 4) + documento[-2:]


def _mascarar_texto(texto: str) -> str:
    """Mascara CNPJ (14) e CPF (11) soltos em textos devolvidos pelo ADN."""
    texto = _RE_DOC_14.sub(
        lambda m: (
            mascarar_documento(m.group(0)) if any(c.isdigit() for c in m.group(0)) else m.group(0)
        ),
        texto,
    )
    return _RE_DOC_11.sub(lambda m: mascarar_documento(m.group(0)), texto)


class MensagemDiagnostico(BaseModel):
    codigo: str = ""
    descricao: str = ""


class DiagnosticoADN(BaseModel):
    """Resultado de `diagnosticar_adn` (seguro para imprimir e para JSON)."""

    model_config = ConfigDict(extra="ignore")

    ambiente: int
    url_base: str
    url_consultada: str
    cnpj_consulta_mascarado: str
    certificado_valido_de: datetime | None = None
    certificado_valido_ate: datetime | None = None
    certificado_vencido: bool | None = None
    conexao_tls: str = "nao_tentada"
    http_status: int | None = None
    status_processamento: str | None = None
    tipo_consulta: str | None = None
    erros: list[MensagemDiagnostico] = Field(default_factory=list)
    alertas: list[MensagemDiagnostico] = Field(default_factory=list)
    itens_por_tipo_documento: dict[str, int] = Field(default_factory=dict)
    categoria_erro: str | None = None
    interpretacao: str = ""
    sucesso: bool = False


def _msgs(lista: list[MensagemADN]) -> list[MensagemDiagnostico]:
    return [
        MensagemDiagnostico(codigo=m.codigo, descricao=_mascarar_texto(m.descricao)) for m in lista
    ]


def diagnosticar_adn(cliente: ClienteADN) -> DiagnosticoADN:
    """Faz UMA consulta a `GET /DFe/0` e resume o resultado, sem persistir nada."""
    meta = cliente.metadados_certificado
    diag = DiagnosticoADN(
        ambiente=cliente.ambiente,
        url_base=cliente.base_url,
        url_consultada=cliente.url_nsu(0),
        cnpj_consulta_mascarado=mascarar_documento(cliente.cnpj_consulta),
        certificado_valido_de=meta.valido_de,
        certificado_valido_ate=meta.valido_ate,
        certificado_vencido=meta.vencido,
    )
    try:
        status, corpo, _ = cliente.requisitar(0)
    except ErroADN as exc:
        diag.conexao_tls = f"falha: {exc}"
        diag.categoria_erro = exc.categoria
        diag.interpretacao = str(exc)
        return diag

    diag.conexao_tls = "ok"
    diag.http_status = status

    try:
        dados = json.loads(corpo.decode("utf-8", errors="replace")) if corpo.strip() else None
    except ValueError:
        dados = None
    if isinstance(dados, dict):
        bruto = _chave_ci(dados, "StatusProcessamento")
        diag.status_processamento = str(bruto).upper() if bruto else None
        diag.tipo_consulta = _chave_ci(dados, "TipoConsulta")
        diag.erros = _msgs(_mensagens(_chave_ci(dados, "Erros")))
        diag.alertas = _msgs(_mensagens(_chave_ci(dados, "Alertas")))
        try:
            resposta = parse_resposta_adn(status, corpo)
        except ErroADN:
            resposta = None
        if resposta is not None:
            diag.itens_por_tipo_documento = dict(Counter(i.tipo_documento for i in resposta.lote))

    if status == 404 and not eh_resposta_fila_zerada(corpo):
        diag.categoria_erro = "ENDERECO"
        diag.interpretacao = mensagem_endereco_nao_encontrado(cliente.url_nsu(0))
    elif status == 404:
        diag.sucesso = True
        diag.interpretacao = "Conexão e consulta funcionaram; não há documentos para este CNPJ."
    elif status == 200 and diag.status_processamento in (None, "DOCUMENTOS_LOCALIZADOS"):
        diag.sucesso = True
        diag.interpretacao = "Conexão e consulta funcionaram; há documentos disponíveis."
    elif status == 400:
        resposta_rej = parse_resposta_adn(400, corpo) if isinstance(dados, dict) else None
        diag.categoria_erro = "REJEICAO"
        diag.interpretacao = (
            mensagem_rejeicao(resposta_rej) if resposta_rej else "Rejeição sem corpo JSON."
        )
    elif status == 429:
        diag.categoria_erro = "LIMITE"
        diag.interpretacao = "O ADN pediu para reduzir o ritmo de consultas (limite de uso)."
    elif status >= 500:
        diag.categoria_erro = "INDISPONIVEL"
        diag.interpretacao = f"O ADN está indisponível no momento (HTTP {status})."
    elif status in (401, 403):
        diag.categoria_erro = "ACESSO"
        diag.interpretacao = f"O ADN negou o acesso (HTTP {status}). Confira o certificado A1."
    else:
        diag.categoria_erro = "RESPOSTA_INVALIDA"
        diag.interpretacao = f"HTTP {status} não documentado."
    return diag
