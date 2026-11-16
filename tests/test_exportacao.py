"""Testes de exportação de lotes fiscais para CSV e XLSX (`reports/exportacao.py`)."""

from __future__ import annotations

import csv
import io
from decimal import Decimal
from pathlib import Path

from openpyxl import load_workbook

from models import DocumentoFiscal, Item, Parte, ResultadoAnalise, Totais
from parsers import processar_lote
from reports import (
    CABECALHOS_ITENS,
    CABECALHOS_NOTAS,
    exportar_itens_csv,
    exportar_lote_xlsx,
    exportar_notas_csv,
    gerar_resumo_lote,
    sanitizar_celula_texto,
)

PASTA_FIXTURES = Path(__file__).resolve().parent / "fixtures"


class TestSanitizacaoFormulaInjection:
    """Testes de proteção contra CSV/Excel Formula Injection."""

    def test_prefixa_apostrofo_em_caracteres_perigosos(self) -> None:
        assert sanitizar_celula_texto("=CMD|' /C calc'!A0") == "'=CMD|' /C calc'!A0"
        assert sanitizar_celula_texto("+SUM(A1:A10)") == "'+SUM(A1:A10)"
        assert sanitizar_celula_texto("-10+20") == "'-10+20"
        assert sanitizar_celula_texto("@HYPERLINK()") == "'@HYPERLINK()"
        assert sanitizar_celula_texto("\tTAB") == "'\tTAB"
        assert sanitizar_celula_texto("INDUSTRIA ALFA LTDA") == "INDUSTRIA ALFA LTDA"
        assert sanitizar_celula_texto(None) == ""

    def test_csv_e_xlsx_sanitizam_razao_social_e_descricao_item(self) -> None:
        doc = DocumentoFiscal(
            tipo="NFE",
            modelo="55",
            numero="999",
            chave_acesso="35260311222333000181550010000010011100010010",
            emitente=Parte(
                documento="11222333000181",
                tipo_pessoa="PJ",
                razao_social="=CMD|' /C calc'!A0",
            ),
            itens=[
                Item(
                    numero_item=1,
                    codigo="+COD",
                    descricao="@MALICIOSO",
                    valor_total=Decimal("10.00"),
                )
            ],
            totais=Totais(
                valor_produtos_servicos=Decimal("10.00"),
                valor_total_nota=Decimal("10.00"),
            ),
        )
        res = ResultadoAnalise(arquivo_origem="malicioso.xml", valido=True, documento=doc)

        csv_notas = exportar_notas_csv([res]).decode("utf-8-sig")
        assert "'=CMD|' /C calc'!A0" in csv_notas

        csv_itens = exportar_itens_csv([res]).decode("utf-8-sig")
        assert "'+COD" in csv_itens
        assert "'@MALICIOSO" in csv_itens


class TestExportacaoCsvEXlsx:
    """Testes de geração de CSV (notas e itens) e XLSX multi-abas."""

    def _carregar_lote_misto(self) -> list[ResultadoAnalise]:
        arquivos = (
            "nfe_55_valida.xml",
            "nfe_55_cnpj_alfanumerico.xml",
            "nfce_65_valida.xml",
            "nfse_nacional_valida.xml",
            "nfe_55_soma_errada.xml",
            "xml_malformado.xml",
        )
        entradas = [(arq, (PASTA_FIXTURES / arq).read_bytes()) for arq in arquivos]
        return processar_lote(entradas)

    def test_exportar_notas_csv_com_bom_e_xml_malformado(self) -> None:
        resultados = self._carregar_lote_misto()
        dados_csv = exportar_notas_csv(resultados)

        assert dados_csv.startswith(b"\xef\xbb\xbf")
        texto_csv = dados_csv.decode("utf-8-sig")
        leitor = list(csv.reader(io.StringIO(texto_csv), delimiter=";"))

        assert tuple(leitor[0]) == CABECALHOS_NOTAS
        assert len(leitor) == 1 + len(resultados)

        # Última linha é xml_malformado.xml
        linha_malformado = leitor[-1]
        assert linha_malformado[0] == "xml_malformado.xml"
        assert linha_malformado[1] == "NAO"
        assert linha_malformado[2] == "ERRO_XML"
        assert "XML_MALFORMADO" in linha_malformado[-2]

    def test_exportar_itens_csv(self) -> None:
        resultados = self._carregar_lote_misto()
        dados_csv = exportar_itens_csv(resultados)

        assert dados_csv.startswith(b"\xef\xbb\xbf")
        texto_csv = dados_csv.decode("utf-8-sig")
        leitor = list(csv.reader(io.StringIO(texto_csv), delimiter=";"))

        assert tuple(leitor[0]) == CABECALHOS_ITENS
        # nfe_55_valida (2) + alnum (1) + nfce (1) + nfse (1) + soma_errada (2) = 7 itens
        assert len(leitor) == 1 + 7

    def test_exportar_lote_xlsx_4_abas_e_chaves_como_texto(self) -> None:
        resultados = self._carregar_lote_misto()
        resumo = gerar_resumo_lote(resultados)
        dados_xlsx = exportar_lote_xlsx(resultados, resumo=resumo)

        wb = load_workbook(io.BytesIO(dados_xlsx), data_only=True)
        assert wb.sheetnames == ["Notas", "Itens", "Resumo", "Alertas"]

        ws_notas = wb["Notas"]
        assert ws_notas.max_row == 1 + len(resultados)

        # Coluna 8 é chave_acesso: deve ser string exata de 44/50 chars (sem float científico)
        celula_chave_nfe = ws_notas.cell(row=2, column=8)
        assert celula_chave_nfe.data_type == "s"
        assert celula_chave_nfe.value == "35260311222333000181550010000010011100010010"

        celula_chave_alnum = ws_notas.cell(row=3, column=8)
        assert celula_chave_alnum.data_type == "s"
        assert celula_chave_alnum.value == "35260312ABC34501DE35550010000010031100010032"

        celula_chave_nfse = ws_notas.cell(row=5, column=8)
        assert celula_chave_nfse.data_type == "s"
        assert len(str(celula_chave_nfse.value)) == 50

        # Aba Itens
        ws_itens = wb["Itens"]
        assert ws_itens.max_row == 1 + 7

        # Aba Resumo: verifica se o total das notas bate com ResumoLote
        ws_resumo = wb["Resumo"]
        mapa_kpis = {
            ws_resumo.cell(row=r, column=1).value: ws_resumo.cell(row=r, column=2).value
            for r in range(2, 21)
        }
        assert mapa_kpis["Total de Arquivos Processados"] == 6
        assert Decimal(str(mapa_kpis["Valor Total das Notas (R$)"])) == resumo.valor_total_notas

        # Aba Alertas: nfe_55_soma_errada (2 erros) + xml_malformado (1 erro) = 3 alertas
        ws_alertas = wb["Alertas"]
        assert ws_alertas.max_row == 1 + 3
