"""Parser de NF-e (modelo 55), NFC-e (modelo 65), Resumo (`resNFe`) e Evento (`procEventoNFe`)."""

from __future__ import annotations

from typing import Literal

from lxml import etree

from models.fiscal import (
    DocumentoFiscal,
    ImpostoItem,
    ImpostoTotal,
    Item,
    ModeloDocumento,
    Pagamento,
    Parte,
    SituacaoDocumento,
    TipoDocumento,
    TipoPessoa,
    Totais,
)
from models.normalizadores import (
    DECIMAL_ZERO,
    descrever_forma_pagamento,
    limpar_chave_acesso,
    limpar_documento,
    para_data,
    para_data_hora,
    para_decimal,
)
from parsers.xml_seguro import (
    ErroLayoutDesconhecido,
    nome_local,
    parse_xml_seguro,
    primeiro,
    primeiro_com_filho,
    texto,
    todos,
)

_TAGS_RAIZ_CTE = {"cteProc", "CTe", "infCte", "cteOSProc", "CTeOS"}


def _extrair_ambiente(valor: str) -> Literal[1, 2]:
    return 2 if valor.strip() == "2" else 1


def _formatar_endereco(el_ender: etree._Element | None) -> str | None:
    if el_ender is None:
        return None
    logradouro = texto(el_ender, "xLgr")
    numero = texto(el_ender, "nro")
    complemento = texto(el_ender, "xCpl")
    bairro = texto(el_ender, "xBairro")

    partes: list[str] = []
    if logradouro:
        trecho = f"{logradouro}, {numero}" if numero else logradouro
        if complemento:
            trecho = f"{trecho} - {complemento}"
        partes.append(trecho)
    if bairro:
        partes.append(bairro)
    return " - ".join(partes) if partes else None


def _extrair_parte(el_parte: etree._Element | None, tag_endereco: str) -> Parte:
    if el_parte is None:
        return Parte()

    cnpj = limpar_documento(texto(el_parte, "CNPJ"))
    cpf = limpar_documento(texto(el_parte, "CPF"))
    id_estrangeiro = texto(el_parte, "idEstrangeiro")

    documento = ""
    tipo_pessoa: TipoPessoa = "NAO_IDENTIFICADO"
    if cnpj:
        documento = cnpj
        tipo_pessoa = "PJ"
    elif cpf:
        documento = cpf
        tipo_pessoa = "PF"
    elif id_estrangeiro:
        documento = id_estrangeiro
        tipo_pessoa = "ESTRANGEIRO"

    el_ender = primeiro(el_parte, tag_endereco)
    fone = texto(el_ender, "fone") or texto(el_parte, "fone") or None
    email = texto(el_parte, "email") or None

    return Parte(
        documento=documento,
        tipo_pessoa=tipo_pessoa,
        razao_social=texto(el_parte, "xNome"),
        nome_fantasia=texto(el_parte, "xFant") or None,
        inscricao_estadual=texto(el_parte, "IE") or None,
        inscricao_municipal=texto(el_parte, "IM") or None,
        uf=texto(el_ender, "UF") or texto(el_parte, "UF") or None,
        municipio=texto(el_ender, "xMun") or None,
        codigo_ibge=texto(el_ender, "cMun") or None,
        endereco=_formatar_endereco(el_ender),
        cep=limpar_documento(texto(el_ender, "CEP")) or None,
        telefone=fone,
        email=email,
    )


def _extrair_imposto_item(el_imposto: etree._Element | None) -> ImpostoItem:
    if el_imposto is None:
        return ImpostoItem()

    cst_ou_csosn: str | None = None
    icms_bc = DECIMAL_ZERO
    icms_aliquota = DECIMAL_ZERO
    icms_valor = DECIMAL_ZERO
    icms_st_valor = DECIMAL_ZERO

    el_icms = primeiro(el_imposto, "ICMS")
    if el_icms is not None and len(el_icms) > 0:
        sub_icms = el_icms[0]
        orig = texto(sub_icms, "orig")
        cst = texto(sub_icms, "CST") or texto(sub_icms, "CSOSN")
        if orig or cst:
            cst_ou_csosn = f"{orig}{cst}"
        icms_bc = para_decimal(texto(sub_icms, "vBC"))
        icms_aliquota = para_decimal(texto(sub_icms, "pICMS"))
        icms_valor = para_decimal(texto(sub_icms, "vICMS"))
        icms_st_valor = para_decimal(texto(sub_icms, "vICMSST"))

    ipi_aliquota = DECIMAL_ZERO
    ipi_valor = DECIMAL_ZERO
    el_ipi = primeiro(el_imposto, "IPI")
    if el_ipi is not None:
        ipi_aliquota = para_decimal(texto(el_ipi, "pIPI"))
        ipi_valor = para_decimal(texto(el_ipi, "vIPI"))

    pis_aliquota = DECIMAL_ZERO
    pis_valor = DECIMAL_ZERO
    el_pis = primeiro(el_imposto, "PIS")
    if el_pis is not None:
        pis_aliquota = para_decimal(texto(el_pis, "pPIS"))
        pis_valor = para_decimal(texto(el_pis, "vPIS"))

    cofins_aliquota = DECIMAL_ZERO
    cofins_valor = DECIMAL_ZERO
    el_cofins = primeiro(el_imposto, "COFINS")
    if el_cofins is not None:
        cofins_aliquota = para_decimal(texto(el_cofins, "pCOFINS"))
        cofins_valor = para_decimal(texto(el_cofins, "vCOFINS"))

    return ImpostoItem(
        cst_ou_csosn=cst_ou_csosn,
        icms_bc=icms_bc,
        icms_aliquota=icms_aliquota,
        icms_valor=icms_valor,
        icms_st_valor=icms_st_valor,
        ipi_aliquota=ipi_aliquota,
        ipi_valor=ipi_valor,
        pis_aliquota=pis_aliquota,
        pis_valor=pis_valor,
        cofins_aliquota=cofins_aliquota,
        cofins_valor=cofins_valor,
    )


