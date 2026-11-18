"""Testes E2E (100% offline) da cadeia `NFeDistribuicaoDFe` (`138`, `137` e `656` — `Task 18`)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient

from api import app, cli
from api.app import (
    definir_consultor_dist_dfe,
    definir_repositorio_recebidas,
    obter_repositorio_lotes,
)
from parsers.xml_seguro import parse_xml_seguro, texto
from sefaz.cert_a1 import OID_ICP_BRASIL_CNPJ
from sefaz.controle_nsu import ErroBloqueioSEFAZ
from sefaz.dist_dfe import ClienteNFeDistribuicaoDFe
from sefaz.storage import RepositorioDistDFeSQLite, sincronizar_dist_dfe

PASTA_FIXTURES = Path(__file__).resolve().parent / "fixtures"
PASTA_SOAP_FIXTURES = PASTA_FIXTURES / "sefaz"

CHAVE_NFE_1001 = "35260311222333000181550010000010011100010010"
CHAVE_NFE_ALNUM = "35260312ABC34501DE35550010000010031100010032"
CHAVE_RESUMO_1007 = "35260311222333000181550010000010071100010073"

ARQUIVOS_SOAP_ESPERADOS = (
    "soap_ret_138_lote1.xml",
    "soap_ret_138_lote2.xml",
    "soap_ret_137_vazio.xml",
    "soap_ret_656_consumo_indevido.xml",
)


def _encode_asn1_utf8(valor: str) -> bytes:
    dados = valor.encode("utf-8")
    return bytes([0x0C, len(dados)]) + dados


def _gerar_pfx_e2e(cnpj: str = "22333444000181", senha: str = "senha-e2e") -> bytes:
    chave_privada = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    agora = datetime.now(UTC)

    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "BR"),
            x509.NameAttribute(NameOID.COMMON_NAME, "COMERCIO BETA DISTRIBUIDORA LTDA"),
        ]
    )
    issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "BR"),
            x509.NameAttribute(NameOID.COMMON_NAME, "AC ICP-BRASIL HOMOLOGACAO"),
        ]
    )
    other_name = x509.OtherName(
        x509.ObjectIdentifier(OID_ICP_BRASIL_CNPJ),
        _encode_asn1_utf8(cnpj),
    )

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(chave_privada.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(agora - timedelta(days=2))
        .not_valid_after(agora + timedelta(days=90))
        .add_extension(x509.SubjectAlternativeName([other_name]), critical=False)
        .sign(private_key=chave_privada, algorithm=hashes.SHA256())
    )

    return pkcs12.serialize_key_and_certificates(
        name=b"cert-e2e",
        key=chave_privada,
        cert=cert,
        cas=None,
        encryption_algorithm=serialization.BestAvailableEncryption(senha.encode("utf-8")),
    )


class FakeHTTPResponse:
    """Resposta HTTP sintética para simulação do WebService SOAP da SEFAZ."""

    def __init__(self, xml_bytes: bytes, status_code: int = 200) -> None:
        self.status_code = status_code
        self.content = xml_bytes
        self.text = xml_bytes.decode("utf-8")


class SessaoHTTPMockSEFAZ:
    """Sessão HTTP fake que inspeciona o `<ultNSU>` do envelope SOAP enviado e retorna fixtures."""

    def __init__(self, mapa_respostas_por_nsu: dict[str, Path]) -> None:
        self._mapa = mapa_respostas_por_nsu
        self.chamadas: list[dict[str, Any]] = []

    def post(
        self,
        url: str,
        *,
        data: bytes,
        headers: dict[str, str],
        timeout: float,
    ) -> FakeHTTPResponse:
        raiz = parse_xml_seguro(data)
        ult_nsu_enviado = texto(raiz, "ultNSU")
        cnpj_enviado = texto(raiz, "CNPJ")
        self.chamadas.append(
            {
                "url": url,
                "ult_nsu": ult_nsu_enviado,
                "cnpj": cnpj_enviado,
                "headers": headers,
                "timeout": timeout,
            }
        )
        caminho_fixture = self._mapa.get(ult_nsu_enviado)
        if caminho_fixture is None:
            raise AssertionError(
                f"Nenhuma fixture SOAP configurada para ultNSU={ult_nsu_enviado!r}"
            )
        return FakeHTTPResponse(caminho_fixture.read_bytes())


class TestSefazE2ESimulacao:
    """Testes E2E cobrindo cliente SOAP -> descompactador -> parser -> SQLite -> API/CLI."""

    @pytest.mark.parametrize("nome_arquivo", ARQUIVOS_SOAP_ESPERADOS)
    def test_fixtures_soap_existem_e_sao_xmls_validos(self, nome_arquivo: str) -> None:
        caminho = PASTA_SOAP_FIXTURES / nome_arquivo
        assert caminho.is_file(), f"Fixture SOAP ausente: {caminho}"
        raiz = parse_xml_seguro(caminho.read_bytes())
        assert texto(raiz, "cStat") in ("138", "137", "656")

    def test_cenario_e2e_1_e_2_paginacao_138_depois_137_sqlite_e_api_web(
        self,
        tmp_path: Path,
    ) -> None:
        repo = RepositorioDistDFeSQLite(tmp_path / "e2e_sefaz.db")
        definir_repositorio_recebidas(repo)
        obter_repositorio_lotes().limpar()

        sessao_mock = SessaoHTTPMockSEFAZ(
            {
                "000000000000000": PASTA_SOAP_FIXTURES / "soap_ret_138_lote1.xml",
                "000000000000002": PASTA_SOAP_FIXTURES / "soap_ret_138_lote2.xml",
                "000000000000004": PASTA_SOAP_FIXTURES / "soap_ret_137_vazio.xml",
            }
        )
        pfx = _gerar_pfx_e2e(cnpj="22333444000181", senha="senha-e2e")
        cliente_soap = ClienteNFeDistribuicaoDFe(
            pfx_bytes=pfx,
            senha="senha-e2e",
            ambiente=2,
            sessao_http=sessao_mock,
        )
        definir_consultor_dist_dfe(lambda ult_nsu: cliente_soap.consultar_dist_nsu(ult_nsu=ult_nsu))
        client = TestClient(app)

        try:
            # --- CENÁRIO E2E 1: Rodada inicial paginada (0 -> 2 -> 4 == maxNSU) ---
            resp_sync_1 = client.post("/api/recebidas/sincronizar")
            assert resp_sync_1.status_code == 200
            resumo_1 = resp_sync_1.json()

            # Deve ter feito exatamente 2 chamadas SOAP e parado ao atingir ultNSU == maxNSU (4)
            assert len(sessao_mock.chamadas) == 2
            assert [c["ult_nsu"] for c in sessao_mock.chamadas] == [
                "000000000000000",
                "000000000000002",
            ]
            assert all(c["cnpj"] == "22333444000181" for c in sessao_mock.chamadas)

            assert resumo_1["paginas_consultadas"] == 2
            assert resumo_1["documentos_processados"] == 4
            assert resumo_1["novas_notas"] == 3
            assert resumo_1["eventos_registrados"] == 1
            assert resumo_1["fila_zerada"] is True
            assert resumo_1["bloqueado_656"] is False
            assert resumo_1["cursor"]["ultimo_nsu"] == "000000000000004"
            assert resumo_1["cursor"]["max_nsu"] == "000000000000004"

            # Verifica estado persistido via GET /api/recebidas
            resp_lista = client.get("/api/recebidas")
            assert resp_lista.status_code == 200
            dados_lista = resp_lista.json()
            assert dados_lista["total"] == 3

            mapa_notas = {n["chave"]: n for n in dados_lista["notas"]}
            # 1) Resumo (NSU 1): exige manifestação
            nota_resumo = mapa_notas[CHAVE_RESUMO_1007]
            assert nota_resumo["categoria_schema"] == "RESUMO_NFE"
            assert nota_resumo["situacao"] == "RESUMO"
            assert nota_resumo["manifestacao_necessaria"] is True

            # 2) Nota completa 1001 (NSU 2) que recebeu evento 110111 no NSU 4 -> CANCELADA
            nota_1001 = mapa_notas[CHAVE_NFE_1001]
            assert nota_1001["categoria_schema"] == "NFE_COMPLETA"
            assert nota_1001["situacao"] == "CANCELADA"
            assert nota_1001["manifestacao_necessaria"] is False

            # 3) Nota completa CNPJ alfanumérico (NSU 3) -> AUTORIZADA
            nota_alnum = mapa_notas[CHAVE_NFE_ALNUM]
            assert nota_alnum["categoria_schema"] == "NFE_COMPLETA"
            assert nota_alnum["situacao"] == "AUTORIZADA"
            assert nota_alnum["emitente_documento"] == "12ABC34501DE35"

            # Verifica renderização do DANFE a partir do XML descompactado no SQLite
            resp_danfe = client.get(f"/recebidas/{CHAVE_NFE_ALNUM}/espelho")
            assert resp_danfe.status_code == 200
            assert "DANFE" in resp_danfe.text
            assert "12.ABC.345/01DE-35" in resp_danfe.text

            # --- CENÁRIO E2E 2: Rodada seguinte (ultNSU=4) recebe cStat 137 (vazio) ---
            resp_sync_2 = client.post("/api/recebidas/sincronizar")
            assert resp_sync_2.status_code == 200
            resumo_2 = resp_sync_2.json()

            assert len(sessao_mock.chamadas) == 3
            assert sessao_mock.chamadas[-1]["ult_nsu"] == "000000000000004"
            assert resumo_2["paginas_consultadas"] == 1
            assert resumo_2["documentos_processados"] == 0
            assert resumo_2["novas_notas"] == 0
            assert resumo_2["cursor"]["ultimo_cstat"] == "137"
            assert resumo_2["fila_zerada"] is True

            # Base permanece intacta com 3 notas e 2 registros no sync_log
            assert client.get("/api/recebidas").json()["total"] == 3
            logs = repo.listar_logs_sync()
            assert len(logs) == 2
            assert [log.cstat for log in logs] == ["137", "138"]
        finally:
            definir_consultor_dist_dfe(None)

    def test_cenario_e2e_3_cstat_656_grava_trava_1h_e_impede_nova_chamada_soap(
        self,
        tmp_path: Path,
    ) -> None:
        repo = RepositorioDistDFeSQLite(tmp_path / "e2e_656.db")
        definir_repositorio_recebidas(repo)

        sessao_mock = SessaoHTTPMockSEFAZ(
            {
                "000000000000000": PASTA_SOAP_FIXTURES / "soap_ret_656_consumo_indevido.xml",
            }
        )
        pfx = _gerar_pfx_e2e(cnpj="22333444000181", senha="senha-e2e")
        cliente_soap = ClienteNFeDistribuicaoDFe(
            pfx_bytes=pfx,
            senha="senha-e2e",
            ambiente=2,
            sessao_http=sessao_mock,
        )
        definir_consultor_dist_dfe(lambda ult_nsu: cliente_soap.consultar_dist_nsu(ult_nsu=ult_nsu))
        client = TestClient(app)

        try:
            # 1ª chamada: SEFAZ devolve cStat 656 com ultNSU=10 e maxNSU=15
            resp_1 = client.post("/api/recebidas/sincronizar")
            assert resp_1.status_code == 200
            dados_1 = resp_1.json()
            assert dados_1["bloqueado_656"] is True
            assert dados_1["cursor"]["ultimo_cstat"] == "656"
            assert dados_1["cursor"]["ultimo_nsu"] == "000000000000010"
            assert dados_1["cursor"]["max_nsu"] == "000000000000015"
            assert dados_1["cursor"]["bloqueado_ate"] is not None
            assert len(sessao_mock.chamadas) == 1

            # Verifica que o cursor no SQLite está bloqueado por ~3600s
            resp_status = client.get("/api/recebidas/status")
            assert resp_status.status_code == 200
            st = resp_status.json()
            assert st["bloqueado"] is True
            assert 3500 <= st["segundos_bloqueio_restantes"] <= 3600

            # 2ª tentativa via API antes de 1h: deve retornar HTTP 429 SEM tocar na sessao_mock!
            resp_bloqueada = client.post("/api/recebidas/sincronizar")
            assert resp_bloqueada.status_code == 429
            assert "656" in resp_bloqueada.json()["detail"]
            assert len(sessao_mock.chamadas) == 1  # Nenhuma requisição HTTP adicional!

            # 3ª tentativa direta via sincronizar_dist_dfe antes de 1h: levanta ErroBloqueioSEFAZ
            with pytest.raises(ErroBloqueioSEFAZ):
                sincronizar_dist_dfe(
                    consultar_fn=lambda ult_nsu: cliente_soap.consultar_dist_nsu(ult_nsu=ult_nsu),
                    repo=repo,
                    ambiente=2,
                )
            assert len(sessao_mock.chamadas) == 1
        finally:
            definir_consultor_dist_dfe(None)

    def test_cenario_e2e_4_cli_sincronizar_sefaz_demo_json(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        caminho_db = tmp_path / "cli_recebidas.db"

        codigo = cli.main(
            [
                "sincronizar-sefaz",
                "--db",
                str(caminho_db),
                "--demo",
                "--json",
            ]
        )
        assert codigo == 0

        saida = capsys.readouterr().out
        dados = json.loads(saida)
        assert dados["paginas_consultadas"] == 2
        assert dados["documentos_processados"] == 4
        assert dados["novas_notas"] == 3
        assert dados["eventos_registrados"] == 1
        assert dados["cursor"]["ultimo_nsu"] == "000000000000004"

        repo = RepositorioDistDFeSQLite(caminho_db)
        notas = repo.listar_notas_recebidas()
        assert len(notas) == 3
