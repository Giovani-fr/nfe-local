"""Diagnóstico de falhas de conexão do ADN: etapa, causa, orientação e nada sensível na saída.

As exceções são montadas com as mesmas classes de `requests`/`urllib3`/`ssl`/`http.client`
(cadeias `__cause__`, `.reason` e `args`), com dados sensíveis propositalmente dentro das
mensagens para provar que nunca chegam à saída.
"""

from __future__ import annotations

import errno
import http.client
import json
import socket
import ssl
import sys

import pytest
import requests
import urllib3.exceptions as u3

import api.cli as cli
from sefaz.adn import ClienteADN, ErroADN, analisar_falha_conexao
from sefaz.adn_demo import CNPJ_DEMO
from sefaz.adn_diagnostico import diagnosticar_adn
from tests.test_adn import CORPO_FIM, _Resp, _Sessao
from tests.test_adn_diagnostico import (
    SENHA,
    _cli_com_sessao,  # noqa: F401 - usado por referência
    _cliente,
    certificado_cadastrado,  # noqa: F401 - fixture
    pfx,  # noqa: F401 - fixture
)

SENSIVEIS = (
    CNPJ_DEMO,
    SENHA,
    "cnpjConsulta=",
    "C:\\segredo\\meu_certificado.pfx",
    "segredo",
    ".pfx",
    "SENHA-NA-MENSAGEM",
)
LIXO = (
    f"HTTPSConnectionPool(host='adn.exemplo.teste', port=443): Max retries exceeded with url: "
    f"/contribuintes/DFe/0?cnpjConsulta={CNPJ_DEMO} cert=C:\\segredo\\meu_certificado.pfx "
    "SENHA-NA-MENSAGEM"
)


def _max_retry(reason: BaseException) -> u3.MaxRetryError:
    return u3.MaxRetryError(None, f"/DFe/0?cnpjConsulta={CNPJ_DEMO}", reason)


def _ctx(exc_externa: BaseException, causa: BaseException) -> BaseException:
    exc_externa.__cause__ = causa
    return exc_externa


def _erro_dns() -> BaseException:
    gai = socket.gaierror(-2, "Name or service not known " + LIXO)
    return requests.ConnectionError(
        _max_retry(u3.NameResolutionError("adn.exemplo.teste", None, gai))
    )


def _erro_tcp_recusado() -> BaseException:
    base = ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused " + LIXO)
    return requests.ConnectionError(
        _max_retry(
            u3.NewConnectionError(None, LIXO)
            if False
            else _ctx(u3.NewConnectionError(None, "Failed to establish " + LIXO), base)
        )
    )


def _erro_connect_timeout() -> BaseException:
    return requests.ConnectTimeout(
        _max_retry(u3.ConnectTimeoutError(None, "Connection timed out " + LIXO))
    )


def _erro_read_timeout() -> BaseException:
    return requests.ReadTimeout(u3.ReadTimeoutError(None, None, "Read timed out. " + LIXO))


def _erro_tls_alerta() -> BaseException:
    ssl_exc = ssl.SSLError(1, "[SSL: TLSV1_ALERT_UNKNOWN_CA] tlsv1 alert unknown ca " + LIXO)
    try:
        ssl_exc.reason = "TLSV1_ALERT_UNKNOWN_CA"
    except (AttributeError, TypeError):  # pragma: no cover - depende da versão do Python
        pass
    return requests.exceptions.SSLError(_max_retry(u3.SSLError(ssl_exc)))


def _erro_tls_eof() -> BaseException:
    ssl_exc = ssl.SSLEOFError(8, "EOF occurred in violation of protocol " + LIXO)
    return requests.exceptions.SSLError(_max_retry(u3.SSLError(ssl_exc)))


def _erro_tls_timeout() -> BaseException:
    ssl_exc = ssl.SSLError("_ssl.c:1000: The handshake operation timed out " + LIXO)
    return requests.exceptions.SSLError(_max_retry(u3.SSLError(ssl_exc)))


def _reset(codigo_win: int = 10054) -> ConnectionResetError:
    exc = ConnectionResetError(errno.ECONNRESET, "reset by peer " + LIXO)
    if sys.platform == "win32":
        exc = ConnectionResetError(codigo_win, "reset " + LIXO, None, codigo_win)
    return exc


def _erro_reset_handshake() -> BaseException:
    return requests.ConnectionError(u3.ProtocolError("Connection aborted. " + LIXO, _reset()))


