"""Testes de integração da API REST FastAPI e da CLI (`api/app.py` e `api/cli.py`)."""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api import __version__, app, cli
from api.app import (
    RepositorioLotesMemoria,
    definir_repositorio_recebidas,
    obter_repositorio_lotes,
)
from sefaz.storage import RepositorioDistDFeSQLite

PASTA_FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture()
def client(tmp_path: Path) -> Generator[TestClient, None, None]:
    """Retorna cliente de teste do FastAPI com repositório limpo e SQLite isolado."""
    obter_repositorio_lotes().limpar()
    definir_repositorio_recebidas(RepositorioDistDFeSQLite(tmp_path / "test_api_cli.db"))
    try:
        yield TestClient(app)
    finally:
        obter_repositorio_lotes().limpar()
        definir_repositorio_recebidas(None)


def _criar_zip_em_memoria(nomes_fixtures: tuple[str, ...]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for nome in nomes_fixtures:
            zf.writestr(nome, (PASTA_FIXTURES / nome).read_bytes())
    return buffer.getvalue()


class TestApiRest:
    """Testes dos endpoints da API REST FastAPI."""

    def test_health_check(self, client: TestClient) -> None:
        resp = client.get("/api/health")
        assert resp.status_code == 200
        dados = resp.json()
        assert dados == {"status": "ok", "version": __version__}

    def test_post_lote_multiplos_xmls_e_armazenamento_memoria(self, client: TestClient) -> None:
        arquivos = [
            (
                "files",
                (
                    "nfe_55_valida.xml",
                    (PASTA_FIXTURES / "nfe_55_valida.xml").read_bytes(),
                    "application/xml",
                ),
            ),
            (
                "files",
                (
                    "nfce_65_valida.xml",
                    (PASTA_FIXTURES / "nfce_65_valida.xml").read_bytes(),
                    "application/xml",
                ),
            ),
            (
                "files",
                (
                    "nfse_nacional_valida.xml",
                    (PASTA_FIXTURES / "nfse_nacional_valida.xml").read_bytes(),
                    "application/xml",
                ),
            ),
            (
                "files",
                (
                    "nfe_55_soma_errada.xml",
                    (PASTA_FIXTURES / "nfe_55_soma_errada.xml").read_bytes(),
                    "application/xml",
                ),
            ),
        ]
        resp = client.post("/api/lote", files=arquivos)
        assert resp.status_code == 200

        corpo = resp.json()
        lote_id = corpo["lote_id"]
        assert isinstance(lote_id, str) and len(lote_id) >= 8
        assert "criado_em" in corpo
        assert corpo["resumo"]["contagem"]["total_arquivos"] == 4
        assert corpo["resumo"]["contagem"]["validos"] == 3
        assert corpo["resumo"]["contagem"]["com_erros"] == 1
        assert len(corpo["resultados"]) == 4

        # Verifica se os XMLs brutos foram indexados por chave e por índice no repositório
        lote_mem = obter_repositorio_lotes().obter(lote_id)
        assert lote_mem is not None
        chave_nfe = "35260311222333000181550010000010011100010010"
        assert chave_nfe in lote_mem.xmls_brutos
        assert "0" in lote_mem.xmls_brutos
        assert lote_mem.xmls_brutos[chave_nfe] == lote_mem.xmls_brutos["0"]

    def test_post_lote_arquivo_zip(self, client: TestClient) -> None:
        zip_bytes = _criar_zip_em_memoria(("nfe_55_valida.xml", "nfse_nacional_valida.xml"))
        resp = client.post(
            "/api/lote",
            files=[("files", ("pacote_notas.zip", zip_bytes, "application/zip"))],
        )
        assert resp.status_code == 200
        corpo = resp.json()
        assert corpo["resumo"]["contagem"]["total_arquivos"] == 2
        assert corpo["resumo"]["contagem"]["validos"] == 2

    def test_post_lote_rejeita_extensao_invalida(self, client: TestClient) -> None:
        resp = client.post(
            "/api/lote",
            files=[("files", ("malware.exe", b"MZ\x90\x00", "application/octet-stream"))],
        )
        assert resp.status_code == 400
        assert ".xml" in resp.json()["detail"].lower()

    def test_get_lote_filtros_e_404(self, client: TestClient) -> None:
        resp_404 = client.get("/api/lote/lote_inexistente")
        assert resp_404.status_code == 404

        arquivos = [
            (
                "files",
                (
                    "nfe_55_valida.xml",
                    (PASTA_FIXTURES / "nfe_55_valida.xml").read_bytes(),
                    "application/xml",
                ),
            ),
            (
                "files",
                (
                    "nfse_nacional_valida.xml",
                    (PASTA_FIXTURES / "nfse_nacional_valida.xml").read_bytes(),
                    "application/xml",
                ),
            ),
            (
                "files",
                (
                    "nfe_55_soma_errada.xml",
                    (PASTA_FIXTURES / "nfe_55_soma_errada.xml").read_bytes(),
                    "application/xml",
                ),
            ),
        ]
        lote_id = client.post("/api/lote", files=arquivos).json()["lote_id"]

        # Sem filtros
        resp_todos = client.get(f"/api/lote/{lote_id}")
        assert resp_todos.status_code == 200
        assert len(resp_todos.json()["resultados"]) == 3

        # Filtro por tipo=NFSE
        resp_nfse = client.get(f"/api/lote/{lote_id}", params={"tipo": "NFSE"})
        assert resp_nfse.status_code == 200
        resultados_nfse = resp_nfse.json()["resultados"]
        assert len(resultados_nfse) == 1
        assert resultados_nfse[0]["documento"]["tipo"] == "NFSE"

        # Filtro por valido=false
        resp_invalidos = client.get(f"/api/lote/{lote_id}", params={"valido": "false"})
        assert resp_invalidos.status_code == 200
        resultados_inv = resp_invalidos.json()["resultados"]
        assert len(resultados_inv) == 1
        assert resultados_inv[0]["arquivo_origem"] == "nfe_55_soma_errada.xml"

        # Filtro por busca (chave, número ou razão social)
        resp_busca = client.get(
            f"/api/lote/{lote_id}",
            params={"busca": "35260311222333000181550010000010011100010010"},
        )
        assert resp_busca.status_code == 200
        assert len(resp_busca.json()["resultados"]) == 1
        assert resp_busca.json()["resultados"][0]["arquivo_origem"] == "nfe_55_valida.xml"

    def test_get_nota_por_chave_e_por_indice(self, client: TestClient) -> None:
        arquivos = [
            (
                "files",
                (
                    "nfe_55_valida.xml",
                    (PASTA_FIXTURES / "nfe_55_valida.xml").read_bytes(),
                    "application/xml",
                ),
            ),
            (
                "files",
                (
                    "nfse_nacional_valida.xml",
                    (PASTA_FIXTURES / "nfse_nacional_valida.xml").read_bytes(),
                    "application/xml",
                ),
            ),
        ]
        lote_id = client.post("/api/lote", files=arquivos).json()["lote_id"]
        chave_nfe = "35260311222333000181550010000010011100010010"

        # Busca por chave
        resp_chave = client.get(f"/api/lote/{lote_id}/nota/{chave_nfe}")
        assert resp_chave.status_code == 200
        assert resp_chave.json()["documento"]["chave_acesso"] == chave_nfe

        # Busca por índice 0 e 1
        resp_idx0 = client.get(f"/api/lote/{lote_id}/nota/0")
        assert resp_idx0.status_code == 200
        assert resp_idx0.json()["documento"]["chave_acesso"] == chave_nfe

        resp_idx1 = client.get(f"/api/lote/{lote_id}/nota/1")
        assert resp_idx1.status_code == 200
        assert resp_idx1.json()["documento"]["tipo"] == "NFSE"

        # Nota inexistente -> 404
        assert client.get(f"/api/lote/{lote_id}/nota/99").status_code == 404
        assert client.get(f"/api/lote/{lote_id}/nota/CHAVE_INEXISTENTE").status_code == 404
        assert client.get("/api/lote/inexistente/nota/0").status_code == 404

    def test_exportar_lote_csv_itens_csv_e_xlsx(self, client: TestClient) -> None:
        arquivos = [
            (
                "files",
                (
                    "nfe_55_valida.xml",
                    (PASTA_FIXTURES / "nfe_55_valida.xml").read_bytes(),
                    "application/xml",
                ),
            ),
        ]
        lote_id = client.post("/api/lote", files=arquivos).json()["lote_id"]

        # CSV Notas
        resp_csv = client.get(f"/api/lote/{lote_id}/exportar", params={"formato": "csv"})
        assert resp_csv.status_code == 200
        assert "text/csv" in resp_csv.headers["content-type"]
        assert f"lote_{lote_id}.csv" in resp_csv.headers["content-disposition"]
        assert resp_csv.content.startswith(b"\xef\xbb\xbf")

        # CSV Itens
        resp_itens = client.get(f"/api/lote/{lote_id}/exportar", params={"formato": "itens_csv"})
        assert resp_itens.status_code == 200
        assert "text/csv" in resp_itens.headers["content-type"]
        assert ".csv" in resp_itens.headers["content-disposition"]
        assert resp_itens.content.startswith(b"\xef\xbb\xbf")

        # XLSX (padrão)
        resp_xlsx = client.get(f"/api/lote/{lote_id}/exportar", params={"formato": "xlsx"})
        assert resp_xlsx.status_code == 200
        assert (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            in resp_xlsx.headers["content-type"]
        )
        assert f"lote_{lote_id}.xlsx" in resp_xlsx.headers["content-disposition"]
        assert resp_xlsx.content.startswith(b"PK\x03\x04")

        # Lote inexistente -> 404
        assert client.get("/api/lote/nao_existe/exportar").status_code == 404

    def test_repositorio_lru_descarta_lotes_antigos(self) -> None:
        repo = RepositorioLotesMemoria(max_lotes=2)
        lote1 = repo.criar_e_salvar([], {})
        lote2 = repo.criar_e_salvar([], {})
        lote3 = repo.criar_e_salvar([], {})

        assert repo.obter(lote1.lote_id) is None
        assert repo.obter(lote2.lote_id) is not None
        assert repo.obter(lote3.lote_id) is not None


class TestCli:
    """Testes dos subcomandos da CLI (`nfe-local analisar` e `nfe-local servidor`)."""

    def test_cli_analisar_arquivos_com_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        arq1 = str(PASTA_FIXTURES / "nfe_55_valida.xml")
        arq2 = str(PASTA_FIXTURES / "nfse_nacional_valida.xml")

        codigo = cli.main(["analisar", arq1, arq2, "--json"])
        assert codigo == 0

        saida = capsys.readouterr().out
        dados = json.loads(saida)
        assert dados["resumo"]["contagem"]["total_arquivos"] == 2
        assert dados["resumo"]["contagem"]["validos"] == 2
        assert len(dados["resultados"]) == 2

    def test_cli_analisar_pasta_e_exportacoes(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        pasta_entrada = tmp_path / "entrada"
        subpasta = pasta_entrada / "sub"
        subpasta.mkdir(parents=True)

        # Copia um XML para a pasta raiz e cria um ZIP na subpasta
        (pasta_entrada / "nfe_55_valida.xml").write_bytes(
            (PASTA_FIXTURES / "nfe_55_valida.xml").read_bytes()
        )
        (subpasta / "lote.zip").write_bytes(
            _criar_zip_em_memoria(("nfce_65_valida.xml", "nfe_55_soma_errada.xml"))
        )

        caminho_csv = tmp_path / "notas.csv"
        caminho_itens_csv = tmp_path / "itens.csv"
        caminho_xlsx = tmp_path / "relatorio.xlsx"

        codigo = cli.main(
            [
                "analisar",
                str(pasta_entrada),
                "--exportar-csv",
                str(caminho_csv),
                "--exportar-itens-csv",
                str(caminho_itens_csv),
                "--exportar-xlsx",
                str(caminho_xlsx),
            ]
        )
        assert codigo == 0

        saida_texto = capsys.readouterr().out
        assert "3" in saida_texto  # Total de arquivos processados
        assert caminho_csv.is_file() and caminho_csv.read_bytes().startswith(b"\xef\xbb\xbf")
        assert caminho_itens_csv.is_file() and caminho_itens_csv.read_bytes().startswith(
            b"\xef\xbb\xbf"
        )
        assert caminho_xlsx.is_file() and caminho_xlsx.read_bytes().startswith(b"PK\x03\x04")

    def test_cli_analisar_caminho_inexistente_retorna_erro(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        codigo = cli.main(["analisar", "arquivo_que_nao_existe_12345.xml"])
        assert codigo != 0
        erro = capsys.readouterr().err
        assert "arquivo_que_nao_existe_12345.xml" in erro

    def test_cli_servidor_help(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as exc_info:
            cli.main(["servidor", "--help"])
        assert exc_info.value.code == 0
        saida = capsys.readouterr().out
        assert "--host" in saida
        assert "--port" in saida
