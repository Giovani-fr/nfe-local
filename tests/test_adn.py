"""ADN Contribuintes (NFS-e): cliente, paginação, cursor, papel, eventos, dedupe e migração.

Tudo sem rede e sem certificado real: respostas simuladas, XMLs e CNPJs 100% fictícios.
"""

from __future__ import annotations

import gzip
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import requests
from fastapi.testclient import TestClient

from api.app import (
    app,
    definir_consultor_adn,
    definir_diretorio_certificado,
    definir_repositorio_recebidas,
)
from api.cli import main as cli_main
from parsers.nfse_nacional import parse_nfse_nacional
from sefaz.adn import (
    MENSAGEM_REJEICAO_SEM_DETALHES,
    ClienteADN,
    ErroADN,
    ErroBloqueioADN,
    ItemADN,
    RespostaADN,
    calcular_papel,
    mensagem_rejeicao,
    parse_resposta_adn,
)
from sefaz.adn_demo import CNPJ_DEMO, _xml, montar_itens_demo
from sefaz.adn_sync import sincronizar_adn
from sefaz.descompactador import compactar_xml_para_doc_zip_b64
from sefaz.storage import RepositorioDistDFeSQLite
from tests.test_certificado_api import _gerar_pfx_sintetico

RELOGIO = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)

NOME_ALVO = ("EMPRESA EXEMPLO LTDA", CNPJ_DEMO)
NOME_OUTRA = ("SERVICOS GAMA EXEMPLO SA", "33444555000181")
NOME_TERCEIRA = ("CONSULTORIA DELTA EXEMPLO LTDA", "55666777000181")


def _item_nfse(numero: int, nsu: int, prest, toma, valor="100.00", interm=None) -> ItemADN:
    chave, xml = _xml(numero, prest, toma, valor, interm=interm)
    return ItemADN(
        nsu=nsu,
        chave_acesso=chave,
        tipo_documento="NFSE",
        arquivo_xml=compactar_xml_para_doc_zip_b64(xml),
        data_hora_geracao=RELOGIO,
    )


def _resposta(itens: list[ItemADN]) -> RespostaADN:
    return RespostaADN(http_status=200, status_processamento="DOCUMENTOS_LOCALIZADOS", lote=itens)


RESPOSTA_FIM = RespostaADN(http_status=404, status_processamento="NENHUM_DOCUMENTO_LOCALIZADO")


@pytest.fixture
def repo(tmp_path: Path) -> RepositorioDistDFeSQLite:
    return RepositorioDistDFeSQLite(tmp_path / "adn.db")


def _sincronizar(consultar, repo, **kw):
    kw.setdefault("documento_alvo", CNPJ_DEMO)
    kw.setdefault("espera_segundos", 0)
    kw.setdefault("relogio_fn", lambda: RELOGIO)
    return sincronizar_adn(consultar, repo, **kw)


# --------------------------------------------------------------------------- cliente HTTP


class _Resp:
    def __init__(self, status_code: int, corpo: dict | str = "", headers=None) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self.content = (
            json.dumps(corpo).encode() if isinstance(corpo, dict) else str(corpo).encode()
        )


class _Sessao:
    def __init__(self, *respostas) -> None:
        self.respostas = list(respostas)
        self.chamadas: list[dict] = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.chamadas.append({"url": url, "params": dict(params or {})})
        r = self.respostas.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture(scope="module")
def pfx_sintetico() -> bytes:
    return _gerar_pfx_sintetico(cnpj=CNPJ_DEMO, senha="senha-teste")


def _cliente(pfx, sessao, **kw) -> ClienteADN:
    return ClienteADN(pfx_bytes=pfx, senha="senha-teste", sessao_http=sessao, **kw)