def _extrair_itens(inf_nfe: etree._Element) -> list[Item]:
    itens: list[Item] = []
    for idx, el_det in enumerate(todos(inf_nfe, "det"), start=1):
        n_item_str = el_det.attrib.get("nItem", "").strip()
        numero_item = int(n_item_str) if n_item_str.isdigit() else idx
        el_prod = primeiro(el_det, "prod")
        el_imposto = primeiro(el_det, "imposto")

        itens.append(
            Item(
                numero_item=numero_item,
                codigo=texto(el_prod, "cProd"),
                descricao=texto(el_prod, "xProd"),
                ncm=texto(el_prod, "NCM") or None,
                cest=texto(el_prod, "CEST") or None,
                cfop=texto(el_prod, "CFOP") or None,
                unidade=texto(el_prod, "uCom") or texto(el_prod, "uTrib") or "UN",
                quantidade=para_decimal(
                    texto(el_prod, "qCom") or texto(el_prod, "qTrib"),
                    padrao=DECIMAL_ZERO,
                ),
                valor_unitario=para_decimal(
                    texto(el_prod, "vUnCom") or texto(el_prod, "vUnTrib"),
                ),
                valor_desconto=para_decimal(texto(el_prod, "vDesc")),
                valor_frete=para_decimal(texto(el_prod, "vFrete")),
                valor_seguro=para_decimal(texto(el_prod, "vSeg")),
                valor_outros=para_decimal(texto(el_prod, "vOutro")),
                valor_total=para_decimal(texto(el_prod, "vProd")),
                imposto=_extrair_imposto_item(el_imposto),
                info_adicional=texto(el_det, "infAdProd") or None,
            )
        )
    return itens


def _extrair_totais(inf_nfe: etree._Element) -> Totais:
    icms_tot = primeiro(inf_nfe, "ICMSTot")
    if icms_tot is None:
        return Totais()

    v_nf = para_decimal(texto(icms_tot, "vNF"))
    impostos = ImpostoTotal(
        icms_bc=para_decimal(texto(icms_tot, "vBC")),
        icms=para_decimal(texto(icms_tot, "vICMS")),
        icms_st_bc=para_decimal(texto(icms_tot, "vBCST")),
        icms_st=para_decimal(texto(icms_tot, "vST")),
        ipi=para_decimal(texto(icms_tot, "vIPI")),
        pis=para_decimal(texto(icms_tot, "vPIS")),
        cofins=para_decimal(texto(icms_tot, "vCOFINS")),
        tributos_aproximados=para_decimal(texto(icms_tot, "vTotTrib")),
    )
    return Totais(
        valor_produtos_servicos=para_decimal(texto(icms_tot, "vProd")),
        valor_frete=para_decimal(texto(icms_tot, "vFrete")),
        valor_seguro=para_decimal(texto(icms_tot, "vSeg")),
        valor_desconto=para_decimal(texto(icms_tot, "vDesc")),
        valor_outras_despesas=para_decimal(texto(icms_tot, "vOutro")),
        impostos=impostos,
        valor_liquido=v_nf,
        valor_total_nota=v_nf,
    )


