"""Testes iniciais de fumaça (smoke tests) da estrutura do projeto."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from api import cli

RAIZ_PROJETO = Path(__file__).resolve().parent.parent

PACOTES_PRINCIPAIS = (
    "parsers",
    "models",
    "validators",
    "reports",
    "sefaz",
    "api",
)


@pytest.mark.parametrize("nome_pacote", PACOTES_PRINCIPAIS)
def test_importacao_e_versao_dos_pacotes(nome_pacote: str) -> None:
    """Garante que todos os pacotes principais são importáveis e expõem a versão 0.1.0."""
    modulo = importlib.import_module(nome_pacote)
    assert hasattr(modulo, "__version__")
    assert modulo.__version__ == "0.1.0"


def test_diretorios_obrigatorios_existem() -> None:
    """Verifica a presença da árvore de diretórios definida na arquitetura."""
    diretorios = (
        "parsers",
        "models",
        "validators",
        "reports",
        "sefaz",
        "api",
        "web/static",
        "web/templates",
        "tests/fixtures",
        "docs/prints",
    )
    for caminho_relativo in diretorios:
        caminho = RAIZ_PROJETO / caminho_relativo
        assert caminho.is_dir(), f"Diretório obrigatório ausente: {caminho_relativo}"


def test_cli_version(capsys: pytest.CaptureFixture[str]) -> None:
    """Verifica que o comando `nfe-local --version` exibe a versão 0.1.0 e encerra com código 0."""
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--version"])
    assert exc_info.value.code == 0
    saida = capsys.readouterr().out.strip()
    assert "0.1.0" in saida


def test_cli_help(capsys: pytest.CaptureFixture[str]) -> None:
    """Verifica que o comando `nfe-local --help` exibe o texto de ajuda."""
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--help"])
    assert exc_info.value.code == 0
    saida = capsys.readouterr().out
    assert "nfe-local" in saida


def test_gitignore_protege_segredos_e_artefatos() -> None:
    """Garante que certificados, segredos e bancos locais estão no .gitignore."""
    caminho_gitignore = RAIZ_PROJETO / ".gitignore"
    assert caminho_gitignore.is_file()
    conteudo = caminho_gitignore.read_text(encoding="utf-8")
    linhas = {linha.strip() for linha in conteudo.splitlines() if linha.strip()}

    padroes_obrigatorios = {
        ".venv/",
        "venv/",
        ".env",
        "*.pfx",
        "*.p12",
        "*.pem",
        "*.key",
        "senha.txt",
        "data/",
        "*.db",
        "saidas/",
        "instalar.log",
        "__pycache__/",
        ".pytest_cache/",
        ".ruff_cache/",
    }
    faltantes = padroes_obrigatorios - linhas
    assert not faltantes, f"Padrões de segurança ausentes no .gitignore: {faltantes}"
