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
        prints = [
            RAIZ_PROJETO / "docs" / "prints" / nome
            for nome in (
                "tabela-notas.png",
                "painel-gerencial.png",
                "sefaz-adn.png",
                "escopo-tudo.png",
                "diagnostico-adn.png",
                "danfe.png",
                "danfse.png",
            )
        ]

        assert readme_pt.is_file()
        assert readme_en.is_file()
        assert benchmark_md.is_file()
        assert all(p.is_file() for p in prints)

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

    def test_readmes_escopo_adn_estado_validado_e_imagens_existentes(self) -> None:
        import re

        for nome in ("README.md", "README.en.md"):
            txt = (RAIZ_PROJETO / nome).read_text(encoding="utf-8")
            # toda imagem referenciada existe (e a antiga, removida, não é mais citada)
            for ref in re.findall(r"\]\((docs/prints/[^)]+)\)", txt):
                assert (RAIZ_PROJETO / ref).is_file(), f"{nome}: imagem ausente {ref}"
            assert "recebidas-sefaz.png" not in txt
            for termo in (
                "ADN",
                "sincronizar-adn",
                "--diagnostico",
                "--max-paginas 1",
                "cnpjConsulta",
                "NFE_ADN_TENTATIVAS",
                "docs/adn.md",
                "tarefa 27" if nome == "README.md" else "task 27",
            ):
                assert termo in txt, f"{nome}: falta '{termo}'"
            assert "NFC-e" in txt
            # a tabela de escopo e a ressalva sobre a sincronização completa
            assert ("Somente upload" in txt) or ("Upload only" in txt)
            # sincronização completa confirmada em uso real, com a ressalva do intermediário
            assert "411" in txt and "391" in txt
            # a NF-e pela SEFAZ NÃO foi confirmada em uso real: o texto precisa dizer isso
            assert ("ainda não foi confirmada em uma nova busca real" in txt) or (
                "not yet confirmed by a new real fetch" in txt
            )
            assert ("não confirma" in txt) or ("does not confirm" in txt)
            assert "Notas Emitidas Contra o CNPJ" not in txt

    def test_backlog_tarefas_1_a_25_e_27_concluidas_e_28_aberta(self) -> None:
        backlog_md = (RAIZ_PROJETO / "docs" / "BACKLOG.md").read_text(encoding="utf-8")
        linhas = [linha.strip() for linha in backlog_md.splitlines()]
        for numero in (*range(1, 26), 27):
            assert any(linha.startswith(f"- [x] **{numero}.") for linha in linhas), (
                f"Tarefa {numero} do backlog não está concluída."
            )
        # a tarefa 28 (avaliar mTLS sem arquivo temporário) continua aberta de propósito
        assert any(linha.startswith("- [ ] **28.") for linha in linhas)
        abertas = [
            int(linha.split("**")[1].split(".")[0])
            for linha in linhas
            if linha.startswith("- [ ] **")
        ]
        assert abertas == [28]


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
