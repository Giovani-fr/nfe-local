"""Sincronização do ADN: paginação por NSU, cursor próprio, eventos, papel e `sync_log`.

Só roda por ação explícita do usuário (CLI ou botão): este módulo nunca agenda nada.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from parsers.nfse_nacional import parse_nfse_nacional
from parsers.xml_seguro import ErroParseXML
from sefaz.adn import (
    TIPOS_DOCUMENTO_GUARDADOS,
    ErroADN,
    ErroBloqueioADN,
    EstadoCursorADN,
    ItemADN,
    RespostaADN,
    calcular_papel,
    mensagem_rejeicao,
)
from sefaz.descompactador import (
    MAX_BYTES_XML_DOC_ZIP_PADRAO,
    ErroDescompactacaoDocZip,
    descompactar_doc_zip,
)
from sefaz.storage import RepositorioDistDFeSQLite

MAX_PAGINAS_PADRAO = 20
ESPERA_ENTRE_CHAMADAS_PADRAO = 1.0

EncerradoPor = Literal["FILA_ZERADA", "LIMITE_PAGINAS", "BLOQUEIO", "REJEICAO", "ERRO"]


class ResumoSyncADN(BaseModel):
    """Resultado de uma execução de `sincronizar_adn`."""

    model_config = ConfigDict(extra="ignore")

    cursor: EstadoCursorADN
    paginas_consultadas: int = 0
    documentos_recebidos: int = 0
    novas_notas: int = 0
    notas_atualizadas: int = 0
    eventos_registrados: int = 0
    ignorados_por_tipo: dict[str, int] = Field(default_factory=dict)
    com_erro_processamento: int = 0
    papeis: dict[str, int] = Field(default_factory=dict)
    encerrado_por: EncerradoPor = "FILA_ZERADA"
    mensagem: str = ""


def verificar_janela_adn(cursor: EstadoCursorADN, agora: datetime) -> None:
    """Impede consultar o ADN antes do fim do intervalo de espera (sem tocar a rede)."""
    if cursor.bloqueado_ate is None:
        return
    alvo = cursor.bloqueado_ate
    if alvo.tzinfo is None:
        alvo = alvo.replace(tzinfo=UTC)
    if agora < alvo:
        segundos = max(1, int((alvo - agora).total_seconds()) + 1)
        raise ErroBloqueioADN(
            f"Consulta ao ADN em espera até {alvo.isoformat()} (~{max(1, segundos // 60)} min) "
            f"por: {cursor.ultimo_motivo or cursor.ultimo_status or 'limite de uso'}. "
            "Nenhuma chamada foi feita.",
            bloqueado_ate=alvo,
            segundos_restantes=segundos,
        )


def _processar_item(
    item: ItemADN,
    *,
    repo: RepositorioDistDFeSQLite,
    ambiente: Literal[1, 2],
    documento_alvo: str,
    agora: datetime,
    resumo: ResumoSyncADN,
    max_bytes_xml: int,
) -> None:
    tipo = item.tipo_documento
    if tipo not in TIPOS_DOCUMENTO_GUARDADOS:
        resumo.ignorados_por_tipo[tipo] = resumo.ignorados_por_tipo.get(tipo, 0) + 1
        return
    nsu = item.nsu or 0
    try:
        if not item.arquivo_xml:
            raise ErroDescompactacaoDocZip("Item sem ArquivoXml.")
        xml = descompactar_doc_zip(item.arquivo_xml, max_bytes_xml=max_bytes_xml)

        if tipo == "EVENTO":
            if not item.chave_acesso:
                raise ErroDescompactacaoDocZip("Evento sem ChaveAcesso.")
            status = repo.salvar_evento_adn(
                chave=item.chave_acesso,
                nsu=nsu,
                tipo_evento=item.tipo_evento or "DESCONHECIDO",
                xml_bytes=xml,
                data_evento=item.data_hora_geracao,
                ambiente=ambiente,
                agora=agora,
            )
        else:
            doc = parse_nfse_nacional(xml)
            if doc.tipo != "NFSE":
                resumo.ignorados_por_tipo[doc.tipo] = resumo.ignorados_por_tipo.get(doc.tipo, 0) + 1
                return
            papel = calcular_papel(documento_alvo, doc, xml)
            status = repo.salvar_nfse_adn(
                chave=item.chave_acesso or doc.chave_acesso,
                nsu=nsu,
                doc=doc,
                xml_bytes=xml,
                papel=papel,
                ambiente=ambiente,
                agora=agora,
            )
            rotulo = papel or "SEM_PAPEL"
            resumo.papeis[rotulo] = resumo.papeis.get(rotulo, 0) + 1
    except (ErroDescompactacaoDocZip, ErroParseXML, ValueError):
        resumo.com_erro_processamento += 1
        return

    if status == "NOVA":
        resumo.novas_notas += 1
    elif status == "ATUALIZADA":
        resumo.notas_atualizadas += 1
    elif status == "EVENTO":
        resumo.eventos_registrados += 1


def sincronizar_adn(
    consultar_fn: Callable[[int], RespostaADN],
    repo: RepositorioDistDFeSQLite,
    *,
    documento_alvo: str,
    ambiente: Literal[1, 2] = 2,
    max_paginas: int = MAX_PAGINAS_PADRAO,
    espera_segundos: float = ESPERA_ENTRE_CHAMADAS_PADRAO,
    dormir_fn: Callable[[float], None] = time.sleep,
    relogio_fn: Callable[[], datetime] | None = None,
    max_bytes_xml: int = MAX_BYTES_XML_DOC_ZIP_PADRAO,
) -> ResumoSyncADN:
    """Uma rodada de sincronização do ADN. Levanta `ErroBloqueioADN` sem tocar a rede."""
    agora_fn = relogio_fn or (lambda: datetime.now(UTC))
    cursor = repo.obter_cursor_adn(ambiente)
    verificar_janela_adn(cursor, agora_fn())

    resumo = ResumoSyncADN(cursor=cursor)
    status_final = ""

    for pagina in range(max(1, max_paginas)):
        if pagina > 0 and espera_segundos > 0:
            dormir_fn(espera_segundos)
        agora = agora_fn()
        try:
            resposta = consultar_fn(cursor.ultimo_nsu)
        except ErroADN as exc:
            resumo.paginas_consultadas += 1
            cursor.ultima_consulta = agora
            cursor.ultimo_status = exc.categoria
            cursor.ultimo_motivo = str(exc)
            if exc.retry_apos_segundos:
                cursor.bloqueado_ate = agora + timedelta(seconds=exc.retry_apos_segundos)
                resumo.encerrado_por = "BLOQUEIO"
            else:
                resumo.encerrado_por = "ERRO"
            resumo.mensagem = str(exc)
            status_final = exc.categoria
            repo.salvar_cursor_adn(cursor)
            break

        resumo.paginas_consultadas += 1
        cursor.ultima_consulta = agora
        cursor.ultimo_status = resposta.status_processamento
        status_final = resposta.status_processamento

        if resposta.status_processamento == "NENHUM_DOCUMENTO_LOCALIZADO":
            cursor.ultimo_motivo = "Nenhum documento novo."
            resumo.encerrado_por = "FILA_ZERADA"
            repo.salvar_cursor_adn(cursor)
            break

        if resposta.status_processamento == "REJEICAO":
            cursor.ultimo_motivo = mensagem_rejeicao(resposta)
            resumo.encerrado_por = "REJEICAO"
            resumo.mensagem = cursor.ultimo_motivo
            repo.salvar_cursor_adn(cursor)
            break

        itens = sorted(resposta.lote, key=lambda i: i.nsu if i.nsu is not None else 0)
        resumo.documentos_recebidos += len(itens)
        for item in itens:
            _processar_item(
                item,
                repo=repo,
                ambiente=ambiente,
                documento_alvo=documento_alvo,
                agora=agora,
                resumo=resumo,
                max_bytes_xml=max_bytes_xml,
            )

        maior_nsu = max((i.nsu for i in itens if i.nsu is not None), default=cursor.ultimo_nsu)
        avancou = maior_nsu > cursor.ultimo_nsu
        if avancou:
            cursor.ultimo_nsu = maior_nsu
        cursor.ultimo_motivo = f"{len(itens)} documento(s) na página."
        repo.salvar_cursor_adn(cursor)  # persiste a cada página

        if not avancou:
            resumo.encerrado_por = "ERRO"
            resumo.mensagem = "O ADN devolveu documentos sem NSU maior que o cursor; encerrado."
            break
        resumo.encerrado_por = "LIMITE_PAGINAS"
    else:
        resumo.encerrado_por = "LIMITE_PAGINAS"

    resumo.cursor = cursor
    repo.registrar_log_sync(
        ambiente=ambiente,
        cstat=status_final,
        motivo=(resumo.mensagem or cursor.ultimo_motivo or "")[:500],
        paginas=resumo.paginas_consultadas,
        novos_docs=resumo.novas_notas + resumo.notas_atualizadas + resumo.eventos_registrados,
        ultimo_nsu=str(cursor.ultimo_nsu),
        ts=agora_fn(),
        origem="NFSE",
    )
    return resumo
