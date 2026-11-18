"""Testes do cadastro, configuração e remoção visual do Certificado Digital A1 (`Task 21`)."""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
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
    definir_diretorio_certificado,
    definir_repositorio_recebidas,
    obter_repositorio_lotes,
)
from sefaz import (
    NOME_ARQUIVO_CONFIG_FIXO,
    NOME_ARQUIVO_PFX_FIXO,
    NOME_ARQUIVO_SENHA_FIXO,
    OID_ICP_BRASIL_CNPJ,
    RepositorioDistDFeSQLite,
)


def _encode_asn1_utf8_string(valor: str) -> bytes:
    dados = valor.encode("utf-8")
    return bytes([0x0C, len(dados)]) + dados


def _gerar_pfx_sintetico(
    *,
    common_name: str = "EMPRESA EXEMPLO SINTETICA LTDA",
    cnpj: str = "11222333000181",
    senha: str = "senha-cert-123",
    dias_validade: int = 240,
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
            x509.NameAttribute(NameOID.COMMON_NAME, "AC ICP-BRASIL SINTETICA"),
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
        .not_valid_after(agora + timedelta(days=dias_validade))
        .add_extension(x509.SubjectAlternativeName([other_name]), critical=False)
        .sign(private_key=chave_privada, algorithm=hashes.SHA256())
    )

    return pkcs12.serialize_key_and_certificates(
        name=b"cert-sintetico-a1",
        key=chave_privada,
        cert=certificado,
        cas=None,
        encryption_algorithm=serialization.BestAvailableEncryption(senha.encode("utf-8")),
    )


@pytest.fixture()
def cert_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[Path, None, None]:
    """Configura diretório isolado para o certificado A1 e banco SQLite em `tmp_path`."""
    monkeypatch.delenv("NFE_CERT_PATH", raising=False)
    monkeypatch.delenv("NFE_CERT_SENHA", raising=False)
    pasta_cert = tmp_path / "certificado_local"
    repo = RepositorioDistDFeSQLite(tmp_path / "recebidas_cert_api.db")

    definir_diretorio_certificado(pasta_cert)
    definir_repositorio_recebidas(repo)
    definir_consultor_dist_dfe(None)
    obter_repositorio_lotes().limpar()
    try:
        yield pasta_cert
    finally:
        definir_diretorio_certificado(None)
        definir_repositorio_recebidas(None)
        definir_consultor_dist_dfe(None)
        obter_repositorio_lotes().limpar()


@pytest.fixture()
def client(cert_dir: Path) -> TestClient:
    """Retorna `TestClient` configurado com `cert_dir` e SQLite isolados."""
    return TestClient(app)


