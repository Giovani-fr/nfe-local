"""404 do ADN, URL base configurável e modo `--diagnostico` (somente leitura).

Sem rede e sem certificado real: sessão HTTP simulada, certificado autoassinado em memória.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path

import pytest
import requests

import api.cli as cli
from sefaz.adn import (
    ClienteADN,
    ErroADN,
    eh_resposta_fila_zerada,
    resolver_url_base,
)
from sefaz.adn_demo import CNPJ_DEMO, criar_consultor_demo
from sefaz.adn_diagnostico import diagnosticar_adn, mascarar_documento
from sefaz.adn_sync import sincronizar_adn
from sefaz.cert_a1 import salvar_certificado_local
from sefaz.storage import RepositorioDistDFeSQLite
from tests.test_adn import CORPO_FIM, _Resp, _Sessao, _sincronizar
from tests.test_certificado_api import _gerar_pfx_sintetico

SENHA = "senha-ficticia-diagnostico-42"


def _cliente(pfx, sessao, **kw) -> ClienteADN:
    return ClienteADN(pfx_bytes=pfx, senha=SENHA, sessao_http=sessao, **kw)


@pytest.fixture(scope="module")
def pfx() -> bytes:
    return _gerar_pfx_sintetico(cnpj=CNPJ_DEMO, senha=SENHA)


def _cli_com_sessao(monkeypatch, sessao) -> None:
    """Faz a CLI usar uma sessão simulada em vez da rede."""
    original = ClienteADN
    monkeypatch.setattr(cli, "ClienteADN", lambda **kw: original(**kw, sessao_http=sessao))


@pytest.fixture
def certificado_cadastrado(pfx, dpapi_falso, monkeypatch):
    if sys.platform == "win32":  # em Windows a DPAPI real também funciona; o falso é inofensivo
        pass
    salvar_certificado_local(pfx, SENHA, uf_autor="35", ambiente=2)


# --------------------------------------------------------------------------- 404


class TestTratamentoDo404:
    @pytest.mark.parametrize(
        "resp",
        [
            _Resp(404, ""),
            _Resp(404, "<html><body>Not Found</body></html>"),
            _Resp(404, {"foo": "bar"}),
            _Resp(404, {"StatusProcessamento": "REJEICAO"}),
            _Resp(404, {"Erros": [{"Codigo": "E2240", "Descricao": "outra coisa"}]}),
            _Resp(404, "[]"),
        ],
        ids=["vazio", "html", "json-outro", "json-status-outro", "json-codigo-outro", "lista"],
    )
    def test_404_inesperado_vira_erro_de_endereco_com_a_url_sem_parametros(self, pfx, resp):
        with pytest.raises(ErroADN) as exc:
            _cliente(pfx, _Sessao(resp)).consultar_nsu(0)
        erro = exc.value
        assert erro.categoria == "ENDERECO" and erro.http_status == 404
        assert erro.retry_apos_segundos is None
        msg = str(erro)
        assert "NFE_ADN_URL_BASE" in msg and "URL base pode estar incorreta" in msg
        assert "https://adn.producaorestrita.nfse.gov.br/contribuintes/DFe/0" in msg
        assert "cnpjConsulta" not in msg and CNPJ_DEMO not in msg and "?" not in msg

    def test_404_json_esperado_por_status(self, pfx):
        resp = _cliente(pfx, _Sessao(_Resp(404, CORPO_FIM))).consultar_nsu(0)
        assert resp.status_processamento == "NENHUM_DOCUMENTO_LOCALIZADO"

    @pytest.mark.parametrize("codigo", ["E2220", "E2230"])
    def test_404_json_esperado_por_codigo_de_erro(self, pfx, codigo):
        corpo = {"Erros": [{"Codigo": codigo, "Descricao": "Nenhum documento localizado"}]}
        resp = _cliente(pfx, _Sessao(_Resp(404, corpo))).consultar_nsu(3)
        assert resp.status_processamento == "NENHUM_DOCUMENTO_LOCALIZADO"

    def test_eh_resposta_fila_zerada(self):
        assert eh_resposta_fila_zerada('{"statusProcessamento": "nenhum_documento_localizado"}')
        assert not eh_resposta_fila_zerada(b"")
        assert not eh_resposta_fila_zerada("<html/>")
        assert not eh_resposta_fila_zerada("{}")

    def test_sync_encerra_como_erro_e_nunca_como_fila_zerada(self, pfx, tmp_path):
        repo = RepositorioDistDFeSQLite(tmp_path / "e.db")
        cliente = _cliente(pfx, _Sessao(_Resp(404, "<html>nao existe</html>")))
        r = _sincronizar(cliente.consultar_nsu, repo)
        assert r.encerrado_por == "ERRO"
        assert "NFE_ADN_URL_BASE" in r.mensagem
        cursor = repo.obter_cursor_adn(2)
        assert cursor.ultimo_nsu == 0 and cursor.bloqueado_ate is None
        log = repo.listar_logs_sync()[0]
        assert log.origem == "NFSE" and log.cstat == "ENDERECO"

    def test_cli_com_404_inesperado_sai_com_erro(
        self, certificado_cadastrado, tmp_path, monkeypatch, capsys
    ):
        _cli_com_sessao(monkeypatch, _Sessao(_Resp(404, "")))
        codigo = cli.main(["sincronizar-adn", "--json", "--db", str(tmp_path / "c.db")])
        saida = json.loads(capsys.readouterr().out)
        assert codigo == 1 and saida["encerrado_por"] == "ERRO"


# --------------------------------------------------------------------------- URL base


class TestUrlBase:
    def test_constantes_por_ambiente(self):
        assert resolver_url_base(1).startswith("https://adn.nfse.gov.br")
        assert resolver_url_base(2).startswith("https://adn.producaorestrita.")

    def test_variavel_de_ambiente_sobrepoe_e_remove_barra_final(self, monkeypatch):
        monkeypatch.setenv("NFE_ADN_URL_BASE", "https://exemplo.teste/api/")
        assert resolver_url_base(2) == "https://exemplo.teste/api"

    def test_argumento_vence_a_variavel(self, monkeypatch):
        monkeypatch.setenv("NFE_ADN_URL_BASE", "https://da-env.teste")
        assert resolver_url_base(2, "https://do-arg.teste") == "https://do-arg.teste"

    @pytest.mark.parametrize("ruim", ["http://inseguro.teste", "adn.teste", "https://", "ftp://x"])
    def test_exige_https(self, ruim):
        with pytest.raises(ValueError, match="https://"):
            resolver_url_base(2, ruim)

    def test_cliente_usa_a_url_configurada_na_requisicao(self, pfx, monkeypatch):
        monkeypatch.setenv("NFE_ADN_URL_BASE", "https://exemplo.teste/nfse")
        sessao = _Sessao(_Resp(404, CORPO_FIM))
        _cliente(pfx, sessao).consultar_nsu(4)
        assert sessao.chamadas[0]["url"] == "https://exemplo.teste/nfse/DFe/4"

    def test_cliente_com_url_invalida_falha_ao_criar(self, pfx):
        with pytest.raises(ValueError, match="https://"):
            _cliente(pfx, _Sessao(), base_url="http://sem-tls.teste")

    def test_cli_rejeita_url_base_invalida(self, certificado_cadastrado, tmp_path, capsys):
        codigo = cli.main(
            ["sincronizar-adn", "--url-base", "http://x.teste", "--db", str(tmp_path / "u.db")]
        )
        assert codigo == 1 and "https://" in capsys.readouterr().err

    def test_cli_url_base_chega_na_requisicao(self, certificado_cadastrado, tmp_path, monkeypatch):
        sessao = _Sessao(_Resp(404, CORPO_FIM))
        _cli_com_sessao(monkeypatch, sessao)
        cli.main(
            ["sincronizar-adn", "--url-base", "https://alt.teste/x", "--db", str(tmp_path / "u.db")]
        )
        assert sessao.chamadas[0]["url"] == "https://alt.teste/x/DFe/0"


# --------------------------------------------------------------------------- diagnóstico


def test_mascara_mantem_dois_primeiros_e_dois_ultimos():
    assert mascarar_documento("11222333000181") == "11**********81"
    assert mascarar_documento("abc") == "***"


class TestDiagnostico:
    def test_200_conta_itens_por_tipo_sem_expor_xml(self, pfx):
        marcador = base64.b64encode(b"CONTEUDO-XML-SECRETO").decode()
        corpo = {
            "TipoConsulta": "CnpjBase",
            "StatusProcessamento": "DOCUMENTOS_LOCALIZADOS",
            "LoteDFe": [
                {
                    "NSU": 1,
                    "ChaveAcesso": "9" * 50,
                    "TipoDocumento": "NFSE",
                    "ArquivoXml": marcador,
                },
                {
                    "NSU": 2,
                    "ChaveAcesso": "9" * 50,
                    "TipoDocumento": "NFSE",
                    "ArquivoXml": marcador,
                },
                {
                    "NSU": 3,
                    "ChaveAcesso": "9" * 50,
                    "TipoDocumento": "EVENTO",
                    "ArquivoXml": marcador,
                },
                {"NSU": 4, "TipoDocumento": "DPS", "ArquivoXml": marcador},
            ],
            "Alertas": [{"Codigo": "A1", "Descricao": "aviso de teste"}],
        }
        sessao = _Sessao(_Resp(200, corpo))
        diag = diagnosticar_adn(_cliente(pfx, sessao))
        assert diag.sucesso and diag.http_status == 200 and diag.conexao_tls == "ok"
        assert diag.itens_por_tipo_documento == {"NFSE": 2, "EVENTO": 1, "DPS": 1}
        assert diag.tipo_consulta == "CnpjBase"
        assert diag.alertas[0].codigo == "A1"
        assert diag.cnpj_consulta_mascarado == "11**********81"
        assert len(sessao.chamadas) == 1  # uma única consulta
        assert sessao.chamadas[0]["url"].endswith("/DFe/0")
        assert sessao.chamadas[0]["params"]["cnpjConsulta"] == CNPJ_DEMO
        texto = diag.model_dump_json()
        assert marcador not in texto and "CONTEUDO" not in texto and CNPJ_DEMO not in texto

    def test_404_esperado_e_sucesso(self, pfx):
        diag = diagnosticar_adn(_cliente(pfx, _Sessao(_Resp(404, CORPO_FIM))))
        assert diag.sucesso and diag.status_processamento == "NENHUM_DOCUMENTO_LOCALIZADO"

    def test_404_inesperado_aponta_a_url_base(self, pfx):
        diag = diagnosticar_adn(_cliente(pfx, _Sessao(_Resp(404, "<html/>"))))
        assert not diag.sucesso and diag.categoria_erro == "ENDERECO"
        assert "NFE_ADN_URL_BASE" in diag.interpretacao and diag.http_status == 404

    def test_400_sem_detalhes_nao_culpa_o_certificado(self, pfx):
        corpo = {
            "TipoConsulta": "InscricaoCertificado",
            "StatusProcessamento": "REJEICAO",
            "Erros": [],
        }
        diag = diagnosticar_adn(_cliente(pfx, _Sessao(_Resp(400, corpo))))
        assert diag.categoria_erro == "REJEICAO" and diag.erros == []
        assert diag.tipo_consulta == "InscricaoCertificado"
        assert "sem detalhes" in diag.interpretacao and "não indica" in diag.interpretacao

    def test_400_com_codigos_mascara_documentos_na_descricao(self, pfx):
        corpo = {
            "StatusProcessamento": "REJEICAO",
            "Erros": [
                {"Codigo": "E2213", "Descricao": "expirado"},
                {
                    "Codigo": "E2245",
                    "Descricao": "Não há procuração da inscrição 99888777000166 para 12345678909",
                },
            ],
        }
        diag = diagnosticar_adn(_cliente(pfx, _Sessao(_Resp(400, corpo))))
        assert [e.codigo for e in diag.erros] == ["E2213", "E2245"]
        texto = diag.model_dump_json()
        assert "99888777000166" not in texto and "12345678909" not in texto
        assert "99**********66" in texto and "12*******09" in texto

    @pytest.mark.parametrize(
        ("status", "categoria"),
        [(429, "LIMITE"), (503, "INDISPONIVEL"), (403, "ACESSO"), (418, "RESPOSTA_INVALIDA")],
    )
    def test_outros_status(self, pfx, status, categoria):
        diag = diagnosticar_adn(_cliente(pfx, _Sessao(_Resp(status, ""))))
        assert diag.http_status == status and diag.categoria_erro == categoria and not diag.sucesso

    def test_falha_de_tls_e_de_conexao(self, pfx):
        tls = diagnosticar_adn(_cliente(pfx, _Sessao(requests.exceptions.SSLError("x"))))
        assert tls.conexao_tls.startswith("falha:") and "TLS" in tls.conexao_tls
        assert tls.http_status is None and not tls.sucesso
        rede = diagnosticar_adn(_cliente(pfx, _Sessao(requests.ConnectionError("y"))))
        assert rede.conexao_tls.startswith("falha:") and rede.categoria_erro == "CONEXAO"

    def test_validade_do_certificado(self, pfx):
        diag = diagnosticar_adn(_cliente(pfx, _Sessao(_Resp(404, CORPO_FIM))))
        assert diag.certificado_valido_de < diag.certificado_valido_ate
        assert diag.certificado_vencido is False


class TestDiagnosticoNaCli:
    def _preparar_banco(self, tmp_path) -> tuple[Path, RepositorioDistDFeSQLite]:
        caminho = tmp_path / "real_simulado.db"
        repo = RepositorioDistDFeSQLite(caminho)
        sincronizar_adn(criar_consultor_demo(2), repo, documento_alvo=CNPJ_DEMO, espera_segundos=0)
        return caminho, repo

    def test_nao_grava_no_banco_nem_altera_o_cursor_nem_imprime_segredos(
        self, certificado_cadastrado, pfx, tmp_path, monkeypatch, capsys
    ):
        caminho, repo = self._preparar_banco(tmp_path)
        hash_antes = hashlib.sha256(caminho.read_bytes()).hexdigest()
        cursor_antes = repo.obter_cursor_adn(2)
        notas_antes = len(repo.listar_notas_recebidas())

        corpo = {
            "TipoConsulta": "CnpjBase",
            "StatusProcessamento": "DOCUMENTOS_LOCALIZADOS",
            "LoteDFe": [
                {
                    "NSU": 99,
                    "ChaveAcesso": "8" * 50,
                    "TipoDocumento": "NFSE",
                    "ArquivoXml": base64.b64encode(b"<?xml SEGREDO").decode(),
                },
            ],
        }
        sessao = _Sessao(_Resp(200, corpo))
        _cli_com_sessao(monkeypatch, sessao)
        codigo = cli.main(["sincronizar-adn", "--diagnostico", "--db", str(caminho)])
        saida = capsys.readouterr()

        assert codigo == 0 and len(sessao.chamadas) == 1
        assert hashlib.sha256(caminho.read_bytes()).hexdigest() == hash_antes
        assert repo.obter_cursor_adn(2) == cursor_antes
        assert len(repo.listar_notas_recebidas()) == notas_antes
        texto = saida.out + saida.err
        for proibido in (SENHA, CNPJ_DEMO, "SEGREDO", "<?xml", base64.b64encode(pfx)[:40].decode()):
            assert proibido not in texto
        assert "11**********81" in saida.out
        assert "Itens NFSE" in saida.out and "Conexão TLS" in saida.out

    def test_nao_cria_banco_inexistente(self, certificado_cadastrado, tmp_path, monkeypatch):
        _cli_com_sessao(monkeypatch, _Sessao(_Resp(404, CORPO_FIM)))
        alvo = tmp_path / "nao_deve_existir" / "x.db"
        assert cli.main(["sincronizar-adn", "--diagnostico", "--db", str(alvo)]) == 0
        assert not alvo.exists() and not alvo.parent.exists()

    def test_json_completo(self, certificado_cadastrado, tmp_path, monkeypatch, capsys):
        _cli_com_sessao(
            monkeypatch, _Sessao(_Resp(400, {"StatusProcessamento": "REJEICAO", "Erros": []}))
        )
        codigo = cli.main(
            [
                "sincronizar-adn",
                "--diagnostico",
                "--json",
                "--ambiente",
                "1",
                "--url-base",
                "https://alt.teste/x",
            ]
        )
        d = json.loads(capsys.readouterr().out)
        assert codigo == 1
        assert d["ambiente"] == 1 and d["url_base"] == "https://alt.teste/x"
        assert d["http_status"] == 400 and d["status_processamento"] == "REJEICAO"
        assert d["cnpj_consulta_mascarado"] == "11**********81"
        assert d["conexao_tls"] == "ok" and d["erros"] == []
        assert d["certificado_valido_de"] and d["certificado_valido_ate"]
        assert CNPJ_DEMO not in json.dumps(d) and SENHA not in json.dumps(d)

    def test_sem_certificado_falha_sem_consulta(self, tmp_path, capsys):
        assert cli.main(["sincronizar-adn", "--diagnostico"]) == 1
        assert "Erro:" in capsys.readouterr().err
