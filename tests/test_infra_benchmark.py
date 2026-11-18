"""Testes de infraestrutura (Dockerfile, scripts .bat ASCII, READMEs e benchmark)."""

from __future__ import annotations

from pathlib import Path

from scripts.benchmark_lote import executar_benchmark

RAIZ_PROJETO = Path(__file__).resolve().parent.parent


class TestInfraestruturaEDocumentacao:
    """Valida Dockerfile, .dockerignore, scripts Windows (.bat) e documentação."""

    def test_dockerfile_seguro_usuario_nao_root(self) -> None:
        caminho = RAIZ_PROJETO / "Dockerfile"
        assert caminho.is_file()
        conteudo = caminho.read_text(encoding="utf-8")
        assert "FROM python:3.12-slim" in conteudo
        assert "USER appuser" in conteudo
        assert "EXPOSE 8765" in conteudo
        assert "uvicorn" in conteudo

    def test_dockerignore_bloqueia_segredos_e_certificados(self) -> None:
        caminho = RAIZ_PROJETO / ".dockerignore"
        assert caminho.is_file()
        linhas = {
            linha.strip()
            for linha in caminho.read_text(encoding="utf-8").splitlines()
            if linha.strip()
        }
        obrigatorios = {
            ".git",
            ".env",
            "*.pfx",
            "*.p12",
            "*.pem",
            "*.key",
            "senha.txt",
            "data/",
            "*.db",
            "instalar.log",
        }
        faltantes = obrigatorios - linhas
        assert not faltantes, f"Itens de segurança ausentes no .dockerignore: {faltantes}"

    def test_scripts_bat_sao_ascii_puro_sem_acentos(self) -> None:
        for nome in ("instalar.bat", "iniciar.bat"):
            caminho = RAIZ_PROJETO / nome
            assert caminho.is_file(), f"Script ausente: {nome}"
            dados = caminho.read_bytes()
            nao_ascii = [b for b in dados if b >= 128]
            assert not nao_ascii, f"O arquivo {nome} contém bytes não-ASCII: {nao_ascii}"

        instalar_txt = (RAIZ_PROJETO / "instalar.bat").read_text(encoding="ascii")
        assert "venvs\\nfe-local" in instalar_txt
        assert "Visualizador NF-e Local.lnk" in instalar_txt
        assert "INSTALAR_ATALHO_PASTA" in instalar_txt

        iniciar_txt = (RAIZ_PROJETO / "iniciar.bat").read_text(encoding="ascii")
        assert "127.0.0.1" in iniciar_txt
        assert "uvicorn api.app:app" in iniciar_txt

    def test_readmes_benchmark_e_secao_como_usei_ia(self) -> None:
        readme_pt = RAIZ_PROJETO / "README.md"
        readme_en = RAIZ_PROJETO / "README.en.md"
        benchmark_md = RAIZ_PROJETO / "docs" / "benchmark.md"
        demo_svg = RAIZ_PROJETO / "docs" / "prints" / "demo.svg"

        assert readme_pt.is_file()
        assert readme_en.is_file()
        assert benchmark_md.is_file()
        assert demo_svg.is_file()

        txt_pt = readme_pt.read_text(encoding="utf-8")
        assert "Como usei IA" in txt_pt
        assert "README.en.md" in txt_pt
        assert "docs/benchmark.md" in txt_pt
        assert "NFE_CERT_PATH" in txt_pt
        assert "NFeDistribuicaoDFe" in txt_pt
        assert "656" in txt_pt
        assert "2.16.76.1.3.3" in txt_pt
        assert "16–18" in txt_pt

        txt_en = readme_en.read_text(encoding="utf-8")
        assert "How I used AI" in txt_en
        assert "README.md" in txt_en
        assert "docs/benchmark.md" in txt_en
        assert "NFE_CERT_PATH" in txt_en
        assert "NFeDistribuicaoDFe" in txt_en
        assert "656" in txt_en
        assert "2.16.76.1.3.3" in txt_en
        assert "16–18" in txt_en

    def test_backlog_completo_com_todas_as_19_tarefas_concluidas(self) -> None:
        backlog_md = (RAIZ_PROJETO / "docs" / "BACKLOG.md").read_text(encoding="utf-8")
        marcados = [linha for linha in backlog_md.splitlines() if linha.strip().startswith("- [x]")]
        pendentes = [
            linha for linha in backlog_md.splitlines() if linha.strip().startswith("- [ ]")
        ]
        assert len(marcados) == 19
        assert len(pendentes) == 0


class TestBenchmarkLote:
    """Testa a execução rápida do benchmark sintético com 50 XMLs."""

    def test_executar_benchmark_lote_reduzido(self) -> None:
        metricas = executar_benchmark(quantidade=50)
        assert metricas["quantidade_xmls"] == 50
        assert metricas["validos"] == 50
        assert metricas["streaming"]["tempo_s"] > 0
        assert metricas["streaming"]["xmls_por_segundo"] > 0
        assert metricas["streaming"]["pico_memoria_mb"] > 0
        assert metricas["lote_completo_xlsx"]["tamanho_xlsx_kb"] > 0
