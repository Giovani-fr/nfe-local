# ruff: noqa: F811  (fixtures do pytest importadas de outros módulos de teste)
"""Papel da NF-e (Emitida/Recebida): CNPJ consultado guardado no cursor da NF-e + fallbacks."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api.cli as cli
from api.app import (
    app,
    definir_consultor_dist_dfe,
    definir_repositorio_recebidas,
    definir_sessao_http_sefaz,
)
from sefaz.adn import EstadoCursorADN
from sefaz.adn_demo import CNPJ_DEMO
from sefaz.controle_nsu import EstadoCursorNSU
from sefaz.dist_dfe import ClienteNFeDistribuicaoDFe, parse_resposta_dist_dfe
from sefaz.storage import RepositorioDistDFeSQLite, sincronizar_dist_dfe
from tests.test_adn import COLS_CURSOR, _criar_banco_antigo, _foto
from tests.test_adn_diagnostico import SENHA, certificado_cadastrado, pfx  # noqa: F401

SOAP = Path(__file__).parent / "fixtures" / "sefaz"
LOTE1 = (SOAP / "soap_ret_138_lote1.xml").read_bytes()
LOTE2 = (SOAP / "soap_ret_138_lote2.xml").read_bytes()
OUTRO_CNPJ = "33444555000181"


@pytest.fixture
def repo(tmp_path) -> RepositorioDistDFeSQLite:
    return RepositorioDistDFeSQLite(tmp_path / "papel.db")


@pytest.fixture
def client(repo):
    definir_repositorio_recebidas(repo)
    try:
        yield TestClient(app)
    finally:
        definir_repositorio_recebidas(None)
        definir_consultor_dist_dfe(None)
        definir_sessao_http_sefaz(None)


def _papeis(client) -> dict[str, str | None]:
    notas = client.get("/api/recebidas", params={"origem": "SEFAZ"}).json()["notas"]
    return {n["chave"]: n["papel_visao"] for n in notas}


def _carregar_nfe_sem_referencia(repo) -> None:
    """Notas de NF-e no banco sem NENHUM CNPJ de referência (como no uso real relatado)."""
    respostas = iter([parse_resposta_dist_dfe(LOTE1), parse_resposta_dist_dfe(LOTE2)])
    sincronizar_dist_dfe(consultar_fn=lambda _n: next(respostas), repo=repo, ambiente=2)


class TestMigracaoDoCursorDaNfe:
    def test_banco_antigo_ganha_a_coluna_sem_alterar_linhas(self, tmp_path):
        caminho = tmp_path / "antigo.db"
        _criar_banco_antigo(caminho, 40)
        antes = _foto(caminho, "dfe_cursor", COLS_CURSOR)
        notas_antes = _foto(caminho, "notas_recebidas", "chave,nsu,valor_total,situacao")
        repo = RepositorioDistDFeSQLite(caminho)
        RepositorioDistDFeSQLite(caminho)  # idempotente
        assert _foto(caminho, "dfe_cursor", COLS_CURSOR) == antes
        assert _foto(caminho, "notas_recebidas", "chave,nsu,valor_total,situacao") == notas_antes
        con = sqlite3.connect(caminho)
        colunas = {r[1] for r in con.execute("PRAGMA table_info(dfe_cursor)")}
        valores = con.execute("SELECT documento_consulta FROM dfe_cursor").fetchall()
        con.close()
        assert "documento_consulta" in colunas and valores == [(None,)]
        assert repo.obter_documento_consulta_nfe_recente() == ""
        assert repo.obter_cursor(2).documento_consulta == ""  # cursor antigo continua legível


class TestGuardaDoCnpjConsultado:
    def test_cada_pagina_recebida_grava_o_cnpj_junto_do_cursor(self, repo):
        respostas = iter([parse_resposta_dist_dfe(LOTE1), parse_resposta_dist_dfe(LOTE2)])
        sincronizar_dist_dfe(
            consultar_fn=lambda _n: next(respostas),
            repo=repo,
            ambiente=2,
            documento_consulta="11.222.333/0001-81",
        )
        assert repo.obter_cursor(2).documento_consulta == CNPJ_DEMO
        assert repo.obter_documento_consulta_nfe_recente() == CNPJ_DEMO

    def test_cursor_sem_cnpj_nao_apaga_o_ja_guardado(self, repo):
        repo.salvar_cursor(EstadoCursorNSU(ambiente=2, documento_consulta=CNPJ_DEMO))
        repo.salvar_cursor(EstadoCursorNSU(ambiente=2, ultimo_nsu="000000000000009"))
        assert repo.obter_cursor(2).documento_consulta == CNPJ_DEMO

    def test_o_cnpj_nunca_sai_no_json_do_cursor(self, repo):
        repo.salvar_cursor(EstadoCursorNSU(ambiente=2, documento_consulta=CNPJ_DEMO))
        assert CNPJ_DEMO not in repo.obter_cursor(2).model_dump_json()

    def test_pagina_que_falha_nao_grava_cursor_nem_cnpj(self, repo):
        from sefaz.adn import analisar_falha_conexao
        from sefaz.dist_dfe import ErroConexaoSEFAZ

        falha = analisar_falha_conexao(TimeoutError("x"), servico="a SEFAZ")

        def cai(_n):
            raise ErroConexaoSEFAZ("x", falha=falha)

        sincronizar_dist_dfe(
            consultar_fn=cai,
            repo=repo,
            ambiente=2,
            documento_consulta=CNPJ_DEMO,
            tentativas=1,
        )
        assert repo.obter_cursor(2).documento_consulta == ""
        assert repo.obter_documento_consulta_nfe_recente() == ""


class TestReferenciaDoPapel:
    def test_sem_nenhuma_referencia_a_nfe_fica_sem_papel(self, client, repo, monkeypatch):
        monkeypatch.delenv("NFE_CNPJ_CONSULTA", raising=False)
        _carregar_nfe_sem_referencia(repo)
        assert set(_papeis(client).values()) == {None}  # o problema relatado em uso real

    def test_cnpj_guardado_pelo_cursor_da_nfe_define_o_papel(self, client, repo):
        _carregar_nfe_sem_referencia(repo)
        notas = client.get("/api/recebidas", params={"origem": "SEFAZ"}).json()["notas"]
        destinatario = next(
            n["destinatario_documento"] for n in notas if n["destinatario_documento"]
        )
        repo.salvar_cursor(EstadoCursorNSU(ambiente=2, documento_consulta=destinatario))
        papeis = _papeis(client)
        assert "RECEBIDA" in set(papeis.values())
        recebidas = client.get("/api/recebidas", params={"papel": "RECEBIDA"}).json()
        assert recebidas["total"] >= 1

    def test_ordem_env_depois_adn_depois_nfe_depois_certificado(
        self, client, repo, certificado_cadastrado, monkeypatch
    ):
        from api.app import _documento_consulta

        monkeypatch.delenv("NFE_CNPJ_CONSULTA", raising=False)
        # só o certificado cadastrado (bancos antigos de NF-e, sem a coluna preenchida)
        assert _documento_consulta(repo) == CNPJ_DEMO
        repo.salvar_cursor(EstadoCursorNSU(ambiente=2, documento_consulta="12345678000195"))
        assert _documento_consulta(repo) == "12345678000195"  # cursor da NF-e vence o certificado
        repo.salvar_cursor_adn(EstadoCursorADN(ambiente=2, documento_consulta=OUTRO_CNPJ))
        assert _documento_consulta(repo) == OUTRO_CNPJ  # ADN vence o da NF-e
        monkeypatch.setenv("NFE_CNPJ_CONSULTA", "99888777000166")
        assert _documento_consulta(repo) == "99888777000166"  # a variável vence tudo

    def test_banco_antigo_usa_o_cnpj_do_certificado_cadastrado(
        self, tmp_path, certificado_cadastrado, monkeypatch
    ):
        monkeypatch.delenv("NFE_CNPJ_CONSULTA", raising=False)
        caminho = tmp_path / "antigo.db"
        _criar_banco_antigo(caminho, 5)  # NF-e antigas, cursor sem a coluna nova
        repo_antigo = RepositorioDistDFeSQLite(caminho)
        definir_repositorio_recebidas(repo_antigo)
        try:
            from api.app import _documento_consulta

            assert repo_antigo.obter_documento_consulta_nfe_recente() == ""
            assert _documento_consulta(repo_antigo) == CNPJ_DEMO
            corpo = TestClient(app).get("/api/recebidas").json()
            assert corpo["total"] == 5
        finally:
            definir_repositorio_recebidas(None)


class _Resp:
    status_code = 200

    def __init__(self, conteudo: bytes) -> None:
        self.content = conteudo
        self.text = conteudo.decode("utf-8", errors="replace")


class _Sessao:
    def __init__(self, *itens) -> None:
        self.itens = list(itens)

    def post(self, *a, **kw):
        return self.itens.pop(0)


class TestBuscaGuardaOCnpjDoCertificado:
    def test_api_guarda_o_cnpj_do_certificado_e_a_nfe_ganha_papel(
        self, client, repo, certificado_cadastrado, monkeypatch
    ):
        monkeypatch.delenv("NFE_CNPJ_CONSULTA", raising=False)
        definir_sessao_http_sefaz(_Sessao(_Resp(LOTE1), _Resp(LOTE2)))
        r = client.post("/api/recebidas/sincronizar")
        assert r.status_code == 200 and r.json()["erro"] is None
        assert CNPJ_DEMO not in r.text  # nunca sai na resposta
        assert repo.obter_cursor(2).documento_consulta == CNPJ_DEMO
        papeis = set(_papeis(client).values())
        assert papeis <= {"EMITIDA", "RECEBIDA", None} and papeis != {None}

    def test_api_com_nfe_cnpj_consulta_guarda_esse_cnpj(
        self, client, repo, certificado_cadastrado, monkeypatch
    ):
        monkeypatch.setenv("NFE_CNPJ_CONSULTA", OUTRO_CNPJ)
        definir_sessao_http_sefaz(_Sessao(_Resp(LOTE1), _Resp(LOTE2)))
        client.post("/api/recebidas/sincronizar")
        assert repo.obter_cursor(2).documento_consulta == OUTRO_CNPJ

    def test_cli_guarda_o_cnpj_do_certificado(self, tmp_path, pfx, monkeypatch, capsys):
        arq = tmp_path / "cert.pfx"
        arq.write_bytes(pfx)
        monkeypatch.setenv("NFE_CERT_PATH", str(arq))
        monkeypatch.setenv("NFE_CERT_SENHA", SENHA)
        monkeypatch.delenv("NFE_CNPJ_CONSULTA", raising=False)
        original = ClienteNFeDistribuicaoDFe
        sessao = _Sessao(_Resp(LOTE1), _Resp(LOTE2))
        monkeypatch.setattr(
            cli, "ClienteNFeDistribuicaoDFe", lambda **kw: original(**kw, sessao_http=sessao)
        )
        banco = tmp_path / "cli.db"
        assert cli.main(["sincronizar-sefaz", "--json", "--db", str(banco)]) == 0
        saida = capsys.readouterr().out
        assert CNPJ_DEMO not in saida and json.loads(saida)["erro"] is None
        assert RepositorioDistDFeSQLite(banco).obter_cursor(2).documento_consulta == CNPJ_DEMO
