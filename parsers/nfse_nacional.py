"""Parser de NFS-e do Padrão Nacional (`http://www.sped.fazenda.gov.br/nfse`)."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from lxml import etree

from models.fiscal import (
    DocumentoFiscal,
    ImpostoItem,
    ImpostoTotal,
    Item,
    Parte,
    SituacaoDocumento,
    TipoPessoa,
    Totais,
)
from models.normalizadores import (
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
    texto,
)

NAMESPACE_NFSE_NACIONAL = "http://www.sped.fazenda.gov.br/nfse"

_TAGS_NFSE_LEGADA = {
    "ConsultarNfseResposta",
    "CompNfse",
    "ListaNfse",
    "GerarNfseResposta",
    "EnviarLoteRpsEnvio",
    "EnviarLoteRpsResposta",
    "ConsultarLoteRpsResposta",
    "ConsultarNfseRpsResposta",
    "Nfse",
    "InfNfse",
    "InfDeclaracaoPrestacaoServico",
}


class ErroLayoutNFSeMunicipalLegado(ErroLayoutDesconhecido):
    """Exceção levantada quando um XML de NFS-e usa layout municipal legado (ex.: ABRASF)."""


def eh_xml_nfse_legada(raiz: etree._Element) -> bool:
    """Detecta se a árvore XML pertence a um layout municipal legado (ex.: ABRASF)."""
    ns_map = " ".join(str(v) for v in (raiz.nsmap or {}).values()).lower()
    if "abrasf" in ns_map or "ginfes" in ns_map or "betha" in ns_map or "issnet" in ns_map:
        return True
    tag_raiz = nome_local(raiz)
    if tag_raiz in _TAGS_NFSE_LEGADA:
        return True
    return primeiro(raiz, "CompNfse") is not None or primeiro(raiz, "ListaNfse") is not None


def eh_xml_nfse_nacional(raiz: etree._Element) -> bool:
    """Detecta se a árvore XML pertence ao Padrão Nacional da NFS-e (Sped/ADN)."""
    ns_vals = [str(v).lower() for v in (raiz.nsmap or {}).values()]
    if any("sped.fazenda.gov.br/nfse" in v for v in ns_vals):
        return True
    tag_raiz = nome_local(raiz)
    if tag_raiz in ("NFSe", "DPS", "pedRegEvento", "eventoNFSe"):
        return True
    return (
        primeiro(raiz, "infNFSe") is not None
        or primeiro(raiz, "infDPS") is not None
        or (primeiro(raiz, "infEvento") is not None and primeiro(raiz, "chNFSe") is not None)
    )


def _escolher_el(*elementos: etree._Element | None) -> etree._Element | None:
    """Retorna o primeiro `etree._Element` que não seja `None` (evita truth-testing do lxml)."""
    for el in elementos:
        if el is not None:
            return el
    return None


def _formatar_endereco_nfse(
    el_pai: etree._Element | None,
) -> tuple[str | None, str | None, str | None, str | None]:
    """Retorna `(endereco_formatado, uf, codigo_ibge, cep)` de blocos `enderNac`/`end`."""
    if el_pai is None:
        return None, None, None, None

    el_end = _escolher_el(primeiro(el_pai, "enderNac"), primeiro(el_pai, "end"), el_pai)
    el_end_nac = _escolher_el(primeiro(el_end, "endNac"), el_end)

    logradouro = texto(el_end, "xLgr")
    numero = texto(el_end, "nro")
    complemento = texto(el_end, "xCpl")
    bairro = texto(el_end, "xBairro")

    partes: list[str] = []
    if logradouro:
        trecho = f"{logradouro}, {numero}" if numero else logradouro
        if complemento:
            trecho = f"{trecho} - {complemento}"
        partes.append(trecho)
    if bairro:
        partes.append(bairro)

    endereco = " - ".join(partes) if partes else None
    uf = texto(el_end_nac, "UF") or texto(el_end, "UF") or None
    c_mun = texto(el_end_nac, "cMun") or texto(el_end, "cMun") or None
    cep = limpar_documento(texto(el_end_nac, "CEP") or texto(el_end, "CEP")) or None
    return endereco, uf, c_mun, cep


def _extrair_prestador(
    el_emit: etree._Element | None,
    el_prest: etree._Element | None,
    municipio_padrao: str | None,
) -> Parte:
    cnpj = limpar_documento(texto(el_prest, "CNPJ") or texto(el_emit, "CNPJ"))
    cpf = limpar_documento(texto(el_prest, "CPF") or texto(el_emit, "CPF"))
    nif = texto(el_prest, "NIF") or texto(el_emit, "NIF")

    documento = ""
    tipo_pessoa: TipoPessoa = "NAO_IDENTIFICADO"
    if cnpj:
        documento = cnpj
        tipo_pessoa = "PJ"
    elif cpf:
        documento = cpf
        tipo_pessoa = "PF"
    elif nif:
        documento = nif
        tipo_pessoa = "ESTRANGEIRO"

    razao = texto(el_emit, "xNome") or texto(el_prest, "xNome")
    fantasia = texto(el_emit, "xFant") or texto(el_prest, "xFant") or None
    im = texto(el_prest, "IM") or texto(el_emit, "IM") or None
    fone = texto(el_emit, "fone") or texto(el_prest, "fone") or None
    email = texto(el_emit, "email") or texto(el_prest, "email") or None

    endereco, uf, c_mun, cep = _formatar_endereco_nfse(_escolher_el(el_emit, el_prest))
    if not endereco and el_prest is not None:
        endereco, uf_p, c_mun_p, cep_p = _formatar_endereco_nfse(el_prest)
        uf = uf or uf_p
        c_mun = c_mun or c_mun_p
        cep = cep or cep_p

    return Parte(
        documento=documento,
        tipo_pessoa=tipo_pessoa,
        razao_social=razao,
        nome_fantasia=fantasia,
        inscricao_municipal=im,
        uf=uf,
        municipio=municipio_padrao,
        codigo_ibge=c_mun,
        endereco=endereco,
        cep=cep,
        telefone=fone,
        email=email,
    )


def _extrair_tomador(el_toma: etree._Element | None) -> Parte | None:
    if el_toma is None:
        return None

    cnpj = limpar_documento(texto(el_toma, "CNPJ"))
    cpf = limpar_documento(texto(el_toma, "CPF"))
    nif = texto(el_toma, "NIF")

    documento = ""
    tipo_pessoa: TipoPessoa = "NAO_IDENTIFICADO"
    if cnpj:
        documento = cnpj
        tipo_pessoa = "PJ"
    elif cpf:
        documento = cpf
        tipo_pessoa = "PF"
    elif nif:
        documento = nif
        tipo_pessoa = "ESTRANGEIRO"

    endereco, uf, c_mun, cep = _formatar_endereco_nfse(el_toma)

    return Parte(
        documento=documento,
        tipo_pessoa=tipo_pessoa,
        razao_social=texto(el_toma, "xNome"),
        nome_fantasia=texto(el_toma, "xFant") or None,
        inscricao_municipal=texto(el_toma, "IM") or None,
        uf=uf,
        codigo_ibge=c_mun,
        endereco=endereco,
        cep=cep,
        telefone=texto(el_toma, "fone") or None,
        email=texto(el_toma, "email") or None,
    )


def _parse_evento_nfse(raiz: etree._Element) -> DocumentoFiscal:
    el_ev = _escolher_el(primeiro(raiz, "infEvento"), primeiro(raiz, "infPedReg"))
    id_ev = el_ev.attrib.get("Id") if el_ev is not None else raiz.attrib.get("Id")
    chave = limpar_chave_acesso(texto(raiz, "chNFSe") or id_ev)
    cnpj = limpar_documento(texto(raiz, "CNPJ") or texto(raiz, "CNPJAutor"))
    cpf = limpar_documento(texto(raiz, "CPF") or texto(raiz, "CPFAutor"))
    doc_autor = cnpj or cpf
    tipo_pessoa: TipoPessoa = "PJ" if cnpj else ("PF" if cpf else "NAO_IDENTIFICADO")

    tp_amb_str = texto(raiz, "tpAmb")
    ambiente: Literal[1, 2] = 2 if tp_amb_str == "2" else 1
    data_ev = para_data_hora(texto(raiz, "dhEvento") or texto(raiz, "dhProc"))
    tp_ev = texto(raiz, "tpEvento") or texto(raiz, "cMotivo") or None
    desc_ev = texto(raiz, "xMotivo") or texto(raiz, "xDescEvento") or None

    return DocumentoFiscal(
        tipo="EVENTO",
        modelo="NFSE",
        chave_acesso=chave,
        data_emissao=data_ev,
        competencia=data_ev.date() if data_ev else None,
        ambiente=ambiente,
        situacao="EVENTO",
        emitente=Parte(documento=doc_autor, tipo_pessoa=tipo_pessoa),
        tipo_evento=tp_ev,
        descricao_evento=desc_ev,
    )


def parse_nfse_nacional_element(raiz: etree._Element) -> DocumentoFiscal:
    """Converte elemento raiz de NFS-e Padrão Nacional em `DocumentoFiscal`."""
    if eh_xml_nfse_legada(raiz):
        raise ErroLayoutNFSeMunicipalLegado(
            "Layout municipal legado de NFS-e (ex.: ABRASF) detectado. "
            "Este sistema suporta exclusivamente a NFS-e no Padrão Nacional "
            "(http://www.sped.fazenda.gov.br/nfse)."
        )

    if not eh_xml_nfse_nacional(raiz):
        raise ErroLayoutDesconhecido(
            f"O documento XML <{nome_local(raiz)}> não pertence ao Padrão Nacional da NFS-e."
        )

    inf_nfse = primeiro(raiz, "infNFSe")
    inf_dps = primeiro(raiz, "infDPS")

    if inf_nfse is None and inf_dps is None:
        if primeiro(raiz, "chNFSe") is not None or primeiro(raiz, "infEvento") is not None:
            return _parse_evento_nfse(raiz)
        raise ErroLayoutDesconhecido("Blocos <infNFSe> e <infDPS> ausentes no XML da NFS-e.")

    chave_raw = (
        (inf_nfse.attrib.get("Id") if inf_nfse is not None else None)
        or texto(raiz, "chNFSe")
        or (inf_dps.attrib.get("Id") if inf_dps is not None else None)
    )
    chave_acesso = limpar_chave_acesso(chave_raw)

    numero = texto(inf_nfse, "nNFSe") or texto(inf_nfse, "nDFSe") or texto(inf_dps, "nDPS")
    serie = texto(inf_dps, "serie") or "1"

    dh_emi = texto(inf_dps, "dhEmi") or texto(inf_nfse, "dhProc")
    data_emissao = para_data_hora(dh_emi)
    d_compet = texto(inf_dps, "dCompet")
    competencia = para_data(d_compet) or (data_emissao.date() if data_emissao else None)

    tp_amb = texto(inf_dps, "tpAmb") or texto(inf_nfse, "tpAmb")
    ambiente: Literal[1, 2] = 2 if tp_amb == "2" else 1

    c_stat = texto(inf_nfse, "cStat") or ("100" if inf_nfse is not None else None)
    situacao: SituacaoDocumento = "SEM_PROTOCOLO" if inf_nfse is None else "AUTORIZADA"
    if c_stat in ("101", "102"):
        situacao = "CANCELADA"
    elif c_stat and c_stat.startswith("2"):
        situacao = "REJEITADA"

    x_loc_emi = texto(inf_nfse, "xLocEmi") or texto(inf_nfse, "xLocPrestacao") or None
    el_emit = primeiro(inf_nfse, "emit") if inf_nfse is not None else None
    el_prest = primeiro(inf_dps, "prest") if inf_dps is not None else None
    el_toma = primeiro(inf_dps, "toma") if inf_dps is not None else None

    emitente = _extrair_prestador(el_emit, el_prest, x_loc_emi)
    destinatario = _extrair_tomador(el_toma)

    el_val_nfse = primeiro(inf_nfse, "valores") if inf_nfse is not None else None
    el_val_dps = primeiro(inf_dps, "valores") if inf_dps is not None else None
    el_v_serv_prest = primeiro(el_val_dps, "vServPrest")
    el_v_desc = primeiro(el_val_dps, "vDescCondIncond")
    el_trib = primeiro(el_val_dps, "trib")
    el_trib_mun = primeiro(el_trib, "tribMun")
    el_trib_fed = primeiro(el_trib, "tribFed")
    el_piscofins = primeiro(el_trib_fed, "piscofins")
    el_tot_trib = primeiro(el_trib, "totTrib")

    v_serv = para_decimal(
        texto(el_v_serv_prest, "vServ") or texto(el_val_nfse, "vBC") or texto(el_val_nfse, "vLiq")
    )
    v_desc = para_decimal(texto(el_v_desc, "vDescIncond")) + para_decimal(
        texto(el_v_desc, "vDescCond")
    )
    v_liq = para_decimal(texto(el_val_nfse, "vLiq"), padrao=v_serv - v_desc)

    p_aliq_iss = para_decimal(texto(el_val_nfse, "pAliqAplic") or texto(el_trib_mun, "pAliq"))
    v_iss = para_decimal(texto(el_val_nfse, "vISSQN") or texto(el_trib_mun, "vISSQN"))
    tp_ret_iss = texto(el_trib_mun, "tpRetISSQN")
    iss_retido = tp_ret_iss in ("2", "3")

    p_pis = para_decimal(texto(el_piscofins, "pAliqPis"))
    v_pis = para_decimal(texto(el_piscofins, "vPis"))
    p_cofins = para_decimal(texto(el_piscofins, "pAliqCofins"))
    v_cofins = para_decimal(texto(el_piscofins, "vCofins"))
    tp_ret_piscofins = texto(el_piscofins, "tpRetPisCofins")

    v_ret_irrf = para_decimal(texto(el_trib_fed, "vRetIRRF"))
    v_ret_csll = para_decimal(texto(el_trib_fed, "vRetCSLL"))
    v_ret_cp = para_decimal(texto(el_trib_fed, "vRetCP"))

    v_total_ret_xml = texto(el_val_nfse, "vTotalRet")
    if v_total_ret_xml:
        retencoes_federais = para_decimal(v_total_ret_xml)
    else:
        retencoes_federais = v_ret_irrf + v_ret_csll + v_ret_cp
        if tp_ret_piscofins == "1":
            retencoes_federais += v_pis + v_cofins

    el_ibscbs = _escolher_el(primeiro(inf_nfse, "IBSCBS"), primeiro(inf_dps, "IBSCBS"))
    v_ibs = para_decimal(texto(el_ibscbs, "vIBSTot"))
    v_cbs = para_decimal(texto(el_ibscbs, "vCBS"))
    ibs_cbs_total = v_ibs + v_cbs

    v_tot_trib = (
        para_decimal(texto(el_tot_trib, "vTotTribFed"))
        + para_decimal(texto(el_tot_trib, "vTotTribEst"))
        + para_decimal(texto(el_tot_trib, "vTotTribMun"))
    )

    el_serv = primeiro(inf_dps, "serv")
    el_c_serv = primeiro(el_serv, "cServ")
    c_trib_nac = texto(el_c_serv, "cTribNac") or None
    c_nbs = texto(el_c_serv, "cNBS") or None
    x_trib_nac = texto(inf_nfse, "xTribNac")
    x_desc_serv = texto(el_c_serv, "xDescServ") or x_trib_nac or "SERVICO PRESTADO"

    item_servico = Item(
        numero_item=1,
        codigo=c_trib_nac or "SERV",
        descricao=x_desc_serv,
        codigo_tributacao_nacional=c_trib_nac,
        codigo_nbs=c_nbs,
        unidade="UN",
        quantidade=Decimal("1.0000"),
        valor_unitario=v_serv,
        valor_desconto=v_desc,
        valor_total=v_serv,
        imposto=ImpostoItem(
            cst_ou_csosn=texto(el_piscofins, "CST") or None,
            pis_aliquota=p_pis,
            pis_valor=v_pis,
            cofins_aliquota=p_cofins,
            cofins_valor=v_cofins,
            iss_aliquota=p_aliq_iss,
            iss_valor=v_iss,
            iss_retido=iss_retido,
        ),
    )

    totais = Totais(
        valor_produtos_servicos=v_serv,
        valor_desconto=v_desc,
        impostos=ImpostoTotal(
            pis=v_pis,
            cofins=v_cofins,
            iss=v_iss,
            retencoes_federais=retencoes_federais,
            ibs_cbs=ibs_cbs_total,
            tributos_aproximados=v_tot_trib,
        ),
        valor_liquido=v_liq,
        valor_total_nota=v_serv,
    )

    inf_compl = texto(el_serv, "xInfComp") or texto(inf_nfse, "xOutInf") or None
    protocolo = texto(inf_nfse, "nDFSe") or None

    return DocumentoFiscal(
        tipo="NFSE",
        modelo="NFSE",
        serie=serie,
        numero=numero,
        chave_acesso=chave_acesso,
        data_emissao=data_emissao,
        competencia=competencia,
        ambiente=ambiente,
        protocolo=protocolo,
        codigo_status=c_stat,
        situacao=situacao,
        natureza_operacao=x_trib_nac or x_loc_emi,
        emitente=emitente,
        destinatario=destinatario,
        itens=[item_servico],
        totais=totais,
        informacoes_complementares=inf_compl,
    )


def parse_nfse_nacional(dados: bytes | str) -> DocumentoFiscal:
    """Faz o parse seguro de `bytes` ou `str` XML de NFS-e Padrão Nacional."""
    raiz = parse_xml_seguro(dados)
    return parse_nfse_nacional_element(raiz)