class TestClienteADN:
    def test_cnpj_consulta_sempre_vai_na_requisicao_com_14_digitos(self, pfx_sintetico):
        sessao = _Sessao(_Resp(404, {"StatusProcessamento": "NENHUM_DOCUMENTO_LOCALIZADO"}))
        resp = _cliente(pfx_sintetico, sessao).consultar_nsu(0)
        chamada = sessao.chamadas[0]
        assert chamada["url"].endswith("/contribuintes/DFe/0")
        assert "producaorestrita" in chamada["url"]
        assert chamada["params"]["cnpjConsulta"] == CNPJ_DEMO
        assert len(chamada["params"]["cnpjConsulta"]) == 14
        assert chamada["params"]["lote"] == "true"
        assert resp.status_processamento == "NENHUM_DOCUMENTO_LOCALIZADO"

    def test_producao_usa_o_outro_host(self, pfx_sintetico):
        sessao = _Sessao(_Resp(404, {}))
        _cliente(pfx_sintetico, sessao, ambiente=1).consultar_nsu(7)
        assert sessao.chamadas[0]["url"] == "https://adn.nfse.gov.br/contribuintes/DFe/7"

    def test_env_nfe_cnpj_consulta_substitui_o_do_certificado(self, pfx_sintetico, monkeypatch):
        monkeypatch.setenv("NFE_CNPJ_CONSULTA", "33.444.555/0001-81")
        sessao = _Sessao(_Resp(404, {}))
        _cliente(pfx_sintetico, sessao).consultar_nsu(0)
        assert sessao.chamadas[0]["params"]["cnpjConsulta"] == "33444555000181"

    def test_cnpj_consulta_incompleto_e_recusado_antes_de_chamar(self, pfx_sintetico, monkeypatch):
        monkeypatch.setenv("NFE_CNPJ_CONSULTA", "1122233")
        with pytest.raises(ValueError, match="14"):
            _cliente(pfx_sintetico, _Sessao())

    def test_resposta_com_documentos(self, pfx_sintetico):
        corpo = {
            "TipoConsulta": "CnpjBase",
            "StatusProcessamento": "DOCUMENTOS_LOCALIZADOS",
            "LoteDFe": [
                {
                    "NSU": 12,
                    "ChaveAcesso": "1" * 50,
                    "TipoDocumento": "NFSE",
                    "TipoEvento": None,
                    "ArquivoXml": "H4sI",
                    "DataHoraGeracao": "2026-10-01T10:00:00-03:00",
                }
            ],
            "Alertas": [],
            "Erros": None,
            "TipoAmbiente": "HOMOLOGACAO",
        }
        resp = _cliente(pfx_sintetico, _Sessao(_Resp(200, corpo))).consultar_nsu(0)
        assert resp.tipo_consulta == "CnpjBase"
        assert resp.lote[0].nsu == 12
        assert resp.lote[0].tipo_documento == "NFSE"
        assert resp.lote[0].data_hora_geracao is not None

    def test_404_e_fim_da_fila_e_nao_erro(self, pfx_sintetico):
        corpo = {
            "StatusProcessamento": "NENHUM_DOCUMENTO_LOCALIZADO",
            "Erros": [{"Codigo": "E2220", "Descricao": "Nenhum documento localizado"}],
        }
        resp = _cliente(pfx_sintetico, _Sessao(_Resp(404, corpo))).consultar_nsu(5)
        assert resp.status_processamento == "NENHUM_DOCUMENTO_LOCALIZADO"

    def test_rejeicao_com_lista_de_erros_vazia_nao_culpa_o_certificado(self, pfx_sintetico):
        corpo = {
            "TipoConsulta": "InscricaoCertificado",
            "StatusProcessamento": "REJEICAO",
            "Erros": [],
        }
        resp = _cliente(pfx_sintetico, _Sessao(_Resp(400, corpo))).consultar_nsu(0)
        msg = mensagem_rejeicao(resp)
        assert msg == MENSAGEM_REJEICAO_SEM_DETALHES
        assert "sem detalhes" in msg
        assert "CNPJ de consulta" in msg and "mais tarde" in msg
        assert "não indica" in msg  # não atribui a causa ao certificado

    @pytest.mark.parametrize(
        ("codigo", "trecho"),
        [
            ("E2210", "inválido"),
            ("E2211", "revogado"),
            ("E2212", "cadeia"),
            ("E2213", "expirado"),
            ("E2214", "LCR"),
            ("E2243", "CNPJ base"),
            ("E2245", "procuração"),
            ("E2246", "dígito verificador"),
        ],
    )
    def test_rejeicoes_documentadas_viram_mensagem_simples(self, codigo, trecho):
        r = parse_resposta_adn(
            400,
            json.dumps(
                {"StatusProcessamento": "REJEICAO", "Erros": [{"Codigo": codigo, "Descricao": "x"}]}
            ),
        )
        msg = mensagem_rejeicao(r)
        assert trecho in msg and codigo in msg

    def test_erro_nao_catalogado_mostra_identificador_de_suporte(self):
        r = parse_resposta_adn(
            400,
            json.dumps(
                {
                    "StatusProcessamento": "REJEICAO",
                    "Erros": [{"Codigo": "E999", "Descricao": "ID-SUPORTE-12345"}],
                }
            ),
        )
        msg = mensagem_rejeicao(r)
        assert "ID-SUPORTE-12345" in msg and "suporte" in msg

    def test_mensagem_de_procuracao_nao_vaza_a_descricao_com_documentos(self):
        r = parse_resposta_adn(
            400,
            json.dumps(
                {
                    "StatusProcessamento": "REJEICAO",
                    "Erros": [{"Codigo": "E2245", "Descricao": "sem procuração 99999999000199"}],
                }
            ),
        )
        assert "99999999000199" not in mensagem_rejeicao(r)

    def test_indisponibilidade_5xx_pede_espera_curta(self, pfx_sintetico):
        with pytest.raises(ErroADN) as exc:
            _cliente(pfx_sintetico, _Sessao(_Resp(503, "x"))).consultar_nsu(0)
        assert exc.value.categoria == "INDISPONIVEL"
        assert exc.value.retry_apos_segundos == 600

    def test_limite_429_respeita_retry_after(self, pfx_sintetico):
        sessao = _Sessao(_Resp(429, "", {"Retry-After": "120"}))
        with pytest.raises(ErroADN) as exc:
            _cliente(pfx_sintetico, sessao).consultar_nsu(0)
        assert exc.value.categoria == "LIMITE"
        assert exc.value.retry_apos_segundos == 120

    def test_falha_de_conexao_nao_trava_novas_tentativas(self, pfx_sintetico):
        sessao = _Sessao(requests.ConnectionError("boom"))
        with pytest.raises(ErroADN) as exc:
            _cliente(pfx_sintetico, sessao).consultar_nsu(0)
        assert exc.value.categoria == "CONEXAO"
        assert exc.value.retry_apos_segundos is None

    def test_corpo_que_nao_e_json_vira_erro_claro(self, pfx_sintetico):
        with pytest.raises(ErroADN) as exc:
            _cliente(pfx_sintetico, _Sessao(_Resp(200, "<html>oi</html>"))).consultar_nsu(0)
        assert exc.value.categoria == "RESPOSTA_INVALIDA"