def _extrair_pagamentos(inf_nfe: etree._Element) -> list[Pagamento]:
    pagamentos: list[Pagamento] = []

    for el_det_pag in todos(inf_nfe, "detPag"):
        t_pag = texto(el_det_pag, "tPag") or "99"
        v_pag = para_decimal(texto(el_det_pag, "vPag"))
        pagamentos.append(
            Pagamento(
                forma_codigo=t_pag,
                forma_descricao=descrever_forma_pagamento(t_pag),
                valor=v_pag,
            )
        )

    duplicatas = todos(inf_nfe, "dup")
    if duplicatas:
        forma_padrao = pagamentos[0].forma_codigo if len(pagamentos) == 1 else "14"
        desc_padrao = descrever_forma_pagamento(forma_padrao)
        if len(pagamentos) == 1 and len(duplicatas) == 1:
            pagamentos[0].numero_duplicata = texto(duplicatas[0], "nDup") or None
            pagamentos[0].vencimento = para_data(texto(duplicatas[0], "dVenc"))
        else:
            for el_dup in duplicatas:
                pagamentos.append(
                    Pagamento(
                        forma_codigo=forma_padrao,
                        forma_descricao=desc_padrao,
                        valor=para_decimal(texto(el_dup, "vDup")),
                        numero_duplicata=texto(el_dup, "nDup") or None,
                        vencimento=para_data(texto(el_dup, "dVenc")),
                    )
                )

    return pagamentos


def _determinar_situacao(c_stat: str | None, tem_protocolo: bool) -> SituacaoDocumento:
    if not tem_protocolo or not c_stat:
        return "SEM_PROTOCOLO"
    if c_stat in ("100", "150"):
        return "AUTORIZADA"
    if c_stat in ("101", "151", "155"):
        return "CANCELADA"
    return "REJEITADA"


def _parse_inf_nfe(raiz: etree._Element, inf_nfe: etree._Element) -> DocumentoFiscal:
    el_ide = primeiro(inf_nfe, "ide")
    mod_raw = texto(el_ide, "mod") or "55"
    modelo: ModeloDocumento = "65" if mod_raw == "65" else "55"
    tipo: TipoDocumento = "NFCE" if modelo == "65" else "NFE"

    inf_prot = primeiro(raiz, "infProt")
    chave_id = limpar_chave_acesso(inf_nfe.attrib.get("Id"))
    chave_prot = limpar_chave_acesso(texto(inf_prot, "chNFe"))
    chave_acesso = chave_id or chave_prot

    protocolo = texto(inf_prot, "nProt") or None
    c_stat = texto(inf_prot, "cStat") or None
    x_motivo = texto(inf_prot, "xMotivo") or None
    situacao = _determinar_situacao(c_stat, inf_prot is not None)

    dh_emi = texto(el_ide, "dhEmi") or texto(el_ide, "dEmi")
    data_emissao = para_data_hora(dh_emi)
    ambiente = _extrair_ambiente(texto(el_ide, "tpAmb") or texto(inf_prot, "tpAmb"))

    el_emit = primeiro(inf_nfe, "emit")
    el_dest = primeiro(inf_nfe, "dest")
    emitente = _extrair_parte(el_emit, "enderEmit")
    destinatario = _extrair_parte(el_dest, "enderDest") if el_dest is not None else None

    chaves_ref = [
        limpar_chave_acesso(el_ref.text)
        for el_ref in todos(inf_nfe, "refNFe")
        if el_ref.text and limpar_chave_acesso(el_ref.text)
    ]

    el_inf_adic = primeiro(inf_nfe, "infAdic")
    inf_cpl = texto(el_inf_adic, "infCpl") if el_inf_adic is not None else ""

    return DocumentoFiscal(
        tipo=tipo,
        modelo=modelo,
        serie=texto(el_ide, "serie"),
        numero=texto(el_ide, "nNF"),
        chave_acesso=chave_acesso,
        data_emissao=data_emissao,
        competencia=data_emissao.date() if data_emissao else None,
        ambiente=ambiente,
        protocolo=protocolo,
        codigo_status=c_stat,
        motivo_status=x_motivo,
        situacao=situacao,
        natureza_operacao=texto(el_ide, "natOp") or None,
        emitente=emitente,
        destinatario=destinatario,
        itens=_extrair_itens(inf_nfe),
        totais=_extrair_totais(inf_nfe),
        pagamentos=_extrair_pagamentos(inf_nfe),
        chaves_referenciadas=chaves_ref,
        informacoes_complementares=inf_cpl or None,
    )


