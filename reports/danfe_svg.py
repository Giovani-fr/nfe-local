"""Formatadores pt-BR, Code 128 SVG puro e contextos DANFE/DANFSe (`reports/danfe_svg.py`)."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from lxml import etree

from models.fiscal import DocumentoFiscal, Parte, ResultadoAnalise
from models.normalizadores import (
    DECIMAL_ZERO,
    limpar_chave_acesso,
    limpar_documento,
    para_data_hora,
)
from parsers.xml_seguro import (
    ErroParseXML,
    nome_local,
    parse_xml_seguro,
    primeiro,
    primeiro_com_filho,
    texto,
    todos,
)

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

# Domínios oficiais NF-e (modalidade de frete)
_MODALIDADE_FRETE: dict[str, str] = {
    "0": "0-Emitente",
    "1": "1-Destinatário",
    "2": "2-Terceiros",
    "3": "3-Próprio rem.",
    "4": "4-Próprio dest.",
    "9": "9-Sem frete",
}

# Domínios oficiais da NFS-e Padrão Nacional (NT 008/2026)
_NFSE_TP_EMIT: dict[str, str] = {"1": "Prestador", "2": "Tomador", "3": "Intermediário"}

_NFSE_C_STAT: dict[str, str] = {
    "100": "NFS-e emitida",
    "101": "NFS-e cancelada",
    "102": "NFS-e cancelada por substituição",
    "103": "NFS-e substituída",
    "105": "NFS-e de decisão judicial/administrativa",
    "107": "NFS-e emitida",
    "150": "NFS-e emitida fora do prazo",
}

_NFSE_FIN_NFSE: dict[str, str] = {
    "0": "NFS-e regular",
    "1": "NFS-e substituição",
    "2": "NFS-e emitida por decisão judicial ou administrativa",
    "3": "NFS-e de escrituração/importação",
}

_NFSE_OP_SIMP_NAC: dict[str, str] = {
    "1": "Não Optante",
    "2": "Optante - Microempreendedor Individual (MEI)",
    "3": "Optante - Microempresa ou Empresa de Pequeno Porte (ME/EPP)",
}

_NFSE_REG_AP_TRIB_SN: dict[str, str] = {
    "1": "Regime de apuração dos tributos federais e municipal pelo Simples Nacional",
    "2": (
        "Regime de apuração dos tributos federais pelo SN e ISSQN por fora do SN "
        "por acordo/convênio"
    ),
    "3": "Regime de apuração de todos os tributos por fora do Simples Nacional",
}

_NFSE_TRIB_ISSQN: dict[str, str] = {
    "1": "Operação Tributável",
    "2": "Exportação de Serviço",
    "3": "Não Incidência / Imunidade",
    "4": "Não Tributável / Isenção",
}

_NFSE_REG_ESP_TRIB: dict[str, str] = {
    "0": "Nenhum",
    "1": "Ato Cooperado (Cooperativa)",
    "2": "Estimativa",
    "3": "Microempresa Municipal",
    "4": "Notário ou Registrador",
    "5": "Profissional Autônomo",
    "6": "Sociedade de Profissionais",
    "9": "Outros",
}

_NFSE_TP_RET_ISSQN: dict[str, str] = {
    "1": "Não Retido",
    "2": "Retido pelo Tomador",
    "3": "Retido pelo Intermediário",
}

_NFSE_TP_RET_PIS_COFINS: dict[str, str] = {
    "0": "PIS/COFINS/CSLL Não Retido",
    "1": "PIS/COFINS Retido",
    "2": "PIS/COFINS Não Retido",
}

_NFSE_TP_AMB: dict[str, str] = {"1": "Produção", "2": "Homologação"}
_NFSE_AMB_GER: dict[str, str] = {"1": "Prefeitura", "2": "Sistema Nacional NFS-e"}


def _desc_dominio(tabela: dict[str, str], codigo: str | None, prefixo: str = "código") -> str:
    limpo = str(codigo).strip() if codigo is not None else ""
    if not limpo:
        return "-"
    return tabela.get(limpo, f"{prefixo} {limpo}")


def _para_decimal_opt(valor: Decimal | float | int | str | None) -> Decimal | None:
    if valor is None:
        return None
    if isinstance(valor, Decimal):
        return valor
    bruto = str(valor).strip()
    if not bruto:
        return None
    try:
        return Decimal(bruto.replace(",", "."))
    except InvalidOperation:
        return None


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


def _formatar_br_opcional(
    valor: Decimal | float | int | str | None,
    casas: int = 2,
    minimo: int | None = None,
    *,
    vazio: str = "",
) -> str:
    """Formata número em pt-BR cortando zeros excedentes além de `minimo`."""
    num = _para_decimal_opt(valor)
    if num is None:
        return vazio
    s = f"{num:,.{casas}f}"
    if minimo is not None and casas > minimo and "." in s:
        inteiro, dec = s.split(".")
        dec = dec.rstrip("0").ljust(minimo, "0")
        s = f"{inteiro}.{dec}"
    return s.replace(",", "_").replace(".", ",").replace("_", ".")


def _formatar_pct_opcional(valor: Decimal | float | int | str | None) -> str:
    num = _para_decimal_opt(valor)
    if num is None:
        return "-"
    return f"{formatar_moeda(num, casas=2)}%"


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


def formatar_telefone(fone: str | None) -> str:
    """Formata telefone brasileiro com DDD (`(11) 3000-1000` ou `(11) 93000-1000`)."""
    if not fone:
        return ""
    digitos = re.sub(r"\D", "", str(fone))
    if len(digitos) == 10:
        return f"({digitos[:2]}) {digitos[2:6]}-{digitos[6:]}"
    if len(digitos) == 11:
        return f"({digitos[:2]}) {digitos[2:7]}-{digitos[7:]}"
    return str(fone).strip()


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


def _formatar_data_xml(valor: str, com_hora: bool = False) -> str:
    dt = para_data_hora(valor)
    if dt is None:
        return ""
    return dt.strftime("%d/%m/%Y %H:%M:%S" if com_hora else "%d/%m/%Y")


def _formatar_hora_xml(valor: str) -> str:
    dt = para_data_hora(valor)
    if dt is None or "T" not in (valor or ""):
        return ""
    return dt.strftime("%H:%M:%S")


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
        f'viewBox="0 0 {largura_total} 40" width="100%" height="100%" '
        f'preserveAspectRatio="none" shape-rendering="crispEdges" '
        f'style="width:100%;height:100%;display:block" '
        f'role="img" aria-label="Código de barras da chave de acesso">'
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
        "telefone": formatar_telefone(parte.telefone),
        "email": parte.email or "",
    }


def _obter_marca_dagua(doc: DocumentoFiscal) -> str | None:
    if doc.situacao == "CANCELADA":
        return "CANCELADA"
    if doc.ambiente == 2:
        return "SEM VALOR FISCAL"
    if doc.situacao == "SEM_PROTOCOLO":
        return "SEM PROTOCOLO"
    if doc.tipo == "RESUMO":
        return "RESUMO (resNFe)"
    return None


# =========================================================================
# Extração DANFE oficial (NF-e 55 / NFC-e 65) a partir do XML bruto
# =========================================================================


def _ender_nfe(el: etree._Element | None) -> dict[str, str]:
    if el is None:
        return {"logr": "", "bairro": "", "cep": "", "mun": "", "uf": "", "fone": ""}
    logr = ", ".join(p for p in (texto(el, "xLgr"), texto(el, "nro")) if p)
    compl = texto(el, "xCpl")
    if compl:
        logr = f"{logr} - {compl}" if logr else compl
    return {
        "logr": logr,
        "bairro": texto(el, "xBairro"),
        "cep": formatar_cep(texto(el, "CEP")),
        "mun": texto(el, "xMun"),
        "uf": texto(el, "UF"),
        "fone": formatar_telefone(texto(el, "fone")),
    }


def _pessoa_nfe(el: etree._Element | None, ender_tag: str) -> dict[str, str]:
    ender = primeiro(el, ender_tag) if el is not None else None
    doc_raw = texto(el, "CNPJ") or texto(el, "CPF") or texto(el, "idEstrangeiro")
    return {
        "nome": texto(el, "xNome"),
        "fant": texto(el, "xFant"),
        "doc": formatar_cnpj_cpf(doc_raw),
        "ie": texto(el, "IE"),
        "iest": texto(el, "IEST"),
        "im": texto(el, "IM"),
        **_ender_nfe(ender),
    }


def _item_nfe_xml(det: etree._Element) -> dict[str, str]:
    prod = primeiro(det, "prod")
    imp = primeiro(det, "imposto")
    icms = primeiro(imp, "ICMS")
    cst_icms = ""
    if icms is not None:
        orig = texto(icms, "orig")
        cst_icms = orig + (texto(icms, "CST") or texto(icms, "CSOSN"))
    ipi = primeiro(imp, "IPI")
    return {
        "cod": texto(prod, "cProd"),
        "desc": texto(prod, "xProd"),
        "info_ad": texto(det, "infAdProd"),
        "ncm": texto(prod, "NCM"),
        "cst": cst_icms,
        "cfop": texto(prod, "CFOP"),
        "un": texto(prod, "uCom"),
        "qtd": _formatar_br_opcional(texto(prod, "qCom"), 4, 2),
        "vun": _formatar_br_opcional(texto(prod, "vUnCom"), 10, 2),
        "vtot": _formatar_br_opcional(texto(prod, "vProd"), 2),
        "bc_icms": _formatar_br_opcional(texto(icms, "vBC"), 2) if icms is not None else "",
        "v_icms": _formatar_br_opcional(texto(icms, "vICMS"), 2) if icms is not None else "",
        "v_ipi": _formatar_br_opcional(texto(ipi, "vIPI"), 2) if ipi is not None else "",
        "p_icms": _formatar_br_opcional(texto(icms, "pICMS"), 2) if icms is not None else "",
        "p_ipi": _formatar_br_opcional(texto(ipi, "pIPI"), 2) if ipi is not None else "",
    }


def _extrair_danfe_xml(xml_bytes: bytes, doc: DocumentoFiscal) -> dict[str, Any] | None:
    try:
        root = parse_xml_seguro(xml_bytes)
    except ErroParseXML:
        return None

    inf = primeiro_com_filho(root, "infNFe", "ide")
    if inf is None:
        return None

    ide = primeiro(inf, "ide")
    emit = primeiro(inf, "emit")
    dest = primeiro(inf, "dest")
    tot = primeiro(primeiro(inf, "total"), "ICMSTot")
    transp = primeiro(inf, "transp")
    prot = primeiro(root, "infProt")

    chave = (
        limpar_chave_acesso(inf.get("Id"))
        or limpar_chave_acesso(texto(prot, "chNFe"))
        or limpar_chave_acesso(doc.chave_acesso)
    )

    dups: list[dict[str, str]] = []
    for dup in todos(inf, "dup"):
        dups.append(
            {
                "num": texto(dup, "nDup"),
                "venc": _formatar_data_xml(texto(dup, "dVenc")),
                "valor": _formatar_br_opcional(texto(dup, "vDup"), 2),
            }
        )

    transporta = primeiro(transp, "transporta")
    veic = primeiro(transp, "veicTransp")
    vols = todos(transp, "vol")
    vol = vols[0] if vols else None
    q_vol = sum(int(_para_decimal_opt(texto(v, "qVol")) or 0) for v in vols)

    refs = [
        formatar_chave((d.text or "").strip())
        for d in todos(inf, "refNFe")
        if d.text and d.text.strip()
    ]
    dh_emi = texto(ide, "dhEmi") or texto(ide, "dEmi")
    dh_sai = texto(ide, "dhSaiEnt") or texto(ide, "dSaiEnt")
    serie_raw = texto(ide, "serie") or doc.serie or "1"

    return {
        "chave": chave,
        "chave_fmt": formatar_chave(chave),
        "numero": formatar_numero_nf(texto(ide, "nNF") or doc.numero),
        "serie": serie_raw.zfill(3) if serie_raw.isdigit() else serie_raw,
        "modelo": texto(ide, "mod") or doc.modelo,
        "tp_nf": texto(ide, "tpNF") or "1",
        "natop": texto(ide, "natOp") or doc.natureza_operacao or "",
        "emissao": _formatar_data_xml(dh_emi) or formatar_data_br(doc.data_emissao),
        "saida": _formatar_data_xml(dh_sai),
        "hora_saida": _formatar_hora_xml(dh_sai),
        "homologacao": (texto(ide, "tpAmb") == "2") or (doc.ambiente == 2),
        "cancelada": doc.situacao == "CANCELADA",
        "emit": _pessoa_nfe(emit, "enderEmit"),
        "dest": _pessoa_nfe(dest, "enderDest"),
        "prot": {
            "num": texto(prot, "nProt") or doc.protocolo or "",
            "data": _formatar_data_xml(texto(prot, "dhRecbto"), com_hora=True),
            "cstat": texto(prot, "cStat"),
            "motivo": texto(prot, "xMotivo"),
        },
        "dups": dups,
        "tot": {
            "bc_icms": _formatar_br_opcional(texto(tot, "vBC"), 2, vazio="0,00"),
            "v_icms": _formatar_br_opcional(texto(tot, "vICMS"), 2, vazio="0,00"),
            "bc_st": _formatar_br_opcional(texto(tot, "vBCST"), 2, vazio="0,00"),
            "v_st": _formatar_br_opcional(texto(tot, "vST"), 2, vazio="0,00"),
            "v_prod": _formatar_br_opcional(texto(tot, "vProd"), 2, vazio="0,00"),
            "frete": _formatar_br_opcional(texto(tot, "vFrete"), 2, vazio="0,00"),
            "seguro": _formatar_br_opcional(texto(tot, "vSeg"), 2, vazio="0,00"),
            "desc": _formatar_br_opcional(texto(tot, "vDesc"), 2, vazio="0,00"),
            "outras": _formatar_br_opcional(texto(tot, "vOutro"), 2, vazio="0,00"),
            "ipi": _formatar_br_opcional(texto(tot, "vIPI"), 2, vazio="0,00"),
            "nf": _formatar_br_opcional(texto(tot, "vNF"), 2, vazio="0,00"),
            "trib": _formatar_br_opcional(texto(tot, "vTotTrib"), 2, vazio="0,00"),
        },
        "transp": {
            "nome": texto(transporta, "xNome"),
            "frete": _MODALIDADE_FRETE.get(texto(transp, "modFrete"), ""),
            "antt": texto(veic, "RNTC"),
            "placa": texto(veic, "placa"),
            "uf_placa": texto(veic, "UF"),
            "doc": formatar_cnpj_cpf(texto(transporta, "CNPJ") or texto(transporta, "CPF")),
            "ender": texto(transporta, "xEnder"),
            "mun": texto(transporta, "xMun"),
            "uf": texto(transporta, "UF"),
            "ie": texto(transporta, "IE"),
            "qvol": str(q_vol) if q_vol else "",
            "esp": texto(vol, "esp"),
            "marca": texto(vol, "marca"),
            "nvol": texto(vol, "nVol"),
            "peso_b": _formatar_br_opcional(texto(vol, "pesoB"), 3),
            "peso_l": _formatar_br_opcional(texto(vol, "pesoL"), 3),
        },
        "itens": [_item_nfe_xml(det) for det in todos(inf, "det")],
        "inf_cpl": texto(inf, "infCpl"),
        "inf_fisco": texto(inf, "infAdFisco"),
        "refs": refs,
    }


def _parte_para_danfe_fallback(parte: Parte | None) -> dict[str, str]:
    if parte is None:
        return {
            "nome": "",
            "fant": "",
            "doc": "",
            "ie": "",
            "iest": "",
            "im": "",
            "logr": "",
            "bairro": "",
            "cep": "",
            "mun": "",
            "uf": "",
            "fone": "",
        }
    return {
        "nome": parte.razao_social or "",
        "fant": parte.nome_fantasia or "",
        "doc": formatar_cnpj_cpf(parte.documento),
        "ie": parte.inscricao_estadual or "",
        "iest": "",
        "im": parte.inscricao_municipal or "",
        "logr": parte.endereco or "",
        "bairro": "",
        "cep": formatar_cep(parte.cep),
        "mun": parte.municipio or "",
        "uf": parte.uf or "",
        "fone": formatar_telefone(parte.telefone),
    }


def _extrair_danfe_de_documento(doc: DocumentoFiscal) -> dict[str, Any]:
    tot = doc.totais
    imp = tot.impostos
    serie_raw = doc.serie or "1"

    dups: list[dict[str, str]] = []
    for pag in doc.pagamentos:
        if pag.numero_duplicata:
            dups.append(
                {
                    "num": pag.numero_duplicata,
                    "venc": formatar_data_br(pag.vencimento),
                    "valor": formatar_moeda(pag.valor),
                }
            )

    itens_d: list[dict[str, str]] = []
    for it in doc.itens:
        im = it.imposto
        itens_d.append(
            {
                "cod": it.codigo,
                "desc": it.descricao,
                "info_ad": it.info_adicional or "",
                "ncm": it.ncm or "",
                "cst": im.cst_ou_csosn or "",
                "cfop": it.cfop or "",
                "un": it.unidade,
                "qtd": _formatar_br_opcional(it.quantidade, 4, 2, vazio="0,00"),
                "vun": _formatar_br_opcional(it.valor_unitario, 10, 2, vazio="0,00"),
                "vtot": formatar_moeda(it.valor_total),
                "bc_icms": formatar_moeda(im.icms_bc) if im.icms_bc else "",
                "v_icms": formatar_moeda(im.icms_valor) if im.icms_valor else "",
                "v_ipi": formatar_moeda(im.ipi_valor) if im.ipi_valor else "",
                "p_icms": formatar_moeda(im.icms_aliquota) if im.icms_aliquota else "",
                "p_ipi": formatar_moeda(im.ipi_aliquota) if im.ipi_aliquota else "",
            }
        )

    return {
        "chave": limpar_chave_acesso(doc.chave_acesso),
        "chave_fmt": formatar_chave(doc.chave_acesso),
        "numero": formatar_numero_nf(doc.numero),
        "serie": serie_raw.zfill(3) if serie_raw.isdigit() else serie_raw,
        "modelo": doc.modelo,
        "tp_nf": "1",
        "natop": doc.natureza_operacao or "OPERAÇÃO FISCAL",
        "emissao": formatar_data_br(doc.data_emissao),
        "saida": "",
        "hora_saida": "",
        "homologacao": doc.ambiente == 2,
        "cancelada": doc.situacao == "CANCELADA",
        "emit": _parte_para_danfe_fallback(doc.emitente),
        "dest": _parte_para_danfe_fallback(doc.destinatario),
        "prot": {
            "num": doc.protocolo or "",
            "data": "",
            "cstat": "",
            "motivo": "",
        },
        "dups": dups,
        "tot": {
            "bc_icms": formatar_moeda(imp.icms_bc),
            "v_icms": formatar_moeda(imp.icms),
            "bc_st": formatar_moeda(imp.icms_st_bc),
            "v_st": formatar_moeda(imp.icms_st),
            "v_prod": formatar_moeda(tot.valor_produtos_servicos),
            "frete": formatar_moeda(tot.valor_frete),
            "seguro": formatar_moeda(tot.valor_seguro),
            "desc": formatar_moeda(tot.valor_desconto),
            "outras": formatar_moeda(tot.valor_outras_despesas),
            "ipi": formatar_moeda(imp.ipi),
            "nf": formatar_moeda(tot.valor_total_nota),
            "trib": formatar_moeda(imp.tributos_aproximados),
        },
        "transp": {
            "nome": "",
            "frete": "",
            "antt": "",
            "placa": "",
            "uf_placa": "",
            "doc": "",
            "ender": "",
            "mun": "",
            "uf": "",
            "ie": "",
            "qvol": "",
            "esp": "",
            "marca": "",
            "nvol": "",
            "peso_b": "",
            "peso_l": "",
        },
        "itens": itens_d,
        "inf_cpl": doc.informacoes_complementares or doc.descricao_evento or "",
        "inf_fisco": "",
        "refs": [],
    }


def montar_contexto_danfe(
    res: ResultadoAnalise,
    xml_bytes: bytes | None = None,
) -> dict[str, Any]:
    """Prepara contexto completo no padrão oficial A4 para `danfe.html` (XML bruto + fallback)."""
    doc = res.documento
    if doc is None:
        raise ValueError("ResultadoAnalise não possui DocumentoFiscal para gerar DANFE.")

    tot = doc.totais
    imp = tot.impostos
    v_liq = tot.valor_liquido if tot.valor_liquido != DECIMAL_ZERO else tot.valor_total_nota

    d = (_extrair_danfe_xml(xml_bytes, doc) if xml_bytes else None) or _extrair_danfe_de_documento(
        doc
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

    itens_legado: list[dict[str, Any]] = []
    for item in doc.itens:
        im = item.imposto
        itens_legado.append(
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

    chave_efetiva = d["chave"] or doc.chave_acesso
    svg_barras = barcode_svg(chave_efetiva)

    return {
        "d": d,
        "barcode": svg_barras,
        "barcode_svg": svg_barras,
        "arquivo_origem": res.arquivo_origem,
        "valido": res.valido,
        "alertas": [a.model_dump() for a in res.alertas],
        "tipo": doc.tipo,
        "modelo": d["modelo"] or doc.modelo,
        "serie": d["serie"],
        "numero": doc.numero,
        "numero_fmt": d["numero"],
        "chave_acesso": chave_efetiva,
        "chave_fmt": d["chave_fmt"],
        "data_emissao_fmt": formatar_data_hora_br(doc.data_emissao),
        "data_emissao_curta": d["emissao"],
        "ambiente": doc.ambiente,
        "ambiente_desc": "1 - Produção"
        if doc.ambiente == 1
        else "2 - Homologação (Sem Valor Fiscal)",
        "protocolo": doc.protocolo or "SEM PROTOCOLO DE AUTORIZAÇÃO",
        "situacao": doc.situacao,
        "natureza_operacao": d["natop"] or "OPERAÇÃO FISCAL",
        "marca_dagua": _obter_marca_dagua(doc),
        "emitente": _formatar_parte(doc.emitente),
        "destinatario": _formatar_parte(doc.destinatario),
        "totais": {
            "icms_bc_fmt": d["tot"]["bc_icms"],
            "icms_fmt": d["tot"]["v_icms"],
            "icms_st_bc_fmt": d["tot"]["bc_st"],
            "icms_st_fmt": d["tot"]["v_st"],
            "valor_produtos_fmt": d["tot"]["v_prod"],
            "valor_frete_fmt": d["tot"]["frete"],
            "valor_seguro_fmt": d["tot"]["seguro"],
            "valor_desconto_fmt": d["tot"]["desc"],
            "valor_outras_fmt": d["tot"]["outras"],
            "ipi_fmt": d["tot"]["ipi"],
            "pis_fmt": formatar_moeda(imp.pis),
            "cofins_fmt": formatar_moeda(imp.cofins),
            "ibs_cbs_fmt": formatar_moeda(imp.ibs_cbs),
            "tributos_aprox_fmt": d["tot"]["trib"],
            "valor_liquido_fmt": formatar_moeda(v_liq),
            "valor_total_nota_fmt": d["tot"]["nf"],
        },
        "itens": itens_legado,
        "pagamentos": pagamentos_fmt,
        "informacoes_complementares": d["inf_cpl"],
        "descricao_evento": doc.descricao_evento or "",
    }


# =========================================================================
# Extração DANFSe v2.0 oficial (NT 008/2026) a partir do XML bruto
# =========================================================================


def _doc_nfse(el: etree._Element | None) -> str:
    if el is None:
        return ""
    for tag in ("CNPJ", "CPF", "NIF"):
        val = texto(el, tag)
        if val:
            return limpar_documento(val) or val
    return ""


def _endereco_nfse(el: etree._Element | None) -> str:
    if el is None:
        return "-"
    partes = [
        p
        for p in (texto(el, "xLgr"), texto(el, "nro"), texto(el, "xCpl"), texto(el, "xBairro"))
        if p
    ]
    return ", ".join(partes) if partes else "-"


def _bloco_pessoa_nfse(
    el: etree._Element | None,
    *,
    fallback: etree._Element | None = None,
    mapa_municipios: dict[str, tuple[str, str]] | None = None,
) -> dict[str, str]:
    if el is None and fallback is None:
        return {}

    def pega(tag: str) -> str:
        val = texto(el, tag) if el is not None else ""
        if not val and fallback is not None:
            val = texto(fallback, tag)
        return val

    end_wrap = primeiro(el, "end") if el is not None else None
    if end_wrap is None and fallback is not None:
        end_wrap = primeiro(fallback, "end")

    nac: etree._Element | None = None
    for origem, tag_nac in (
        (el, "endNac"),
        (el, "enderNac"),
        (fallback, "endNac"),
        (fallback, "enderNac"),
    ):
        if origem is not None:
            cand = primeiro(origem, tag_nac)
            if cand is not None:
                nac = cand
                break
    end_addr = end_wrap if end_wrap is not None else nac
    cmun = texto(nac, "cMun") if nac is not None else ""
    uf = texto(nac, "UF") if nac is not None else ""
    nome_mun = ""
    if mapa_municipios and cmun and cmun in mapa_municipios:
        nome_mun, uf_map = mapa_municipios[cmun]
        uf = uf or uf_map
    if not nome_mun and nac is not None:
        nome_mun = texto(nac, "xCidade")

    mun_uf = f"{nome_mun} / {uf}".strip(" /") if (nome_mun or uf) else (cmun or "-")
    cep_fmt = formatar_cep(texto(nac, "CEP")) if nac is not None else ""
    ibge_cep = f"{cmun} / {cep_fmt or '-'}" if cmun else (cep_fmt or "-")

    doc_raw = _doc_nfse(el) or _doc_nfse(fallback)
    return {
        "doc": formatar_cnpj_cpf(doc_raw) or "-",
        "im": pega("IM") or "-",
        "fone": formatar_telefone(pega("fone")) or "-",
        "nome": pega("xNome") or "-",
        "municipio_uf": mun_uf or "-",
        "ibge_cep": ibge_cep,
        "endereco": _endereco_nfse(end_addr),
        "email": pega("email") or "-",
    }


def _extrair_danfse_xml(xml_bytes: bytes, doc: DocumentoFiscal) -> dict[str, Any] | None:
    try:
        root = parse_xml_seguro(xml_bytes)
    except ErroParseXML:
        return None

    inf = primeiro(root, "infNFSe")
    if inf is None:
        return None

    dps = primeiro(inf, "infDPS")
    emit = primeiro(inf, "emit")
    prest = primeiro(dps, "prest") if dps is not None else None
    toma = primeiro(dps, "toma") if dps is not None else None
    interm = primeiro(dps, "interm") if dps is not None else None
    serv = primeiro(dps, "serv") if dps is not None else None
    cserv = primeiro(serv, "cServ") if serv is not None else None
    val_nfse = primeiro(inf, "valores")
    ibscbs_nfse = primeiro(inf, "IBSCBS")
    val_dps = primeiro(dps, "valores") if dps is not None else None
    trib = primeiro(val_dps, "trib") if val_dps is not None else None
    trib_mun = primeiro(trib, "tribMun") if trib is not None else None
    trib_fed = primeiro(trib, "tribFed") if trib is not None else None
    piscofins = primeiro(trib_fed, "piscofins") if trib_fed is not None else None
    reg_trib = primeiro(prest, "regTrib") if prest is not None else None

    # Constrói mapa local cMun -> (xMun, UF) usando as próprias tags do XML
    uf_emit = texto(primeiro(emit, "enderNac"), "UF") if emit is not None else ""
    mapa_mun: dict[str, tuple[str, str]] = {}
    c_loc_emi = texto(dps, "cLocEmi") or (
        texto(primeiro(emit, "enderNac"), "cMun") if emit is not None else ""
    )
    x_loc_emi = texto(inf, "xLocEmi")
    if c_loc_emi and x_loc_emi:
        mapa_mun[c_loc_emi] = (x_loc_emi, uf_emit)
    c_loc_incid = texto(inf, "cLocIncid")
    x_loc_incid = texto(inf, "xLocIncid")
    if c_loc_incid and x_loc_incid:
        mapa_mun.setdefault(c_loc_incid, (x_loc_incid, uf_emit))

    chave = limpar_chave_acesso(inf.get("Id")) or limpar_chave_acesso(doc.chave_acesso)
    tp_amb = texto(dps, "tpAmb") or texto(inf, "tpAmb") or str(doc.ambiente)
    cstat = texto(inf, "cStat")

    v_serv = _para_decimal_opt(
        texto(primeiro(val_dps, "vServPrest"), "vServ") if val_dps is not None else ""
    )
    v_liq = _para_decimal_opt(texto(val_nfse, "vLiq"))
    v_iss = _para_decimal_opt(texto(val_nfse, "vISSQN")) or _para_decimal_opt(
        texto(trib_mun, "vISSQN")
    )
    v_bc = _para_decimal_opt(texto(val_nfse, "vBC"))
    p_aliq = _para_decimal_opt(texto(val_nfse, "pAliqAplic")) or _para_decimal_opt(
        texto(trib_mun, "pAliq")
    )
    v_totret = _para_decimal_opt(texto(val_nfse, "vTotalRet"))

    ibs: dict[str, Any] = {"presente": False}
    if ibscbs_nfse is not None:
        totc = primeiro(ibscbs_nfse, "totCIBS")
        gibs = primeiro(totc, "gIBS") if totc is not None else None
        gcbs = primeiro(totc, "gCBS") if totc is not None else None
        valores_ibs = primeiro(ibscbs_nfse, "valores")
        v_ibs_tot = _para_decimal_opt(texto(gibs, "vIBSTot"))
        v_cbs = _para_decimal_opt(texto(gcbs, "vCBS"))
        v_tot_nf = _para_decimal_opt(texto(totc, "vTotNF"))
        soma_ibs_cbs = (v_ibs_tot or DECIMAL_ZERO) + (v_cbs or DECIMAL_ZERO)
        ibs = {
            "presente": True,
            "v_bc_fmt": _formatar_br_opcional(
                texto(valores_ibs, "vBC") if valores_ibs is not None else "", vazio="-"
            ),
            "p_ibs_uf_fmt": _formatar_pct_opcional(
                texto(primeiro(valores_ibs, "uf"), "pIBSUF") if valores_ibs is not None else ""
            ),
            "p_ibs_mun_fmt": _formatar_pct_opcional(
                texto(primeiro(valores_ibs, "mun"), "pIBSMun") if valores_ibs is not None else ""
            ),
            "p_cbs_fmt": _formatar_pct_opcional(
                texto(primeiro(valores_ibs, "fed"), "pCBS") if valores_ibs is not None else ""
            ),
            "v_ibs_tot_fmt": _formatar_br_opcional(v_ibs_tot, vazio="-"),
            "v_cbs_fmt": _formatar_br_opcional(v_cbs, vazio="-"),
            "v_total_ibs_cbs_fmt": formatar_moeda(soma_ibs_cbs),
            "v_tot_nf_fmt": formatar_moeda(v_tot_nf or v_liq or v_serv or DECIMAL_ZERO),
        }

    tot_trib = primeiro(primeiro(trib, "totTrib"), "vTotTrib") if trib is not None else None
    aprox_fed = _para_decimal_opt(texto(tot_trib, "vTotTribFed")) or DECIMAL_ZERO
    aprox_est = _para_decimal_opt(texto(tot_trib, "vTotTribEst")) or DECIMAL_ZERO
    aprox_mun = _para_decimal_opt(texto(tot_trib, "vTotTribMun")) or DECIMAL_ZERO

    infocompl = texto(primeiro(serv, "infoCompl"), "xInfComp") if serv is not None else ""
    fin_el = primeiro(dps, "IBSCBS") if dps is not None else None
    fin_cod = texto(fin_el, "finNFSe") if fin_el is not None else ""

    return {
        "chave": chave,
        "cancelada": cstat in ("101", "102") or doc.situacao == "CANCELADA",
        "substituida": cstat == "103",
        "homologacao": tp_amb == "2",
        # Identificação
        "numero_nfse": texto(inf, "nNFSe") or doc.numero or "-",
        "competencia_fmt": _formatar_data_xml(texto(dps, "dCompet"))
        or formatar_data_br(doc.competencia or doc.data_emissao)
        or "-",
        "dh_nfse_fmt": _formatar_data_xml(texto(inf, "dhProc"), com_hora=True)
        or formatar_data_hora_br(doc.data_emissao)
        or "-",
        "numero_dps": texto(dps, "nDPS") or "-",
        "serie_dps": texto(dps, "serie") or doc.serie or "-",
        "dh_dps_fmt": _formatar_data_xml(texto(dps, "dhEmi"), com_hora=True) or "-",
        "emitente": _desc_dominio(_NFSE_TP_EMIT, texto(dps, "tpEmit")),
        "situacao": _desc_dominio(_NFSE_C_STAT, cstat, "situação") if cstat else doc.situacao,
        "finalidade": _desc_dominio(_NFSE_FIN_NFSE, fin_cod),
        "municipio_emit": x_loc_emi or doc.emitente.municipio or "-",
        "uf_emit": uf_emit or doc.emitente.uf or "",
        "amb_ger": _desc_dominio(_NFSE_AMB_GER, texto(inf, "ambGer")),
        "tp_amb": _desc_dominio(_NFSE_TP_AMB, tp_amb),
        # Pessoas
        "prestador": _bloco_pessoa_nfse(prest, fallback=emit, mapa_municipios=mapa_mun),
        "prestador_simples": _desc_dominio(_NFSE_OP_SIMP_NAC, texto(reg_trib, "opSimpNac")),
        "prestador_reg_sn": _desc_dominio(_NFSE_REG_AP_TRIB_SN, texto(reg_trib, "regApTribSN")),
        "tomador": _bloco_pessoa_nfse(toma, mapa_municipios=mapa_mun),
        "intermediario": _bloco_pessoa_nfse(interm, mapa_municipios=mapa_mun),
        # Serviço
        "cod_trib_nac": texto(cserv, "cTribNac") or "-",
        "cod_nbs": texto(cserv, "cNBS") or "-",
        "local_prestacao": texto(inf, "xLocPrestacao") or x_loc_emi or "-",
        "desc_trib": texto(inf, "xTribNac") or texto(cserv, "xDescServ") or "-",
        "desc_servico": texto(cserv, "xDescServ") or "-",
        # ISSQN
        "sem_iss": not (trib_mun is not None and bool(texto(trib_mun, "tribISSQN"))),
        "iss_tipo": _desc_dominio(_NFSE_TRIB_ISSQN, texto(trib_mun, "tribISSQN")),
        "iss_municipio": x_loc_incid or x_loc_emi or "-",
        "iss_reg_esp": _desc_dominio(_NFSE_REG_ESP_TRIB, texto(reg_trib, "regEspTrib")),
        "iss_bc_fmt": _formatar_br_opcional(v_bc, vazio="-"),
        "iss_aliq_fmt": _formatar_pct_opcional(p_aliq),
        "iss_retencao": _desc_dominio(_NFSE_TP_RET_ISSQN, texto(trib_mun, "tpRetISSQN")),
        "iss_apurado_fmt": _formatar_br_opcional(v_iss, vazio="-"),
        # Federal (exceto CBS)
        "fed_mostrar": True,
        "fed_irrf_fmt": _formatar_br_opcional(texto(trib_fed, "vRetIRRF"), vazio="-"),
        "fed_cp_fmt": _formatar_br_opcional(texto(trib_fed, "vRetCP"), vazio="-"),
        "fed_csll_fmt": _formatar_br_opcional(texto(trib_fed, "vRetCSLL"), vazio="-"),
        "fed_pis_fmt": _formatar_br_opcional(texto(piscofins, "vPis"), vazio="-"),
        "fed_cofins_fmt": _formatar_br_opcional(texto(piscofins, "vCofins"), vazio="-"),
        "fed_ret_desc": _desc_dominio(_NFSE_TP_RET_PIS_COFINS, texto(piscofins, "tpRetPisCofins")),
        # IBS/CBS
        "ibs": ibs,
        # Valores totais
        "v_servico_fmt": formatar_moeda(v_serv or doc.totais.valor_produtos_servicos),
        "v_total_ret_fmt": formatar_moeda(
            v_totret if v_totret is not None else doc.totais.impostos.retencoes_federais
        ),
        "v_liquido_fmt": formatar_moeda(
            v_liq
            if v_liq is not None
            else (doc.totais.valor_liquido or doc.totais.valor_total_nota)
        ),
        # Complementares
        "info_compl": infocompl or doc.informacoes_complementares or "",
        "aprox_fed_fmt": formatar_moeda(aprox_fed),
        "aprox_est_fmt": formatar_moeda(aprox_est),
        "aprox_mun_fmt": formatar_moeda(aprox_mun),
    }


def _parte_para_danfse_fallback(parte: Parte | None) -> dict[str, str]:
    if parte is None:
        return {}
    mun_uf = f"{parte.municipio or ''} / {parte.uf or ''}".strip(" /") or "-"
    ibge_cep = (
        f"{parte.codigo_ibge} / {formatar_cep(parte.cep) or '-'}"
        if parte.codigo_ibge
        else (formatar_cep(parte.cep) or "-")
    )
    return {
        "doc": formatar_cnpj_cpf(parte.documento) or "-",
        "im": parte.inscricao_municipal or "-",
        "fone": formatar_telefone(parte.telefone) or "-",
        "nome": parte.razao_social or "-",
        "municipio_uf": mun_uf,
        "ibge_cep": ibge_cep,
        "endereco": parte.endereco or "-",
        "email": parte.email or "-",
    }


def _extrair_danfse_de_documento(doc: DocumentoFiscal) -> dict[str, Any]:
    tot = doc.totais
    imp = tot.impostos
    v_liq = tot.valor_liquido if tot.valor_liquido != DECIMAL_ZERO else tot.valor_total_nota
    item = doc.itens[0] if doc.itens else None
    iss_aliq = item.imposto.iss_aliquota if item else DECIMAL_ZERO
    iss_retido = item.imposto.iss_retido if item else False
    tem_ibs = imp.ibs_cbs > DECIMAL_ZERO

    return {
        "chave": limpar_chave_acesso(doc.chave_acesso),
        "cancelada": doc.situacao == "CANCELADA",
        "substituida": False,
        "homologacao": doc.ambiente == 2,
        "numero_nfse": doc.numero or "-",
        "competencia_fmt": formatar_data_br(doc.competencia or doc.data_emissao) or "-",
        "dh_nfse_fmt": formatar_data_hora_br(doc.data_emissao) or "-",
        "numero_dps": doc.numero or "-",
        "serie_dps": doc.serie or "-",
        "dh_dps_fmt": formatar_data_hora_br(doc.data_emissao) or "-",
        "emitente": "Prestador",
        "situacao": "NFS-e emitida" if doc.situacao == "AUTORIZADA" else doc.situacao,
        "finalidade": "NFS-e regular",
        "municipio_emit": doc.emitente.municipio or "-",
        "uf_emit": doc.emitente.uf or "",
        "amb_ger": "Sistema Nacional NFS-e",
        "tp_amb": "Produção" if doc.ambiente == 1 else "Homologação",
        "prestador": _parte_para_danfse_fallback(doc.emitente),
        "prestador_simples": "-",
        "prestador_reg_sn": "-",
        "tomador": _parte_para_danfse_fallback(doc.destinatario),
        "intermediario": {},
        "cod_trib_nac": (item.codigo_tributacao_nacional if item else "") or "-",
        "cod_nbs": (item.codigo_nbs if item else "") or "-",
        "local_prestacao": doc.emitente.municipio or "-",
        "desc_trib": doc.natureza_operacao or "-",
        "desc_servico": (item.descricao if item else "") or "-",
        "sem_iss": False,
        "iss_tipo": "Operação Tributável",
        "iss_municipio": doc.emitente.municipio or "-",
        "iss_reg_esp": "-",
        "iss_bc_fmt": formatar_moeda(tot.valor_produtos_servicos),
        "iss_aliq_fmt": _formatar_pct_opcional(iss_aliq),
        "iss_retencao": "Retido pelo Tomador" if iss_retido else "Não Retido",
        "iss_apurado_fmt": formatar_moeda(imp.iss),
        "fed_mostrar": True,
        "fed_irrf_fmt": "-",
        "fed_cp_fmt": "-",
        "fed_csll_fmt": "-",
        "fed_pis_fmt": formatar_moeda(imp.pis),
        "fed_cofins_fmt": formatar_moeda(imp.cofins),
        "fed_ret_desc": "-",
        "ibs": {
            "presente": tem_ibs,
            "v_bc_fmt": "-",
            "p_ibs_uf_fmt": "-",
            "p_ibs_mun_fmt": "-",
            "p_cbs_fmt": "-",
            "v_ibs_tot_fmt": formatar_moeda(imp.ibs_cbs) if tem_ibs else "-",
            "v_cbs_fmt": "-",
            "v_total_ibs_cbs_fmt": formatar_moeda(imp.ibs_cbs),
            "v_tot_nf_fmt": formatar_moeda(v_liq),
        },
        "v_servico_fmt": formatar_moeda(tot.valor_produtos_servicos),
        "v_total_ret_fmt": formatar_moeda(imp.retencoes_federais),
        "v_liquido_fmt": formatar_moeda(v_liq),
        "info_compl": doc.informacoes_complementares or "",
        "aprox_fed_fmt": "0,00",
        "aprox_est_fmt": "0,00",
        "aprox_mun_fmt": "0,00",
    }


def montar_contexto_danfse(
    res: ResultadoAnalise,
    xml_bytes: bytes | None = None,
) -> dict[str, Any]:
    """Prepara contexto completo no padrão oficial DANFSe v2.0 (NT 008/2026)."""
    doc = res.documento
    if doc is None:
        raise ValueError("ResultadoAnalise não possui DocumentoFiscal para gerar DANFSe.")

    tot = doc.totais
    imp = tot.impostos

    d = (
        _extrair_danfse_xml(xml_bytes, doc) if xml_bytes else None
    ) or _extrair_danfse_de_documento(doc)

    item_principal = doc.itens[0] if doc.itens else None
    iss_aliquota = item_principal.imposto.iss_aliquota if item_principal else DECIMAL_ZERO
    iss_retido = item_principal.imposto.iss_retido if item_principal else False
    chave_efetiva = d["chave"] or doc.chave_acesso

    return {
        "d": d,
        "chave": chave_efetiva,
        "arquivo_origem": res.arquivo_origem,
        "valido": res.valido,
        "alertas": [a.model_dump() for a in res.alertas],
        "numero": doc.numero,
        "numero_fmt": formatar_numero_nf(doc.numero),
        "serie": doc.serie or "1",
        "chave_acesso": chave_efetiva,
        "chave_fmt": formatar_chave(chave_efetiva),
        "data_emissao_fmt": formatar_data_hora_br(doc.data_emissao),
        "competencia_fmt": d["competencia_fmt"],
        "ambiente": doc.ambiente,
        "ambiente_desc": "1 - Produção" if doc.ambiente == 1 else "2 - Homologação",
        "situacao": doc.situacao,
        "natureza_operacao": doc.natureza_operacao or "Tributação no Município",
        "marca_dagua": _obter_marca_dagua(doc),
        "prestador": _formatar_parte(doc.emitente),
        "tomador": _formatar_parte(doc.destinatario),
        "servico": {
            "codigo_tributacao_nacional": d["cod_trib_nac"],
            "codigo_nbs": d["cod_nbs"],
            "codigo_interno": (item_principal.codigo if item_principal else "") or "-",
            "descricao": d["desc_servico"],
            "iss_aliquota_fmt": formatar_moeda(iss_aliquota),
            "iss_retido_str": "SIM (Retido pelo Tomador)" if iss_retido else "NÃO",
        },
        "totais": {
            "valor_servicos_fmt": d["v_servico_fmt"],
            "valor_desconto_fmt": formatar_moeda(tot.valor_desconto),
            "valor_liquido_fmt": d["v_liquido_fmt"],
            "valor_total_nota_fmt": formatar_moeda(tot.valor_total_nota),
            "iss_fmt": formatar_moeda(imp.iss),
            "pis_fmt": formatar_moeda(imp.pis),
            "cofins_fmt": formatar_moeda(imp.cofins),
            "retencoes_federais_fmt": formatar_moeda(imp.retencoes_federais),
            "ibs_cbs_fmt": formatar_moeda(imp.ibs_cbs),
        },
        "informacoes_complementares": d["info_compl"],
    }


__all__ = [
    "barcode_svg",
    "formatar_cep",
    "formatar_chave",
    "formatar_cnpj_cpf",
    "formatar_data_br",
    "formatar_data_hora_br",
    "formatar_moeda",
    "formatar_numero_nf",
    "formatar_telefone",
    "montar_contexto_danfe",
    "montar_contexto_danfse",
    "nome_local",
]