# --------------------------------------------------------------------------- papel


class TestPapel:
    def _doc(self, prest, toma):
        _, xml = _xml(1, prest, toma, "10.00")
        return parse_nfse_nacional(xml), xml

    def test_prestador_tomador_e_intermediario(self):
        doc, xml = self._doc(NOME_ALVO, NOME_OUTRA)
        assert calcular_papel(CNPJ_DEMO, doc, xml) == "PRESTADOR"
        doc, xml = self._doc(NOME_OUTRA, NOME_ALVO)
        assert calcular_papel(CNPJ_DEMO, doc, xml) == "TOMADOR"
        _, xml = _xml(2, NOME_OUTRA, NOME_TERCEIRA, "10.00", interm=NOME_ALVO)
        doc = parse_nfse_nacional(xml)
        assert calcular_papel(CNPJ_DEMO, doc, xml) == "INTERMEDIARIO"

    def test_sem_relacao_com_o_cnpj_nao_tem_papel(self):
        doc, xml = self._doc(NOME_OUTRA, NOME_TERCEIRA)
        assert calcular_papel(CNPJ_DEMO, doc, xml) is None

    def test_cnpj_alfanumerico_e_formatacao(self):
        alfa = ("EMPRESA ALFA EXEMPLO LTDA", "12ABC34501DE35")
        doc, xml = self._doc(alfa, NOME_OUTRA)
        assert calcular_papel("12.ABC.345/01de-35", doc, xml) == "PRESTADOR"
        assert calcular_papel("12ABC34501DE99", doc, xml) is None
        assert calcular_papel("", doc, xml) is None


# --------------------------------------------------------------------------- sincronização


