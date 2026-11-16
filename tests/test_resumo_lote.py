"""Testes do agregador de resumo de lote e processamento em streaming (`reports/resumo.py`)."""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

from parsers import iterar_lote, processar_lote
from reports import gerar_resumo_lote
from validators import calcular_dv_chave_44

PASTA_FIXTURES = Path(__file__).resolve().parent / "fixtures"


class TestResumoLote:
    """Testes de agregação contábil e gerencial por emitente, imposto e período."""

    def test_agregacao_por_emitente_imposto_e_periodo(self) -> None:
        arquivos = (
            "nfe_55_valida.xml",  # Emit ALFA (11222333000181), vProd=1500, vNF=1595, 2026-03-10
            "nfe_55_cnpj_alfanumerico.xml",  # Emit DELTA (12ABC34501DE35), vProd=800, 2026-07-15
            "nfce_65_valida.xml",  # Emit BETA (22333444000181), vProd=120, vNF=120, 2026-03-15
            "nfse_nacional_valida.xml",  # Emit GAMA (33444555000181), vServ=2000, vLiq=1877
            "nfe_evento_cancelamento.xml",  # Evento (não deve somar valores financeiros)
            "xml_malformado.xml",  # Erro fatal XML
        )
        entradas = [(arq, (PASTA_FIXTURES / arq).read_bytes()) for arq in arquivos]
        resultados = processar_lote(entradas)
        resumo = gerar_resumo_lote(resultados)

        assert resumo.contagem.total_arquivos == 6
        assert resumo.contagem.validos == 5
        assert resumo.contagem.com_erros == 1
        assert resumo.contagem.erros_fatais_xml == 1
        assert resumo.contagem.por_tipo["NFE"] == 2
        assert resumo.contagem.por_tipo["NFCE"] == 1
        assert resumo.contagem.por_tipo["NFSE"] == 1
        assert resumo.contagem.por_tipo["EVENTO"] == 1
        assert resumo.contagem.por_tipo["ERRO_XML"] == 1

        # Somatórios financeiros (1500 + 800 + 120 + 2000 = 4420.00)
        assert resumo.valor_produtos_servicos_total == Decimal("4420.00")
        assert resumo.valor_frete_total == Decimal("50.00")
        assert resumo.valor_desconto_total == Decimal("30.00")
        # vNF: 1595 + 800 + 120 + 2000 = 4515.00
        assert resumo.valor_total_notas == Decimal("4515.00")
        # vLiq: 1595 + 800 + 120 + 1877 = 4392.00
        assert resumo.valor_liquido_total == Decimal("4392.00")

        # Impostos consolidados
        imp = resumo.impostos_totais
        assert imp.icms == Decimal("435.60")  # 270 + 144 + 21.60
        assert imp.ipi == Decimal("75.00")
        assert imp.pis == Decimal("52.93")  # 24.75 + 13.20 + 1.98 + 13.00
        assert imp.cofins == Decimal("243.92")  # 114.00 + 60.80 + 9.12 + 60.00
        assert imp.iss == Decimal("100.00")
        assert imp.retencoes_federais == Decimal("123.00")
        assert imp.ibs_cbs == Decimal("38.00")

        # Emitentes ordenados por valor_total_notas decrescente
        assert len(resumo.por_emitente) == 4
        assert resumo.por_emitente[0].documento == "33444555000181"  # 2000.00
        assert resumo.por_emitente[1].documento == "11222333000181"  # 1595.00
        assert resumo.por_emitente[2].documento == "12ABC34501DE35"  # 800.00
        assert resumo.por_emitente[3].documento == "22333444000181"  # 120.00

        # Períodos mensais (2026-03 e 2026-07)
        assert [m.periodo for m in resumo.por_mes] == ["2026-03", "2026-07"]
        assert resumo.por_mes[0].quantidade_notas == 3
        assert resumo.por_mes[0].valor_total_notas == Decimal("3715.00")
        assert resumo.por_mes[1].quantidade_notas == 1
        assert resumo.por_mes[1].valor_total_notas == Decimal("800.00")

        # Períodos diários
        assert [d.periodo for d in resumo.por_dia] == [
            "2026-03-10",
            "2026-03-15",
            "2026-03-16",
            "2026-07-15",
        ]

    def test_protecao_contra_soma_duplicada_no_lote(self) -> None:
        dados_nfe = (PASTA_FIXTURES / "nfe_55_valida.xml").read_bytes()
        entradas = [
            ("nota_original.xml", dados_nfe),
            ("nota_copia.xml", dados_nfe),
        ]
        resultados = processar_lote(entradas)
        resumo = gerar_resumo_lote(resultados)

        assert resumo.contagem.total_arquivos == 2
        assert resumo.contagem.duplicados_ignorados_nos_totais == 1
        assert resumo.valor_total_notas == Decimal("1595.00")
        assert resumo.por_emitente[0].quantidade_notas == 1

    def test_streaming_1000_xmls_com_iterar_lote(self) -> None:
        xml_base = (PASTA_FIXTURES / "nfe_55_sem_protocolo.xml").read_text(encoding="utf-8")
        chave_antiga = "35260311222333000181550010000010021100010026"

        def gerador_1000_notas() -> Iterator[tuple[str, str]]:
            for i in range(1, 1001):
                nnf = f"{i:09d}"
                cnf = f"{10000000 + i:08d}"
                base_43 = f"3526031122233300018155001{nnf}1{cnf}"
                dv = calcular_dv_chave_44(base_43)
                chave_nova = f"{base_43}{dv}"
                xml_i = (
                    xml_base.replace(chave_antiga, chave_nova)
                    .replace("<nNF>1002</nNF>", f"<nNF>{i}</nNF>")
                    .replace("<cNF>10001002</cNF>", f"<cNF>{cnf}</cNF>")
                    .replace("<cDV>6</cDV>", f"<cDV>{dv}</cDV>")
                )
                yield (f"nota_{i:04d}.xml", xml_i)

        stream_resultados = iterar_lote(gerador_1000_notas())
        resumo = gerar_resumo_lote(stream_resultados)

        assert resumo.contagem.total_arquivos == 1000
        assert resumo.contagem.validos == 1000
        assert resumo.contagem.com_erros == 0
        assert resumo.contagem.com_avisos == 1000  # SEM_PROTOCOLO é aviso
        assert resumo.contagem.duplicados_ignorados_nos_totais == 0
        assert resumo.valor_total_notas == Decimal("500000.00")
        assert resumo.impostos_totais.icms == Decimal("90000.00")
