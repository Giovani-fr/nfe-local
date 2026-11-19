"""Senha do certificado A1 protegida (DPAPI), pasta fora do projeto e migração do formato antigo."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import sefaz.cert_a1 as cert_a1
from api.app import app, definir_diretorio_certificado
from sefaz import segredos
from sefaz.cert_a1 import (
    NOME_ARQUIVO_CONFIG_FIXO,
    NOME_ARQUIVO_PFX_FIXO,
    NOME_ARQUIVO_SENHA_DPAPI,
    NOME_ARQUIVO_SENHA_FIXO,
    ErroCertificadoA1,
    carregar_certificado_configurado,
    detectar_cadastro_legado,
    migrar_cadastro_legado,
    resolver_diretorio_certificado,
    salvar_certificado_local,
)
from tests.test_certificado_api import _gerar_pfx_sintetico

SENHA = "senha-super-secreta-ficticia-987"


@pytest.fixture
def dpapi(request):
    """DPAPI real no Windows; simulada nos demais sistemas."""
    if sys.platform != "win32":
        request.getfixturevalue("dpapi_falso")


def _todos_arquivos_contem(pasta: Path, trecho: bytes) -> bool:
    return any(trecho in p.read_bytes() for p in pasta.rglob("*") if p.is_file())


class TestPastaPadrao:
    def test_padrao_fica_fora_do_projeto_e_respeita_nfe_data_dir(self, tmp_path, monkeypatch):
        alvo = resolver_diretorio_certificado()
        assert alvo == tmp_path / "dados_nfe" / "certificado"
        raiz_projeto = Path(cert_a1.__file__).resolve().parent.parent
        assert raiz_projeto not in alvo.resolve().parents

    def test_padrao_sem_env_usa_localappdata_no_windows(self, monkeypatch, tmp_path):
        monkeypatch.delenv("NFE_DATA_DIR")
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
        monkeypatch.setattr(segredos.sys, "platform", "win32")
        assert segredos.diretorio_dados() == tmp_path / "local" / "nfe-local"

    def test_nfe_cert_dir_tem_prioridade(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NFE_CERT_DIR", str(tmp_path / "outra"))
        assert resolver_diretorio_certificado() == tmp_path / "outra"

    @pytest.mark.parametrize(
        "caminho",
        [
            r"C:\Users\x\OneDrive\Documents\Python\nfe-local\data",
            r"C:\Users\x\OneDrive - Empresa\a",
            "/home/x/Dropbox/a",
            r"D:\Google Drive\a",
        ],
    )
    def test_detecta_pasta_sincronizada(self, caminho):
        assert segredos.pasta_parece_sincronizada(caminho)

    def test_pasta_local_nao_e_sincronizada(self, tmp_path):
        assert not segredos.pasta_parece_sincronizada(tmp_path / "nfe-local")


class TestSenhaProtegida:
    def test_senha_nunca_aparece_em_texto_puro_e_leitura_funciona_apos_reinicio(
        self, tmp_path, dpapi
    ):
        pfx = _gerar_pfx_sintetico(senha=SENHA)
        pasta = tmp_path / "cert"
        salvar_certificado_local(pfx, SENHA, cert_dir=pasta)

        assert (pasta / NOME_ARQUIVO_SENHA_DPAPI).is_file()
        assert not (pasta / NOME_ARQUIVO_SENHA_FIXO).exists()
        assert not _todos_arquivos_contem(pasta, SENHA.encode())
        assert not _todos_arquivos_contem(pasta, SENHA[::-1].encode())

        # "reinício": nenhum estado em memória; tudo vem do disco.
        pfx_lido, senha_lida, meta, cfg = carregar_certificado_configurado(pasta)
        assert senha_lida == SENHA
        assert pfx_lido == pfx
        assert meta.documento == "11222333000181"
        assert cfg.origem == "interface"

    def test_salvar_remove_senha_txt_preexistente(self, tmp_path, dpapi):
        pasta = tmp_path / "cert"
        pasta.mkdir()
        (pasta / NOME_ARQUIVO_SENHA_FIXO).write_text("antiga", encoding="utf-8")
        salvar_certificado_local(_gerar_pfx_sintetico(senha=SENHA), SENHA, cert_dir=pasta)
        assert not (pasta / NOME_ARQUIVO_SENHA_FIXO).exists()

    def test_senha_nao_vaza_em_mensagens_de_erro(self, tmp_path, dpapi):
        pfx = _gerar_pfx_sintetico(senha=SENHA)
        pasta = tmp_path / "cert"
        salvar_certificado_local(pfx, SENHA, cert_dir=pasta)
        (pasta / NOME_ARQUIVO_SENHA_DPAPI).write_bytes(b"lixo-invalido")
        with pytest.raises(ErroCertificadoA1) as exc:
            carregar_certificado_configurado(pasta)
        assert SENHA not in str(exc.value)

    def test_sem_dpapi_a_senha_nao_e_gravada_e_usa_env(self, tmp_path, monkeypatch):
        monkeypatch.setattr(segredos, "dpapi_disponivel", lambda: False)
        pfx = _gerar_pfx_sintetico(senha=SENHA)
        pasta = tmp_path / "cert"
        salvar_certificado_local(pfx, SENHA, cert_dir=pasta)

        assert (pasta / NOME_ARQUIVO_PFX_FIXO).is_file()
        assert not (pasta / NOME_ARQUIVO_SENHA_DPAPI).exists()
        assert not (pasta / NOME_ARQUIVO_SENHA_FIXO).exists()
        assert not _todos_arquivos_contem(pasta, SENHA.encode())

        with pytest.raises(ErroCertificadoA1, match="NFE_CERT_SENHA"):
            carregar_certificado_configurado(pasta)

        monkeypatch.setenv("NFE_CERT_SENHA", SENHA)
        _, senha_lida, _, cfg = carregar_certificado_configurado(pasta)
        assert senha_lida == SENHA
        assert cfg.origem == "interface"


def _criar_cadastro_legado(pasta: Path, senha: str = SENHA, *, pfx: bytes | None = None) -> bytes:
    pfx = pfx or _gerar_pfx_sintetico(senha=senha)
    pasta.mkdir(parents=True)
    (pasta / NOME_ARQUIVO_PFX_FIXO).write_bytes(pfx)
    (pasta / NOME_ARQUIVO_SENHA_FIXO).write_text(senha, encoding="utf-8")
    (pasta / NOME_ARQUIVO_CONFIG_FIXO).write_text('{"uf_autor": "41", "ambiente": 1}')
    return pfx


class TestMigracaoLegado:
    def test_migra_protege_senha_e_apaga_arquivos_antigos(self, tmp_path, dpapi, capsys):
        legado = tmp_path / "projeto" / "data" / "certificado"
        pfx = _criar_cadastro_legado(legado)
        destino = tmp_path / "seguro" / "certificado"

        assert detectar_cadastro_legado(legado) == legado
        meta, cfg = migrar_cadastro_legado(legado, destino)

        assert meta.documento == "11222333000181"
        assert (cfg.uf_autor, cfg.ambiente) == ("41", 1)
        assert not (legado / NOME_ARQUIVO_SENHA_FIXO).exists()
        assert not (legado / NOME_ARQUIVO_PFX_FIXO).exists()
        assert detectar_cadastro_legado(legado) is None
        assert (destino / NOME_ARQUIVO_PFX_FIXO).read_bytes() == pfx
        assert (destino / NOME_ARQUIVO_SENHA_DPAPI).is_file()
        assert not _todos_arquivos_contem(destino, SENHA.encode())
        assert SENHA not in capsys.readouterr().out
        assert carregar_certificado_configurado(destino)[1] == SENHA

    def test_senha_invalida_nao_apaga_nada(self, tmp_path, dpapi):
        legado = tmp_path / "legado"
        _criar_cadastro_legado(legado, pfx=_gerar_pfx_sintetico(senha="outra"))
        with pytest.raises(ErroCertificadoA1):
            migrar_cadastro_legado(legado, tmp_path / "novo")
        assert (legado / NOME_ARQUIVO_SENHA_FIXO).is_file()
        assert not (tmp_path / "novo" / NOME_ARQUIVO_SENHA_DPAPI).exists()

    def test_sem_dpapi_recusa_e_preserva_arquivos(self, tmp_path, monkeypatch):
        monkeypatch.setattr(segredos, "dpapi_disponivel", lambda: False)
        legado = tmp_path / "legado"
        _criar_cadastro_legado(legado)
        with pytest.raises(ErroCertificadoA1, match="DPAPI"):
            migrar_cadastro_legado(legado, tmp_path / "novo")
        assert (legado / NOME_ARQUIVO_SENHA_FIXO).is_file()

    def test_sem_cadastro_antigo(self, tmp_path):
        assert detectar_cadastro_legado(tmp_path / "nada") is None
        with pytest.raises(ErroCertificadoA1):
            migrar_cadastro_legado(tmp_path / "nada", tmp_path / "novo")


class TestMigracaoViaApi:
    def test_status_oferece_migracao_e_endpoint_migra(self, tmp_path, monkeypatch, dpapi):
        legado = tmp_path / "legado_api"
        _criar_cadastro_legado(legado)
        monkeypatch.setattr(cert_a1, "DIRETORIO_CERTIFICADO_LEGADO", legado)
        destino = tmp_path / "destino_api"
        definir_diretorio_certificado(destino)
        try:
            client = TestClient(app)
            antes = client.get("/api/recebidas/status").json()
            assert antes["migracao_legada_pendente"] is True

            resp = client.post("/api/recebidas/certificado/migrar")
            assert resp.status_code == 200
            depois = resp.json()
            assert depois["migracao_legada_pendente"] is False
            assert depois["certificado_configurado"] is True
            assert depois["protecao_senha"] == "dpapi"
            assert SENHA not in resp.text
            assert not (legado / NOME_ARQUIVO_SENHA_FIXO).exists()
        finally:
            definir_diretorio_certificado(None)


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI real só existe no Windows")
def test_dpapi_real_roundtrip_e_nao_guarda_texto_puro():
    cifrado = segredos.proteger_senha(SENHA)
    assert SENHA.encode() not in cifrado
    assert segredos.desproteger_senha(cifrado) == SENHA


def test_suite_nao_usa_pasta_real_do_certificado(tmp_path):
    """O conftest redireciona `NFE_DATA_DIR`; o padrão resolvido nunca é a pasta real."""
    assert tmp_path in resolver_diretorio_certificado().parents