class TestSincronizacao:
    def test_paginacao_por_nsu_cursor_persistido_a_cada_pagina(self, repo, monkeypatch):
        paginas = [
            _resposta(
                [_item_nfse(1, 1, NOME_ALVO, NOME_OUTRA), _item_nfse(2, 2, NOME_ALVO, NOME_OUTRA)]
            ),
            _resposta([_item_nfse(3, 5, NOME_OUTRA, NOME_ALVO)]),
            RESPOSTA_FIM,
        ]
        pedidos: list[int] = []
        salvos: list[int] = []
        original = repo.salvar_cursor_adn
        monkeypatch.setattr(
            repo, "salvar_cursor_adn", lambda c: (salvos.append(c.ultimo_nsu), original(c))[1]
        )

        def consultar(nsu: int) -> RespostaADN:
            pedidos.append(nsu)
            return paginas[len(pedidos) - 1]

        r = _sincronizar(consultar, repo)
        assert pedidos == [0, 2, 5]  # NSU exclusivo: pede a partir do último recebido
        assert salvos == [2, 5, 5]  # persistido a cada página (a última grava o fim da fila)
        assert r.paginas_consultadas == 3
        assert r.novas_notas == 3
        assert r.encerrado_por == "FILA_ZERADA"
        assert repo.obter_cursor_adn(2).ultimo_nsu == 5

    def test_cursor_sobrevive_a_reinicio_e_e_independente_do_distdfe(self, repo, tmp_path):
        _sincronizar(
            lambda nsu: (
                _resposta([_item_nfse(1, 9, NOME_ALVO, NOME_OUTRA)]) if nsu == 0 else RESPOSTA_FIM
            ),
            repo,
        )
        reaberto = RepositorioDistDFeSQLite(repo.caminho_db)
        pedidos: list[int] = []

        def consultar(nsu):
            pedidos.append(nsu)
            return RESPOSTA_FIM

        _sincronizar(consultar, reaberto)
        assert pedidos == [9]
        assert reaberto.obter_cursor(2).ultimo_nsu == "000000000000000"  # DistDFe intocado

    def test_limite_de_paginas_e_espera_entre_chamadas(self, repo):
        n = {"i": 0}

        def consultar(nsu):
            n["i"] += 1
            return _resposta([_item_nfse(n["i"], nsu + 1, NOME_ALVO, NOME_OUTRA)])

        esperas: list[float] = []
        r = _sincronizar(
            consultar, repo, max_paginas=3, espera_segundos=2.5, dormir_fn=esperas.append
        )
        assert r.paginas_consultadas == 3
        assert r.encerrado_por == "LIMITE_PAGINAS"
        assert esperas == [2.5, 2.5]  # entre chamadas, não antes da primeira

    def test_fila_vazia_na_primeira_pagina(self, repo):
        r = _sincronizar(lambda nsu: RESPOSTA_FIM, repo)
        assert (r.paginas_consultadas, r.documentos_recebidos, r.encerrado_por) == (
            1,
            0,
            "FILA_ZERADA",
        )

    def test_sync_log_registra_origem_nfse_e_nfe_continua_como_antes(self, repo):
        repo.registrar_log_sync(
            ambiente=2, cstat="138", motivo="ok", paginas=1, novos_docs=1, ultimo_nsu="1"
        )
        _sincronizar(
            lambda nsu: (
                RESPOSTA_FIM if nsu else _resposta([_item_nfse(1, 3, NOME_ALVO, NOME_OUTRA)])
            ),
            repo,
        )
        logs = {log.origem: log for log in repo.listar_logs_sync()}
        assert set(logs) == {"NFE", "NFSE"}
        assert logs["NFSE"].ultimo_nsu == "3"
        assert logs["NFSE"].novos_docs == 1

    def test_rejeicao_registra_motivo_sem_travar(self, repo):
        resp = RespostaADN(http_status=400, status_processamento="REJEICAO")
        r = _sincronizar(lambda nsu: resp, repo)
        assert r.encerrado_por == "REJEICAO"
        assert r.mensagem == MENSAGEM_REJEICAO_SEM_DETALHES
        assert repo.obter_cursor_adn(2).bloqueado_ate is None
        assert repo.listar_logs_sync()[0].cstat == "REJEICAO"

    def test_indisponibilidade_bloqueia_e_a_proxima_execucao_nao_toca_a_rede(self, repo):
        def cai(nsu):
            raise ErroADN(
                "fora", categoria="INDISPONIVEL", http_status=503, retry_apos_segundos=600
            )

        r = _sincronizar(cai, repo)
        assert r.encerrado_por == "BLOQUEIO"
        assert repo.obter_cursor_adn(2).bloqueado_ate == RELOGIO + timedelta(seconds=600)
        assert repo.listar_logs_sync()[0].origem == "NFSE"

        chamadas: list[int] = []
        with pytest.raises(ErroBloqueioADN) as exc:
            _sincronizar(lambda nsu: chamadas.append(nsu) or RESPOSTA_FIM, repo)
        assert chamadas == []
        assert exc.value.segundos_restantes > 0

        # depois do intervalo volta a consultar
        depois = RELOGIO + timedelta(seconds=601)
        _sincronizar(
            lambda nsu: chamadas.append(nsu) or RESPOSTA_FIM, repo, relogio_fn=lambda: depois
        )
        assert chamadas == [0]

    def test_ignora_tipos_nao_guardados_e_conta(self, repo):
        itens = [
            ItemADN(nsu=1, chave_acesso="a", tipo_documento="DPS", arquivo_xml="x"),
            ItemADN(nsu=2, chave_acesso="b", tipo_documento="CNC", arquivo_xml="x"),
            ItemADN(nsu=3, chave_acesso="c", tipo_documento="CNC", arquivo_xml="x"),
            ItemADN(nsu=4, chave_acesso="d", tipo_documento="PEDIDO_REGISTRO_EVENTO"),
            _item_nfse(1, 5, NOME_ALVO, NOME_OUTRA),
        ]
        r = _sincronizar(lambda nsu: _resposta(itens) if nsu == 0 else RESPOSTA_FIM, repo)
        assert r.ignorados_por_tipo == {"DPS": 1, "CNC": 2, "PEDIDO_REGISTRO_EVENTO": 1}
        assert r.novas_notas == 1
        assert r.com_erro_processamento == 0
        assert r.cursor.ultimo_nsu == 5

    def test_gzip_bomb_e_base64_invalido_nao_derrubam_e_o_cursor_avanca(self, repo):
        bomba = compactar_xml_para_doc_zip_b64(b"<a>" + b"0" * 200_000 + b"</a>")
        itens = [
            ItemADN(nsu=1, chave_acesso="x", tipo_documento="NFSE", arquivo_xml=bomba),
            ItemADN(nsu=2, chave_acesso="y", tipo_documento="NFSE", arquivo_xml="@@nao-e-base64"),
            ItemADN(nsu=3, chave_acesso="z", tipo_documento="NFSE", arquivo_xml=None),
            _item_nfse(1, 4, NOME_ALVO, NOME_OUTRA),
        ]
        r = _sincronizar(
            lambda nsu: _resposta(itens) if nsu == 0 else RESPOSTA_FIM, repo, max_bytes_xml=50_000
        )
        assert r.com_erro_processamento == 3
        assert r.novas_notas == 1
        assert r.cursor.ultimo_nsu == 4

    def test_pagina_sem_progresso_encerra_sem_laco(self, repo):
        itens = [_item_nfse(1, 0, NOME_ALVO, NOME_OUTRA)]
        r = _sincronizar(lambda nsu: _resposta(itens), repo, max_paginas=50)
        assert r.paginas_consultadas == 1
        assert r.encerrado_por == "ERRO"


