"""Validação de batimento financeiro (soma dos itens e consistência de vNF)."""

from __future__ import annotations

from decimal import Decimal

from models.fiscal import AlertaValidacao, DocumentoFiscal
from models.normalizadores import DECIMAL_ZERO

_CENTAVO = Decimal("0.01")
_TOLERANCIA_UNITARIO = Decimal("0.05")


def validar_financeiro(
    doc: DocumentoFiscal,
    tolerancia: Decimal = DECIMAL_ZERO,
) -> list[AlertaValidacao]:
    """Verifica o batimento entre itens, vProd e vNF da nota fiscal."""
    if doc.tipo in ("RESUMO", "EVENTO"):
        return []

    alertas: list[AlertaValidacao] = []

    soma_itens = sum((item.valor_total for item in doc.itens), start=DECIMAL_ZERO)
    v_prod = doc.totais.valor_produtos_servicos
    diferenca_itens = abs(soma_itens - v_prod)
    if diferenca_itens > tolerancia:
        alertas.append(
            AlertaValidacao(
                codigo="SOMA_ITENS_DIVERGENTE",
                severidade="ERRO",
                mensagem=(
                    f"Soma dos valores dos itens (R$ {soma_itens:.2f}) diverge do total de "
                    f"produtos/serviços da nota (R$ {v_prod:.2f})."
                ),
                campo="totais.valor_produtos_servicos",
            )
        )

    if doc.tipo in ("NFE", "NFCE"):
        tot = doc.totais
        vnf_calculado = (
            tot.valor_produtos_servicos
            - tot.valor_desconto
            + tot.valor_frete
            + tot.valor_seguro
            + tot.valor_outras_despesas
            + tot.impostos.ipi
            + tot.impostos.icms_st
        )
        diferenca_vnf = abs(vnf_calculado - tot.valor_total_nota)
        if diferenca_vnf > tolerancia:
            alertas.append(
                AlertaValidacao(
                    codigo="TOTAL_NOTA_DIVERGENTE",
                    severidade="ERRO",
                    mensagem=(
                        f"Equação financeira do total da nota (R$ {vnf_calculado:.2f}) "
                        f"diverge de vNF informado (R$ {tot.valor_total_nota:.2f})."
                    ),
                    campo="totais.valor_total_nota",
                )
            )

    for item in doc.itens:
        calculado_item = (item.quantidade * item.valor_unitario).quantize(_CENTAVO)
        if abs(calculado_item - item.valor_total) > _TOLERANCIA_UNITARIO:
            alertas.append(
                AlertaValidacao(
                    codigo="ITEM_CALCULO_DIVERGENTE",
                    severidade="AVISO",
                    mensagem=(
                        f"Item {item.numero_item} ({item.codigo}): qtd x unitário "
                        f"(R$ {calculado_item:.2f}) diverge do valor total do item "
                        f"(R$ {item.valor_total:.2f})."
                    ),
                    campo=f"itens[{item.numero_item}].valor_total",
                )
            )

    return alertas
