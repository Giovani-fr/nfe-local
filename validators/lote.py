"""Orquestrador de validação de documento único e de lotes (duplicidade, homologação, etc.)."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal

from models.fiscal import AlertaValidacao, DocumentoFiscal, ResultadoAnalise
from models.normalizadores import DECIMAL_ZERO, limpar_chave_acesso
from validators.chave_acesso import validar_chave_documento
from validators.documentos_parte import validar_partes
from validators.financeiro import validar_financeiro

_TIPOS_CHAVE_UNICA_LOTE = {"NFE", "NFCE", "NFSE", "RESUMO"}


def validar_documento(
    doc: DocumentoFiscal,
    *,
    tolerancia_financeira: Decimal = DECIMAL_ZERO,
) -> list[AlertaValidacao]:
    """Executa todas as regras de validação sobre um `DocumentoFiscal`."""
    alertas: list[AlertaValidacao] = []
    alertas.extend(validar_chave_documento(doc))
    alertas.extend(validar_partes(doc))
    alertas.extend(validar_financeiro(doc, tolerancia=tolerancia_financeira))

    if doc.ambiente == 2:
        alertas.append(
            AlertaValidacao(
                codigo="AMBIENTE_HOMOLOGACAO",
                severidade="AVISO",
                mensagem="Documento emitido em ambiente de homologação (sem valor fiscal).",
                campo="ambiente",
            )
        )

    if doc.tipo in ("NFE", "NFCE") and doc.situacao == "SEM_PROTOCOLO":
        alertas.append(
            AlertaValidacao(
                codigo="SEM_PROTOCOLO",
                severidade="AVISO",
                mensagem="Nota fiscal sem protocolo de autorização da SEFAZ (protNFe ausente).",
                campo="protocolo",
            )
        )

    if doc.situacao == "CANCELADA":
        alertas.append(
            AlertaValidacao(
                codigo="DOCUMENTO_CANCELADO",
                severidade="AVISO",
                mensagem="Documento fiscal encontra-se cancelado.",
                campo="situacao",
            )
        )
    elif doc.situacao == "REJEITADA":
        alertas.append(
            AlertaValidacao(
                codigo="DOCUMENTO_REJEITADO",
                severidade="ERRO",
                mensagem="Documento fiscal com status de rejeição na SEFAZ.",
                campo="situacao",
            )
        )

    return alertas


def analisar_documento(
    arquivo_origem: str,
    doc: DocumentoFiscal,
    *,
    tolerancia_financeira: Decimal = DECIMAL_ZERO,
) -> ResultadoAnalise:
    """Valida um `DocumentoFiscal` e encapsula em `ResultadoAnalise`."""
    alertas = validar_documento(doc, tolerancia_financeira=tolerancia_financeira)
    tem_erro = any(alerta.severidade == "ERRO" for alerta in alertas)
    return ResultadoAnalise(
        arquivo_origem=arquivo_origem,
        valido=not tem_erro,
        documento=doc,
        alertas=alertas,
        erro_fatal=None,
    )


def criar_resultado_erro_xml(
    arquivo_origem: str,
    mensagem_erro: str,
    codigo: str = "XML_INVALIDO",
) -> ResultadoAnalise:
    """Cria um `ResultadoAnalise` de falha fatal sem interromper o processamento de lote."""
    return ResultadoAnalise(
        arquivo_origem=arquivo_origem,
        valido=False,
        documento=None,
        alertas=[
            AlertaValidacao(
                codigo=codigo,
                severidade="ERRO",
                mensagem=mensagem_erro,
                campo=None,
            )
        ],
        erro_fatal=mensagem_erro,
    )


def validar_lote(resultados: list[ResultadoAnalise]) -> list[ResultadoAnalise]:
    """Detecta chaves de acesso duplicadas no lote (ignorando eventos vinculados à nota)."""
    contagem_chaves: Counter[str] = Counter()

    for res in resultados:
        if res.documento is not None and res.documento.tipo in _TIPOS_CHAVE_UNICA_LOTE:
            chave = limpar_chave_acesso(res.documento.chave_acesso)
            if chave:
                contagem_chaves[chave] += 1

    chaves_duplicadas = {ch for ch, qtd in contagem_chaves.items() if qtd > 1}
    if not chaves_duplicadas:
        return resultados

    for res in resultados:
        if res.documento is None or res.documento.tipo not in _TIPOS_CHAVE_UNICA_LOTE:
            continue
        chave = limpar_chave_acesso(res.documento.chave_acesso)
        if chave in chaves_duplicadas:
            ja_possui = any(a.codigo == "CHAVE_DUPLICADA_LOTE" for a in res.alertas)
            if not ja_possui:
                res.alertas.append(
                    AlertaValidacao(
                        codigo="CHAVE_DUPLICADA_LOTE",
                        severidade="ERRO",
                        mensagem=(
                            f"Chave de acesso '{chave}' aparece duplicada "
                            f"({contagem_chaves[chave]} vezes) no lote."
                        ),
                        campo="chave_acesso",
                    )
                )
            res.valido = False

    return resultados
