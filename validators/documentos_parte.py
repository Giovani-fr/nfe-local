"""Validação de CPF e CNPJ (numérico e alfanumérico NT 2014.002 v1.40) das partes."""

from __future__ import annotations

import re

from models.fiscal import AlertaValidacao, DocumentoFiscal, Parte
from models.normalizadores import limpar_documento

_RE_CNPJ_BASE_12 = re.compile(r"^[0-9A-Z]{12}$")


def cpf_valido(cpf: str | None) -> bool:
    """Valida CPF numérico de 11 dígitos e seus 2 dígitos verificadores."""
    limpo = limpar_documento(cpf)
    if len(limpo) != 11 or not limpo.isdigit():
        return False
    if len(set(limpo)) == 1:
        return False

    soma_1 = sum(int(c) * p for c, p in zip(limpo[:9], range(10, 1, -1), strict=True))
    resto_1 = (soma_1 * 10) % 11
    dv_1 = 0 if resto_1 == 10 else resto_1

    soma_2 = sum(int(c) * p for c, p in zip(limpo[:10], range(11, 1, -1), strict=True))
    resto_2 = (soma_2 * 10) % 11
    dv_2 = 0 if resto_2 == 10 else resto_2

    return limpo[9:] == f"{dv_1}{dv_2}"


def calcular_dv_cnpj(base_12: str) -> str:
    """Calcula os 2 dígitos verificadores de CNPJ usando `ord(c) - 48` (NT 2014.002 v1.40)."""
    base = base_12.upper()
    if not _RE_CNPJ_BASE_12.match(base):
        raise ValueError(f"Base de CNPJ inválida: {base_12!r}")

    pesos_1 = (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
    soma_1 = sum((ord(c) - 48) * p for c, p in zip(base, pesos_1, strict=True))
    r_1 = soma_1 % 11
    dv_1 = 0 if r_1 < 2 else 11 - r_1

    base_13 = f"{base}{dv_1}"
    pesos_2 = (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
    soma_2 = sum((ord(c) - 48) * p for c, p in zip(base_13, pesos_2, strict=True))
    r_2 = soma_2 % 11
    dv_2 = 0 if r_2 < 2 else 11 - r_2

    return f"{dv_1}{dv_2}"


def cnpj_valido(cnpj: str | None) -> bool:
    """Valida CNPJ de 14 posições (numérico ou alfanumérico conforme NT 2014.002 v1.40)."""
    limpo = limpar_documento(cnpj)
    if len(limpo) != 14:
        return False
    base_12 = limpo[:12]
    dvs = limpo[12:]
    if not _RE_CNPJ_BASE_12.match(base_12) or not dvs.isdigit():
        return False
    if len(set(limpo)) == 1:
        return False
    return calcular_dv_cnpj(base_12) == dvs


def _documento_parte_valido(parte: Parte) -> bool:
    doc = limpar_documento(parte.documento)
    if parte.tipo_pessoa == "PJ":
        return cnpj_valido(doc)
    if parte.tipo_pessoa == "PF":
        return cpf_valido(doc)
    if len(doc) == 14:
        return cnpj_valido(doc)
    if len(doc) == 11 and doc.isdigit():
        return cpf_valido(doc)
    return False


def validar_partes(doc: DocumentoFiscal) -> list[AlertaValidacao]:
    """Valida o CNPJ/CPF do emitente e, quando aplicável, do destinatário."""
    alertas: list[AlertaValidacao] = []

    if doc.tipo in ("NFE", "NFCE", "NFSE", "RESUMO"):
        if not doc.emitente.documento or not _documento_parte_valido(doc.emitente):
            alertas.append(
                AlertaValidacao(
                    codigo="EMITENTE_DOCUMENTO_INVALIDO",
                    severidade="ERRO",
                    mensagem=(
                        f"Documento (CNPJ/CPF) do emitente inválido ou ausente: "
                        f"'{doc.emitente.documento}'."
                    ),
                    campo="emitente.documento",
                )
            )

    if (
        doc.destinatario is not None
        and doc.destinatario.tipo_pessoa in ("PJ", "PF")
        and not _documento_parte_valido(doc.destinatario)
    ):
        alertas.append(
            AlertaValidacao(
                codigo="DESTINATARIO_DOCUMENTO_INVALIDO",
                severidade="ERRO",
                mensagem=(
                    f"Documento (CNPJ/CPF) do destinatário inválido: "
                    f"'{doc.destinatario.documento}'."
                ),
                campo="destinatario.documento",
            )
        )

    return alertas