def _erro_remote_disconnected() -> BaseException:
    remoto = http.client.RemoteDisconnected("Remote end closed connection without response " + LIXO)
    return requests.ConnectionError(u3.ProtocolError("Connection aborted.", remoto))


def _erro_desconhecido() -> BaseException:
    return requests.ConnectionError("algo estranho " + LIXO)


CASOS = {
    "dns": (_erro_dns, "resolução de nome", "NameResolutionError", False, False),
    "tcp_recusado": (_erro_tcp_recusado, "conexão TCP", "ConnectionRefusedError", False, False),
    "connect_timeout": (_erro_connect_timeout, "conexão TCP", "ConnectTimeoutError", False, True),
    "read_timeout": (_erro_read_timeout, "leitura da resposta", "ReadTimeoutError", False, True),
    "tls_alerta": (_erro_tls_alerta, "handshake TLS", "SSLError", False, False),
    "tls_eof": (_erro_tls_eof, "handshake TLS", "SSLEOFError", True, False),
    "tls_timeout": (_erro_tls_timeout, "handshake TLS", "SSLError", False, True),
    "reset_handshake": (
        _erro_reset_handshake,
        "handshake TLS (provável)",
        "ConnectionResetError",
        True,
        False,
    ),
    "remote_disconnected": (
        _erro_remote_disconnected,
        "leitura da resposta",
        "RemoteDisconnected",
        True,
        False,
    ),
    "desconhecido": (_erro_desconhecido, "não identificada", "ConnectionError", False, False),
}


@pytest.mark.parametrize("nome", list(CASOS))
class TestClassificacao:
    def test_etapa_raiz_encerramento_e_timeout(self, nome):
        fabrica, etapa, raiz, encerrou, timeout = CASOS[nome]
        falha = analisar_falha_conexao(fabrica())
        assert falha.etapa == etapa
        assert falha.excecao_raiz == raiz
        assert falha.servidor_encerrou_conexao is encerrou
        assert falha.tempo_esgotado is timeout
        assert falha.cadeia_excecoes[0] in {
            "ConnectionError",
            "SSLError",
            "ConnectTimeout",
            "ReadTimeout",
        }
        assert falha.orientacao

    def test_nada_sensivel_no_resultado(self, nome):
        falha = analisar_falha_conexao(CASOS[nome][0]())
        texto = falha.model_dump_json()
        for proibido in SENSIVEIS:
            assert proibido not in texto
        assert (
            "http" not in texto.lower().replace("httpsconnection", "")
            or "HTTPSConnectionPool" not in texto
        )


class TestDetalhes:
    def test_codigo_do_so_aparece(self):
        falha = analisar_falha_conexao(_erro_reset_handshake())
        if sys.platform == "win32":
            assert falha.codigo_so == "WinError 10054"
        else:
            assert falha.codigo_so == "errno 104 (ECONNRESET)"
        assert (
            "errno 111 (ECONNREFUSED)" == analisar_falha_conexao(_erro_tcp_recusado()).codigo_so
            or sys.platform == "win32"
        )

    def test_motivo_tls_curado(self):
        falha = analisar_falha_conexao(_erro_tls_alerta())
        assert falha.motivo_tls in (None, "TLSV1_ALERT_UNKNOWN_CA")
        assert "certificado" in falha.orientacao.lower() or "TLS" in falha.orientacao

    def test_orientacoes_em_portugues_por_causa(self):
        assert "DNS" in analisar_falha_conexao(_erro_dns()).orientacao
        assert "--timeout" in analisar_falha_conexao(_erro_connect_timeout()).orientacao
        assert "--timeout" in analisar_falha_conexao(_erro_read_timeout()).orientacao
        reset = analisar_falha_conexao(_erro_reset_handshake()).orientacao
        assert "outra rede" in reset and "mesmo certificado ao mesmo tempo" in reset
        remoto = analisar_falha_conexao(_erro_remote_disconnected()).orientacao
        assert "sem responder" in remoto and "outra rede" in remoto
        assert "firewall" in analisar_falha_conexao(_erro_tcp_recusado()).orientacao

    def test_ciclo_de_causas_nao_trava(self):
        a, b = RuntimeError("a"), RuntimeError("b")
        a.__cause__, b.__cause__ = b, a
        assert analisar_falha_conexao(a).excecao_raiz == "RuntimeError"


# --------------------------------------------------------------------------- diagnóstico


