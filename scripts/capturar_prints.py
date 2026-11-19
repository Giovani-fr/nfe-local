"""Gera os prints do README (`docs/prints/`) com dados 100% sintéticos, com checagem de privacidade.

Sobe o app em memória, num banco e numa pasta de dados temporários (nunca toca em
`data/nfe_recebidas.db`, no certificado real nem em XMLs reais), carrega só as demonstrações
sintéticas, abre o navegador e tira os prints. ANTES de cada print o texto e o HTML da página
passam por uma verificação automática: se aparecer CNPJ/CPF/e-mail fora da lista sintética
(fixtures e demos), o nome do usuário do Windows ou um caminho local, o script aborta sem gravar
aquela imagem.

Requisitos extras (só para este script): `pip install playwright` e o Google Chrome instalado
(ou `playwright install chromium`).

Uso:  python scripts/capturar_prints.py [--saida docs/prints]
"""

from __future__ import annotations

import argparse
import getpass
import importlib
import os
import re
import shutil
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

SENHA_SINTETICA = "senha-ficticia-prints"  # certificado autoassinado gerado na hora
_RE_CNPJ = re.compile(r"(?<!\d)\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}(?!\d)")
_RE_CPF = re.compile(r"(?<!\d)\d{3}\.\d{3}\.\d{3}-\d{2}(?!\d)")
_RE_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def _somente_digitos(texto: str) -> str:
    return re.sub(r"\D", "", texto)


def _lista_permitida() -> tuple[set[str], set[str]]:
    """Documentos e e-mails que existem nas fixtures sintéticas e nas demos do repositório."""
    docs: set[str] = set()
    emails: set[str] = set()
    fontes = list((RAIZ / "tests" / "fixtures").rglob("*.xml")) + [RAIZ / "sefaz" / "adn_demo.py"]
    for arq in fontes:
        texto = arq.read_text(encoding="utf-8", errors="ignore")
        docs |= {_somente_digitos(m) for m in _RE_CNPJ.findall(texto)}
        docs |= {_somente_digitos(m) for m in _RE_CPF.findall(texto)}
        docs |= set(re.findall(r"(?<!\d)\d{11}(?!\d)", texto))  # CPF sem pontuação nas fixtures
        emails |= {m.lower() for m in _RE_EMAIL.findall(texto)}
    return docs, emails


def verificar_privacidade(
    nome: str, textos: list[str], permitidos: tuple[set[str], set[str]], proibidos: list[str]
) -> None:
    """Aborta (sem imprimir o valor encontrado) se a página tiver algo fora da lista sintética."""
    docs_ok, emails_ok = permitidos
    corpo = "\n".join(textos)
    problemas: list[str] = []
    for m in _RE_CNPJ.findall(corpo) + _RE_CPF.findall(corpo):
        if _somente_digitos(m) not in docs_ok:
            problemas.append("CNPJ/CPF fora da lista sintética")
            if os.environ.get("NFE_PRINTS_DEBUG"):  # só para depurar dados sintéticos locais
                print("DEBUG doc fora da lista:", m)
    for m in _RE_EMAIL.findall(corpo):
        if m.lower() not in emails_ok and not m.lower().endswith("@2x.png"):
            problemas.append("e-mail fora da lista sintética")
    if re.search(r"[A-Za-z]:[\\]Users[\\]|/Users/|/home/[a-z]", corpo):
        problemas.append("nome de usuário ou caminho local")
    baixo = corpo.lower()
    for termo in proibidos:
        if termo and termo.lower() in baixo:
            problemas.append("nome de usuário ou caminho local")
    if problemas:
        raise SystemExit(
            f"PRIVACIDADE: '{nome}' não foi gravado ({len(problemas)} ocorrência(s): "
            f"{sorted(set(problemas))}). Nada foi salvo para essa imagem."
        )


def _porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _preparar_ambiente(tmp: Path) -> None:
    for var in (
        "NFE_CERT_PATH",
        "NFE_CERT_SENHA",
        "NFE_CERT_DIR",
        "LLM_API_KEY",
        "NFE_ADN_URL_BASE",
    ):
        os.environ.pop(var, None)
    os.environ["NFE_DB_PATH"] = str(tmp / "prints.db")
    os.environ["NFE_DATA_DIR"] = str(tmp / "dados")
    from sefaz.adn_demo import CNPJ_DEMO

    os.environ["NFE_CNPJ_CONSULTA"] = CNPJ_DEMO  # referência fictícia para Emitida/Recebida


