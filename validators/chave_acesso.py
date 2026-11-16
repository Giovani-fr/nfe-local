"""Validação de Chaves de Acesso (44 posições NF-e/NFC-e alfanumérica e 50 dígitos NFS-e)."""

from __future__ import annotations

import re

from models.fiscal import AlertaValidacao, DocumentoFiscal
from models.normalizadores import limpar_chave_acesso

_RE_ALFANUM_14 = re.compile(r"^[0-9A-Z]{14}$")


def calcular_dv_chave_44(chave_43: str) -> int:
    """Calcula o DV Módulo 11 de uma base de 43 caracteres usando `ord(c) - 48` (NT 2014.002)."""
    if len(chave_43) != 43:
        raise ValueError(f"Base da chave deve ter 43 caracteres, recebido: {len(chave_43)}")
    pesos = (2, 3, 4, 5, 6, 7, 8, 9)
    soma = sum((ord(c.upper()) - 48) * pesos[i % 8] for i, c in enumerate(reversed(chave_43)))
    resto = soma % 11
    return 0 if resto < 2 else 11 - resto


def _estrutura_chave_44_valida(chave_limpa: str) -> bool:
    """Verifica tamanho, blocos numéricos e bloco do CNPJ alfanumérico (posições 6..19)."""
    if len(chave_limpa) != 44:
        return False
    bloco_uf_aamm = chave_limpa[:6]
    bloco_cnpj = chave_limpa[6:20]
    bloco_final = chave_limpa[20:]
    if not bloco_uf_aamm.isdigit() or not bloco_final.isdigit():
        return False
    return bool(_RE_ALFANUM_14.match(bloco_cnpj))


def chave_44_valida(chave: str | None) -> bool:
    """Verifica se a chave de 44 posições tem estrutura e dígito verificador Módulo 11 válidos."""
    chave_limpa = limpar_chave_acesso(chave)
    if not _estrutura_chave_44_valida(chave_limpa):
        return False
    dv_esperado = calcular_dv_chave_44(chave_limpa[:43])
    return int(chave_limpa[43]) == dv_esperado


def chave_nfse_50_valida(chave: str | None) -> bool:
    """Verifica se a chave de acesso da NFS-e Padrão Nacional possui 50 dígitos numéricos."""
    chave_limpa = limpar_chave_acesso(chave)
    return len(chave_limpa) == 50 and chave_limpa.isdigit()


def validar_chave_documento(doc: DocumentoFiscal) -> list[AlertaValidacao]:
    """Valida a chave de acesso de um `DocumentoFiscal` conforme seu tipo."""
    alertas: list[AlertaValidacao] = []
    chave = limpar_chave_acesso(doc.chave_acesso)

    if doc.tipo == "NFSE":
        if not chave_nfse_50_valida(chave):
            alertas.append(
                AlertaValidacao(
                    codigo="CHAVE_NFSE_INVALIDA",
                    severidade="ERRO",
                    mensagem=(
                        f"Chave de acesso da NFS-e Nacional inválida ({len(chave)} posições; "
                        "esperados 50 dígitos numéricos)."
                    ),
                    campo="chave_acesso",
                )
            )
        return alertas

    if not _estrutura_chave_44_valida(chave):
        alertas.append(
            AlertaValidacao(
                codigo="CHAVE_ACESSO_INVALIDA",
                severidade="ERRO",
                mensagem=(
                    f"Chave de acesso inválida ({len(chave)} posições; "
                    "esperadas 44 posições no padrão SEFAZ)."
                ),
                campo="chave_acesso",
            )
        )
        return alertas

    dv_calculado = calcular_dv_chave_44(chave[:43])
    if int(chave[43]) != dv_calculado:
        alertas.append(
            AlertaValidacao(
                codigo="CHAVE_DV_INVALIDO",
                severidade="ERRO",
                mensagem=(
                    f"Dígito verificador da chave de acesso inválido: "
                    f"informado '{chave[43]}', esperado '{dv_calculado}'."
                ),
                campo="chave_acesso",
            )
        )

    if (
        doc.tipo in ("NFE", "NFCE", "RESUMO")
        and doc.emitente.tipo_pessoa == "PJ"
        and len(doc.emitente.documento) == 14
    ):
        cnpj_na_chave = chave[6:20]
        if cnpj_na_chave != doc.emitente.documento:
            alertas.append(
                AlertaValidacao(
                    codigo="CHAVE_CNPJ_EMITENTE_DIVERGENTE",
                    severidade="ERRO",
                    mensagem=(
                        f"O CNPJ contido na chave de acesso ({cnpj_na_chave}) diverge "
                        f"do CNPJ do emitente ({doc.emitente.documento})."
                    ),
                    campo="chave_acesso",
                )
            )

    return alertas
