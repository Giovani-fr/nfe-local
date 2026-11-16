"""Exportação de lotes fiscais para CSV (UTF-8 com BOM) e XLSX multi-abas (`openpyxl`)."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.worksheet import Worksheet

from models.fiscal import ResultadoAnalise
from reports.resumo import ResumoLote, gerar_resumo_lote

_PREFIXOS_FORMULA_PERIGOSOS = ("=", "+", "-", "@", "\t", "\r")
_FORMATO_MOEDA = "#,##0.00"
_FORMATO_QUANTIDADE = "#,##0.0000"

CABECALHOS_NOTAS = (
    "arquivo_origem",
    "valido",
    "tipo",
    "modelo",
    "situacao",
    "numero",
    "serie",
    "chave_acesso",
    "data_emissao",
    "ambiente",
    "protocolo",
    "emitente_documento",
    "emitente_razao_social",
    "emitente_uf",
    "destinatario_documento",
    "destinatario_razao_social",
    "destinatario_uf",
    "qtd_itens",
    "valor_produtos_servicos",
    "valor_frete",
    "valor_desconto",
    "valor_liquido",
    "valor_total_nota",
    "icms",
    "icms_st",
    "ipi",
    "pis",
    "cofins",
    "iss",
    "retencoes_federais",
    "ibs_cbs",
    "qtd_erros",
    "qtd_avisos",
    "codigos_alertas",
    "erro_fatal",
)

CABECALHOS_ITENS = (
    "arquivo_origem",
    "tipo",
    "modelo",
    "numero_nota",
    "chave_acesso",
    "data_emissao",
    "emitente_documento",
    "emitente_razao_social",
    "numero_item",
    "codigo",
    "descricao",
    "ncm",
    "cfop",
    "codigo_tributacao_nacional",
    "unidade",
    "quantidade",
    "valor_unitario",
    "valor_desconto",
    "valor_total",
    "cst_ou_csosn",
    "icms_bc",
    "icms_aliquota",
    "icms_valor",
    "icms_st_valor",
    "ipi_valor",
    "pis_valor",
    "cofins_valor",
    "iss_valor",
    "iss_retido",
)

CABECALHOS_ALERTAS = (
    "arquivo_origem",
    "chave_acesso",
    "numero_nota",
    "emitente",
    "severidade",
    "codigo",
    "campo",
    "mensagem",
)


def sanitizar_celula_texto(valor: str | None) -> str:
    """Neutraliza CSV/Excel Formula Injection prefixando `'` em células iniciadas por `=+-@`."""
    if not valor:
        return ""
    texto = str(valor)
    if texto.startswith(_PREFIXOS_FORMULA_PERIGOSOS):
        return f"'{texto}"
    return texto


def _formatar_data_iso(res: ResultadoAnalise) -> str:
    doc = res.documento
    if doc is None:
        return ""
    if doc.data_emissao is not None:
        return doc.data_emissao.isoformat()
    if doc.competencia is not None:
        return doc.competencia.isoformat()
    return ""


def _linha_nota_valores(
    res: ResultadoAnalise,
    *,
    para_xlsx: bool,
) -> list[str | int | float | Decimal]:
    qtd_erros = sum(1 for a in res.alertas if a.severidade == "ERRO")
    qtd_avisos = sum(1 for a in res.alertas if a.severidade == "AVISO")
    codigos_alertas = ", ".join(a.codigo for a in res.alertas)
    status_valido = "SIM" if res.valido else "NAO"
    doc = res.documento

    def _num(val: Decimal) -> str | Decimal:
        return val if para_xlsx else f"{val:.2f}"

    if doc is None:
        z = _num(Decimal("0.00"))
        return [
            sanitizar_celula_texto(res.arquivo_origem),
            status_valido,
            "ERRO_XML",
            "",
            "ERRO_XML",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            0,
            z,
            z,
            z,
            z,
            z,
            z,
            z,
            z,
            z,
            z,
            z,
            z,
            z,
            qtd_erros,
            qtd_avisos,
            sanitizar_celula_texto(codigos_alertas),
            sanitizar_celula_texto(res.erro_fatal),
        ]

    dest = doc.destinatario
    tot = doc.totais
    imp = tot.impostos
    v_liq = tot.valor_liquido if tot.valor_liquido != Decimal("0.00") else tot.valor_total_nota

    return [
        sanitizar_celula_texto(res.arquivo_origem),
        status_valido,
        doc.tipo,
        doc.modelo,
        doc.situacao,
        sanitizar_celula_texto(doc.numero),
        sanitizar_celula_texto(doc.serie),
        sanitizar_celula_texto(doc.chave_acesso),
        _formatar_data_iso(res),
        str(doc.ambiente),
        sanitizar_celula_texto(doc.protocolo),
        sanitizar_celula_texto(doc.emitente.documento),
        sanitizar_celula_texto(doc.emitente.razao_social),
        sanitizar_celula_texto(doc.emitente.uf),
        sanitizar_celula_texto(dest.documento if dest else None),
        sanitizar_celula_texto(dest.razao_social if dest else None),
        sanitizar_celula_texto(dest.uf if dest else None),
        len(doc.itens),
        _num(tot.valor_produtos_servicos),
        _num(tot.valor_frete),
        _num(tot.valor_desconto),
        _num(v_liq),
        _num(tot.valor_total_nota),
        _num(imp.icms),
        _num(imp.icms_st),
        _num(imp.ipi),
        _num(imp.pis),
        _num(imp.cofins),
        _num(imp.iss),
        _num(imp.retencoes_federais),
        _num(imp.ibs_cbs),
        qtd_erros,
        qtd_avisos,
        sanitizar_celula_texto(codigos_alertas),
        sanitizar_celula_texto(res.erro_fatal),
    ]


def _linhas_itens_valores(
    res: ResultadoAnalise,
    *,
    para_xlsx: bool,
) -> list[list[str | int | float | Decimal]]:
    doc = res.documento
    if doc is None or not doc.itens:
        return []

    def _moeda(val: Decimal) -> str | Decimal:
        return val if para_xlsx else f"{val:.2f}"

    def _qtd(val: Decimal) -> str | Decimal:
        return val if para_xlsx else f"{val:.4f}"

    data_iso = _formatar_data_iso(res)
    linhas: list[list[str | int | float | Decimal]] = []

    for item in doc.itens:
        imp = item.imposto
        linhas.append(
            [
                sanitizar_celula_texto(res.arquivo_origem),
                doc.tipo,
                doc.modelo,
                sanitizar_celula_texto(doc.numero),
                sanitizar_celula_texto(doc.chave_acesso),
                data_iso,
                sanitizar_celula_texto(doc.emitente.documento),
                sanitizar_celula_texto(doc.emitente.razao_social),
                item.numero_item,
                sanitizar_celula_texto(item.codigo),
                sanitizar_celula_texto(item.descricao),
                sanitizar_celula_texto(item.ncm),
                sanitizar_celula_texto(item.cfop),
                sanitizar_celula_texto(item.codigo_tributacao_nacional),
                sanitizar_celula_texto(item.unidade),
                _qtd(item.quantidade),
                _qtd(item.valor_unitario),
                _moeda(item.valor_desconto),
                _moeda(item.valor_total),
                sanitizar_celula_texto(imp.cst_ou_csosn),
                _moeda(imp.icms_bc),
                _moeda(imp.icms_aliquota),
                _moeda(imp.icms_valor),
                _moeda(imp.icms_st_valor),
                _moeda(imp.ipi_valor),
                _moeda(imp.pis_valor),
                _moeda(imp.cofins_valor),
                _moeda(imp.iss_valor),
                "SIM" if imp.iss_retido else "NAO",
            ]
        )
    return linhas


def exportar_notas_csv(
    resultados: Iterable[ResultadoAnalise],
    separador: str = ";",
    incluir_bom: bool = True,
) -> bytes:
    """Exporta todas as notas analisadas para CSV (UTF-8 com BOM por padrão)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=separador, lineterminator="\r\n")
    writer.writerow(CABECALHOS_NOTAS)

    for res in resultados:
        writer.writerow(_linha_nota_valores(res, para_xlsx=False))

    codificacao = "utf-8-sig" if incluir_bom else "utf-8"
    return buffer.getvalue().encode(codificacao)