def _semear(base: str) -> None:
    """Certificado sintético, demos e logs sintéticos para os cartões de status."""
    import httpx

    modulo_app = importlib.import_module("api.app")
    from sefaz.adn import EstadoCursorADN
    from sefaz.cert_a1 import salvar_certificado_local
    from sefaz.controle_nsu import EstadoCursorNSU
    from tests.test_certificado_api import _gerar_pfx_sintetico

    if sys.platform != "win32":
        os.environ["NFE_CERT_SENHA"] = SENHA_SINTETICA
    pfx = _gerar_pfx_sintetico(
        common_name="EMPRESA EXEMPLO LTDA", cnpj="11222333000181", senha=SENHA_SINTETICA
    )
    salvar_certificado_local(pfx, SENHA_SINTETICA, uf_autor="35", ambiente=2)

    with httpx.Client(base_url=base, timeout=30) as cli:
        # XML sintético quebrado (fixture) para mostrar a lista de ilegíveis no resumo
        quebrado = (RAIZ / "tests" / "fixtures" / "xml_malformado.xml").read_bytes()
        cli.post(
            "/api/lote", files=[("files", ("xml_malformado.xml", quebrado, "application/xml"))]
        )
        for rota in ("/api/lote/demo", "/api/recebidas/demo"):
            if cli.post(rota).status_code != 200:
                raise SystemExit(f"Falha ao carregar a demonstração ({rota}).")

    repo = modulo_app.obter_repositorio_recebidas()
    repo.salvar_cursor(
        EstadoCursorNSU(
            ambiente=2,
            ultimo_nsu="000000000000003",
            max_nsu="000000000000003",
            ultimo_cstat="138",
            ultimo_motivo="Documento(s) localizado(s)",
        )
    )
    repo.salvar_cursor_adn(
        EstadoCursorADN(ambiente=2, ultimo_nsu=8, documento_consulta="11222333000181")
    )
    repo.registrar_log_sync(
        ambiente=2,
        cstat="138",
        motivo="Documento(s) localizado(s)",
        paginas=1,
        novos_docs=3,
        ultimo_nsu="000000000000003",
    )
    repo.registrar_log_sync(
        ambiente=2,
        cstat="NENHUM_DOCUMENTO_LOCALIZADO",
        motivo="Nenhum documento novo.",
        paginas=2,
        novos_docs=7,
        ultimo_nsu="8",
        origem="NFSE",
        tentativas=3,
        falhas_conexao="pág. 1, tentativa 1: leitura da resposta [RemoteDisconnected]",
    )


class _RespostaFalsa:
    status_code = 200
    headers: dict[str, str] = {}

    def __init__(self) -> None:
        import json

        itens = [
            {"NSU": n, "ChaveAcesso": "1" * 50, "TipoDocumento": tipo, "ArquivoXml": "AAAA"}
            for n, tipo in enumerate(["NFSE", "NFSE", "NFSE", "NFSE", "EVENTO", "DPS"], start=1)
        ]
        self.content = json.dumps(
            {
                "TipoConsulta": "CnpjBase",
                "StatusProcessamento": "DOCUMENTOS_LOCALIZADOS",
                "LoteDFe": itens,
                "Alertas": [],
                "Erros": [],
                "TipoAmbiente": "HOMOLOGACAO",
            }
        ).encode()

        class _Sock:
            def version(self) -> str:
                return "TLSv1.3"

            def cipher(self) -> tuple[str, str, int]:
                return ("TLS_AES_256_GCM_SHA384", "TLSv1.3", 256)

        class _Raw:
            class _Con:
                sock = _Sock()

            _connection = _Con()

        self.raw = _Raw()


