# ruff: noqa: F811  (fixtures do pytest importadas de outros módulos de teste)
"""Repetição de consultas do ADN em falhas de CONEXÃO (GET idempotente), cursor e sync_log.

Sem rede: sessão HTTP simulada que levanta exceções reais de `requests`/`urllib3`/`http.client`.
"""

from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

import api.cli as cli
from api.app import (
    app,
    definir_consultor_adn,
    definir_diretorio_certificado,
    definir_repositorio_recebidas,
)
from sefaz.adn import (
    ErroADN,
    analisar_falha_conexao,
    mensagem_falha_conexao,
    resolver_tentativas,
)
from sefaz.adn_demo import CNPJ_DEMO
from sefaz.storage import RepositorioDistDFeSQLite
from tests.test_adn import (
    CORPO_FIM,
    NOME_ALVO,
    NOME_OUTRA,
    RESPOSTA_FIM,
    _criar_banco_antigo,
    _item_nfse,
    _Resp,
    _resposta,
    _sincronizar,
)
from tests.test_adn_diagnostico import (
    _cli_com_sessao,
    _cliente,
    certificado_cadastrado,  # noqa: F401 - fixture
    pfx,  # noqa: F401 - fixture
)
from tests.test_adn_falhas_conexao import (
    SENSIVEIS,
    _erro_dns,
    _erro_remote_disconnected,
    _erro_reset_handshake,
)
from tests.test_adn_falhas_conexao import _Sessao as _SessaoBase


def _corpo_pagina(itens) -> dict:
    return {
        "TipoConsulta": "CnpjBase",
        "StatusProcessamento": "DOCUMENTOS_LOCALIZADOS",
        "LoteDFe": [
            {
                "NSU": i.nsu,
                "ChaveAcesso": i.chave_acesso,
                "TipoDocumento": "NFSE",
                "ArquivoXml": i.arquivo_xml,
            }
            for i in itens
        ],
    }


@pytest.fixture
def repo(tmp_path) -> RepositorioDistDFeSQLite:
    return RepositorioDistDFeSQLite(tmp_path / "retry.db")


def _sync(cliente, repo, esperas: list[float], **kw):
    kw.setdefault("esperas_retentativa", (2.0, 5.0, 10.0))
    return _sincronizar(cliente.consultar_nsu, repo, dormir_fn=esperas.append, **kw)


def _sessao(*respostas) -> _SessaoBase:
    return _SessaoBase(*respostas)


# --------------------------------------------------------------------------- repetição