class TestCadastroVisualCertificadoA1:
    """Valida upload, proteção contra path traversal, troca de ambiente/UF e remoção do `.pfx`."""

    def test_upload_pfx_valido_com_nome_fixo_anti_path_traversal(
        self,
        client: TestClient,
        cert_dir: Path,
    ) -> None:
        pfx_bytes = _gerar_pfx_sintetico(
            common_name="METALURGICA ALFA SINTETICA LTDA",
            cnpj="11222333000181",
            senha="minha-senha-secreta",
            dias_validade=150,
        )

        # Mesmo enviando um filename malicioso com path traversal, deve salvar como certificado.pfx
        resp = client.post(
            "/api/recebidas/certificado",
            files=[
                (
                    "arquivo",
                    ("../../tentativa_traversal_maliciosa.pfx", pfx_bytes, "application/x-pkcs12"),
                )
            ],
            data={
                "senha": "minha-senha-secreta",
                "uf_autor": "41",
                "ambiente": "2",
            },
        )
        assert resp.status_code == 200
        dados = resp.json()

        assert dados["certificado_configurado"] is True
        assert dados["erro_certificado"] is None
        assert dados["uf_autor"] == "41"
        assert dados["ambiente"] == 2
        assert dados["origem_certificado"] == "interface"
        assert dados["certificado"]["titular"] == "METALURGICA ALFA SINTETICA LTDA"
        assert dados["certificado"]["documento"] == "11222333000181"
        assert dados["certificado"]["dias_para_vencer"] >= 140
        assert dados["certificado"]["vencido"] is False

        # Verifica arquivos gravados no disco com nomes fixos seguros
        assert (cert_dir / NOME_ARQUIVO_PFX_FIXO).is_file()
        assert (cert_dir / NOME_ARQUIVO_SENHA_FIXO).is_file()
        assert (cert_dir / NOME_ARQUIVO_CONFIG_FIXO).is_file()
        assert (cert_dir / NOME_ARQUIVO_PFX_FIXO).read_bytes() == pfx_bytes
        assert not (cert_dir.parent.parent / "tentativa_traversal_maliciosa.pfx").exists()

        # GET /api/recebidas/status reflete o certificado salvo via interface
        resp_status = client.get("/api/recebidas/status")
        assert resp_status.status_code == 200
        status_json = resp_status.json()
        assert status_json["certificado_configurado"] is True
        assert status_json["certificado"]["documento"] == "11222333000181"
        assert status_json["uf_autor"] == "41"
        assert status_json["ambiente"] == 2
        assert status_json["origem_certificado"] == "interface"

    def test_upload_com_senha_incorreta_ou_arquivo_invalido_retorna_400_sem_gravar_lixo(
        self,
        client: TestClient,
        cert_dir: Path,
    ) -> None:
        pfx_bytes = _gerar_pfx_sintetico(senha="senha-correta")

        # 1. Senha incorreta -> HTTP 400 e nenhum arquivo gravado em disco
        resp_senha_errada = client.post(
            "/api/recebidas/certificado",
            files=[("arquivo", ("empresa.pfx", pfx_bytes, "application/x-pkcs12"))],
            data={"senha": "senha-errada", "uf_autor": "35", "ambiente": "2"},
        )
        assert resp_senha_errada.status_code == 400
        assert "senha" in resp_senha_errada.json()["detail"].lower()
        assert not (cert_dir / NOME_ARQUIVO_PFX_FIXO).exists()
        assert not (cert_dir / NOME_ARQUIVO_SENHA_FIXO).exists()

        # 2. Conteúdo corrompido (não é PKCS#12) -> HTTP 400
        resp_corrompido = client.post(
            "/api/recebidas/certificado",
            files=[("arquivo", ("corrompido.p12", b"isto-nao-eh-pkcs12", "application/x-pkcs12"))],
            data={"senha": "qualquer", "uf_autor": "35", "ambiente": "2"},
        )
        assert resp_corrompido.status_code == 400
        assert not (cert_dir / NOME_ARQUIVO_PFX_FIXO).exists()

        # 3. Extensão não permitida (.txt / .exe) -> HTTP 400
        resp_ext = client.post(
            "/api/recebidas/certificado",
            files=[("arquivo", ("certificado.txt", pfx_bytes, "text/plain"))],
            data={"senha": "senha-correta", "uf_autor": "35", "ambiente": "2"},
        )
        assert resp_ext.status_code == 400
        assert ".pfx" in resp_ext.json()["detail"].lower()

        # 4. Se já havia um certificado válido salvo, tentativa inválida NÃO sobrescreve o bom
        client.post(
            "/api/recebidas/certificado",
            files=[("arquivo", ("valido.pfx", pfx_bytes, "application/x-pkcs12"))],
            data={"senha": "senha-correta", "uf_autor": "35", "ambiente": "2"},
        )
        assert (cert_dir / NOME_ARQUIVO_PFX_FIXO).is_file()

        resp_tentativa_ruim = client.post(
            "/api/recebidas/certificado",
            files=[("arquivo", ("outro.pfx", pfx_bytes, "application/x-pkcs12"))],
            data={"senha": "senha-errada-nova", "uf_autor": "35", "ambiente": "1"},
        )
        assert resp_tentativa_ruim.status_code == 400

        # O certificado anterior permanece íntegro e funcional
        status_apos = client.get("/api/recebidas/status").json()
        assert status_apos["certificado_configurado"] is True
        assert status_apos["ambiente"] == 2

    def test_patch_config_alterna_ambiente_e_uf_e_delete_remove_certificado(
        self,
        client: TestClient,
        cert_dir: Path,
    ) -> None:
        pfx_bytes = _gerar_pfx_sintetico(cnpj="11222333000181", senha="123")
        client.post(
            "/api/recebidas/certificado",
            files=[("arquivo", ("cert.p12", pfx_bytes, "application/x-pkcs12"))],
            data={"senha": "123", "uf_autor": "35", "ambiente": "2"},
        )

        # Alterna para Produção (1) e UF 31 (MG) via PATCH /api/recebidas/config
        resp_patch = client.patch(
            "/api/recebidas/config",
            json={"uf_autor": "31", "ambiente": 1},
        )
        assert resp_patch.status_code == 200
        dados_patch = resp_patch.json()
        assert dados_patch["certificado_configurado"] is True
        assert dados_patch["uf_autor"] == "31"
        assert dados_patch["ambiente"] == 1
        assert dados_patch["cursor"]["ambiente"] == 1

        # UF inválida no PATCH -> HTTP 400
        resp_uf_invalida = client.patch("/api/recebidas/config", json={"uf_autor": "SP"})
        assert resp_uf_invalida.status_code == 400

        # Remove o certificado via DELETE /api/recebidas/certificado
        resp_del = client.delete("/api/recebidas/certificado")
        assert resp_del.status_code == 200
        dados_del = resp_del.json()
        assert dados_del["certificado_configurado"] is False
        assert dados_del["certificado"] is None
        assert dados_del["origem_certificado"] == "nao_configurado"
        assert not (cert_dir / NOME_ARQUIVO_PFX_FIXO).exists()
        assert not (cert_dir / NOME_ARQUIVO_SENHA_FIXO).exists()