# --------------------------------------------------------------------------- eventos e dedupe


def _evento(chave: str, nsu: int, tipo_evento: str) -> ItemADN:
    return ItemADN(
        nsu=nsu,
        chave_acesso=chave,
        tipo_documento="EVENTO",
        tipo_evento=tipo_evento,
        arquivo_xml=compactar_xml_para_doc_zip_b64(b"<evento/>"),
        data_hora_geracao=RELOGIO,
    )


class TestEventosEDedupe:
    def test_cancelamento_atualiza_a_situacao_da_nota(self, repo):
        item = _item_nfse(1, 1, NOME_ALVO, NOME_OUTRA)
        _sincronizar(
            lambda n: (
                _resposta([item, _evento(item.chave_acesso, 2, "CANCELAMENTO")])
                if n == 0
                else RESPOSTA_FIM
            ),
            repo,
        )
        nota = repo.obter_nota_recebida(item.chave_acesso)
        assert nota is not None and nota.situacao == "CANCELADA"

    def test_evento_antes_da_nota_cancela_quando_ela_chega(self, repo):
        item = _item_nfse(1, 2, NOME_ALVO, NOME_OUTRA)
        ev = _evento(item.chave_acesso, 1, "CANCELAMENTO_POR_SUBSTITUICAO")
        r = _sincronizar(lambda n: _resposta([ev, item]) if n == 0 else RESPOSTA_FIM, repo)
        assert r.eventos_registrados == 1
        assert repo.obter_nota_recebida(item.chave_acesso).situacao == "CANCELADA"

    def test_solicitacao_de_cancelamento_nao_cancela(self, repo):
        item = _item_nfse(1, 1, NOME_ALVO, NOME_OUTRA)
        ev = _evento(item.chave_acesso, 2, "SOLICITACAO_CANCELAMENTO_ANALISE_FISCAL")
        _sincronizar(lambda n: _resposta([item, ev]) if n == 0 else RESPOSTA_FIM, repo)
        assert repo.obter_nota_recebida(item.chave_acesso).situacao == "AUTORIZADA"

    def test_evento_tardio_nao_rebaixa_nota_completa(self, repo):
        item = _item_nfse(1, 1, NOME_ALVO, NOME_OUTRA, valor="321.00")
        _sincronizar(lambda n: _resposta([item]) if n == 0 else RESPOSTA_FIM, repo)
        antes = repo.obter_nota_recebida(item.chave_acesso)
        ev = _evento(item.chave_acesso, 7, "CONFIRMACAO_TOMADOR")
        _sincronizar(lambda n: _resposta([ev]) if n == 1 else RESPOSTA_FIM, repo)
        depois = repo.obter_nota_recebida(item.chave_acesso)
        assert depois.situacao == "AUTORIZADA"
        assert (depois.valor_total, depois.emitente_nome, depois.papel, depois.numero) == (
            antes.valor_total,
            antes.emitente_nome,
            antes.papel,
            antes.numero,
        )
        assert repo.obter_xml_nota_recebida(item.chave_acesso).startswith(b"<?xml")

    def test_duplicata_por_chave_nao_cria_linha_nova_nem_descancela(self, repo):
        item = _item_nfse(1, 1, NOME_ALVO, NOME_OUTRA)
        ev = _evento(item.chave_acesso, 2, "CANCELAMENTO")
        _sincronizar(lambda n: _resposta([item, ev]) if n == 0 else RESPOSTA_FIM, repo)
        repetido = item.model_copy(update={"nsu": 3})
        r = _sincronizar(lambda n: _resposta([repetido]) if n == 2 else RESPOSTA_FIM, repo)
        assert r.notas_atualizadas == 1 and r.novas_notas == 0
        notas = repo.listar_notas_recebidas()
        assert len(notas) == 1 and notas[0].situacao == "CANCELADA"

    def test_nota_sem_protocolo_nao_rebaixa_nota_autorizada(self, repo):
        item = _item_nfse(1, 1, NOME_ALVO, NOME_OUTRA)
        _sincronizar(lambda n: _resposta([item]) if n == 0 else RESPOSTA_FIM, repo)
        nota = repo.obter_nota_recebida(item.chave_acesso)
        _, xml = _xml(1, NOME_ALVO, NOME_OUTRA, "100.00")
        doc = parse_nfse_nacional(xml).model_copy(update={"situacao": "SEM_PROTOCOLO"})
        status = repo.salvar_nfse_adn(
            chave=item.chave_acesso, nsu=9, doc=doc, xml_bytes=xml, papel=None
        )
        assert status == "IGNORADA"
        assert repo.obter_nota_recebida(item.chave_acesso).situacao == nota.situacao

    def test_demo_completo_papeis_e_cancelada(self, repo):
        itens = montar_itens_demo()
        r = _sincronizar(
            lambda n: _resposta([i for i in itens if i.nsu > n]) if n < 8 else RESPOSTA_FIM, repo
        )
        assert r.papeis == {"PRESTADOR": 3, "TOMADOR": 2, "INTERMEDIARIO": 1}
        assert r.ignorados_por_tipo == {"DPS": 1}
        assert len(repo.listar_notas_recebidas(papel="PRESTADOR")) == 3
        assert len(repo.listar_notas_recebidas(papel="tomador")) == 2
        assert len(repo.listar_notas_recebidas(situacao="CANCELADA", origem="ADN")) == 1
        assert len(repo.listar_notas_recebidas(origem="DISTDFE")) == 0
        assert len(repo.listar_notas_recebidas(tipo="NFSE")) == 6
        assert len(repo.listar_notas_recebidas(tipo="NFE")) == 0