class _SessaoFalsa:
    """Sessão HTTP simulada: o diagnóstico dos prints nunca toca a rede."""

    def get(self, url, params=None, headers=None, timeout=None, **kw):  # noqa: ANN001, ANN003
        return _RespostaFalsa()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--saida", default=str(RAIZ / "docs" / "prints"))
    args = ap.parse_args()
    saida = Path(args.saida)
    saida.mkdir(parents=True, exist_ok=True)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "Instale o Playwright: pip install playwright (e use o Chrome instalado).",
            file=sys.stderr,
        )
        return 2

    tmp = Path(tempfile.mkdtemp(prefix="nfe_prints_"))
    try:
        _preparar_ambiente(tmp)
        import uvicorn

        modulo_app = importlib.import_module("api.app")

        porta = _porta_livre()
        servidor = uvicorn.Server(
            uvicorn.Config(modulo_app.app, host="127.0.0.1", port=porta, log_level="warning")
        )
        threading.Thread(target=servidor.run, daemon=True).start()
        base = f"http://127.0.0.1:{porta}"
        import httpx

        for _ in range(100):
            try:
                if httpx.get(base + "/api/llm/status", timeout=1).status_code == 200:
                    break
            except Exception:  # noqa: BLE001
                time.sleep(0.1)
        _semear(base)
        modulo_app.definir_sessao_http_adn(_SessaoFalsa())

        permitidos = _lista_permitida()
        proibidos = [
            getpass.getuser(),
            Path.home().name,
            str(tmp),
            str(RAIZ),
        ]

        with sync_playwright() as pw:
            try:
                navegador = pw.chromium.launch(channel="chrome", headless=True)
            except Exception:  # noqa: BLE001 - cai para o Chromium do Playwright
                navegador = pw.chromium.launch(headless=True)
            ctx = navegador.new_context(
                viewport={"width": 1440, "height": 900}, locale="pt-BR", color_scheme="dark"
            )
            page = ctx.new_page()

            def tirar(nome: str, *, elemento: str | None = None, completa: bool = False) -> None:
                page.wait_for_timeout(400)
                textos = [page.inner_text("body"), page.content()]
                verificar_privacidade(nome, textos, permitidos, proibidos)
                destino = saida / nome
                if elemento:
                    page.locator(elemento).screenshot(path=str(destino))
                else:
                    page.screenshot(path=str(destino), full_page=completa)
                print(
                    f"ok  {destino.relative_to(RAIZ) if destino.is_relative_to(RAIZ) else destino}"
                )

            page.goto(base)
            page.wait_for_selector("#painel-lote:not(.hidden)")
            page.wait_for_selector("#tbody-notas tr")
            tirar("tabela-notas.png")

            page.click("#btn-subtab-resumo")
            page.wait_for_timeout(300)
            tirar("painel-gerencial.png")
            page.click("#btn-subtab-notas")

            page.click("#btn-escopo-tudo")
            page.wait_for_function(
                "document.getElementById('badge-escopo-ativo').textContent.includes('VISÃO TOTAL')"
            )
            tirar("escopo-tudo.png")

            page.set_viewport_size({"width": 1440, "height": 1500})
            page.click("#btn-tab-recebidas")
            page.wait_for_selector("#tbody-recebidas tr td.col-papel-rec")
            page.wait_for_function("document.getElementById('lbl-adn-nsu').textContent !== '0'")
            tirar("sefaz-adn.png")

            page.click("#btn-diag-adn")
            page.wait_for_selector("#adn-diagnostico .badge-ok")
            tirar("diagnostico-adn.png", elemento="#adn-diagnostico")

            page.set_viewport_size({"width": 1100, "height": 920})
            notas = httpx.get(
                base + "/api/recebidas", params={"origem": "SEFAZ", "categoria": "NFE_COMPLETA"}
            ).json()["notas"]
            page.goto(f"{base}/recebidas/{notas[0]['chave']}/espelho")
            tirar("danfe.png")
            nfse = httpx.get(
                base + "/api/recebidas",
                params={"origem": "ADN", "papel": "EMITIDA", "situacao": "AUTORIZADA"},
            ).json()["notas"]
            page.set_viewport_size({"width": 1100, "height": 1180})
            page.goto(f"{base}/recebidas/{nfse[0]['chave']}/espelho")
            tirar("danfse.png")
            navegador.close()
        servidor.should_exit = True
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