def exportar_itens_csv(
    resultados: Iterable[ResultadoAnalise],
    separador: str = ";",
    incluir_bom: bool = True,
) -> bytes:
    """Exporta todos os itens das notas analisadas para CSV (UTF-8 com BOM por padrão)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=separador, lineterminator="\r\n")
    writer.writerow(CABECALHOS_ITENS)

    for res in resultados:
        for linha in _linhas_itens_valores(res, para_xlsx=False):
            writer.writerow(linha)

    codificacao = "utf-8-sig" if incluir_bom else "utf-8"
    return buffer.getvalue().encode(codificacao)


def _estilizar_cabecalho_aba(ws: Worksheet, num_colunas: int, linha: int = 1) -> None:
    fill_header = PatternFill(start_color="1F2937", end_color="1F2937", fill_type="solid")
    font_header = Font(bold=True, color="FFFFFF")
    align_header = Alignment(horizontal="center", vertical="center")

    for col in range(1, num_colunas + 1):
        celula = ws.cell(row=linha, column=col)
        celula.fill = fill_header
        celula.font = font_header
        celula.alignment = align_header


def _gravar_linha_formatada(
    ws: Worksheet,
    indice_linha: int,
    valores: list[str | int | float | Decimal],
    colunas_texto_forcado: set[int],
    colunas_moeda: set[int],
    colunas_quantidade: set[int] | None = None,
) -> None:
    cols_qtd = colunas_quantidade or set()
    for idx_col, valor in enumerate(valores, start=1):
        celula = ws.cell(row=indice_linha, column=idx_col)
        if idx_col in colunas_texto_forcado:
            celula.data_type = "s"
            celula.number_format = "@"
            celula.value = "" if valor is None else str(valor)
        elif idx_col in colunas_moeda and isinstance(valor, (Decimal, int, float)):
            celula.value = valor
            celula.number_format = _FORMATO_MOEDA
        elif idx_col in cols_qtd and isinstance(valor, (Decimal, int, float)):
            celula.value = valor
            celula.number_format = _FORMATO_QUANTIDADE
        else:
            celula.value = valor


def _preencher_aba_resumo(ws: Worksheet, resumo: ResumoLote) -> None:
    ws.freeze_panes = "A2"
    ws.append(["Indicador do Lote", "Valor / Quantidade"])
    _estilizar_cabecalho_aba(ws, 2, linha=1)

    c = resumo.contagem
    imp = resumo.impostos_totais
    kpis: list[tuple[str, int | Decimal, bool]] = [
        ("Total de Arquivos Processados", c.total_arquivos, False),
        ("Documentos Válidos", c.validos, False),
        ("Documentos com Erros", c.com_erros, False),
        ("Documentos com Avisos", c.com_avisos, False),
        ("Erros Fatais de XML/Layout", c.erros_fatais_xml, False),
        ("Duplicados Ignorados nos Totais", c.duplicados_ignorados_nos_totais, False),
        ("Valor Total Produtos/Serviços (R$)", resumo.valor_produtos_servicos_total, True),
        ("Valor Total Frete (R$)", resumo.valor_frete_total, True),
        ("Valor Total Desconto (R$)", resumo.valor_desconto_total, True),
        ("Valor Líquido Total (R$)", resumo.valor_liquido_total, True),
        ("Valor Total das Notas (R$)", resumo.valor_total_notas, True),
        ("ICMS Total (R$)", imp.icms, True),
        ("ICMS-ST Total (R$)", imp.icms_st, True),
        ("IPI Total (R$)", imp.ipi, True),
        ("PIS Total (R$)", imp.pis, True),
        ("COFINS Total (R$)", imp.cofins, True),
        ("ISS Total (R$)", imp.iss, True),
        ("Retenções Federais Total (R$)", imp.retencoes_federais, True),
        ("IBS/CBS Total (R$)", imp.ibs_cbs, True),
    ]
    for rotulo, val, eh_moeda in kpis:
        ws.append([rotulo, val])
        if eh_moeda:
            ws.cell(row=ws.max_row, column=2).number_format = _FORMATO_MOEDA

    ws.append([])
    cab_emit = [
        "CNPJ/CPF Emitente",
        "Razão Social",
        "UF",
        "Qtd Notas",
        "Valor Produtos/Serviços",
        "Valor Líquido",
        "Valor Total Notas",
        "ICMS",
        "IPI",
        "PIS",
        "COFINS",
        "ISS",
        "Retenções Federais",
        "IBS/CBS",
    ]
    ws.append(cab_emit)
    _estilizar_cabecalho_aba(ws, len(cab_emit), linha=ws.max_row)

    for em in resumo.por_emitente:
        linha_idx = ws.max_row + 1
        valores_em: list[str | int | float | Decimal] = [
            sanitizar_celula_texto(em.documento),
            sanitizar_celula_texto(em.razao_social),
            sanitizar_celula_texto(em.uf),
            em.quantidade_notas,
            em.valor_produtos_servicos,
            em.valor_liquido,
            em.valor_total_notas,
            em.impostos.icms,
            em.impostos.ipi,
            em.impostos.pis,
            em.impostos.cofins,
            em.impostos.iss,
            em.impostos.retencoes_federais,
            em.impostos.ibs_cbs,
        ]
        _gravar_linha_formatada(
            ws,
            linha_idx,
            valores_em,
            colunas_texto_forcado={1, 2, 3},
            colunas_moeda=set(range(5, 15)),
        )

    ws.append([])
    cab_mes = [
        "Período (YYYY-MM)",
        "Qtd Notas",
        "Valor Produtos/Serviços",
        "Valor Líquido",
        "Valor Total Notas",
        "ICMS",
        "IPI",
        "ISS",
        "IBS/CBS",
    ]
    ws.append(cab_mes)
    _estilizar_cabecalho_aba(ws, len(cab_mes), linha=ws.max_row)

    for per in resumo.por_mes:
        linha_idx = ws.max_row + 1
        valores_per: list[str | int | float | Decimal] = [
            sanitizar_celula_texto(per.periodo),
            per.quantidade_notas,
            per.valor_produtos_servicos,
            per.valor_liquido,
            per.valor_total_notas,
            per.impostos.icms,
            per.impostos.ipi,
            per.impostos.iss,
            per.impostos.ibs_cbs,
        ]
        _gravar_linha_formatada(
            ws,
            linha_idx,
            valores_per,
            colunas_texto_forcado={1},
            colunas_moeda=set(range(3, 10)),
        )


def exportar_lote_xlsx(
    resultados: Iterable[ResultadoAnalise],
    resumo: ResumoLote | None = None,
) -> bytes:
    """Exporta o lote analisado para um arquivo XLSX em memória com 4 abas estruturadas."""
    lista_resultados = list(resultados)
    resumo_consolidado = resumo if resumo is not None else gerar_resumo_lote(lista_resultados)

    wb = Workbook()
    ws_notas = wb.active or wb.create_sheet(title="Notas")
    ws_notas.title = "Notas"
    ws_itens = wb.create_sheet(title="Itens")
    ws_resumo = wb.create_sheet(title="Resumo")
    ws_alertas = wb.create_sheet(title="Alertas")

    # Aba 1: Notas
    ws_notas.freeze_panes = "A2"
    ws_notas.append(list(CABECALHOS_NOTAS))
    _estilizar_cabecalho_aba(ws_notas, len(CABECALHOS_NOTAS))
    cols_texto_notas = {6, 7, 8, 11, 12, 15}
    cols_moeda_notas = set(range(19, 32))

    for idx_linha, res in enumerate(lista_resultados, start=2):
        valores_nota = _linha_nota_valores(res, para_xlsx=True)
        _gravar_linha_formatada(
            ws_notas,
            idx_linha,
            valores_nota,
            colunas_texto_forcado=cols_texto_notas,
            colunas_moeda=cols_moeda_notas,
        )

    # Aba 2: Itens
    ws_itens.freeze_panes = "A2"
    ws_itens.append(list(CABECALHOS_ITENS))
    _estilizar_cabecalho_aba(ws_itens, len(CABECALHOS_ITENS))
    cols_texto_itens = {4, 5, 7, 10, 12, 13, 14, 20}
    cols_qtd_itens = {16, 17}
    cols_moeda_itens = {18, 19, 21, 22, 23, 24, 25, 26, 27, 28}

    linha_item_atual = 2
    for res in lista_resultados:
        for valores_item in _linhas_itens_valores(res, para_xlsx=True):
            _gravar_linha_formatada(
                ws_itens,
                linha_item_atual,
                valores_item,
                colunas_texto_forcado=cols_texto_itens,
                colunas_moeda=cols_moeda_itens,
                colunas_quantidade=cols_qtd_itens,
            )
            linha_item_atual += 1

    # Aba 3: Resumo
    _preencher_aba_resumo(ws_resumo, resumo_consolidado)

    # Aba 4: Alertas
    ws_alertas.freeze_panes = "A2"
    ws_alertas.append(list(CABECALHOS_ALERTAS))
    _estilizar_cabecalho_aba(ws_alertas, len(CABECALHOS_ALERTAS))
    linha_alerta_atual = 2
    for res in lista_resultados:
        doc = res.documento
        chave = doc.chave_acesso if doc else ""
        num_nota = doc.numero if doc else ""
        nome_emit = (doc.emitente.razao_social or doc.emitente.documento) if doc else ""
        for alerta in res.alertas:
            valores_alerta: list[str | int | float | Decimal] = [
                sanitizar_celula_texto(res.arquivo_origem),
                sanitizar_celula_texto(chave),
                sanitizar_celula_texto(num_nota),
                sanitizar_celula_texto(nome_emit),
                alerta.severidade,
                sanitizar_celula_texto(alerta.codigo),
                sanitizar_celula_texto(alerta.campo),
                sanitizar_celula_texto(alerta.mensagem),
            ]
            _gravar_linha_formatada(
                ws_alertas,
                linha_alerta_atual,
                valores_alerta,
                colunas_texto_forcado={1, 2, 3, 4, 6, 7, 8},
                colunas_moeda=set(),
            )
            linha_alerta_atual += 1

    buffer_xlsx = io.BytesIO()
    wb.save(buffer_xlsx)
    return buffer_xlsx.getvalue()