# --------------------------------------------------------------------------- migração do banco

_DDL_ANTIGO = """
CREATE TABLE dfe_cursor (ambiente INTEGER PRIMARY KEY, ultimo_nsu TEXT NOT NULL,
    max_nsu TEXT NOT NULL, ultima_consulta TEXT, bloqueado_ate TEXT, ultimo_cstat TEXT,
    ultimo_motivo TEXT);
CREATE TABLE notas_recebidas (chave TEXT PRIMARY KEY, nsu TEXT NOT NULL, ambiente INTEGER NOT NULL,
    schema_xml TEXT NOT NULL, categoria_schema TEXT NOT NULL, tipo_documento TEXT NOT NULL,
    modelo TEXT NOT NULL, numero TEXT, serie TEXT, emitente_documento TEXT, emitente_nome TEXT,
    emitente_uf TEXT, destinatario_documento TEXT, destinatario_nome TEXT, data_emissao TEXT,
    data_emissao_iso_date TEXT, valor_total TEXT NOT NULL, valor_total_num REAL NOT NULL,
    situacao TEXT NOT NULL, manifestacao_necessaria INTEGER NOT NULL, valido INTEGER NOT NULL,
    xml_gzip BLOB, atualizado_em TEXT NOT NULL);
CREATE TABLE sync_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
    ambiente INTEGER NOT NULL, cstat TEXT, motivo TEXT, paginas INTEGER NOT NULL,
    novos_docs INTEGER NOT NULL, ultimo_nsu TEXT);
"""


