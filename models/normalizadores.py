"""Funções puras e defensivas de normalização de valores fiscais."""

from __future__ import annotations

import math
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

DECIMAL_ZERO = Decimal("0.00")

_RE_ALFANUMERICO = re.compile(r"[^0-9A-Za-z]")
_RE_PREFIXO_CHAVE = re.compile(r"^(?:NFSE|NFE|CTE|NFS|DPS|ID)", re.IGNORECASE)
_RE_DATA_COMPACTA = re.compile(r"^\d{8}$")

MAPA_FORMAS_PAGAMENTO: dict[str, str] = {
    "01": "Dinheiro",
    "02": "Cheque",
    "03": "Cartão de Crédito",
    "04": "Cartão de Débito",
    "05": "Crédito Loja",
    "10": "Vale Alimentação",
    "11": "Vale Refeição",
    "12": "Vale Presente",
    "13": "Vale Combustível",
    "14": "Duplicata Mercantil",
    "15": "Boleto Bancário",
    "16": "Depósito Bancário",
    "17": "Pagamento Instantâneo (PIX)",
    "18": "Transferência Bancária",
    "19": "Programa de Fidelidade / Cashback",
    "90": "Sem Pagamento",
    "99": "Outros",
}


def para_decimal(
    valor: str | int | float | Decimal | None,
    padrao: Decimal = DECIMAL_ZERO,
    *,
    estrito: bool = False,
) -> Decimal:
    """Converte entradas numéricas ou textuais (XML/pt-BR) em `Decimal` finito."""
    if valor is None or isinstance(valor, bool):
        if isinstance(valor, bool) and estrito:
            raise ValueError(f"Booleano não é um valor decimal válido: {valor!r}")
        return padrao

    if isinstance(valor, Decimal):
        if not valor.is_finite():
            if estrito:
                raise ValueError(f"Decimal não finito não permitido: {valor!r}")
            return padrao
        return valor

    if isinstance(valor, int):
        return Decimal(valor)

    if isinstance(valor, float):
        if not math.isfinite(valor):
            if estrito:
                raise ValueError(f"Float não finito não permitido: {valor!r}")
            return padrao
        return Decimal(str(valor))

    texto = str(valor).strip()
    if not texto:
        return padrao

    if "," in texto and "." in texto:
        if texto.rfind(",") > texto.rfind("."):
            texto = texto.replace(".", "").replace(",", ".")
        else:
            texto = texto.replace(",", "")
    elif "," in texto:
        texto = texto.replace(",", ".")

    try:
        resultado = Decimal(texto)
    except InvalidOperation as exc:
        if estrito:
            raise ValueError(f"Valor decimal inválido: {valor!r}") from exc
        return padrao

    if not resultado.is_finite():
        if estrito:
            raise ValueError(f"Valor decimal não finito: {valor!r}")
        return padrao

    return resultado


def para_data_hora(
    valor: str | datetime | date | None,
    *,
    estrito: bool = False,
) -> datetime | None:
    """Converte string ISO 8601 (com ou sem fuso), `YYYY-MM-DD` ou `YYYYMMDD` em `datetime`."""
    if valor is None:
        return None
    if isinstance(valor, datetime):
        return valor
    if isinstance(valor, date):
        return datetime(valor.year, valor.month, valor.day)

    texto = str(valor).strip()
    if not texto:
        return None

    if _RE_DATA_COMPACTA.match(texto):
        try:
            return datetime.strptime(texto, "%Y%m%d")
        except ValueError as exc:
            if estrito:
                raise ValueError(f"Data compacta inválida: {valor!r}") from exc
            return None

    try:
        return datetime.fromisoformat(texto)
    except ValueError as exc:
        if estrito:
            raise ValueError(f"Data/hora ISO inválida: {valor!r}") from exc
        return None


def para_data(
    valor: str | date | datetime | None,
    *,
    estrito: bool = False,
) -> date | None:
    """Converte string (`YYYY-MM-DD`, ISO 8601 ou `YYYYMMDD`), `date` ou `datetime` em `date`."""
    if valor is None:
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor

    dt = para_data_hora(valor, estrito=estrito)
    return dt.date() if dt is not None else None


def limpar_documento(valor: str | None) -> str:
    """Remove pontuação mantendo dígitos e letras maiúsculas `[0-9A-Z]` (NT 2014.002)."""
    if not valor:
        return ""
    return _RE_ALFANUMERICO.sub("", str(valor)).upper()


def limpar_chave_acesso(valor: str | None) -> str:
    """Remove prefixos de atributo `@Id` (`NFe`, `NFS`, `DPS`, etc.) e mantém `[0-9A-Z]`."""
    if not valor:
        return ""
    texto = str(valor).strip()
    texto_sem_prefixo = _RE_PREFIXO_CHAVE.sub("", texto, count=1)
    return _RE_ALFANUMERICO.sub("", texto_sem_prefixo).upper()


def descrever_forma_pagamento(codigo: str | None) -> str:
    """Retorna a descrição amigável do código de pagamento `tPag` da SEFAZ."""
    if not codigo:
        return "Não informado"
    cod_limpo = str(codigo).strip()
    if not cod_limpo:
        return "Não informado"
    if cod_limpo.isdigit() and len(cod_limpo) == 1:
        cod_limpo = cod_limpo.zfill(2)
    return MAPA_FORMAS_PAGAMENTO.get(cod_limpo, f"Código {cod_limpo}")