class TestRepeticaoDeConexao:
    def test_sucesso_na_segunda_tentativa(self, pfx, repo):  # noqa: F811
        item = _item_nfse(1, 1, NOME_ALVO, NOME_OUTRA)
        sessao = _sessao(
            _erro_remote_disconnected(), _Resp(200, _corpo_pagina([item])), _Resp(404, CORPO_FIM)
        )
        esperas: list[float] = []
        r = _sync(_cliente(pfx, sessao), repo, esperas)
        assert r.encerrado_por == "FILA_ZERADA" and r.novas_notas == 1
        assert r.tentativas_usadas == 3 and len(sessao.chamadas) == 3
        assert esperas == [2.0]  # espera só antes da 2ª tentativa (nenhuma entre páginas: 0)
        assert len(r.falhas_conexao) == 1
        assert "tentativa 1" in r.falhas_conexao[0]
        assert "leitura da resposta [RemoteDisconnected]" in r.falhas_conexao[0]
        log = repo.listar_logs_sync()[0]
        assert log.origem == "NFSE" and log.tentativas == 3
        assert "RemoteDisconnected" in log.falhas_conexao and "pág. 1" in log.falhas_conexao
        assert repo.obter_cursor_adn(2).ultimo_nsu == 1

    def test_espera_crescente_e_limite_de_tentativas(self, pfx, repo):  # noqa: F811
        sessao = _sessao(*[_erro_reset_handshake() for _ in range(5)])
        esperas: list[float] = []
        r = _sync(_cliente(pfx, sessao), repo, esperas, tentativas=5)
        assert len(sessao.chamadas) == 5 and r.tentativas_usadas == 5
        assert esperas == [2.0, 5.0, 10.0, 10.0]  # crescente; a última se repete
        assert r.encerrado_por == "ERRO"

    def test_falha_em_todas_encerra_com_erro_sem_perder_nada(self, pfx, repo):  # noqa: F811
        sessao = _sessao(_erro_reset_handshake(), _erro_dns(), _erro_remote_disconnected())
        esperas: list[float] = []
        r = _sync(_cliente(pfx, sessao), repo, esperas)  # padrão: 3 tentativas
        assert r.encerrado_por == "ERRO" and r.tentativas_usadas == 3
        assert esperas == [2.0, 5.0]
        assert r.novas_notas == 0
        cursor = repo.obter_cursor_adn(2)
        assert cursor.ultimo_nsu == 0 and cursor.bloqueado_ate is None
        assert cursor.ultimo_status == "CONEXAO"
        log = repo.listar_logs_sync()[0]
        assert (log.cstat, log.tentativas) == ("CONEXAO", 3)
        assert log.falhas_conexao.count("pág. 1") == 3
        for esperado in (
            "handshake TLS (provável) [ConnectionResetError]",
            "resolução de nome [NameResolutionError]",
            "leitura da resposta [RemoteDisconnected]",
        ):
            assert esperado in log.falhas_conexao
        for proibido in SENSIVEIS:
            assert proibido not in log.falhas_conexao + log.motivo

    def test_mensagem_final_diz_que_e_intermitente_antes_de_rede_e_certificado(self, pfx, repo):  # noqa: F811
        sessao = _sessao(*[_erro_reset_handshake() for _ in range(3)])
        r = _sync(_cliente(pfx, sessao), repo, [])
        msg = r.mensagem
        assert "Falhas intermitentes do ADN são comuns" in msg
        assert "O programa já tentou 3 vezes" in msg
        assert "tentar de novo em alguns minutos" in msg
        assert msg.index("Falhas intermitentes") < msg.index("Se continuar:")
        assert msg.index("Se continuar:") < msg.index("outra rede")
        assert msg.index("alguns minutos") < msg.index("mesmo certificado ao mesmo tempo")
        assert repo.obter_cursor_adn(2).ultimo_motivo == msg

    def test_mensagem_sem_numero_de_tentativas(self):
        falha = analisar_falha_conexao(_erro_reset_handshake())
        assert "já tentou" not in mensagem_falha_conexao(falha)
        assert "1 vez." in mensagem_falha_conexao(falha, 1)

    def test_falha_no_meio_da_paginacao_preserva_o_cursor_da_pagina_anterior(self, pfx, repo):  # noqa: F811
        p1 = [_item_nfse(1, 3, NOME_ALVO, NOME_OUTRA), _item_nfse(2, 5, NOME_OUTRA, NOME_ALVO)]
        sessao = _sessao(
            _Resp(200, _corpo_pagina(p1)),
            _erro_remote_disconnected(),
            _erro_remote_disconnected(),
            _erro_remote_disconnected(),
        )
        esperas: list[float] = []
        r = _sync(_cliente(pfx, sessao), repo, esperas)
        assert r.encerrado_por == "ERRO" and r.paginas_consultadas == 2
        assert r.novas_notas == 2  # o que já foi gravado fica
        assert repo.obter_cursor_adn(2).ultimo_nsu == 5
        assert len(repo.listar_notas_recebidas()) == 2
        assert "pág. 2" in repo.listar_logs_sync()[0].falhas_conexao

        # nova execução continua do cursor (pede a partir do NSU 5)
        p2 = [_item_nfse(3, 9, NOME_ALVO, NOME_OUTRA)]
        sessao2 = _sessao(_Resp(200, _corpo_pagina(p2)), _Resp(404, CORPO_FIM))
        r2 = _sync(_cliente(pfx, sessao2), repo, [])
        assert sessao2.chamadas[0]["url"].endswith("/DFe/5")
        assert r2.novas_notas == 1 and r2.encerrado_por == "FILA_ZERADA"
        assert repo.obter_cursor_adn(2).ultimo_nsu == 9
        assert len(repo.listar_notas_recebidas()) == 3

    @pytest.mark.parametrize(
        ("resposta", "encerrado"),
        [
            (_Resp(400, {"StatusProcessamento": "REJEICAO", "Erros": []}), "REJEICAO"),
            (_Resp(403, ""), "ERRO"),
            (_Resp(404, CORPO_FIM), "FILA_ZERADA"),
            (_Resp(404, "<html/>"), "ERRO"),
            (_Resp(429, "", {"Retry-After": "90"}), "BLOQUEIO"),
            (_Resp(503, ""), "BLOQUEIO"),
            (ValueError("Client certificate expired"), "ERRO"),
        ],
        ids=["400", "403", "404-fila", "404-endereco", "429", "503", "cert-vencido"],
    )
    def test_nao_repete_resposta_http_valida_limite_nem_certificado(
        self,
        pfx,
        repo,
        resposta,
        encerrado,  # noqa: F811
    ):
        sessao = _sessao(resposta, _Resp(200, _corpo_pagina([])))
        esperas: list[float] = []
        r = _sync(_cliente(pfx, sessao), repo, esperas)
        assert len(sessao.chamadas) == 1 and r.tentativas_usadas == 1
        assert esperas == [] and r.falhas_conexao == []
        assert r.encerrado_por == encerrado
        assert repo.listar_logs_sync()[0].tentativas == 1

    def test_uma_tentativa_nao_repete(self, pfx, repo):  # noqa: F811
        sessao = _sessao(_erro_dns(), _Resp(404, CORPO_FIM))
        r = _sync(_cliente(pfx, sessao), repo, [], tentativas=1)
        assert len(sessao.chamadas) == 1 and r.encerrado_por == "ERRO"

    def test_sem_falhas_nada_muda(self, repo):
        r = _sincronizar(
            lambda n: (
                _resposta([_item_nfse(1, 1, NOME_ALVO, NOME_OUTRA)]) if n == 0 else RESPOSTA_FIM
            ),
            repo,
        )
        assert r.tentativas_usadas == 2 and r.falhas_conexao == []
        assert repo.listar_logs_sync()[0].falhas_conexao == ""


