"""Testes unitários dos normalizadores e modelos Pydantic v2 (`models/`)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from models import (
    AlertaValidacao,
    DocumentoFiscal,
    ImpostoItem,
    ImpostoTotal,
    Item,
    Pagamento,
    Parte,
    ResultadoAnalise,
    Totais,
    descrever_forma_pagamento,
    limpar_chave_acesso,
    limpar_documento,
    para_data,
    para_data_hora,
    para_decimal,
)


class TestParaDecimal:
    """Testes de conversão segura para Decimal."""

    def test_valores_padrao_xml_e_pt_br(self) -> None:
        assert para_decimal("1234.56") == Decimal("1234.56")
        assert para_decimal("1.234,56") == Decimal("1234.56")
        assert para_decimal("1234,56") == Decimal("1234.56")
        assert para_decimal("1,234.56") == Decimal("1234.56")
        assert para_decimal(100) == Decimal("100")
        assert para_decimal(19.95) == Decimal("19.95")
        assert para_decimal(Decimal("42.10")) == Decimal("42.10")

    def test_valores_vazios_ou_nulos_retornam_padrao(self) -> None:
        assert para_decimal(None) == Decimal("0.00")
        assert para_decimal("") == Decimal("0.00")
        assert para_decimal("   ") == Decimal("0.00")
        assert para_decimal(None, padrao=Decimal("9.99")) == Decimal("9.99")

    def test_valores_invalidos_e_nao_finitos(self) -> None:
        assert para_decimal("abc") == Decimal("0.00")
        assert para_decimal("NaN") == Decimal("0.00")
        assert para_decimal("Infinity") == Decimal("0.00")
        assert para_decimal(float("nan")) == Decimal("0.00")
        assert para_decimal(float("inf")) == Decimal("0.00")
        assert para_decimal(Decimal("NaN")) == Decimal("0.00")
        assert para_decimal(True) == Decimal("0.00")

        with pytest.raises(ValueError):
            para_decimal("abc", estrito=True)
        with pytest.raises(ValueError):
            para_decimal("NaN", estrito=True)
        with pytest.raises(ValueError):
            para_decimal(float("inf"), estrito=True)
        with pytest.raises(ValueError):
            para_decimal(Decimal("Infinity"), estrito=True)
        with pytest.raises(ValueError):
            para_decimal(True, estrito=True)


class TestDatas:
    """Testes de conversão de datas e timestamps ISO 8601 / compactos."""

    def test_para_data_hora_formatos_validos(self) -> None:
        dt_iso = para_data_hora("2026-03-15T10:30:00-03:00")
        assert dt_iso is not None
        assert dt_iso.year == 2026
        assert dt_iso.month == 3
        assert dt_iso.day == 15
        assert dt_iso.hour == 10
        assert dt_iso.minute == 30
        assert dt_iso.tzinfo is not None

        dt_simples = para_data_hora("2026-03-15")
        assert dt_simples == datetime(2026, 3, 15, 0, 0)

        dt_compacta = para_data_hora("20260315")
        assert dt_compacta == datetime(2026, 3, 15, 0, 0)

        agora = datetime.now(tz=UTC)
        assert para_data_hora(agora) == agora
        assert para_data_hora(date(2026, 3, 15)) == datetime(2026, 3, 15, 0, 0)

    def test_para_data_hora_invalida_ou_vazia(self) -> None:
        assert para_data_hora(None) is None
        assert para_data_hora("   ") is None
        assert para_data_hora("data-invalida") is None
        assert para_data_hora("20261399") is None

        with pytest.raises(ValueError):
            para_data_hora("data-invalida", estrito=True)
        with pytest.raises(ValueError):
            para_data_hora("20261399", estrito=True)

    def test_para_data(self) -> None:
        assert para_data("2026-03-15T10:30:00-03:00") == date(2026, 3, 15)
        assert para_data("2026-03-15") == date(2026, 3, 15)
        assert para_data("20260315") == date(2026, 3, 15)
        assert para_data(datetime(2026, 3, 15, 14, 0)) == date(2026, 3, 15)
        assert para_data(date(2026, 3, 15)) == date(2026, 3, 15)
        assert para_data(None) is None
        assert para_data("") is None


class TestLimpezaDocumentosEChaves:
    """Testes de higienização de CNPJ (numérico/alfanumérico), CPF e Chaves."""

    def test_limpar_documento_preserva_alfanumerico(self) -> None:
        assert limpar_documento("11.222.333/0001-81") == "11222333000181"
        assert limpar_documento("12.abc.345/01de-35") == "12ABC34501DE35"
        assert limpar_documento(" 123.456.789-09 ") == "12345678909"
        assert limpar_documento(None) == ""
        assert limpar_documento("") == ""

    def test_limpar_chave_acesso_remove_prefixos_e_mantem_letras(self) -> None:
        chave_nfe = "NFe35260311222333000181550010000010011100010010"
        assert limpar_chave_acesso(chave_nfe) == "35260311222333000181550010000010011100010010"

        chave_alnum = "nfe35260312abc34501de35550010000010031100010032"
        assert limpar_chave_acesso(chave_alnum) == "35260312ABC34501DE35550010000010031100010032"

        chave_nfse = "NFS35503082233444555000181000000000050126030000001005"
        assert (
            limpar_chave_acesso(chave_nfse) == "35503082233444555000181000000000050126030000001005"
        )

        chave_dps = "DPS355030823344455500018100001000000000000501"
        assert limpar_chave_acesso(chave_dps) == "355030823344455500018100001000000000000501"

        chave_formatada = "3526 0311 2223 3300 0181 5500 1000 0010 0111 0001 0010"
        assert (
            limpar_chave_acesso(chave_formatada) == "35260311222333000181550010000010011100010010"
        )
        assert limpar_chave_acesso(None) == ""

    def test_descrever_forma_pagamento(self) -> None:
        assert descrever_forma_pagamento("17") == "Pagamento Instantâneo (PIX)"
        assert descrever_forma_pagamento("1") == "Dinheiro"
        assert descrever_forma_pagamento("15") == "Boleto Bancário"
        assert descrever_forma_pagamento("99") == "Outros"
        assert descrever_forma_pagamento("77") == "Código 77"
        assert descrever_forma_pagamento(None) == "Não informado"
        assert descrever_forma_pagamento("  ") == "Não informado"


class TestModelosPydantic:
    """Testes de instanciação, validação e serialização JSON dos modelos fiscais."""

    def test_construcao_documento_fiscal_completo_e_serializacao(self) -> None:
        emitente = Parte(
            documento="12ABC34501DE35",
            tipo_pessoa="PJ",
            razao_social="INOVACAO DELTA COMPONENTES S.A.",
            nome_fantasia="DELTA COMP",
            inscricao_estadual="110042490114",
            uf="SP",
            municipio="SAO PAULO",
            codigo_ibge="3550308",
        )
        destinatario = Parte(
            documento="22333444000181",
            tipo_pessoa="PJ",
            razao_social="COMERCIO BETA DISTRIBUIDORA LTDA",
            uf="SP",
        )
        item = Item(
            numero_item=1,
            codigo="ALN-01",
            descricao="SENSOR OPTICO INDUSTRIAL",
            ncm="90318099",
            cfop="5101",
            unidade="UN",
            quantidade=Decimal("4.0000"),
            valor_unitario=Decimal("200.0000"),
            valor_total=Decimal("800.00"),
            imposto=ImpostoItem(
                cst_ou_csosn="00",
                icms_bc=Decimal("800.00"),
                icms_aliquota=Decimal("18.00"),
                icms_valor=Decimal("144.00"),
                pis_valor=Decimal("13.20"),
                cofins_valor=Decimal("60.80"),
            ),
        )
        totais = Totais(
            valor_produtos_servicos=Decimal("800.00"),
            impostos=ImpostoTotal(
                icms_bc=Decimal("800.00"),
                icms=Decimal("144.00"),
                pis=Decimal("13.20"),
                cofins=Decimal("60.80"),
            ),
            valor_liquido=Decimal("800.00"),
            valor_total_nota=Decimal("800.00"),
        )
        pagamento = Pagamento(
            forma_codigo="15",
            forma_descricao=descrever_forma_pagamento("15"),
            valor=Decimal("800.00"),
            numero_duplicata="001",
            vencimento=date(2026, 8, 15),
        )
        doc = DocumentoFiscal(
            tipo="NFE",
            modelo="55",
            serie="1",
            numero="1003",
            chave_acesso="35260312ABC34501DE35550010000010031100010032",
            data_emissao=para_data_hora("2026-07-15T10:00:00-03:00"),
            ambiente=1,
            protocolo="135260000001003",
            codigo_status="100",
            motivo_status="Autorizado o uso da NF-e",
            situacao="AUTORIZADA",
            natureza_operacao="VENDA DE PRODUCAO",
            emitente=emitente,
            destinatario=destinatario,
            itens=[item],
            totais=totais,
            pagamentos=[pagamento],
        )
        resultado = ResultadoAnalise(
            arquivo_origem="nfe_55_cnpj_alfanumerico.xml",
            valido=True,
            documento=doc,
            alertas=[
                AlertaValidacao(
                    codigo="HOMOLOGACAO",
                    severidade="AVISO",
                    mensagem="Exemplo de aviso",
                    campo="ide.tpAmb",
                )
            ],
        )

        payload_json = resultado.model_dump_json()
        reconstruido = ResultadoAnalise.model_validate_json(payload_json)
        assert reconstruido.valido is True
        assert reconstruido.documento is not None
        assert reconstruido.documento.emitente.documento == "12ABC34501DE35"
        assert reconstruido.documento.totais.valor_total_nota == Decimal("800.00")
        assert reconstruido.documento.itens[0].imposto.icms_valor == Decimal("144.00")
        assert len(reconstruido.alertas) == 1

    def test_validacao_rejeita_literais_invalidos(self) -> None:
        with pytest.raises(ValidationError):
            DocumentoFiscal(
                tipo="CTE_INVALIDO",  # type: ignore[arg-type]
                modelo="55",
            )
        with pytest.raises(ValidationError):
            AlertaValidacao(
                codigo="X",
                severidade="CRITICO",  # type: ignore[arg-type]
                mensagem="Severidade inexistente",
            )