def _criar_banco_antigo(caminho: Path, quantidade: int = 120) -> None:
    con = sqlite3.connect(caminho)
    con.executescript(_DDL_ANTIGO)
    for i in range(quantidade):
        con.execute(
            "INSERT INTO notas_recebidas VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"{i:044d}",
                f"{i:015d}",
                2,
                "procNFe_v4.00.xsd",
                "NFE_COMPLETA",
                "NFE",
                "55",
                str(i),
                "1",
                "12345678000195",
                f"EMITENTE EXEMPLO {i}",
                "SP",
                "98765432000100",
                "DEST EXEMPLO",
                "2026-01-01T00:00:00+00:00",
                "2026-01-01",
                f"{i}.50",
                i + 0.5,
                "AUTORIZADA",
                0,
                1,
                gzip.compress(f"<x>{i}</x>".encode()),
                "2026-01-02T00:00:00+00:00",
            ),
        )
    con.execute(
        "INSERT INTO sync_log (ts,ambiente,cstat,motivo,paginas,novos_docs,ultimo_nsu) "
        "VALUES ('2026-01-02T00:00:00+00:00',2,'138','ok',1,3,'000000000000003')"
    )
    con.execute(
        "INSERT INTO dfe_cursor VALUES (2,'000000000000120','000000000000120',NULL,NULL,'138','ok')"
    )
    con.commit()
    con.close()


def _foto(caminho: Path, tabela: str, colunas: str) -> list[tuple]:
    con = sqlite3.connect(caminho)
    try:
        return con.execute(f"SELECT {colunas} FROM {tabela} ORDER BY 1").fetchall()  # noqa: S608
    finally:
        con.close()


class TestMigracaoDoBanco:
    COLS_NOTAS = (
        "chave,nsu,ambiente,schema_xml,categoria_schema,tipo_documento,modelo,numero,serie,"
        "emitente_documento,emitente_nome,emitente_uf,destinatario_documento,destinatario_nome,"
        "data_emissao,data_emissao_iso_date,valor_total,valor_total_num,situacao,"
        "manifestacao_necessaria,valido,xml_gzip,atualizado_em"
    )

    def test_banco_antigo_ganha_colunas_sem_perder_nem_alterar_linhas(self, tmp_path):
        caminho = tmp_path / "antigo.db"
        _criar_banco_antigo(caminho, 120)
        antes = _foto(caminho, "notas_recebidas", self.COLS_NOTAS)
        logs_antes = _foto(
            caminho, "sync_log", "id,ts,ambiente,cstat,motivo,paginas,novos_docs,ultimo_nsu"
        )
        cursor_antes = _foto(caminho, "dfe_cursor", "*")

        repo = RepositorioDistDFeSQLite(caminho)

        assert _foto(caminho, "notas_recebidas", self.COLS_NOTAS) == antes
        assert len(antes) == 120
        assert (
            _foto(caminho, "sync_log", "id,ts,ambiente,cstat,motivo,paginas,novos_docs,ultimo_nsu")
            == logs_antes
        )
        assert _foto(caminho, "dfe_cursor", "*") == cursor_antes
        assert {r[0] for r in _foto(caminho, "notas_recebidas", "origem")} == {"DISTDFE"}
        assert {r[0] for r in _foto(caminho, "notas_recebidas", "papel")} == {None}
        assert {r[0] for r in _foto(caminho, "sync_log", "origem")} == {"NFE"}
        nota = repo.obter_nota_recebida(f"{5:044d}")
        assert nota is not None and nota.origem == "DISTDFE" and nota.papel is None
        assert len(repo.listar_notas_recebidas(origem="DISTDFE")) == 120

        con = sqlite3.connect(caminho)
        indices = {r[1] for r in con.execute("PRAGMA index_list(notas_recebidas)")}
        con.close()
        assert {"idx_notas_recebidas_origem", "idx_notas_recebidas_papel"} <= indices

    def test_migracao_e_idempotente(self, tmp_path):
        caminho = tmp_path / "antigo.db"
        _criar_banco_antigo(caminho, 30)
        RepositorioDistDFeSQLite(caminho)
        depois_1 = _foto(caminho, "notas_recebidas", self.COLS_NOTAS + ",origem,papel")
        RepositorioDistDFeSQLite(caminho)
        RepositorioDistDFeSQLite(caminho)
        assert _foto(caminho, "notas_recebidas", self.COLS_NOTAS + ",origem,papel") == depois_1

    def test_nfse_do_adn_convive_com_linhas_antigas(self, tmp_path):
        caminho = tmp_path / "antigo.db"
        _criar_banco_antigo(caminho, 10)
        repo = RepositorioDistDFeSQLite(caminho)
        item = _item_nfse(1, 1, NOME_ALVO, NOME_OUTRA)
        _sincronizar(lambda n: _resposta([item]) if n == 0 else RESPOSTA_FIM, repo)
        assert len(repo.listar_notas_recebidas()) == 11
        assert len(repo.listar_notas_recebidas(origem="ADN", papel="PRESTADOR")) == 1
        assert len(repo.listar_notas_recebidas(origem="DISTDFE")) == 10