class TestDiagnosticoComFalhas:
    @pytest.mark.parametrize("nome", list(CASOS))
    def test_diagnostico_mostra_causa_sem_vazar(self, pfx, nome):  # noqa: F811
        fabrica, etapa, *_ = CASOS[nome]
        diag = diagnosticar_adn(_cliente(pfx, _Sessao(fabrica())))
        assert diag.categoria_erro == "CONEXAO" and not diag.sucesso
        assert diag.falha_conexao is not None and diag.falha_conexao.etapa == etapa
        assert diag.tentativas_realizadas == 1 and diag.tentativas_com_falha == 1
        texto = diag.model_dump_json()
        for proibido in SENSIVEIS:
            assert proibido not in texto

    def test_tentativas_com_espera_e_contagem_de_falhas(self, pfx):  # noqa: F811
        esperas: list[float] = []
        sessao = _Sessao(_erro_reset_handshake(), _erro_dns(), _Resp(404, CORPO_FIM))
        diag = diagnosticar_adn(
            _cliente(pfx, sessao), tentativas=3, espera_segundos=1.5, dormir_fn=esperas.append
        )
        assert diag.sucesso and diag.http_status == 404
        assert (diag.tentativas_realizadas, diag.tentativas_com_falha) == (3, 2)
        assert esperas == [1.5, 1.5]
        assert [f.etapa for f in diag.falhas_conexao] == [
            "handshake TLS (provável)",
            "resolução de nome",
        ]

    def test_todas_as_tentativas_falham(self, pfx):  # noqa: F811
        sessao = _Sessao(_erro_remote_disconnected(), _erro_remote_disconnected())
        diag = diagnosticar_adn(
            _cliente(pfx, sessao), tentativas=2, espera_segundos=0, dormir_fn=lambda s: None
        )
        assert not diag.sucesso
        assert (diag.tentativas_realizadas, diag.tentativas_com_falha) == (2, 2)
        assert diag.falha_conexao.etapa == "leitura da resposta"

    def test_limite_de_3_tentativas(self, pfx):  # noqa: F811
        sessao = _Sessao(*[_erro_dns() for _ in range(9)])
        diag = diagnosticar_adn(
            _cliente(pfx, sessao), tentativas=9, espera_segundos=0, dormir_fn=lambda s: None
        )
        assert diag.tentativas_solicitadas == 3 and diag.tentativas_realizadas == 3
        assert len(sessao.chamadas) == 3

    def test_padrao_e_uma_tentativa_sem_espera(self, pfx):  # noqa: F811
        sessao = _Sessao(_erro_dns())
        diag = diagnosticar_adn(_cliente(pfx, sessao))
        assert diag.tentativas_realizadas == 1 and len(sessao.chamadas) == 1

    def test_resposta_http_nao_reprocura_tentativas(self, pfx):  # noqa: F811
        sessao = _Sessao(_Resp(400, {"StatusProcessamento": "REJEICAO", "Erros": []}))
        diag = diagnosticar_adn(_cliente(pfx, sessao), tentativas=3)
        assert diag.tentativas_realizadas == 1 and len(sessao.chamadas) == 1

    def test_certificado_vencido_recusado_pelo_adaptador_nao_tenta_de_novo(self, pfx):  # noqa: F811
        sessao = _Sessao(ValueError("Client certificate expired: C:\\segredo\\meu_certificado.pfx"))
        diag = diagnosticar_adn(_cliente(pfx, sessao), tentativas=3, espera_segundos=0)
        assert diag.categoria_erro == "ACESSO" and diag.tentativas_realizadas == 1
        assert "segredo" not in diag.model_dump_json()

    def test_tls_negociado_quando_o_handshake_completa(self, pfx):  # noqa: F811
        class _Sock:
            def version(self):
                return "TLSv1.3"

            def cipher(self):
                return ("TLS_AES_256_GCM_SHA384", "TLSv1.3", 256)

        class _Raw:
            class _Con:
                sock = _Sock()

            _connection = _Con()

        resp = _Resp(404, CORPO_FIM)
        resp.raw = _Raw()
        sessao = _Sessao(resp)
        diag = diagnosticar_adn(_cliente(pfx, sessao))
        assert (diag.tls_versao, diag.tls_cifrador) == ("TLSv1.3", "TLS_AES_256_GCM_SHA384")
        assert sessao.chamadas[0]["kw"].get("stream") is True

    def test_sem_info_tls_nao_quebra(self, pfx):  # noqa: F811
        diag = diagnosticar_adn(_cliente(pfx, _Sessao(_Resp(404, CORPO_FIM))))
        assert diag.tls_versao is None and diag.tls_cifrador is None and diag.sucesso

    def test_sincronizacao_normal_nao_usa_stream(self, pfx):  # noqa: F811
        sessao = _Sessao(_Resp(404, CORPO_FIM))
        _cliente(pfx, sessao).consultar_nsu(0)
        assert "stream" not in sessao.chamadas[0]["kw"]

    def test_mensagem_do_erro_de_sincronizacao_traz_a_etapa(self, pfx):  # noqa: F811
        with pytest.raises(ErroADN) as exc:
            _cliente(pfx, _Sessao(_erro_remote_disconnected())).consultar_nsu(0)
        assert "leitura da resposta" in str(exc.value) and exc.value.falha is not None
        assert "ConnectionError)" not in str(exc.value)


