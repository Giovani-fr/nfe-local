# ruff: noqa: F811  (fixtures do pytest importadas de outros módulos de teste)
"""NFeDistribuicaoDFe: tempo limite configurável e repetição só em falha de CONEXÃO.

Sem rede: sessão HTTP simulada que levanta exceções reais de `requests`/`urllib3`/`http.client`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import requests
import urllib3.exceptions as u3
from fastapi.testclient import TestClient

import api.cli as cli
from api.app import (
    app,
    definir_consultor_dist_dfe,
    definir_repositorio_recebidas,
    definir_sessao_http_sefaz,
)
from sefaz.adn import analisar_falha_conexao
from sefaz.cert_a1 import ErroCertificadoA1
from sefaz.controle_nsu import ErroBloqueioSEFAZ
from sefaz.dist_dfe import (
    ClienteNFeDistribuicaoDFe,
    ErroComunicacaoSEFAZ,
    ErroConexaoSEFAZ,
    mensagem_conexao_sefaz,
    parse_resposta_dist_dfe,
    resolver_tentativas_sefaz,
    resolver_timeout_sefaz,
)
from sefaz.storage import RepositorioDistDFeSQLite, sincronizar_dist_dfe
from tests.test_certificado_api import _gerar_pfx_sintetico

SOAP = Path(__file__).parent / "fixtures" / "sefaz"
LOTE1 = (SOAP / "soap_ret_138_lote1.xml").read_bytes()  # ultNSU 2 / maxNSU 4
LOTE2 = (SOAP / "soap_ret_138_lote2.xml").read_bytes()  # ultNSU 4 / maxNSU 4 (topo da fila)
VAZIO_137 = (SOAP / "soap_ret_137_vazio.xml").read_bytes()
BLOQ_656 = (SOAP / "soap_ret_656_consumo_indevido.xml").read_bytes()
SENHA = "senha-ficticia-sefaz-77"
CNPJ = "11222333000181"
SENSIVEIS = (CNPJ, SENHA, "senha", ".pfx", "NFeDistribuicaoDFe.asmx", "https://", "cnpj")


class _Resp:
    def __init__(self, conteudo: bytes, status_code: int = 200) -> None:
        self.content = conteudo
        self.status_code = status_code
        self.text = conteudo.decode("utf-8", errors="replace")


class _Sessao:
    def __init__(self, *itens) -> None:
        self.itens = list(itens)
        self.chamadas: list[dict] = []

    def post(self, url, data=None, headers=None, timeout=None):
        self.chamadas.append({"timeout": timeout, "corpo": data})
        item = self.itens.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _timeout_leitura() -> Exception:
    return requests.ReadTimeout(u3.ReadTimeoutError(None, None, f"Read timed out. {CNPJ} {SENHA}"))


def _timeout_conexao() -> Exception:
    return requests.ConnectTimeout(u3.ConnectTimeoutError(None, f"Connection timed out {CNPJ}"))


@pytest.fixture(scope="module")
def pfx() -> bytes:
    return _gerar_pfx_sintetico(cnpj=CNPJ, senha=SENHA)


@pytest.fixture
def repo(tmp_path) -> RepositorioDistDFeSQLite:
    return RepositorioDistDFeSQLite(tmp_path / "sef.db")


def _cliente(pfx, sessao, **kw) -> ClienteNFeDistribuicaoDFe:
    return ClienteNFeDistribuicaoDFe(pfx_bytes=pfx, senha=SENHA, sessao_http=sessao, **kw)


def _sync(cliente, repo, esperas: list[float], **kw):
    kw.setdefault("esperas_retentativa", (5.0, 15.0, 30.0))
    return sincronizar_dist_dfe(
        consultar_fn=lambda nsu: cliente.consultar_dist_nsu(ult_nsu=nsu),
        repo=repo,
        ambiente=2,
        dormir_fn=esperas.append,
        **kw,
    )


# --------------------------------------------------------------------------- resolvers


class TestResolvers:
    def test_timeout_padrao_env_argumento_e_limites(self, monkeypatch):
        assert resolver_timeout_sefaz() == 90.0
        monkeypatch.setenv("NFE_SEFAZ_TIMEOUT", "120")
        assert resolver_timeout_sefaz() == 120.0
        assert resolver_timeout_sefaz(45) == 45.0  # o argumento vence a variável
        assert resolver_timeout_sefaz(180) == 180.0

    @pytest.mark.parametrize("ruim", ["0", "0.5", "181", "abc", "-5"])
    def test_timeout_invalido(self, monkeypatch, ruim):
        monkeypatch.setenv("NFE_SEFAZ_TIMEOUT", ruim)
        with pytest.raises(ValueError, match="1 a 180"):
            resolver_timeout_sefaz()

    def test_tentativas_padrao_env_argumento_e_limites(self, monkeypatch):
        assert resolver_tentativas_sefaz() == 3
        monkeypatch.setenv("NFE_SEFAZ_TENTATIVAS", "5")
        assert resolver_tentativas_sefaz() == 5
        assert resolver_tentativas_sefaz(2) == 2
        monkeypatch.setenv("NFE_SEFAZ_TENTATIVAS", "  ")
        assert resolver_tentativas_sefaz() == 3

    @pytest.mark.parametrize("ruim", ["0", "6", "abc", "2.5"])
    def test_tentativas_invalidas(self, monkeypatch, ruim):
        monkeypatch.setenv("NFE_SEFAZ_TENTATIVAS", ruim)
        with pytest.raises(ValueError, match="1 a 5"):
            resolver_tentativas_sefaz()

    def test_cliente_usa_o_timeout_resolvido_na_chamada(self, pfx, monkeypatch):
        sessao = _Sessao(_Resp(VAZIO_137))
        _cliente(pfx, sessao).consultar_dist_nsu(ult_nsu=0)
        assert sessao.chamadas[0]["timeout"] == 90.0  # novo padrão (antes era 30 s)
        monkeypatch.setenv("NFE_SEFAZ_TIMEOUT", "150")
        sessao2 = _Sessao(_Resp(VAZIO_137))
        _cliente(pfx, sessao2).consultar_dist_nsu(ult_nsu=0)
        assert sessao2.chamadas[0]["timeout"] == 150.0
        sessao3 = _Sessao(_Resp(VAZIO_137))
        _cliente(pfx, sessao3, timeout_segundos=60).consultar_dist_nsu(ult_nsu=0)
        assert sessao3.chamadas[0]["timeout"] == 60.0


# --------------------------------------------------------------------------- repetição


class TestRepeticaoDeConexao:
    def test_sucesso_na_segunda_tentativa(self, pfx, repo):
        sessao = _Sessao(_timeout_leitura(), _Resp(LOTE1), _Resp(LOTE2))
        esperas: list[float] = []
        r = _sync(_cliente(pfx, sessao), repo, esperas)
        assert r.erro is None and r.fila_zerada
        assert len(sessao.chamadas) == 3 and r.tentativas_usadas == 3
        assert esperas == [5.0]
        assert r.novas_notas > 0
        assert repo.obter_cursor(2).ultimo_nsu == "000000000000004"
        log = repo.listar_logs_sync(origem="NFE")[0]
        assert log.tentativas == 3
        assert "pág. 1, tentativa 1: leitura da resposta [ReadTimeoutError]" in log.falhas_conexao

    def test_falha_em_todas_nao_grava_cursor_nem_trava_e_avisa_com_clareza(self, pfx, repo):
        sessao = _Sessao(_timeout_leitura(), _timeout_leitura(), _timeout_leitura())
        esperas: list[float] = []
        r = _sync(_cliente(pfx, sessao), repo, esperas)
        assert len(sessao.chamadas) == 3 and esperas == [5.0, 15.0]
        assert r.erro is not None
        assert "A SEFAZ demorou demais para responder (tentamos 3 vezes)" in r.erro
        assert "costuma ficar lento" in r.erro and "fora do horário comercial" in r.erro
        # o cursor NÃO foi tocado e não há trava indevida
        cursor = repo.obter_cursor(2)
        assert cursor.ultimo_nsu == "000000000000000" and cursor.max_nsu == "000000000000000"
        assert cursor.bloqueado_ate is None and cursor.ultima_consulta is None
        assert cursor.ultimo_cstat is None
        assert r.cursor.bloqueado_ate is None and r.paginas_consultadas == 0
        # nova execução imediata NÃO é bloqueada (não houve 656)
        sessao2 = _Sessao(_Resp(LOTE1), _Resp(LOTE2))
        assert _sync(_cliente(pfx, sessao2), repo, []).erro is None

    def test_log_guarda_so_etapa_e_classe_nunca_dados_sensiveis(self, pfx, repo):
        sessao = _Sessao(_timeout_conexao(), _timeout_leitura(), _timeout_leitura())
        r = _sync(_cliente(pfx, sessao), repo, [])
        log = repo.listar_logs_sync(origem="NFE")[0]
        assert (log.cstat, log.tentativas) == ("TIMEOUT", 3)
        assert "conexão TCP [ConnectTimeoutError]" in log.falhas_conexao
        assert log.falhas_conexao.count("pág. 1") == 3
        for texto in (log.motivo, log.falhas_conexao, r.erro or "", " ".join(r.falhas_conexao)):
            for proibido in SENSIVEIS:
                assert proibido.lower() not in texto.lower(), (proibido, texto)

    def test_timeout_no_meio_da_paginacao_preserva_cursor_e_notas(self, pfx, repo):
        sessao = _Sessao(_Resp(LOTE1), _timeout_leitura(), _timeout_leitura(), _timeout_leitura())
        r = _sync(_cliente(pfx, sessao), repo, [])
        assert r.erro and "tentamos 3 vezes" in r.erro
        assert r.paginas_consultadas == 1 and r.novas_notas > 0
        notas_antes = len(repo.listar_notas_recebidas())
        assert notas_antes > 0  # o que veio na 1ª página ficou GRAVADO
        cursor = repo.obter_cursor(2)
        assert cursor.ultimo_nsu == "000000000000002"  # só avançou pela página recebida
        assert cursor.bloqueado_ate is None
        # a próxima execução continua de onde parou (pede a partir de ultNSU 2)
        sessao2 = _Sessao(_Resp(LOTE2))
        r2 = _sync(_cliente(pfx, sessao2), repo, [])
        assert r2.erro is None and r2.fila_zerada
        assert b"000000000000002" in sessao2.chamadas[0]["corpo"]
        assert repo.obter_cursor(2).ultimo_nsu == "000000000000004"

    def test_uma_tentativa_nao_repete(self, pfx, repo):
        sessao = _Sessao(_timeout_leitura(), _Resp(LOTE1))
        r = _sync(_cliente(pfx, sessao), repo, [], tentativas=1)
        assert len(sessao.chamadas) == 1 and r.erro and "tentamos 1 vez" in r.erro

    def test_espera_crescente_com_cinco_tentativas(self, pfx, repo):
        sessao = _Sessao(*[_timeout_leitura() for _ in range(5)])
        esperas: list[float] = []
        _sync(_cliente(pfx, sessao), repo, esperas, tentativas=5)
        assert len(sessao.chamadas) == 5 and esperas == [5.0, 15.0, 30.0, 30.0]

    def test_outras_falhas_de_conexao_tambem_repetem_com_mensagem_propria(self, pfx, repo):
        reset = requests.ConnectionError(
            u3.ProtocolError("Connection aborted.", ConnectionResetError())
        )
        sessao = _Sessao(reset, reset, reset)
        r = _sync(_cliente(pfx, sessao), repo, [])
        assert len(sessao.chamadas) == 3 and r.erro
        assert "Falha de conexão com a SEFAZ" in r.erro and "tentamos 3 vezes" in r.erro
        assert "ADN" not in r.erro  # texto do serviço certo
        assert "a SEFAZ pode não ter aceitado" in r.erro

    def test_falha_de_tls_repete(self, pfx, repo):
        sessao = _Sessao(requests.exceptions.SSLError("x"), _Resp(VAZIO_137))
        r = _sync(_cliente(pfx, sessao), repo, [])
        assert len(sessao.chamadas) == 2 and r.erro is None


class TestNaoRepete:
    def test_656_nao_repete_e_a_trava_de_1h_e_preservada(self, pfx, repo):
        sessao = _Sessao(_Resp(BLOQ_656), _Resp(LOTE1))
        esperas: list[float] = []
        r = _sync(_cliente(pfx, sessao), repo, esperas)
        assert len(sessao.chamadas) == 1 and esperas == [] and r.bloqueado_656
        assert r.erro is None and r.tentativas_usadas == 1
        bloqueado_ate = repo.obter_cursor(2).bloqueado_ate
        assert bloqueado_ate is not None
        # a execução seguinte recusa SEM tocar a rede
        sessao2 = _Sessao(_Resp(LOTE1))
        with pytest.raises(ErroBloqueioSEFAZ):
            _sync(_cliente(pfx, sessao2), repo, [])
        assert sessao2.chamadas == []

    def test_137_nao_repete_e_encerra(self, pfx, repo):
        sessao = _Sessao(_Resp(VAZIO_137), _Resp(LOTE1))
        r = _sync(_cliente(pfx, sessao), repo, [])
        assert len(sessao.chamadas) == 1 and r.fila_zerada and r.erro is None

    def test_138_encerra_quando_ultnsu_igual_maxnsu_sem_chamada_extra(self, pfx, repo):
        sessao = _Sessao(_Resp(LOTE2), _Resp(LOTE1))
        r = _sync(_cliente(pfx, sessao), repo, [])
        assert len(sessao.chamadas) == 1 and r.fila_zerada

    def test_soap_fault_nao_repete(self, pfx, repo):
        fault = b"""<?xml version="1.0"?><e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope">
        <e:Body><e:Fault><e:Reason><e:Text>Erro interno</e:Text></e:Reason></e:Fault>
        </e:Body></e:Envelope>"""
        sessao = _Sessao(_Resp(fault, 500), _Resp(LOTE1))
        with pytest.raises(ErroComunicacaoSEFAZ) as exc:
            _sync(_cliente(pfx, sessao), repo, [])
        assert not isinstance(exc.value, ErroConexaoSEFAZ) and len(sessao.chamadas) == 1

    def test_http_de_erro_sem_soap_nao_repete(self, pfx, repo):
        sessao = _Sessao(_Resp(b"<html>502</html>", 502), _Resp(LOTE1))
        with pytest.raises(ErroComunicacaoSEFAZ):
            _sync(_cliente(pfx, sessao), repo, [])
        assert len(sessao.chamadas) == 1

    def test_certificado_vencido_nao_repete(self, pfx, repo):
        sessao = _Sessao(ValueError("Client certificate expired: C:\\segredo\\a.pfx"), _Resp(LOTE1))
        with pytest.raises(ErroCertificadoA1) as exc:
            _sync(_cliente(pfx, sessao), repo, [])
        assert len(sessao.chamadas) == 1 and "segredo" not in str(exc.value)


def test_mensagem_exata_do_tempo_esgotado():
    falha = analisar_falha_conexao(_timeout_leitura(), servico="a SEFAZ")
    assert mensagem_conexao_sefaz(falha, 3) == (
        "A SEFAZ demorou demais para responder (tentamos 3 vezes). "
        "O serviço de NF-e costuma ficar lento; tente de novo em alguns minutos, "
        "de preferência fora do horário comercial."
    )
    assert "tentamos 1 vez)" in mensagem_conexao_sefaz(falha, 1)


# --------------------------------------------------------------------------- CLI e API


@pytest.fixture
def cert_env(tmp_path, pfx, monkeypatch):
    arq = tmp_path / "cert_sintetico.pfx"
    arq.write_bytes(pfx)
    monkeypatch.setenv("NFE_CERT_PATH", str(arq))
    monkeypatch.setenv("NFE_CERT_SENHA", SENHA)


def _cli_com_sessao(monkeypatch, sessao):
    original = ClienteNFeDistribuicaoDFe
    monkeypatch.setattr(
        cli, "ClienteNFeDistribuicaoDFe", lambda **kw: original(**kw, sessao_http=sessao)
    )
    monkeypatch.setattr("sefaz.dist_dfe.time.sleep", lambda s: None)


class TestCli:
    def test_timeout_e_tentativas_na_cli_com_mensagem_amigavel(
        self, cert_env, monkeypatch, tmp_path, capsys
    ):
        sessao = _Sessao(_timeout_leitura(), _timeout_leitura())
        _cli_com_sessao(monkeypatch, sessao)
        codigo = cli.main(
            [
                "sincronizar-sefaz",
                "--timeout",
                "120",
                "--tentativas",
                "2",
                "--json",
                "--db",
                str(tmp_path / "c.db"),
            ]
        )
        saida = capsys.readouterr()
        d = json.loads(saida.out)
        assert codigo == 1 and len(sessao.chamadas) == 2
        assert all(c["timeout"] == 120.0 for c in sessao.chamadas)
        assert d["tentativas_usadas"] == 2 and len(d["falhas_conexao"]) == 2
        assert "A SEFAZ demorou demais para responder (tentamos 2 vezes)" in d["erro"]
        assert "A SEFAZ demorou demais" in saida.err
        for proibido in SENSIVEIS:
            assert proibido.lower() not in (saida.out + saida.err).lower(), proibido

    def test_padrao_90s_e_3_tentativas_e_variaveis(self, cert_env, monkeypatch, tmp_path, capsys):
        sessao = _Sessao(*[_timeout_leitura() for _ in range(3)])
        _cli_com_sessao(monkeypatch, sessao)
        cli.main(["sincronizar-sefaz", "--json", "--db", str(tmp_path / "a.db")])
        capsys.readouterr()
        assert len(sessao.chamadas) == 3 and sessao.chamadas[0]["timeout"] == 90.0
        monkeypatch.setenv("NFE_SEFAZ_TIMEOUT", "100")
        monkeypatch.setenv("NFE_SEFAZ_TENTATIVAS", "4")
        sessao2 = _Sessao(*[_timeout_leitura() for _ in range(4)])
        _cli_com_sessao(monkeypatch, sessao2)
        cli.main(["sincronizar-sefaz", "--json", "--db", str(tmp_path / "b.db")])
        assert len(sessao2.chamadas) == 4 and sessao2.chamadas[0]["timeout"] == 100.0

    @pytest.mark.parametrize("args", [["--timeout", "500"], ["--timeout", "0"]])
    def test_timeout_invalido_falha_antes_de_consultar(
        self, cert_env, monkeypatch, tmp_path, capsys, args
    ):
        sessao = _Sessao()
        _cli_com_sessao(monkeypatch, sessao)
        assert cli.main(["sincronizar-sefaz", *args, "--db", str(tmp_path / "x.db")]) == 1
        assert "1 a 180" in capsys.readouterr().err and sessao.chamadas == []

    def test_tentativas_acima_de_5_e_rejeitada_pelo_parser(self):
        with pytest.raises(SystemExit) as exc:
            cli.main(["sincronizar-sefaz", "--tentativas", "6"])
        assert exc.value.code == 2

    def test_texto_mostra_tentativas_e_erro(self, cert_env, monkeypatch, tmp_path, capsys):
        _cli_com_sessao(monkeypatch, _Sessao(_timeout_leitura(), _Resp(LOTE1), _Resp(LOTE2)))
        assert cli.main(["sincronizar-sefaz", "--db", str(tmp_path / "t.db")]) == 0
        saida = capsys.readouterr().out
        assert "Tentativas de conexão usadas    : 3" in saida
        assert "pág. 1, tentativa 1: leitura da resposta [ReadTimeoutError]" in saida


class TestApi:
    @pytest.fixture
    def client(self, repo):
        definir_repositorio_recebidas(repo)
        try:
            yield TestClient(app)
        finally:
            definir_repositorio_recebidas(None)
            definir_consultor_dist_dfe(None)
            definir_sessao_http_sefaz(None)

    def test_api_devolve_a_mensagem_amigavel_sem_perder_o_que_gravou(self, client, repo):
        falha = analisar_falha_conexao(_timeout_leitura(), servico="a SEFAZ")
        chamadas: list[str] = []

        def consulta(nsu: str):
            chamadas.append(nsu)
            if len(chamadas) == 1:
                return parse_resposta_dist_dfe(LOTE1)
            raise ErroConexaoSEFAZ(mensagem_conexao_sefaz(falha, 1), falha=falha)

        definir_consultor_dist_dfe(consulta)
        r = client.post("/api/recebidas/sincronizar", params={"tentativas": 2})
        assert r.status_code == 200
        corpo = r.json()
        assert "A SEFAZ demorou demais para responder (tentamos 2 vezes)" in corpo["erro"]
        assert corpo["novas_notas"] > 0 and corpo["cursor"]["ultimo_nsu"] == "000000000000002"
        assert len(chamadas) == 3  # 1 página ok + 2 tentativas da página que falhou
        assert len(repo.listar_notas_recebidas()) > 0
        for proibido in SENSIVEIS:
            assert proibido.lower() not in r.text.lower()

    def test_api_parametros_invalidos(self, client):
        assert (
            client.post("/api/recebidas/sincronizar", params={"tentativas": 6}).status_code == 422
        )
        assert client.post("/api/recebidas/sincronizar", params={"timeout": 500}).status_code == 422

    def test_api_certificado_vencido_503_sem_repetir(self, client, cert_env, pfx):
        sessao = _Sessao(ValueError("expired"), _Resp(LOTE1))
        definir_sessao_http_sefaz(sessao)
        r = client.post("/api/recebidas/sincronizar")
        assert r.status_code in (503,) and len(sessao.chamadas) <= 1


def test_ui_trava_cliques_e_mostra_a_mensagem_de_espera():
    js = (Path(__file__).parent.parent / "web" / "static" / "app.js").read_text(encoding="utf-8")
    assert "Aguardando a SEFAZ (pode levar até alguns minutos)" in js
    assert "estado.buscando" in js and "definirBuscando(true)" in js
    assert "if (estado.buscando) return;" in js
    for id_ in ("btn-sync-sefaz", "btn-sync-adn", "btn-sync-tudo"):
        assert id_ in js