# --------------------------------------------------------------------------- CLI e API


class TestCliEApi:
    def test_cli_demo_json(self, tmp_path, capsys):
        banco = tmp_path / "cli.db"
        assert cli_main(["sincronizar-adn", "--demo", "--json", "--db", str(banco)]) == 0
        saida = json.loads(capsys.readouterr().out)
        assert saida["novas_notas"] == 6 and saida["eventos_registrados"] == 1
        assert saida["papeis"] == {"PRESTADOR": 3, "TOMADOR": 2, "INTERMEDIARIO": 1}
        assert saida["encerrado_por"] == "FILA_ZERADA"
        # segunda execução: nada novo, cursor mantido
        assert cli_main(["sincronizar-adn", "--demo", "--json", "--db", str(banco)]) == 0
        assert json.loads(capsys.readouterr().out)["documentos_recebidos"] == 0

    def test_cli_demo_texto(self, tmp_path, capsys):
        assert cli_main(["sincronizar-adn", "--demo", "--db", str(tmp_path / "t.db")]) == 0
        assert "Papel PRESTADOR" in capsys.readouterr().out

    def test_cli_sem_certificado_falha_com_mensagem(self, tmp_path, capsys):
        assert cli_main(["sincronizar-adn", "--db", str(tmp_path / "t.db")]) == 1
        assert "Erro:" in capsys.readouterr().err

    @pytest.fixture
    def client(self, tmp_path):
        definir_repositorio_recebidas(RepositorioDistDFeSQLite(tmp_path / "api.db"))
        definir_diretorio_certificado(tmp_path / "sem_cert")
        try:
            yield TestClient(app)
        finally:
            definir_repositorio_recebidas(None)
            definir_diretorio_certificado(None)
            definir_consultor_adn(None)

    def test_api_demo_e_filtros_origem_papel_tipo(self, client):
        r = client.post("/api/recebidas/sincronizar-adn", params={"demo": "true"})
        assert r.status_code == 200 and r.json()["novas_notas"] == 6
        todas = client.get("/api/recebidas").json()
        assert todas["total"] == 6
        assert {n["origem"] for n in todas["notas"]} == {"ADN"}
        assert client.get("/api/recebidas", params={"papel": "TOMADOR"}).json()["total"] == 2
        assert (
            client.get("/api/recebidas", params={"origem": "ADN", "papel": "PRESTADOR"}).json()[
                "total"
            ]
            == 3
        )
        assert client.get("/api/recebidas", params={"origem": "DISTDFE"}).json()["total"] == 0
        assert client.get("/api/recebidas", params={"tipo": "NFSE"}).json()["total"] == 6
        assert client.get("/api/recebidas", params={"tipo": "NFE"}).json()["total"] == 0
        assert client.get("/api/recebidas", params={"situacao": "CANCELADA"}).json()["total"] == 1
        papeis = {n["papel"] for n in todas["notas"]}
        assert papeis == {"PRESTADOR", "TOMADOR", "INTERMEDIARIO"}

    def test_api_bloqueio_vira_429_sem_nova_chamada(self, client):
        chamadas: list[int] = []

        def cai(nsu):
            chamadas.append(nsu)
            raise ErroADN("fora", categoria="LIMITE", http_status=429, retry_apos_segundos=3600)

        definir_consultor_adn(cai)
        primeira = client.post("/api/recebidas/sincronizar-adn")
        assert primeira.status_code == 200
        assert primeira.json()["encerrado_por"] == "BLOQUEIO"
        segunda = client.post("/api/recebidas/sincronizar-adn")
        assert segunda.status_code == 429
        assert chamadas == [0]

    def test_api_sem_certificado_retorna_503(self, client):
        r = client.post("/api/recebidas/sincronizar-adn")
        assert r.status_code == 503