class TestCliDiagnosticoFalhas:
    def _rodar(self, monkeypatch, *respostas, args=()):
        sessao = _Sessao(*respostas)
        monkeypatch.setattr("sefaz.adn_diagnostico.time.sleep", lambda s: None)
        _cli_com_sessao(monkeypatch, sessao)
        return sessao, cli.main(["sincronizar-adn", "--diagnostico", *args])

    def test_texto_mostra_etapa_causa_codigo_e_orientacao(
        self,
        certificado_cadastrado,  # noqa: F811
        monkeypatch,
        capsys,
    ):
        sessao, codigo = self._rodar(
            monkeypatch,
            _erro_reset_handshake(),
            _erro_reset_handshake(),
            args=("--tentativas", "2", "--timeout", "7", "--ambiente", "1"),
        )
        saida = capsys.readouterr()
        assert codigo == 1 and len(sessao.chamadas) == 2
        assert all(c["timeout"] == 7.0 for c in sessao.chamadas)
        assert "https://adn.nfse.gov.br/contribuintes" in saida.out
        for trecho in (
            "Tentativas                      : 2 de 2 (2 com falha) | timeout 7s",
            "Falha - etapa                   : handshake TLS (provável)",
            "Falha - exceção raiz            : ConnectionResetError",
            "Falha - código do SO",
            "servidor encerrou       : SIM",
            "O que fazer",
            "mesmo certificado ao mesmo tempo",
        ):
            assert trecho in saida.out
        for proibido in SENSIVEIS:
            assert proibido not in saida.out + saida.err

    def test_json_traz_a_falha_estruturada(self, certificado_cadastrado, monkeypatch, capsys):  # noqa: F811
        _, codigo = self._rodar(monkeypatch, _erro_dns(), args=("--json",))
        d = json.loads(capsys.readouterr().out)
        assert codigo == 1
        assert d["falha_conexao"]["etapa"] == "resolução de nome"
        assert d["falha_conexao"]["excecao_raiz"] == "NameResolutionError"
        assert d["tentativas_realizadas"] == 1 and d["tentativas_com_falha"] == 1
        assert d["timeout_segundos"] == 30.0
        assert d["cnpj_consulta_mascarado"] == "11**********81"
        assert d["tls_versao"] is None
        texto = json.dumps(d, ensure_ascii=False)
        for proibido in SENSIVEIS:
            assert proibido not in texto

    def test_recupera_na_segunda_tentativa(self, certificado_cadastrado, monkeypatch, capsys):  # noqa: F811
        _, codigo = self._rodar(
            monkeypatch,
            _erro_remote_disconnected(),
            _Resp(404, CORPO_FIM),
            args=("--tentativas", "3", "--json"),
        )
        d = json.loads(capsys.readouterr().out)
        assert codigo == 0 and d["sucesso"] is True
        assert (d["tentativas_realizadas"], d["tentativas_com_falha"]) == (2, 1)
        assert d["falhas_conexao"][0]["etapa"] == "leitura da resposta"

    def test_tentativas_acima_de_3_e_rejeitado_pelo_parser(self, capsys):
        with pytest.raises(SystemExit) as exc:
            cli.main(["sincronizar-adn", "--diagnostico", "--tentativas", "4"])
        assert exc.value.code == 2

    def test_nao_toca_no_banco_mesmo_com_falha(self, certificado_cadastrado, monkeypatch, tmp_path):  # noqa: F811
        alvo = tmp_path / "nao_criar" / "x.db"
        self._rodar(monkeypatch, _erro_tcp_recusado(), args=("--db", str(alvo)))
        assert not alvo.parent.exists()


def test_cliente_expoe_timeout(pfx):  # noqa: F811
    assert ClienteADN(pfx_bytes=pfx, senha=SENHA, timeout_segundos=12).timeout_segundos == 12
