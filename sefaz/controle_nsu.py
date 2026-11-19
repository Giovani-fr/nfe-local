"""Máquina de estados de paginação de `ultNSU` e trava obrigatória do erro `656` (NT 2014.002)."""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from sefaz.adn import FalhaConexao
from sefaz.dist_dfe import (
    ErroConexaoSEFAZ,
    ItemDocZip,
    RespostaDistDFe,
    formatar_nsu,
)

CSTAT_DOCS_LOCALIZADOS = "138"
CSTAT_NENHUM_DOC = "137"
CSTAT_CONSUMO_INDEVIDO = "656"

TEMPO_BLOQUEIO_656_SEGUNDOS = 3600
TEMPO_ESPERA_137_SEGUNDOS = 3600


class ErroBloqueioSEFAZ(Exception):
    """Exceção levantada quando uma consulta é tentada durante a janela de bloqueio da SEFAZ."""

    def __init__(
        self,
        mensagem: str,
        *,
        bloqueado_ate: datetime,
        segundos_restantes: int,
        motivo: str = "Consumo Indevido (cStat 656)",
    ) -> None:
        super().__init__(mensagem)
        self.bloqueado_ate = bloqueado_ate
        self.segundos_restantes = segundos_restantes
        self.motivo = motivo


class EstadoCursorNSU(BaseModel):
    """Estado persistível do cursor de sincronização `NFeDistribuicaoDFe`."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    ambiente: Literal[1, 2] = 2
    ultimo_nsu: str = "000000000000000"
    max_nsu: str = "000000000000000"
    ultima_consulta: datetime | None = None
    bloqueado_ate: datetime | None = None
    ultimo_cstat: str | None = None
    ultimo_motivo: str | None = None
    # CNPJ consultado (o do certificado, ou `NFE_CNPJ_CONSULTA`): base do papel Emitida/Recebida.
    # Nunca sai em JSON/API.
    documento_consulta: str = Field(default="", exclude=True)


class ResultadoSincronizacaoNSU(BaseModel):
    """Resultado consolidado de uma rodada de sincronização paginada por `ultNSU`."""

    model_config = ConfigDict(extra="ignore")

    cursor: EstadoCursorNSU
    paginas_consultadas: int = 0
    respostas: list[RespostaDistDFe] = Field(default_factory=list)
    itens_doc_zip: list[ItemDocZip] = Field(default_factory=list)
    fila_zerada: bool = False
    bloqueado_656: bool = False
    interrompido_por_erro: bool = False
    erro_conexao: str | None = None
    falha_conexao: FalhaConexao | None = None


def _normalizar_utc(dt: datetime | None) -> datetime:
    if dt is None:
        return datetime.now(UTC)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _nsu_para_int(nsu: str | int | None) -> int:
    if nsu is None:
        return 0
    if isinstance(nsu, int):
        return max(0, nsu)
    limpo = str(nsu).strip()
    return int(limpo) if limpo.isdigit() else 0


def verificar_janela_consulta(
    cursor: EstadoCursorNSU,
    agora: datetime | None = None,
) -> None:
    """Impede consultas antes do término de `cursor.bloqueado_ate` (evitando reset do erro 656)."""
    if cursor.bloqueado_ate is None:
        return

    ref = _normalizar_utc(agora)
    alvo = _normalizar_utc(cursor.bloqueado_ate)
    if ref < alvo:
        segundos = max(1, int(math.ceil((alvo - ref).total_seconds())))
        minutos = max(1, math.ceil(segundos / 60))
        motivo_str = cursor.ultimo_motivo or "Consumo Indevido (cStat 656)"
        raise ErroBloqueioSEFAZ(
            (
                f"Sincronização SEFAZ em janela obrigatória de espera até "
                f"{alvo.isoformat()} (~{minutos} min / {segundos}s restantes). "
                "Consultar o WebService antes do término reinicia o bloqueio de 1 hora "
                f"(cStat 656 - {motivo_str})."
            ),
            bloqueado_ate=alvo,
            segundos_restantes=segundos,
            motivo=motivo_str,
        )


def aplicar_resposta_no_cursor(
    cursor: EstadoCursorNSU,
    resposta: RespostaDistDFe,
    agora: datetime | None = None,
    bloquear_apos_137: bool = False,
) -> EstadoCursorNSU:
    """Aplica as regras de transição da NT 2014.002 (`138`, `137` e `656`) sobre o cursor."""
    ref = _normalizar_utc(agora)
    novo = cursor.model_copy(deep=True)
    novo.ultima_consulta = ref
    novo.ultimo_cstat = resposta.cstat
    novo.ultimo_motivo = resposta.xmotivo

    ult_resp_int = _nsu_para_int(resposta.ult_nsu)
    max_resp_int = _nsu_para_int(resposta.max_nsu)
    ult_atual_int = _nsu_para_int(novo.ultimo_nsu)
    max_atual_int = _nsu_para_int(novo.max_nsu)

    if resposta.cstat == CSTAT_DOCS_LOCALIZADOS:
        novo.ultimo_nsu = formatar_nsu(ult_resp_int)
        novo.max_nsu = formatar_nsu(max(max_resp_int, ult_resp_int))
        if bloquear_apos_137 and ult_resp_int >= max_resp_int and max_resp_int > 0:
            novo.bloqueado_ate = ref + timedelta(seconds=TEMPO_ESPERA_137_SEGUNDOS)
        else:
            novo.bloqueado_ate = None

    elif resposta.cstat == CSTAT_NENHUM_DOC:
        if ult_resp_int > ult_atual_int:
            novo.ultimo_nsu = formatar_nsu(ult_resp_int)
        if max_resp_int > max_atual_int:
            novo.max_nsu = formatar_nsu(max_resp_int)
        if bloquear_apos_137:
            novo.bloqueado_ate = ref + timedelta(seconds=TEMPO_ESPERA_137_SEGUNDOS)
        else:
            novo.bloqueado_ate = None

    elif resposta.cstat == CSTAT_CONSUMO_INDEVIDO:
        novo.bloqueado_ate = ref + timedelta(seconds=TEMPO_BLOQUEIO_656_SEGUNDOS)
        # Se a SEFAZ devolveu o ultNSU correto na própria rejeição 656, sincroniza o cursor
        if ult_resp_int > 0:
            novo.ultimo_nsu = formatar_nsu(ult_resp_int)
        if max_resp_int > 0:
            novo.max_nsu = formatar_nsu(max_resp_int)

    return novo


def executar_loop_nsu(
    consultar_fn: Callable[[str], RespostaDistDFe],
    cursor_inicial: EstadoCursorNSU,
    max_paginas: int = 20,
    relogio_fn: Callable[[], datetime] | None = None,
    salvar_cursor_fn: Callable[[EstadoCursorNSU], None] | None = None,
    bloquear_apos_137: bool = False,
) -> ResultadoSincronizacaoNSU:
    """Executa paginação segura de `ultNSU` respeitando `138`, `137`, `656` e `ultNSU == maxNSU`."""
    obter_agora = relogio_fn if relogio_fn is not None else (lambda: datetime.now(UTC))
    verificar_janela_consulta(cursor_inicial, agora=obter_agora())

    cursor = cursor_inicial.model_copy(deep=True)
    limite_paginas = max(1, max_paginas)
    respostas: list[RespostaDistDFe] = []
    itens_acumulados: list[ItemDocZip] = []
    fila_zerada = False
    bloqueado_656 = False
    interrompido_por_erro = False
    erro_conexao: ErroConexaoSEFAZ | None = None

    for _ in range(limite_paginas):
        nsu_anterior_int = _nsu_para_int(cursor.ultimo_nsu)
        try:
            resposta = consultar_fn(cursor.ultimo_nsu)
        except ErroConexaoSEFAZ as exc:
            # Página que falhou: o cursor NÃO avança nem é salvo; o que veio antes é preservado.
            erro_conexao = exc
            interrompido_por_erro = True
            break
        agora = obter_agora()

        cursor = aplicar_resposta_no_cursor(
            cursor,
            resposta,
            agora=agora,
            bloquear_apos_137=bloquear_apos_137,
        )
        if salvar_cursor_fn is not None:
            salvar_cursor_fn(cursor)

        respostas.append(resposta)
        if resposta.lote_doc_zip:
            itens_acumulados.extend(resposta.lote_doc_zip)

        if resposta.cstat == CSTAT_CONSUMO_INDEVIDO:
            bloqueado_656 = True
            break

        if resposta.cstat == CSTAT_NENHUM_DOC:
            fila_zerada = True
            break

        if resposta.cstat != CSTAT_DOCS_LOCALIZADOS:
            interrompido_por_erro = True
            break

        ult_atual_int = _nsu_para_int(cursor.ultimo_nsu)
        max_atual_int = _nsu_para_int(cursor.max_nsu)

        # Topo da fila atingido (ultNSU >= maxNSU): encerra sem fazer chamada extra!
        if ult_atual_int >= max_atual_int and max_atual_int > 0:
            fila_zerada = True
            break

        # Trava anti-loop infinito caso o WebService não avance o NSU ou não retorne docs
        if ult_atual_int <= nsu_anterior_int or not resposta.lote_doc_zip:
            break

    return ResultadoSincronizacaoNSU(
        cursor=cursor,
        paginas_consultadas=len(respostas),
        respostas=respostas,
        itens_doc_zip=itens_acumulados,
        fila_zerada=fila_zerada,
        bloqueado_656=bloqueado_656,
        interrompido_por_erro=interrompido_por_erro,
        erro_conexao=str(erro_conexao) if erro_conexao else None,
        falha_conexao=erro_conexao.falha if erro_conexao else None,
    )