def _parse_resumo_nfe(el_res: etree._Element) -> DocumentoFiscal:
    chave = limpar_chave_acesso(texto(el_res, "chNFe"))
    cnpj = limpar_documento(texto(el_res, "CNPJ"))
    cpf = limpar_documento(texto(el_res, "CPF"))
    doc_emit = cnpj or cpf
    tipo_pessoa: TipoPessoa = "PJ" if cnpj else ("PF" if cpf else "NAO_IDENTIFICADO")

    v_nf = para_decimal(texto(el_res, "vNF"))
    data_emissao = para_data_hora(texto(el_res, "dhEmi"))
    c_sit = texto(el_res, "cSitNFe") or None

    serie = chave[22:25].lstrip("0") or "0" if len(chave) == 44 else ""
    numero = chave[25:34].lstrip("0") or "0" if len(chave) == 44 else ""

    return DocumentoFiscal(
        tipo="RESUMO",
        modelo="55",
        serie=serie,
        numero=numero,
        chave_acesso=chave,
        data_emissao=data_emissao,
        competencia=data_emissao.date() if data_emissao else None,
        protocolo=texto(el_res, "nProt") or None,
        codigo_status=c_sit,
        situacao="RESUMO",
        emitente=Parte(
            documento=doc_emit,
            tipo_pessoa=tipo_pessoa,
            razao_social=texto(el_res, "xNome"),
            inscricao_estadual=texto(el_res, "IE") or None,
        ),
        totais=Totais(
            valor_liquido=v_nf,
            valor_total_nota=v_nf,
        ),
    )


def _parse_evento_nfe(raiz: etree._Element, inf_evento: etree._Element) -> DocumentoFiscal:
    chave = limpar_chave_acesso(texto(inf_evento, "chNFe"))
    cnpj = limpar_documento(texto(inf_evento, "CNPJ"))
    cpf = limpar_documento(texto(inf_evento, "CPF"))
    doc_autor = cnpj or cpf
    tipo_pessoa: TipoPessoa = "PJ" if cnpj else ("PF" if cpf else "NAO_IDENTIFICADO")

    tp_evento = texto(inf_evento, "tpEvento") or None
    el_det_ev = primeiro(inf_evento, "detEvento")
    desc_ev = (
        texto(el_det_ev, "xJust")
        or texto(el_det_ev, "xCorrecao")
        or texto(el_det_ev, "descEvento")
        or None
    )

    el_ret_ev = primeiro(raiz, "retEvento")
    el_inf_ret = primeiro(el_ret_ev, "infEvento") if el_ret_ev is not None else None
    n_prot = texto(el_inf_ret, "nProt") or texto(el_det_ev, "nProt") or None
    c_stat = texto(el_inf_ret, "cStat") or None
    x_motivo = texto(el_inf_ret, "xMotivo") or None

    data_emissao = para_data_hora(texto(inf_evento, "dhEvento"))
    ambiente = _extrair_ambiente(texto(inf_evento, "tpAmb"))

    serie = chave[22:25].lstrip("0") or "0" if len(chave) == 44 else ""
    numero = chave[25:34].lstrip("0") or "0" if len(chave) == 44 else ""

    return DocumentoFiscal(
        tipo="EVENTO",
        modelo="55",
        serie=serie,
        numero=numero,
        chave_acesso=chave,
        data_emissao=data_emissao,
        competencia=data_emissao.date() if data_emissao else None,
        ambiente=ambiente,
        protocolo=n_prot,
        codigo_status=c_stat,
        motivo_status=x_motivo,
        situacao="EVENTO",
        emitente=Parte(
            documento=doc_autor,
            tipo_pessoa=tipo_pessoa,
        ),
        tipo_evento=tp_evento,
        descricao_evento=desc_ev,
    )


def parse_nfe_element(raiz: etree._Element) -> DocumentoFiscal:
    """Converte um elemento XML raiz já parseado em `DocumentoFiscal` (NF-e/NFC-e/Resumo/Evento)."""
    tag_raiz = nome_local(raiz)
    if tag_raiz in _TAGS_RAIZ_CTE or primeiro(raiz, "infCte") is not None:
        raise ErroLayoutDesconhecido(
            f"Documento CT-e ({tag_raiz}) não é suportado pelo parser de NF-e/NFC-e."
        )

    inf_nfe = primeiro_com_filho(raiz, "infNFe", "ide")
    if inf_nfe is not None:
        return _parse_inf_nfe(raiz, inf_nfe)

    if tag_raiz == "resNFe":
        return _parse_resumo_nfe(raiz)
    el_res = primeiro(raiz, "resNFe")
    if el_res is not None:
        return _parse_resumo_nfe(el_res)

    if tag_raiz in ("procEventoNFe", "evento", "envEvento"):
        inf_ev = primeiro(raiz, "infEvento")
        if inf_ev is not None:
            return _parse_evento_nfe(raiz, inf_ev)

    raise ErroLayoutDesconhecido(
        f"Layout XML não reconhecido como NF-e (55), NFC-e (65), Resumo ou Evento: <{tag_raiz}>."
    )


def parse_nfe(dados: bytes | str) -> DocumentoFiscal:
    """Faz o parse seguro de `bytes` ou `str` XML de NF-e/NFC-e/Resumo/Evento."""
    raiz = parse_xml_seguro(dados)
    return parse_nfe_element(raiz)
