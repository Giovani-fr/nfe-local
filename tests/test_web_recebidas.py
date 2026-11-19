"""Testes da API e interface Web para Notas Recebidas via `NFeDistribuicaoDFe` (`Task 17`)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient

from api import app
from api.app import (
    definir_consultor_dist_dfe,
    definir_repositorio_recebidas,
    obter_repositorio_lotes,
)
from sefaz.cert_a1 import OID_ICP_BRASIL_CNPJ
from sefaz.controle_nsu import (
    CSTAT_CONSUMO_INDEVIDO,
    CSTAT_DOCS_LOCALIZADOS,
    EstadoCursorNSU,
)
from sefaz.descompactador import compactar_xml_para_doc_zip_b64
from sefaz.dist_dfe import ErroComunicacaoSEFAZ, ItemDocZip, RespostaDistDFe
from sefaz.storage import RepositorioDistDFeSQLite

PASTA_FIXTURES = Path(__file__).resolve().parent / "fixtures"
CHAVE_NFE_1001 = "35260311222333000181550010000010011100010010"
CHAVE_NFE_ALNUM = "35260312ABC34501DE35550010000010031100010032"
CHAVE_RESUMO_1007 = "35260311222333000181550010000010071100010073"


def _encode_asn1_utf8_string(valor: str) -> bytes:
    dados = valor.encode("utf-8")
    return bytes([0x0C, len(dados)]) + dados


def _gerar_pfx_teste(
    *,
    common_name: str = "EMPRESA EXEMPLO SINTETICA LTDA",
    cnpj: str = "11222333000181",
    senha: str = "senha-segura",
    dias_fim: int = 120,
) -> bytes:
    chave_privada = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    agora = datetime.now(UTC)

    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "BR"),
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
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
        _encode_asn1_utf8_string(cnpj),
    )

    certificado = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(chave_privada.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(agora - timedelta(days=5))
        .not_valid_after(agora + timedelta(days=dias_fim))
        .add_extension(x509.SubjectAlternativeName([other_name]), critical=False)
        .sign(private_key=chave_privada, algorithm=hashes.SHA256())
    )

    return pkcs12.serialize_key_and_certificates(
        name=b"cert-a1-teste",
        key=chave_privada,
        cert=certificado,
        cas=None,
        encryption_algorithm=serialization.BestAvailableEncryption(senha.encode("utf-8")),
    )


@pytest.fixture()
def repo_sqlite(tmp_path: Path) -> RepositorioDistDFeSQLite:
    """Injeta um repositório SQLite isolado em `tmp_path` para cada teste."""
    repo = RepositorioDistDFeSQLite(tmp_path / "recebidas_teste.db")
    definir_repositorio_recebidas(repo)
    definir_consultor_dist_dfe(None)
    obter_repositorio_lotes().limpar()
    yield repo
    definir_consultor_dist_dfe(None)


@pytest.fixture()
def client(repo_sqlite: RepositorioDistDFeSQLite) -> TestClient:
    """Retorna `TestClient` configurado com o banco SQLite temporário."""
    return TestClient(app)


class TestRotasRecebidasSEFAZ:
    """Testes das rotas `/api/recebidas/*` e `/recebidas/{chave}/*`."""

    def test_status_recebidas_sem_certificado_e_com_certificado_e_cursor(
        self,
        client: TestClient,
        repo_sqlite: RepositorioDistDFeSQLite,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("NFE_CERT_PATH", raising=False)
        monkeypatch.delenv("NFE_CERT_SENHA", raising=False)

        resp_sem_cert = client.get("/api/recebidas/status")
        assert resp_sem_cert.status_code == 200
        dados_sem = resp_sem_cert.json()
        assert dados_sem["certificado_configurado"] is False
        assert dados_sem["certificado"] is None
        assert "NFE_CERT_PATH" in dados_sem["erro_certificado"]
        assert dados_sem["bloqueado"] is False
        assert dados_sem["segundos_bloqueio_restantes"] == 0
        assert dados_sem["cursor"]["ultimo_nsu"] == "000000000000000"

        # Configura certificado A1 temporário e simula cursor bloqueado por cStat 656
        caminho_pfx = tmp_path / "empresa_a1.pfx"
        caminho_pfx.write_bytes(_gerar_pfx_teste(cnpj="11222333000181", senha="123"))
        monkeypatch.setenv("NFE_CERT_PATH", str(caminho_pfx))
        monkeypatch.setenv("NFE_CERT_SENHA", "123")

        agora = datetime.now(UTC)
        repo_sqlite.salvar_cursor(
            EstadoCursorNSU(
                ambiente=2,
                ultimo_nsu="000000000000045",
                max_nsu="000000000000050",
                ultima_consulta=agora,
                bloqueado_ate=agora + timedelta(minutes=30),
                ultimo_cstat=CSTAT_CONSUMO_INDEVIDO,
                ultimo_motivo="Rejeicao: Consumo Indevido",
            )
        )
        repo_sqlite.registrar_log_sync(
            ambiente=2,
            cstat="656",
            motivo="Rejeicao: Consumo Indevido",
            paginas=1,
            novos_docs=0,
            ultimo_nsu="000000000000045",
            ts=agora,
        )

        resp_com_cert = client.get("/api/recebidas/status")
        assert resp_com_cert.status_code == 200
        dados_com = resp_com_cert.json()
        assert dados_com["certificado_configurado"] is True
        assert dados_com["erro_certificado"] is None
        assert dados_com["certificado"]["documento"] == "11222333000181"
        assert dados_com["certificado"]["vencido"] is False
        assert dados_com["cursor"]["ultimo_nsu"] == "000000000000045"
        assert dados_com["bloqueado"] is True
        assert dados_com["segundos_bloqueio_restantes"] > 1000
        assert len(dados_com["ultimos_logs"]) == 1
        assert dados_com["ultimos_logs"][0]["cstat"] == "656"

    def test_demo_recebidas_e_filtros_por_emitente_periodo_valor_categoria_e_situacao(
        self,
        client: TestClient,
    ) -> None:
        resp_demo = client.post("/api/recebidas/demo")
        assert resp_demo.status_code == 200
        resumo_demo = resp_demo.json()
        assert resumo_demo["novas_notas"] >= 3
        assert resumo_demo["eventos_registrados"] >= 1

        # Listagem geral sem filtros
        resp_todas = client.get("/api/recebidas")
        assert resp_todas.status_code == 200
        corpo_todas = resp_todas.json()
        assert corpo_todas["total"] == 9
        assert Decimal(str(corpo_todas["valor_total_filtrado"])) > Decimal("0")
        chaves_todas = {n["chave"] for n in corpo_todas["notas"]}
        assert CHAVE_NFE_1001 in chaves_todas
        assert CHAVE_NFE_ALNUM in chaves_todas
        assert CHAVE_RESUMO_1007 in chaves_todas

        # Filtro por emitente (CNPJ alfanumérico)
        resp_emit = client.get("/api/recebidas", params={"emitente": "12.ABC.345/01DE-35"})
        assert resp_emit.status_code == 200
        notas_emit = resp_emit.json()["notas"]
        assert len(notas_emit) == 1
        assert notas_emit[0]["chave"] == CHAVE_NFE_ALNUM

        # Filtro por período (data_inicio / data_fim)
        resp_periodo = client.get(
            "/api/recebidas",
            params={"data_inicio": "2026-03-15", "data_fim": "2026-03-20"},
        )
        assert resp_periodo.status_code == 200
        notas_periodo = resp_periodo.json()["notas"]
        assert [n["chave"] for n in notas_periodo] == [CHAVE_RESUMO_1007]

        # Filtro por faixa de valor (valor_min / valor_max)
        resp_valor = client.get(
            "/api/recebidas",
            params={"valor_min": "2000.00", "valor_max": "2600.00", "origem": "SEFAZ"},
        )
        assert resp_valor.status_code == 200
        notas_valor = resp_valor.json()["notas"]
        assert [n["chave"] for n in notas_valor] == [CHAVE_RESUMO_1007]

        # Filtro por categoria (RESUMO_NFE vs NFE_COMPLETA)
        resp_cat = client.get("/api/recebidas", params={"categoria": "RESUMO_NFE"})
        assert resp_cat.status_code == 200
        notas_cat = resp_cat.json()["notas"]
        assert len(notas_cat) == 1
        assert notas_cat[0]["chave"] == CHAVE_RESUMO_1007
        assert notas_cat[0]["manifestacao_necessaria"] is True

        # Filtro por situação (CANCELADA pela fixture de evento 110111 vs AUTORIZADA)
        resp_cancel = client.get(
            "/api/recebidas", params={"situacao": "CANCELADA", "origem": "SEFAZ"}
        )
        assert resp_cancel.status_code == 200
        notas_cancel = resp_cancel.json()["notas"]
        assert [n["chave"] for n in notas_cancel] == [CHAVE_NFE_1001]

        resp_autorizada = client.get(
            "/api/recebidas", params={"situacao": "AUTORIZADA", "origem": "SEFAZ"}
        )
        assert resp_autorizada.status_code == 200
        notas_aut = resp_autorizada.json()["notas"]
        assert [n["chave"] for n in notas_aut] == [CHAVE_NFE_ALNUM]

    def test_sincronizar_com_consultor_mockado_e_bloqueio_429_cstat_656(
        self,
        client: TestClient,
        repo_sqlite: RepositorioDistDFeSQLite,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # 1. Sem consultor injetado e sem certificado no env -> HTTP 503
        monkeypatch.delenv("NFE_CERT_PATH", raising=False)
        resp_503 = client.post("/api/recebidas/sincronizar")
        assert resp_503.status_code == 503
        assert "NFE_CERT_PATH" in resp_503.json()["detail"]

        # 2. Com consultor mockado retornando 1 nota procNFe -> HTTP 200
        xml_nfe = (PASTA_FIXTURES / "nfe_55_valida.xml").read_bytes()

        def fake_consultar(ult_nsu: str) -> RespostaDistDFe:
            return RespostaDistDFe(
                ambiente=2,
                ver_aplic="1.6.3",
                cstat=CSTAT_DOCS_LOCALIZADOS,
                xmotivo="Documento(s) localizado(s)",
                ult_nsu="000000000000010",
                max_nsu="000000000000010",
                lote_doc_zip=[
                    ItemDocZip(
                        nsu="000000000000010",
                        schema_xml="procNFe_v4.00.xsd",
                        conteudo_base64=compactar_xml_para_doc_zip_b64(xml_nfe),
                    )
                ],
            )

        definir_consultor_dist_dfe(fake_consultar)
        resp_sync = client.post("/api/recebidas/sincronizar")
        assert resp_sync.status_code == 200
        dados_sync = resp_sync.json()
        assert dados_sync["paginas_consultadas"] == 1
        assert dados_sync["novas_notas"] == 1
        assert dados_sync["cursor"]["ultimo_nsu"] == "000000000000010"

        # 3. Quando a SEFAZ levanta ErroComunicacaoSEFAZ -> HTTP 502
        def fake_erro_rede(ult_nsu: str) -> RespostaDistDFe:
            raise ErroComunicacaoSEFAZ("Falha simulada de TLS com a SEFAZ")

        definir_consultor_dist_dfe(fake_erro_rede)
        resp_502 = client.post("/api/recebidas/sincronizar")
        assert resp_502.status_code == 502
        assert "Falha simulada de TLS" in resp_502.json()["detail"]

        # 4. Quando o cursor está bloqueado pelo cStat 656 -> HTTP 429 sem chamar a SEFAZ
        chamou_indevidamente = False

        def consultor_proibido(ult_nsu: str) -> RespostaDistDFe:
            nonlocal chamou_indevidamente
            chamou_indevidamente = True
            raise AssertionError("Não deveria consultar a SEFAZ durante o bloqueio 656")

        definir_consultor_dist_dfe(consultor_proibido)
        agora = datetime.now(UTC)
        repo_sqlite.salvar_cursor(
            EstadoCursorNSU(
                ambiente=2,
                ultimo_nsu="000000000000010",
                max_nsu="000000000000010",
                ultima_consulta=agora,
                bloqueado_ate=agora + timedelta(hours=1),
                ultimo_cstat=CSTAT_CONSUMO_INDEVIDO,
                ultimo_motivo="Rejeicao: Consumo Indevido",
            )
        )

        resp_429 = client.post("/api/recebidas/sincronizar")
        assert resp_429.status_code == 429
        assert "656" in resp_429.json()["detail"]
        assert chamou_indevidamente is False

    def test_espelho_danfe_e_download_xml_de_nota_recebida_no_sqlite(
        self,
        client: TestClient,
    ) -> None:
        client.post("/api/recebidas/demo")

        # Espelho DANFE de nota completa (padrão oficial completo)
        resp_espelho = client.get(f"/recebidas/{CHAVE_NFE_1001}/espelho")
        assert resp_espelho.status_code == 200
        assert "text/html" in resp_espelho.headers["content-type"]
        assert "DANFE" in resp_espelho.text
        assert "11.222.333/0001-81" in resp_espelho.text
        assert "0 - ENTRADA" in resp_espelho.text
        assert "Transportador / Volumes transportados" in resp_espelho.text
        assert "BC ICMS" in resp_espelho.text
        assert "Alíq. ICMS" in resp_espelho.text
        assert f"/recebidas/{CHAVE_NFE_1001}/xml" in resp_espelho.text

        # Download XML descompactado a partir do SQLite
        resp_xml = client.get(f"/recebidas/{CHAVE_NFE_1001}/xml")
        assert resp_xml.status_code == 200
        assert "application/xml" in resp_xml.headers["content-type"]
        assert "attachment" in resp_xml.headers["content-disposition"]
        assert b"<nfeProc" in resp_xml.content

        # Chave inexistente -> 404
        assert (
            client.get(
                "/recebidas/00000000000000000000000000000000000000000000/espelho"
            ).status_code
            == 404
        )
        assert (
            client.get("/recebidas/00000000000000000000000000000000000000000000/xml").status_code
            == 404
        )

    def test_delete_api_recebidas_e_limpar_notas_recebidas_no_storage(
        self,
        client: TestClient,
        repo_sqlite: RepositorioDistDFeSQLite,
    ) -> None:
        # Popula banco com demonstração DistDFe (3 notas + 1 evento + cursor + sync_log)
        resp_demo = client.post("/api/recebidas/demo")
        assert resp_demo.status_code == 200
        assert client.get("/api/recebidas").json()["total"] == 9
        assert repo_sqlite.obter_cursor(ambiente=2).ultimo_nsu != "000000000000000"
        assert len(repo_sqlite.listar_logs_sync()) >= 1

        # Primeiro testa limpar_notas_recebidas(zerar_cursor=False) preservando cursor/logs
        removidas_sem_cursor = repo_sqlite.limpar_notas_recebidas(zerar_cursor=False)
        assert removidas_sem_cursor == 9
        assert repo_sqlite.listar_notas_recebidas() == []
        assert repo_sqlite.obter_cursor(ambiente=2).ultimo_nsu != "000000000000000"
        assert len(repo_sqlite.listar_logs_sync()) >= 1

        # Recarrega demonstração e testa DELETE /api/recebidas (zera notas, eventos, cursor e logs)
        assert client.post("/api/recebidas/demo").status_code == 200
        assert client.get("/api/recebidas").json()["total"] == 9

        resp_del = client.delete("/api/recebidas")
        assert resp_del.status_code == 200
        assert resp_del.json() == {"removidas": 9}

        assert client.get("/api/recebidas").json()["total"] == 0
        status_pos = client.get("/api/recebidas/status").json()
        assert status_pos["cursor"]["ultimo_nsu"] == "000000000000000"
        assert status_pos["ultimos_logs"] == []

    def test_interface_web_contem_abas_e_painel_recebidas_dist_dfe(
        self,
        client: TestClient,
    ) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        html = resp.text
        assert "Analisador de Lotes (Upload)" in html
        assert "Buscar notas (SEFAZ e Portal Nacional)" in html
        assert "Recebidas da SEFAZ (DistDFe)" not in html
        assert ">🏛️ SEFAZ e ADN" not in html
        # nomes antigos de botões não aparecem em nenhum texto da interface
        raiz = Path(__file__).parent.parent
        js = (raiz / "web" / "static" / "app.js").read_text(encoding="utf-8")
        for antigo in (
            "Sincronizar tudo",
            "Sincronizar NF-e",
            "Sincronizar NFS-e",
            "Sincronizar com a SEFAZ",
            "🩺 Diagnóstico do ADN",
            "Recebidas da SEFAZ",
        ):
            assert antigo not in html, antigo
            assert antigo not in js, antigo
        assert "<strong>Buscar tudo</strong>" in html  # passo a passo sem certificado
        for rotulo in (
            "Buscar NF-e de produto (SEFAZ)",
            "Buscar NFS-e de serviço (Portal Nacional)",
            "Buscar tudo",
            "NF-e (produto)",
            "NFS-e (serviço)",
            "NFC-e (consumidor)",
            "Só NF-e",
            "Só NFS-e",
            "O Portal Nacional só traz NFS-e do Padrão Nacional",
            "XMLs ilegíveis (ERRO_XML)",
            "Totais por tipo e por papel",
            "Valor total (autorizadas)",
            "Testar conexão (Portal Nacional)",
            "Diagnóstico do ADN: uma consulta de leitura",
            "Tudo (acervo + SEFAZ + ADN)",
            "Só emitidas",
            "Só tomadas",
            "Serviço (Portal Nacional): até onde já buscamos",
            "Produto (SEFAZ): até onde já buscamos",
        ):
            assert rotulo in html
        for ident in (
            "btn-sync-sefaz",
            "btn-sync-adn",
            "btn-sync-tudo",
            "btn-diag-adn",
            "btn-escopo-tudo",
            "rec-filtro-tipo",
            "rec-filtro-origem",
            "rec-filtro-papel",
            "passo-a-passo-cert",
            "rec-seletor-tipo",
            "seletor-tipo-notas",
            "rec-cartoes-tipo",
            "cartoes-tipo-notas",
            "tbody-quebras",
            "tbody-erros-xml",
            "erros-xml-busca",
            "erros-xml-codigo",
            "kpi-fora-soma",
            'data-tipo-sel=""',
            'data-tipo-sel="NFSE"',
            'data-tipo-sel-rec="NFCE"',
            'data-tipo-rapido="NFE"',
            'data-tipo-rapido="NFSE"',
            "adn-diagnostico",
            'data-papel-rapido="EMITIDA"',
            'data-papel-rapido="RECEBIDA"',
        ):
            assert ident in html
        assert "Critério" in html  # dica do papel (comparação com o CNPJ do certificado)
        assert 'data-sort="origem"' in html
        assert "Carregar Recebidas de Demonstração" in html
        assert "Cadastrar / Trocar Certificado (.pfx)" in html
        assert "form-cadastro-certificado" in html
        assert "Validar e Salvar Certificado" in html
        assert "Zerar Banco Local" in html
        assert "Limpar Recebidas" in html
        assert "btn-resetar-banco-topo" in html
        assert "btn-resetar-banco-sidebar" in html
        assert "btn-limpar-recebidas" in html
        assert "col-actions" in html
        for campo_sort in (
            'data-sort="tipo"',
            'data-sort="papel"',
            'data-sort="destinatario"',
            'data-sort="data_emissao"',
            'data-sort="numero"',
            'data-sort="emitente"',
            'data-sort="valor_total"',
            'data-sort="situacao"',
        ):
            assert campo_sort in html

        resp_css = client.get("/static/styles.css")
        assert resp_css.status_code == 200
        assert "position: sticky" in resp_css.text
        assert "data-table-notas" in resp_css.text
