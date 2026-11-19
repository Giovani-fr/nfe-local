"""Privacidade do Assistente LLM: no modo padrão nada identificável sai do computador."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import app, definir_servico_assistente, obter_repositorio_lotes
from parsers.detector import processar_arquivo_xml
from reports.assistente_lote import (
    CachePerguntasLLM,
    ServicoAssistenteLote,
    anonimizar_texto,
    carregar_configuracao_llm,
    mapear_codigos_emitentes,
    montar_contexto_lote_llm,
)
from reports.resumo import gerar_resumo_lote
from validators.lote import validar_lote

PASTA_FIXTURES = Path(__file__).resolve().parent / "fixtures"
NOMES = (
    "nfe_55_valida.xml",
    "nfe_55_cnpj_alfanumerico.xml",
    "nfce_65_valida.xml",
    "nfe_55_soma_errada.xml",
    "nfse_nacional_valida.xml",
    "nfe_resumo.xml",
)
RAZOES = (
    "INDUSTRIA ALFA METALURGICA LTDA",
    "INOVACAO DELTA COMPONENTES S.A.",
    "COMERCIO BETA DISTRIBUIDORA LTDA",
    "SERVICOS TECNOLOGIA GAMA SA",
)
DOCUMENTOS = ("11222333000181", "12ABC34501DE35", "22333444000181", "33444555000181")
FORMATADOS = ("11.222.333/0001-81", "12.ABC.345/01DE-35", "22.333.444/0001-81")


def _lote(nomes: tuple[str, ...] = NOMES):
    brutos = [processar_arquivo_xml((PASTA_FIXTURES / n).read_bytes(), n) for n in nomes]
    resultados = validar_lote(brutos)
    return gerar_resumo_lote(resultados), resultados


def _sem_identificadores(texto: str) -> None:
    baixo = texto.lower()
    for proibido in (*RAZOES, *DOCUMENTOS, *FORMATADOS, *NOMES):
        assert proibido.lower() not in baixo, f"vazou: {proibido}"


@pytest.fixture(autouse=True)
def _ambiente_limpo(monkeypatch: pytest.MonkeyPatch) -> None:
    for nome in ("LLM_API_KEY", "LLM_ENVIAR_IDENTIFICADORES", "LLM_ENVIAR_AMOSTRA"):
        monkeypatch.delenv(nome, raising=False)


def test_opcoes_de_privacidade_vem_desligadas_por_padrao(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = carregar_configuracao_llm()
    assert cfg.enviar_identificadores is False and cfg.enviar_amostra is False
    monkeypatch.setenv("LLM_ENVIAR_IDENTIFICADORES", "1")
    monkeypatch.setenv("LLM_ENVIAR_AMOSTRA", "true")
    cfg = carregar_configuracao_llm()
    assert cfg.enviar_identificadores is True and cfg.enviar_amostra is True
    monkeypatch.setenv("LLM_ENVIAR_IDENTIFICADORES", "0")
    monkeypatch.setenv("LLM_ENVIAR_AMOSTRA", "talvez")
    cfg = carregar_configuracao_llm()
    assert cfg.enviar_identificadores is False and cfg.enviar_amostra is False


def test_contexto_padrao_nao_tem_razao_social_cnpj_nem_amostra() -> None:
    resumo, resultados = _lote()
    contexto = montar_contexto_lote_llm(resumo, resultados)
    _sem_identificadores(json.dumps(contexto, ensure_ascii=False))
    assert "notas_amostradas" not in contexto
    assert contexto["modo_privacidade"] == "anonimizado"
    codigos = [e["emitente"] for e in contexto["resumo"]["por_emitente"]]
    assert "Emitente 01" in codigos and len(set(codigos)) == len(codigos)


def test_amostra_sem_identificadores_usa_codigos_e_remove_arquivos_e_mensagens() -> None:
    resumo, resultados = _lote()
    contexto = montar_contexto_lote_llm(resumo, resultados, enviar_amostra=True)
    _sem_identificadores(json.dumps(contexto, ensure_ascii=False))
    nota = contexto["notas_amostradas"][0]
    assert nota["arquivo_origem"].startswith("Nota ")
    assert nota["emitente"].startswith("Emitente ")
    assert all("mensagem" not in a for n in contexto["notas_amostradas"] for a in n["alertas"])


def test_identificadores_reais_so_com_a_opcao_ligada() -> None:
    resumo, resultados = _lote()
    contexto = montar_contexto_lote_llm(
        resumo, resultados, enviar_identificadores=True, enviar_amostra=True
    )
    serializado = json.dumps(contexto, ensure_ascii=False)
    assert RAZOES[0] in serializado and DOCUMENTOS[0] in serializado
    assert contexto["modo_privacidade"] == "identificadores_reais"
    so_ids = montar_contexto_lote_llm(resumo, resultados, enviar_identificadores=True)
    assert RAZOES[0] in json.dumps(so_ids) and "notas_amostradas" not in so_ids


def test_codigos_sao_estaveis_independente_da_ordem_das_notas() -> None:
    resumo_a, resultados_a = _lote(NOMES)
    resumo_b, resultados_b = _lote(tuple(reversed(NOMES)))
    mapa_a = mapear_codigos_emitentes(resumo_a, resultados_a)
    mapa_b = mapear_codigos_emitentes(resumo_b, resultados_b)
    assert {k: v["codigo"] for k, v in mapa_a.items()} == {
        k: v["codigo"] for k, v in mapa_b.items()
    }
    assert sorted(v["codigo"] for v in mapa_a.values())[0] == "Emitente 01"


def test_anonimizar_texto_troca_nome_e_cnpj_formatado_ou_nao() -> None:
    resumo, resultados = _lote()
    mapa = mapear_codigos_emitentes(resumo, resultados)
    texto = (
        "Quanto a industria alfa metalurgica ltda vendeu? CNPJ 11.222.333/0001-81 "
        "ou 11222333000181 e 12ABC34501DE35?"
    )
    anonimo = anonimizar_texto(texto, mapa)
    _sem_identificadores(anonimo)
    assert "Emitente" in anonimo


def test_o_que_vai_ao_provedor_nao_tem_identificadores_e_a_traducao_e_local(
    tmp_path: Path,
) -> None:
    resumo, resultados = _lote()
    enviados: list[list[dict[str, str]]] = []

    def provedor(mensagens: list[dict[str, str]], modelo: str, base_url: str, api_key: str) -> str:
        enviados.append(mensagens)
        return "O Emitente 01 tem o maior valor; o Emitente 02 vem depois."

    servico = ServicoAssistenteLote(
        cliente_completion=provedor, cache=CachePerguntasLLM(tmp_path / "c.json")
    )
    resp = servico.perguntar(
        resumo=resumo,
        resultados=resultados,
        pergunta="Compare INDUSTRIA ALFA METALURGICA LTDA (11.222.333/0001-81) com os demais",
    )
    assert len(enviados) == 1
    _sem_identificadores(json.dumps(enviados[0], ensure_ascii=False))
    assert "Emitente 01" in resp.resposta
    nomes = {item["razao_social"] for item in resp.legenda_codigos}
    assert nomes <= set(RAZOES) and len(nomes) == 2


def test_com_opcoes_ligadas_o_provedor_recebe_nomes_reais(tmp_path: Path) -> None:
    resumo, resultados = _lote()
    enviados: list[list[dict[str, str]]] = []

    def provedor(mensagens: list[dict[str, str]], modelo: str, base_url: str, api_key: str) -> str:
        enviados.append(mensagens)
        return "ok"

    servico = ServicoAssistenteLote(
        cliente_completion=provedor,
        cache=CachePerguntasLLM(tmp_path / "c.json"),
        enviar_identificadores=True,
        enviar_amostra=True,
    )
    resp = servico.perguntar(resumo=resumo, resultados=resultados, pergunta="Resumo do lote?")
    texto = json.dumps(enviados[0], ensure_ascii=False)
    assert RAZOES[0] in texto and "nfe_55_soma_errada.xml" in texto
    assert resp.legenda_codigos == ()


def test_api_devolve_legenda_e_status_mostra_o_modo(tmp_path: Path) -> None:
    obter_repositorio_lotes().limpar()
    definir_servico_assistente(
        ServicoAssistenteLote(
            cliente_completion=lambda m, mo, b, k: "Emitente 01 lidera o faturamento.",
            cache=CachePerguntasLLM(tmp_path / "c.json"),
        )
    )
    try:
        client = TestClient(app)
        st = client.get("/api/llm/status").json()
        assert st["enviar_identificadores"] is False and st["enviar_amostra"] is False
        lote_id = client.post("/api/lote/demo").json()["lote_id"]
        r = client.post(f"/api/lote/{lote_id}/perguntar", json={"pergunta": "Quem lidera?"})
        assert r.status_code == 200
        legenda = r.json()["legenda_codigos"]
        assert legenda and legenda[0]["codigo"] == "Emitente 01"
        assert legenda[0]["razao_social"] in RAZOES
    finally:
        definir_servico_assistente(ServicoAssistenteLote())


def test_chip_de_notas_com_alertas_so_aparece_com_amostra_ligada() -> None:
    raiz = Path(__file__).resolve().parents[1] / "web"
    html = (raiz / "templates" / "index.html").read_text(encoding="utf-8")
    js = (raiz / "static" / "app.js").read_text(encoding="utf-8")
    chip = html[html.index('id="chip-llm-notas-alertas"') :].split(">", 1)[0]
    assert "hidden" in chip and "data-requer-amostra" in chip
    assert 'classList.toggle("hidden", !st.enviar_amostra)' in js
