"""Testes unitários (100% offline) da máquina de estados de NSU e trava do erro 656."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sefaz.controle_nsu import (
    CSTAT_CONSUMO_INDEVIDO,
    CSTAT_DOCS_LOCALIZADOS,
    CSTAT_NENHUM_DOC,
    TEMPO_BLOQUEIO_656_SEGUNDOS,
    ErroBloqueioSEFAZ,
    EstadoCursorNSU,
    aplicar_resposta_no_cursor,
    executar_loop_nsu,
    verificar_janela_consulta,
)
from sefaz.dist_dfe import ItemDocZip, RespostaDistDFe


def _criar_resposta(
    *,
    cstat: str,
    xmotivo: str,
    ult_nsu: int | str,
    max_nsu: int | str,
    nsus_docs: tuple[int, ...] = (),
) -> RespostaDistDFe:
    itens = [
        ItemDocZip(
            nsu=f"{n:015d}",
            schema_xml="resNFe_v1.01.xsd",
            conteudo_base64=f"B64_{n}",
        )
        for n in nsus_docs
    ]
    return RespostaDistDFe(
        ambiente=2,
        ver_aplic="1.6.3",
        cstat=cstat,
        xmotivo=xmotivo,
        dh_resp=datetime(2026, 3, 15, 12, 0, tzinfo=UTC),
        ult_nsu=f"{int(ult_nsu):015d}",
        max_nsu=f"{int(max_nsu):015d}",
        lote_doc_zip=itens,
    )


class TestControleNSU:
    """Testes da máquina de estados `138`, `137` e `656` e paginação de `ultNSU`."""

    def test_paginacao_multi_lote_138_para_ao_atingir_max_nsu_sem_chamada_extra(self) -> None:
        agora = datetime(2026, 3, 15, 14, 0, tzinfo=UTC)
        nsus_enviados: list[str] = []
        cursores_salvos: list[EstadoCursorNSU] = []

        paginas = [
            _criar_resposta(
                cstat=CSTAT_DOCS_LOCALIZADOS,
                xmotivo="Documento(s) localizado(s)",
                ult_nsu=50,
                max_nsu=100,
                nsus_docs=(49, 50),
            ),
            _criar_resposta(
                cstat=CSTAT_DOCS_LOCALIZADOS,
                xmotivo="Documento(s) localizado(s)",
                ult_nsu=100,
                max_nsu=100,
                nsus_docs=(99, 100),
            ),
        ]

        def fake_consultar(ult_nsu: str) -> RespostaDistDFe:
            nsus_enviados.append(ult_nsu)
            return paginas[len(nsus_enviados) - 1]

        cursor_inicial = EstadoCursorNSU(ambiente=2, ultimo_nsu="000000000000000")
        resultado = executar_loop_nsu(
            consultar_fn=fake_consultar,
            cursor_inicial=cursor_inicial,
            relogio_fn=lambda: agora,
            salvar_cursor_fn=lambda c: cursores_salvos.append(c.model_copy(deep=True)),
        )

        # Fez exatamente 2 chamadas (parou ao ver ultNSU == maxNSU == 100, evitando uma 3ª chamada!)
        assert nsus_enviados == ["000000000000000", "000000000000050"]
        assert resultado.paginas_consultadas == 2
        assert resultado.fila_zerada is True
        assert resultado.bloqueado_656 is False
        assert resultado.interrompido_por_erro is False
        assert resultado.cursor.ultimo_nsu == "000000000000100"
        assert resultado.cursor.max_nsu == "000000000000100"
        assert len(resultado.itens_doc_zip) == 4
        assert [i.nsu for i in resultado.itens_doc_zip] == [
            "000000000000049",
            "000000000000050",
            "000000000000099",
            "000000000000100",
        ]

        # Garante que o callback salvar_cursor_fn foi chamado incrementalmente a cada página
        assert len(cursores_salvos) == 2
        assert cursores_salvos[0].ultimo_nsu == "000000000000050"
        assert cursores_salvos[1].ultimo_nsu == "000000000000100"

    def test_retorno_137_encerra_na_primeira_chamada_com_fila_zerada(self) -> None:
        agora = datetime(2026, 3, 15, 15, 0, tzinfo=UTC)
        chamadas = 0

        def fake_consultar(ult_nsu: str) -> RespostaDistDFe:
            nonlocal chamadas
            chamadas += 1
            return _criar_resposta(
                cstat=CSTAT_NENHUM_DOC,
                xmotivo="Nenhum documento localizado",
                ult_nsu=100,
                max_nsu=100,
            )

        cursor_inicial = EstadoCursorNSU(
            ambiente=2,
            ultimo_nsu="000000000000100",
            max_nsu="000000000000100",
        )
        resultado = executar_loop_nsu(
            consultar_fn=fake_consultar,
            cursor_inicial=cursor_inicial,
            relogio_fn=lambda: agora,
            bloquear_apos_137=True,
        )

        assert chamadas == 1
        assert resultado.paginas_consultadas == 1
        assert resultado.fila_zerada is True
        assert resultado.bloqueado_656 is False
        assert resultado.itens_doc_zip == []
        assert resultado.cursor.ultimo_cstat == "137"
        assert resultado.cursor.bloqueado_ate == agora + timedelta(hours=1)

    def test_retorno_656_grava_trava_de_1_hora_sincroniza_nsu_e_bloqueia_novas_consultas(
        self,
    ) -> None:
        relogio = [datetime(2026, 3, 15, 16, 0, tzinfo=UTC)]
        chamadas = 0

        def fake_consultar(ult_nsu: str) -> RespostaDistDFe:
            nonlocal chamadas
            chamadas += 1
            # SEFAZ devolve 656 informando que o ultNSU correto na base dela é 250
            return _criar_resposta(
                cstat=CSTAT_CONSUMO_INDEVIDO,
                xmotivo="Rejeicao: Consumo Indevido",
                ult_nsu=250,
                max_nsu=300,
            )

        cursor_inicial = EstadoCursorNSU(ambiente=2, ultimo_nsu="000000000000100")
        resultado = executar_loop_nsu(
            consultar_fn=fake_consultar,
            cursor_inicial=cursor_inicial,
            relogio_fn=lambda: relogio[0],
        )

        assert chamadas == 1
        assert resultado.bloqueado_656 is True
        assert resultado.fila_zerada is False
        # Sincronizou o ultimo_nsu com o valor retornado pela SEFAZ na rejeição 656
        assert resultado.cursor.ultimo_nsu == "000000000000250"
        assert resultado.cursor.max_nsu == "000000000000300"
        assert resultado.cursor.bloqueado_ate == relogio[0] + timedelta(
            seconds=TEMPO_BLOQUEIO_656_SEGUNDOS
        )

        # Tentar consultar 30 minutos depois deve levantar ErroBloqueioSEFAZ sem tocar na rede!
        relogio[0] = relogio[0] + timedelta(minutes=30)
        with pytest.raises(ErroBloqueioSEFAZ) as exc_info:
            executar_loop_nsu(
                consultar_fn=fake_consultar,
                cursor_inicial=resultado.cursor,
                relogio_fn=lambda: relogio[0],
            )
        assert exc_info.value.segundos_restantes == 1800
        assert "656" in str(exc_info.value)
        assert chamadas == 1  # Nenhuma chamada extra foi feita à SEFAZ

        # Avança mais 30m01s (total > 3600s): o bloqueio expira e a consulta é liberada
        relogio[0] = relogio[0] + timedelta(minutes=30, seconds=1)
        verificar_janela_consulta(resultado.cursor, agora=relogio[0])

    def test_protecao_anti_loop_infinito_quando_ult_nsu_nao_avanca_e_erro_cstat(self) -> None:
        agora = datetime(2026, 3, 15, 18, 0, tzinfo=UTC)
        chamadas = 0

        def fake_consultar_estagnado(ult_nsu: str) -> RespostaDistDFe:
            nonlocal chamadas
            chamadas += 1
            # Mesmo retornando 138 e ultNSU < maxNSU, o ultNSU não avançou em relação a 50
            return _criar_resposta(
                cstat=CSTAT_DOCS_LOCALIZADOS,
                xmotivo="Documento(s) localizado(s)",
                ult_nsu=50,
                max_nsu=100,
                nsus_docs=(50,),
            )

        cursor = EstadoCursorNSU(ambiente=2, ultimo_nsu="000000000000050")
        res_estagnado = executar_loop_nsu(
            consultar_fn=fake_consultar_estagnado,
            cursor_inicial=cursor,
            max_paginas=10,
            relogio_fn=lambda: agora,
        )
        assert chamadas == 1
        assert res_estagnado.paginas_consultadas == 1
        assert res_estagnado.fila_zerada is False

        # Testa cStat inesperado (ex.: 215 - Falha no schema XML)
        res_erro = executar_loop_nsu(
            consultar_fn=lambda _: _criar_resposta(
                cstat="215",
                xmotivo="Rejeicao: Falha no schema XML",
                ult_nsu=50,
                max_nsu=100,
            ),
            cursor_inicial=cursor,
            relogio_fn=lambda: agora,
        )
        assert res_erro.interrompido_por_erro is True
        assert res_erro.bloqueado_656 is False
        assert res_erro.fila_zerada is False

    def test_aplicar_resposta_no_cursor_preserva_imutabilidade_do_argumento(self) -> None:
        agora = datetime(2026, 3, 15, 19, 0, tzinfo=UTC)
        original = EstadoCursorNSU(ambiente=2, ultimo_nsu="000000000000010")
        resp = _criar_resposta(
            cstat=CSTAT_DOCS_LOCALIZADOS,
            xmotivo="Documento(s) localizado(s)",
            ult_nsu=25,
            max_nsu=40,
            nsus_docs=(25,),
        )
        novo = aplicar_resposta_no_cursor(original, resp, agora=agora)
        assert original.ultimo_nsu == "000000000000010"
        assert novo.ultimo_nsu == "000000000000025"
        assert novo.max_nsu == "000000000000040"
