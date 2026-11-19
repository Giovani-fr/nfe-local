"""Estrutura dos READMEs: selo, "Em 30 segundos", sumário, ordem e privacidade dos textos."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent


def _slug(titulo: str) -> str:
    """Âncora no estilo do GitHub: minúsculas, sem pontuação, espaços viram hífens."""
    s = re.sub(r"[^\w\s-]", "", titulo.strip().lower())
    return re.sub(r"\s", "-", s)


CASOS = [
    (
        "README.md",
        "### Em 30 segundos",
        "Instalação e Como Usar",
        ("Validação detalhada", "Velocidade", "Como usei IA"),
    ),
    (
        "README.en.md",
        "### In 30 seconds",
        "Installation & Usage",
        ("Detailed validation", "Measured Speed", "How I used AI"),
    ),
]


@pytest.mark.parametrize(("nome", "em30", "instalacao", "detalhes"), CASOS)
class TestReadme:
    def _txt(self, nome: str) -> str:
        return (RAIZ / nome).read_text(encoding="utf-8")

    def test_selo_do_ci_logo_abaixo_do_titulo(self, nome, em30, instalacao, detalhes):
        topo = "\n".join(self._txt(nome).splitlines()[:6])
        assert "actions/workflows/ci.yml/badge.svg" in topo
        assert "github.com/Giovani-fr/nfe-local/actions/workflows/ci.yml" in topo

    def test_bloco_curto_com_no_maximo_5_linhas_e_imagem_principal(
        self, nome, em30, instalacao, detalhes
    ):
        linhas = self._txt(nome).splitlines()
        bloco = []
        for ln in linhas[linhas.index(em30) + 1 :]:
            if ln.startswith("#"):
                break
            if ln.startswith("- "):
                bloco.append(ln)
        assert 1 <= len(bloco) <= 5
        junto = "\n".join(bloco)
        assert "docs/prints/sefaz-adn.png" in junto
        assert (RAIZ / "docs" / "prints" / "sefaz-adn.png").is_file()
        assert "SEFAZ" in junto and "ADN" in junto

    def test_todo_link_do_sumario_aponta_para_um_titulo_existente(
        self, nome, em30, instalacao, detalhes
    ):
        txt = self._txt(nome)
        titulos = {_slug(m) for m in re.findall(r"(?m)^#{2,4} (.+)$", txt)}
        ancoras = re.findall(r"\]\(#([^)]+)\)", txt)
        assert len(ancoras) >= 10
        for ancora in ancoras:
            assert ancora in titulos, f"{nome}: âncora quebrada #{ancora}"

    def test_detalhes_longos_vem_depois_da_instalacao(self, nome, em30, instalacao, detalhes):
        txt = self._txt(nome)
        pos = {m.group(1): m.start() for m in re.finditer(r"(?m)^## (.+)$", txt)}
        pos_inst = next(v for k, v in pos.items() if k.startswith(instalacao))
        for pref in detalhes:
            assert next(v for k, v in pos.items() if k.startswith(pref)) > pos_inst, pref

    def test_esclarece_o_que_local_significa(self, nome, em30, instalacao, detalhes):
        txt = self._txt(nome)
        assert ("rede só é usada" in txt) or ("network is only used" in txt)
        assert "100% local" not in txt.split("### Telas reais")[0].lower().replace(
            "100% sintéticos", ""
        )


def test_textos_sem_email_caminho_pessoal_nem_cnpj_real():
    permitidos: set[str] = set()
    for arq in (RAIZ / "tests" / "fixtures").rglob("*.xml"):
        permitidos |= set(re.findall(r"\d{14}", arq.read_text(encoding="utf-8")))
    permitidos |= set(
        re.findall(r"\d{14}", (RAIZ / "sefaz" / "adn_demo.py").read_text(encoding="utf-8"))
    )
    arquivos = [RAIZ / n for n in ("README.md", "README.en.md", "CLAUDE.md", ".env.example")]
    arquivos += list((RAIZ / "docs").glob("*.md"))
    for arq in arquivos:
        txt = arq.read_text(encoding="utf-8")
        assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", txt), f"{arq.name}: e-mail"
        assert not re.search(r"[A-Za-z]:[\\/]Users[\\/](?!voce)", txt), (
            f"{arq.name}: caminho pessoal"
        )
        # o endereço público do repositório (selo do CI) é o único lugar onde o dono aparece
        sem_repo = re.sub(r"github\.com/giovani-fr/nfe-local", "", txt, flags=re.IGNORECASE)
        assert "giova" not in sem_repo.lower(), f"{arq.name}: nome de usuário"
        for doc in re.findall(r"(?<!\d)\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}(?!\d)", txt):
            assert re.sub(r"\D", "", doc) in permitidos, f"{arq.name}: CNPJ fora da lista sintética"
