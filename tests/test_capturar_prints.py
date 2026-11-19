"""Checagem de privacidade do `scripts/capturar_prints.py` (sem navegador e sem rede)."""

from __future__ import annotations

import getpass
import importlib.util
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "capturar_prints", RAIZ / "scripts" / "capturar_prints.py"
)
capturar = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(capturar)  # type: ignore[union-attr]


@pytest.fixture(scope="module")
def permitidos():
    return capturar._lista_permitida()


def test_lista_permitida_vem_das_fixtures_e_das_demos(permitidos):
    docs, emails = permitidos
    assert "11222333000181" in docs and "33444555000181" in docs
    assert "12345678909" in docs  # CPF fictício das fixtures, sem pontuação
    assert emails and all(e.endswith((".br", ".com", ".teste")) for e in emails)


def test_pagina_sintetica_passa(permitidos):
    capturar.verificar_privacidade(
        "ok.png",
        ["CNPJ 11.222.333/0001-81  EMPRESA EXEMPLO LTDA  33.444.555/0001-81  CPF 123.456.789-09"],
        permitidos,
        ["UsuarioReal"],
    )


@pytest.mark.parametrize(
    "texto",
    [
        "CNPJ 99.888.777/0001-66",
        "CPF 987.654.321-00",
        "contato: pessoa@empresa-real.com.br",
        "C:\\Users\\alguem\\Documents\\x.xml",
        "/home/alguem/x",
        "UsuarioReal",
    ],
)
def test_dado_fora_da_lista_aborta_sem_gravar(permitidos, texto):
    with pytest.raises(SystemExit) as exc:
        capturar.verificar_privacidade("x.png", [texto], permitidos, ["UsuarioReal"])
    mensagem = str(exc.value)
    assert "PRIVACIDADE" in mensagem and "não foi gravado" in mensagem
    # o valor encontrado nunca é impresso na mensagem
    for trecho in ("99.888.777", "987.654.321", "empresa-real", "alguem"):
        assert trecho not in mensagem


def test_nome_do_usuario_do_windows_e_proibido(permitidos):
    with pytest.raises(SystemExit):
        capturar.verificar_privacidade(
            "x.png", [f"pasta de {getpass.getuser()}"], permitidos, [getpass.getuser()]
        )


def test_front_end_tem_as_funcoes_da_visao_unica():
    js = (RAIZ / "web" / "static" / "app.js").read_text(encoding="utf-8")
    for nome in (
        "sincronizarTudo",
        "sincronizarComAdn",
        "diagnosticarAdn",
        "aplicarPapelRapido",
        "mensagemUltimaSync",
        "badgePapel",
        '"/api/recebidas/adn/diagnostico',
        '"/api/recebidas/sincronizar-adn"',
        "/api/lote/${encodeURIComponent(escopoId)}",
    ):
        assert nome in js, nome