class TestResolverTentativas:
    def test_padrao_env_e_argumento(self, monkeypatch):
        assert resolver_tentativas() == 3
        monkeypatch.setenv("NFE_ADN_TENTATIVAS", "5")
        assert resolver_tentativas() == 5
        assert resolver_tentativas(2) == 2  # o argumento vence a variável
        monkeypatch.setenv("NFE_ADN_TENTATIVAS", "  ")
        assert resolver_tentativas() == 3

    @pytest.mark.parametrize("ruim", ["0", "6", "abc", "-1", "2.5"])
    def test_env_invalida(self, monkeypatch, ruim):
        monkeypatch.setenv("NFE_ADN_TENTATIVAS", ruim)
        with pytest.raises(ValueError, match="1 a 5"):
            resolver_tentativas()

    @pytest.mark.parametrize("ruim", [0, 6])
    def test_argumento_invalido(self, ruim):
        with pytest.raises(ValueError, match="1 a 5"):
            resolver_tentativas(ruim)


# --------------------------------------------------------------------------- CLI e API


class TestCliEApi:
    def _cli(self, monkeypatch, *respostas, args=()):
        sessao = _sessao(*respostas)
        dormidas: list[float] = []
        monkeypatch.setattr("sefaz.adn_sync.time.sleep", dormidas.append)
        _cli_com_sessao(monkeypatch, sessao)
        return sessao, dormidas, cli.main(["sincronizar-adn", "--json", *args])

    def test_cli_tentativas_explicitas_e_json(
        self, certificado_cadastrado, monkeypatch, capsys, tmp_path
    ):  # noqa: F811
        sessao, dormidas, codigo = self._cli(
            monkeypatch,
            *[_erro_reset_handshake() for _ in range(2)],
            args=("--tentativas", "2", "--db", str(tmp_path / "c.db")),
        )
        saida = json.loads(capsys.readouterr().out)
        assert codigo == 1 and len(sessao.chamadas) == 2 and dormidas == [2.0]
        assert saida["tentativas_usadas"] == 2 and len(saida["falhas_conexao"]) == 2
        assert saida["encerrado_por"] == "ERRO"
        assert "O programa já tentou 2 vezes" in saida["mensagem"]
        for proibido in SENSIVEIS:
            assert proibido not in json.dumps(saida, ensure_ascii=False)

    def test_cli_padrao_3_e_variavel_de_ambiente(
        self, certificado_cadastrado, monkeypatch, capsys, tmp_path
    ):  # noqa: F811
        sessao, _, _ = self._cli(
            monkeypatch, *[_erro_dns() for _ in range(3)], args=("--db", str(tmp_path / "a.db"))
        )
        capsys.readouterr()
        assert len(sessao.chamadas) == 3
        monkeypatch.setenv("NFE_ADN_TENTATIVAS", "4")
        sessao, _, _ = self._cli(
            monkeypatch, *[_erro_dns() for _ in range(4)], args=("--db", str(tmp_path / "b.db"))
        )
        assert len(sessao.chamadas) == 4

    def test_cli_variavel_invalida_falha_antes_de_consultar(
        self, certificado_cadastrado, monkeypatch, capsys, tmp_path
    ):  # noqa: F811
        monkeypatch.setenv("NFE_ADN_TENTATIVAS", "9")
        sessao, _, codigo = self._cli(monkeypatch, args=("--db", str(tmp_path / "x.db")))
        assert codigo == 1 and sessao.chamadas == []
        assert "1 a 5" in capsys.readouterr().err

    def test_cli_texto_lista_as_falhas(self, certificado_cadastrado, monkeypatch, capsys, tmp_path):  # noqa: F811
        sessao = _sessao(_erro_remote_disconnected(), _Resp(404, CORPO_FIM))
        monkeypatch.setattr("sefaz.adn_sync.time.sleep", lambda s: None)
        _cli_com_sessao(monkeypatch, sessao)
        assert cli.main(["sincronizar-adn", "--db", str(tmp_path / "t.db")]) == 0
        saida = capsys.readouterr().out
        assert "Tentativas de conexão usadas    : 2" in saida
        assert "pág. 1, tentativa 1: leitura da resposta [RemoteDisconnected]" in saida

    def test_cli_tentativas_acima_de_5_rejeitada(self):
        with pytest.raises(SystemExit) as exc:
            cli.main(["sincronizar-adn", "--tentativas", "6"])
        assert exc.value.code == 2

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

    def test_api_repete_conexao_e_respeita_o_parametro(self, client):
        chamadas: list[int] = []
        falha = analisar_falha_conexao(_erro_remote_disconnected())

        def cai(nsu):
            chamadas.append(nsu)
            raise ErroADN(mensagem_falha_conexao(falha), categoria="CONEXAO", falha=falha)

        definir_consultor_adn(cai, CNPJ_DEMO)
        r = client.post("/api/recebidas/sincronizar-adn", params={"tentativas": 2})
        assert r.status_code == 200
        corpo = r.json()
        assert len(chamadas) == 2 and corpo["tentativas_usadas"] == 2
        assert corpo["encerrado_por"] == "ERRO" and len(corpo["falhas_conexao"]) == 2
        assert (
            client.post("/api/recebidas/sincronizar-adn", params={"tentativas": 6}).status_code
            == 422
        )

    def test_api_429_nao_repete(self, client):
        chamadas: list[int] = []

        def limite(nsu):
            chamadas.append(nsu)
            raise ErroADN("limite", categoria="LIMITE", http_status=429, retry_apos_segundos=60)

        definir_consultor_adn(limite, CNPJ_DEMO)
        r = client.post("/api/recebidas/sincronizar-adn")
        assert r.json()["encerrado_por"] == "BLOQUEIO" and chamadas == [0]


class TestMigracaoDoSyncLog:
    def test_banco_antigo_ganha_colunas_com_valores_padrao(self, tmp_path):
        caminho = tmp_path / "antigo.db"
        _criar_banco_antigo(caminho, 5)
        RepositorioDistDFeSQLite(caminho)
        RepositorioDistDFeSQLite(caminho)  # idempotente
        con = sqlite3.connect(caminho)
        linhas = con.execute("SELECT tentativas, falhas_conexao, origem FROM sync_log").fetchall()
        con.close()
        assert linhas == [(1, None, "NFE")]
        log = RepositorioDistDFeSQLite(caminho).listar_logs_sync()[0]
        assert (log.tentativas, log.falhas_conexao) == (1, "")
