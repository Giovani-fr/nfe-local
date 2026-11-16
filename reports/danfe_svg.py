"""Formatadores pt-BR, Code 128 SVG puro e contextos DANFE/DANFSe (`reports/danfe_svg.py`)."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from models.fiscal import DocumentoFiscal, Parte, ResultadoAnalise
from models.normalizadores import DECIMAL_ZERO, limpar_chave_acesso, limpar_documento

# Tabela oficial ISO/IEC 15417 — larguras de barras/espaços (valores 0 a 106)
_CODE128_PATTERNS: tuple[str, ...] = (
    "212222",
    "222122",
    "222221",
    "121223",
    "121322",
    "131222",
    "122213",
    "122312",
    "132212",
    "221213",
    "221312",
    "231212",
    "112232",
    "122132",
    "122231",
    "113222",
    "123122",
    "123221",
    "223211",
    "221132",
    "221231",
    "213212",
    "223112",
    "312131",
    "311222",
    "321122",
    "321221",
    "312212",
    "322112",
    "322211",
    "212123",
    "212321",
    "232121",
    "111323",
    "131123",
    "131321",
    "112313",
    "132113",
    "132311",
    "211313",
    "231113",
    "231311",
    "112133",
    "112331",
    "132131",
    "113123",
    "113321",
    "133121",
    "313121",
    "211331",
    "231131",
    "213113",
    "213311",
    "213131",
    "311123",
    "311321",
    "331121",
    "312113",
    "312311",
    "332111",
    "314111",
    "221411",
    "431111",
    "111224",
    "111422",
    "121124",
    "121421",
    "141122",
    "141221",
    "112214",
    "112412",
    "122114",
    "122411",
    "142112",
    "142211",
    "241211",
    "221114",
    "413111",
    "241112",
    "134111",
    "111242",
    "121142",
    "121241",
    "114212",
    "124112",
    "124211",
    "411212",
    "421112",
    "421211",
    "212141",
    "214121",
    "412121",
    "111143",
    "111341",
    "131141",
    "114113",
    "114311",
    "411113",
    "411311",
    "113141",
    "114131",
    "311141",
    "411131",
    "211412",  # 103: Start A
    "211214",  # 104: Start B
    "211232",  # 105: Start C
    "2331112",  # 106: Stop (13 módulos / 7 elementos)
)

_RE_44_NUMERICO = re.compile(r"^\d{44}$")
_RE_44_ALFANUMERICO = re.compile(r"^[0-9A-Z]{44}$")


def formatar_moeda(valor: Decimal | float | int | None, casas: int = 2) -> str:
    """Formata um valor numérico no padrão monetário brasileiro (`1.234,56`)."""
    if valor is None:
        num = Decimal("0")
    elif isinstance(valor, Decimal):
        num = valor
    else:
        num = Decimal(str(valor))

    formato_en = f"{num:,.{casas}f}"
    return formato_en.replace(",", "_").replace(".", ",").replace("_", ".")


def formatar_cnpj_cpf(doc: str | None) -> str:
    """Formata CPF (11 dígitos) ou CNPJ (14 posições numéricas ou alfanuméricas NT 2014.002)."""
    limpo = limpar_documento(doc)
    if not limpo:
        return ""
    if len(limpo) == 14:
        return f"{limpo[:2]}.{limpo[2:5]}.{limpo[5:8]}/{limpo[8:12]}-{limpo[12:]}"
    if len(limpo) == 11 and limpo.isdigit():
        return f"{limpo[:3]}.{limpo[3:6]}.{limpo[6:9]}-{limpo[9:]}"
    return limpo


def formatar_chave(chave: str | None) -> str:
    """Agrupa a chave de acesso em blocos de 4 caracteres separados por espaço."""
    limpa = limpar_chave_acesso(chave)
    if not limpa:
        return ""
    return " ".join(limpa[i : i + 4] for i in range(0, len(limpa), 4))


def formatar_cep(cep: str | None) -> str:
    """Formata CEP no padrão `00000-000`."""
    if not cep:
        return ""
    digitos = re.sub(r"\D", "", str(cep))
    if len(digitos) == 8:
        return f"{digitos[:5]}-{digitos[5:]}"
    return str(cep).strip()


def formatar_numero_nf(numero: str | int | None) -> str:
    """Formata o número da NF no padrão `000.001.001`."""
    if numero is None or str(numero).strip() == "":
        return "000.000.000"
    digitos = re.sub(r"\D", "", str(numero))
    if not digitos:
        return str(numero).strip()
    preenchido = digitos.zfill(9)
    if len(preenchido) == 9:
        return f"{preenchido[:3]}.{preenchido[3:6]}.{preenchido[6:]}"
    return digitos


def formatar_data_hora_br(dt: datetime | date | None) -> str:
    """Formata `datetime` ou `date` no padrão brasileiro (`DD/MM/YYYY HH:MM:SS` ou `DD/MM/YYYY`)."""
    if dt is None:
        return ""
    if isinstance(dt, datetime):
        return dt.strftime("%d/%m/%Y %H:%M:%S")
    return dt.strftime("%d/%m/%Y")


def formatar_data_br(dt: datetime | date | None) -> str:
    """Formata apenas a data `DD/MM/YYYY`."""
    if dt is None:
        return ""
    return dt.strftime("%d/%m/%Y")


def barcode_svg(chave: str | None) -> str:
    """Gera código de barras Code 128 em SVG puro (Subset C numérico, Subset B alfanumérico)."""
    limpa = limpar_chave_acesso(chave)
    if not limpa or len(limpa) != 44:
        return ""

    if _RE_44_NUMERICO.match(limpa):
        subset = "C"
        codigos = [105]  # Start C
        for i in range(0, 44, 2):
            codigos.append(int(limpa[i : i + 2]))
    elif _RE_44_ALFANUMERICO.match(limpa):
        subset = "B"
        codigos = [104]  # Start B
        for ch in limpa:
            codigos.append(ord(ch) - 32)
    else:
        return ""

    checksum = codigos[0] + sum(idx * val for idx, val in enumerate(codigos[1:], start=1))
    codigos.append(checksum % 103)
    codigos.append(106)  # Stop

    x = 10  # Quiet zone esquerda
    retangulos: list[str] = []
    for codigo in codigos:
        padrao = _CODE128_PATTERNS[codigo]
        for idx_elem, char_largura in enumerate(padrao):
            largura = int(char_largura)
            if idx_elem % 2 == 0:
                retangulos.append(
                    f'<rect x="{x}" y="0" width="{largura}" height="40" fill="#000"/>'
                )
            x += largura

    largura_total = x + 10  # Quiet zone direita
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" data-subset="{subset}" '
        f'viewBox="0 0 {largura_total} 40" width="100%" height="44" '
        f'preserveAspectRatio="none" role="img" aria-label="Código de barras da chave de acesso">'
        f"{''.join(retangulos)}</svg>"
    )


def _formatar_parte(parte: Parte | None) -> dict[str, str]:
    if parte is None:
        return {
            "documento": "",
            "documento_fmt": "",
            "razao_social": "NÃO IDENTIFICADO",
            "nome_fantasia": "",
            "inscricao_estadual": "",
            "inscricao_municipal": "",
            "uf": "",
            "municipio": "",
            "codigo_ibge": "",
            "endereco": "",
            "cep_fmt": "",
            "telefone": "",
            "email": "",
        }
    return {
        "documento": parte.documento,
        "documento_fmt": formatar_cnpj_cpf(parte.documento),
        "razao_social": parte.razao_social or "NÃO INFORMADO",
        "nome_fantasia": parte.nome_fantasia or "",
        "inscricao_estadual": parte.inscricao_estadual or "ISENTO",
        "inscricao_municipal": parte.inscricao_municipal or "",
        "uf": parte.uf or "",
        "municipio": parte.municipio or "",
        "codigo_ibge": parte.codigo_ibge or "",
        "endereco": parte.endereco or "",
        "cep_fmt": formatar_cep(parte.cep),
        "telefone": parte.telefone or "",
        "email": parte.email or "",
    }


def _obter_marca_dagua(doc: DocumentoFiscal) -> str | None:
    if doc.situacao == "CANCELADA":
        return "DOCUMENTO CANCELADO — SEM VALOR FISCAL"
    if doc.ambiente == 2:
        return "SEM VALOR FISCAL — EMITIDA EM HOMOLOGAÇÃO"
    if doc.situacao == "SEM_PROTOCOLO":
        return "SEM PROTOCOLO DE AUTORIZAÇÃO — VERIFICAR VALIDADE"
    if doc.tipo == "RESUMO":
        return "RESUMO DE NF-e (resNFe) — MANIFESTAÇÃO NECESSÁRIA"
    return None


def montar_contexto_danfe(res: ResultadoAnalise) -> dict[str, Any]:
    """Prepara o dicionário de contexto formatado em pt-BR para renderização do `danfe.html`."""
    doc = res.documento
    if doc is None:
        raise ValueError("ResultadoAnalise não possui DocumentoFiscal para gerar DANFE.")

    tot = doc.totais
    imp = tot.impostos
    v_liq = tot.valor_liquido if tot.valor_liquido != DECIMAL_ZERO else tot.valor_total_nota

    itens_fmt: list[dict[str, Any]] = []
    for item in doc.itens:
        im = item.imposto
        itens_fmt.append(
            {
                "numero_item": item.numero_item,
                "codigo": item.codigo,
                "descricao": item.descricao,
                "info_adicional": item.info_adicional or "",
                "ncm": item.ncm or "",
                "cst_ou_csosn": im.cst_ou_csosn or "",
                "cfop": item.cfop or "",
                "unidade": item.unidade,
                "quantidade_fmt": formatar_moeda(item.quantidade, casas=4),
                "valor_unitario_fmt": formatar_moeda(item.valor_unitario, casas=4),
                "valor_desconto_fmt": formatar_moeda(item.valor_desconto, casas=2),
                "valor_total_fmt": formatar_moeda(item.valor_total, casas=2),
                "icms_bc_fmt": formatar_moeda(im.icms_bc, casas=2),
                "icms_valor_fmt": formatar_moeda(im.icms_valor, casas=2),
                "ipi_valor_fmt": formatar_moeda(im.ipi_valor, casas=2),
                "icms_aliquota_fmt": formatar_moeda(im.icms_aliquota, casas=2),
                "ipi_aliquota_fmt": formatar_moeda(im.ipi_aliquota, casas=2),
            }
        )

    pagamentos_fmt: list[dict[str, str]] = []
    for pag in doc.pagamentos:
        pagamentos_fmt.append(
            {
                "forma_codigo": pag.forma_codigo,
                "forma_descricao": pag.forma_descricao,
                "numero_duplicata": pag.numero_duplicata or "-",
                "vencimento_fmt": formatar_data_br(pag.vencimento) or "À vista",
                "valor_fmt": formatar_moeda(pag.valor),
            }
        )

    return {
        "arquivo_origem": res.arquivo_origem,
        "valido": res.valido,
        "alertas": [a.model_dump() for a in res.alertas],
        "tipo": doc.tipo,
        "modelo": doc.modelo,
        "serie": doc.serie or "1",
        "numero": doc.numero,
        "numero_fmt": formatar_numero_nf(doc.numero),
        "chave_acesso": doc.chave_acesso,
        "chave_fmt": formatar_chave(doc.chave_acesso),
        "barcode_svg": barcode_svg(doc.chave_acesso),
        "data_emissao_fmt": formatar_data_hora_br(doc.data_emissao),
        "data_emissao_curta": formatar_data_br(doc.data_emissao),
        "ambiente": doc.ambiente,
        "ambiente_desc": "1 - Produção"
        if doc.ambiente == 1
        else "2 - Homologação (Sem Valor Fiscal)",
        "protocolo": doc.protocolo or "SEM PROTOCOLO DE AUTORIZAÇÃO",
        "situacao": doc.situacao,
        "natureza_operacao": doc.natureza_operacao or "OPERAÇÃO FISCAL",
        "marca_dagua": _obter_marca_dagua(doc),
        "emitente": _formatar_parte(doc.emitente),
        "destinatario": _formatar_parte(doc.destinatario),
        "totais": {
            "icms_bc_fmt": formatar_moeda(imp.icms_bc),
            "icms_fmt": formatar_moeda(imp.icms),
            "icms_st_bc_fmt": formatar_moeda(imp.icms_st_bc),
            "icms_st_fmt": formatar_moeda(imp.icms_st),
            "valor_produtos_fmt": formatar_moeda(tot.valor_produtos_servicos),
            "valor_frete_fmt": formatar_moeda(tot.valor_frete),
            "valor_seguro_fmt": formatar_moeda(tot.valor_seguro),
            "valor_desconto_fmt": formatar_moeda(tot.valor_desconto),
            "valor_outras_fmt": formatar_moeda(tot.valor_outras_despesas),
            "ipi_fmt": formatar_moeda(imp.ipi),
            "pis_fmt": formatar_moeda(imp.pis),
            "cofins_fmt": formatar_moeda(imp.cofins),
            "ibs_cbs_fmt": formatar_moeda(imp.ibs_cbs),
            "tributos_aprox_fmt": formatar_moeda(imp.tributos_aproximados),
            "valor_liquido_fmt": formatar_moeda(v_liq),
            "valor_total_nota_fmt": formatar_moeda(tot.valor_total_nota),
        },
        "itens": itens_fmt,
        "pagamentos": pagamentos_fmt,
        "informacoes_complementares": doc.informacoes_complementares or "",
        "descricao_evento": doc.descricao_evento or "",
    }


def montar_contexto_danfse(res: ResultadoAnalise) -> dict[str, Any]:
    """Prepara o dicionário de contexto em pt-BR para o `danfse.html` (NT 008/2026)."""
    doc = res.documento
    if doc is None:
        raise ValueError("ResultadoAnalise não possui DocumentoFiscal para gerar DANFSe.")

    tot = doc.totais
    imp = tot.impostos
    v_liq = tot.valor_liquido if tot.valor_liquido != DECIMAL_ZERO else tot.valor_total_nota

    item_principal = doc.itens[0] if doc.itens else None
    iss_aliquota = item_principal.imposto.iss_aliquota if item_principal else DECIMAL_ZERO
    iss_retido = item_principal.imposto.iss_retido if item_principal else False

    return {
        "arquivo_origem": res.arquivo_origem,
        "valido": res.valido,
        "alertas": [a.model_dump() for a in res.alertas],
        "numero": doc.numero,
        "numero_fmt": formatar_numero_nf(doc.numero),
        "serie": doc.serie or "1",
        "chave_acesso": doc.chave_acesso,
        "chave_fmt": formatar_chave(doc.chave_acesso),
        "data_emissao_fmt": formatar_data_hora_br(doc.data_emissao),
        "competencia_fmt": formatar_data_br(doc.competencia or doc.data_emissao),
        "ambiente": doc.ambiente,
        "ambiente_desc": "1 - Produção" if doc.ambiente == 1 else "2 - Homologação",
        "situacao": doc.situacao,
        "natureza_operacao": doc.natureza_operacao or "Tributação no Município",
        "marca_dagua": _obter_marca_dagua(doc),
        "prestador": _formatar_parte(doc.emitente),
        "tomador": _formatar_parte(doc.destinatario),
        "servico": {
            "codigo_tributacao_nacional": (
                item_principal.codigo_tributacao_nacional if item_principal else ""
            )
            or "-",
            "codigo_nbs": (item_principal.codigo_nbs if item_principal else "") or "-",
            "codigo_interno": (item_principal.codigo if item_principal else "") or "-",
            "descricao": (item_principal.descricao if item_principal else "")
            or "Serviço prestado conforme discriminação.",
            "iss_aliquota_fmt": formatar_moeda(iss_aliquota),
            "iss_retido_str": "SIM (Retido pelo Tomador)" if iss_retido else "NÃO",
        },
        "totais": {
            "valor_servicos_fmt": formatar_moeda(tot.valor_produtos_servicos),
            "valor_desconto_fmt": formatar_moeda(tot.valor_desconto),
            "valor_liquido_fmt": formatar_moeda(v_liq),
            "valor_total_nota_fmt": formatar_moeda(tot.valor_total_nota),
            "iss_fmt": formatar_moeda(imp.iss),
            "pis_fmt": formatar_moeda(imp.pis),
            "cofins_fmt": formatar_moeda(imp.cofins),
            "retencoes_federais_fmt": formatar_moeda(imp.retencoes_federais),
            "ibs_cbs_fmt": formatar_moeda(imp.ibs_cbs),
        },
        "informacoes_complementares": doc.informacoes_complementares or "",
    }
