# CLAUDE.md — Diretrizes de Arquitetura e Execução (`nfe-local`)

Este documento é a fonte de verdade para as sessões de execução no VS Code. Leia-o integralmente antes de iniciar qualquer tarefa do `docs/BACKLOG.md`.

---

## 1. Visão Geral do Projeto
O **`nfe-local`** é um visualizador, validador e analisador em lote de documentos fiscais eletrônicos brasileiros:
- **NF-e** (modelo 55)
- **NFC-e** (modelo 65)
- **NFS-e** (exclusivamente no **Padrão Nacional** — `http://www.sped.fazenda.gov.br/nfse`, ignorando layouts municipais legados como ABRASF).

O sistema opera **100% local e sem custo de API**, oferecendo:
1. **Fase 1 (MVP):** Upload de um ou vários XMLs (ou `.zip`), detecção automática do tipo de documento, validação fiscal e financeira com alertas claros, visualização estilo DANFE (NF-e/NFC-e) e DANFSe (NFS-e NT 008/2026), resumo gerencial de lote (por emitente, imposto e período), exportação CSV/XLSX, API REST (FastAPI), interface web simples e CLI amigável (inspirada em ferramentas agent-ready como `pycodebr/nfseai`).
2. **Fase 2 (DistDFe SEFAZ + ADN NFS-e):** Busca automática de notas emitidas contra o CNPJ/CPF da empresa via WebService SOAP `NFeDistribuicaoDFe` (Ambiente Nacional da NF-e) com autenticação mTLS por certificado digital ICP-Brasil **e-CNPJ A1** (`.pfx`), controle incremental de NSU (`ultNSU` / `maxNSU`), tratamento de bloqueio `cStat 656` (1 hora) e persistência local leve em SQLite.

---

## 2. Regras Inegociáveis de Execução (Workflow)
1. **Uma tarefa por vez:** Execute estritamente a tarefa solicitada do `docs/BACKLOG.md`. Não antecipe código de tarefas futuras.
2. **TDD (Teste antes do código):** Escreva os testes automatizados (`pytest`) cobrindo os cenários de sucesso e de borda antes (ou junto) da implementação e garanta que toda a suíte passe.
3. **Qualidade e Linter:** Todo código deve passar sem avisos no `ruff check` e `ruff format --check`.
4. **Privacidade e Segurança Absoluta (Repositório Público desde o Dia 1):**
   - **NUNCA** inclua XMLs reais, chaves de acesso reais, CNPJs/CPFs de empresas reais (como da a empresa do autor ou de clientes/fornecedores) ou certificados digitais no código, nos testes ou nos commits.
   - Todas as fixtures em `tests/fixtures/` devem usar dados **100% fictícios** (ex.: `"EMPRESA EXEMPLO LTDA"`, CNPJs gerados matematicamente para teste) e chaves com dígito verificador calculado sobre dados sintéticos.
   - Segredos (caminho do certificado `.pfx`, senha do certificado, chaves de API opcionais) ficam **exclusivamente em variáveis de ambiente** (`.env` listado no `.gitignore`), nunca hardcoded.
5. **Segurança no Parser XML (Anti-XXE):**
   - Todo parse de XML enviado pelo usuário ou recebido da rede deve usar `lxml.etree.XMLParser(resolve_entities=False, no_network=True, dtd_validation=False, load_dtd=False)`.
6. **Commits pequenos e semânticos:** Um commit limpo ao fechar cada tarefa, atualizando o checkbox correspondente em `docs/BACKLOG.md`.
7. **Registro "Como usei IA":** Ao concluir cada tarefa, entregue um resumo curto (3 linhas) do que foi feito e eventuais ajustes técnicos para alimentar a seção *"Como usei IA"* do `README.md`.

---

## 3. Estrutura do Repositório

```text
nfe-local/
├── CLAUDE.md
├── README.md                  # Português (padrão transcritor-local)
├── README.en.md               # Inglês
├── LICENSE                    # MIT
├── pyproject.toml             # Configuração de pacote, pytest, ruff e CLI script
├── requirements.txt
├── requirements-dev.txt
├── Dockerfile
├── .env.example
├── .gitignore
├── instalar.bat               # Instalador amigável Windows (padrão transcritor-local)
├── iniciar.bat                # Inicializador local (127.0.0.1)
├── docs/
│   ├── BACKLOG.md
│   ├── benchmark.md
│   └── prints/
├── parsers/
│   ├── __init__.py
│   ├── xml_seguro.py          # Wrapper lxml anti-XXE e helpers namespace-agnósticos
│   ├── detector.py            # Identifica NFE (55), NFCE (65), NFSE Nacional, RESUMO, EVENTO
│   ├── nfe.py                 # Parser NF-e (55) e NFC-e (65), com/sem protNFe, resNFe, evento
│   └── nfse_nacional.py       # Parser NFS-e Padrão Nacional (infNFSe / infDPS / IBSCBS)
├── models/
│   ├── __init__.py
│   ├── normalizadores.py      # Decimal, datas ISO, limpeza segura de CNPJ/Chave alfanumérica
│   └── fiscal.py              # Objetos Pydantic v2 (DocumentoFiscal, Parte, Item, Imposto, etc.)
├── validators/
│   ├── __init__.py
│   ├── chave_acesso.py        # 44 dígitos/alfanumérico + módulo 11 (NT 2014.002 v1.40)
│   ├── documentos_parte.py    # CPF e CNPJ (numérico e alfanumérico ASCII - 48)
│   ├── financeiro.py          # Soma dos itens == vProd e consistência de vNF
│   └── lote.py                # Chave duplicada no lote, alerta homologação, sem protocolo
├── reports/
│   ├── __init__.py
│   ├── resumo.py              # Agregações por emitente, imposto e período (streaming-friendly)
│   ├── exportacao.py          # Geração de CSV e XLSX (openpyxl)
│   └── danfe_svg.py           # Code 128 SVG zero-dependência e formatadores pt-BR para DANFE/DANFSe
├── sefaz/                     # (Fase 2)
│   ├── __init__.py
│   ├── cert_a1.py             # Leitura PKCS#12 (.pfx) em memória via cryptography
│   ├── dist_dfe.py            # Cliente SOAP 1.2 NFeDistribuicaoDFe (mTLS) do zero
│   ├── descompactador.py      # docZip (base64 + gzip) -> XML bytes
│   └── storage.py             # SQLite mínimo (dfe_cursor, notas_recebidas, sync_log)
├── api/
│   ├── __init__.py
│   └── app.py                 # Rotas FastAPI (upload XML/ZIP, lote, resumo, exportação, recebidas)
├── web/
│   ├── static/
│   └── templates/             # UI em Dark Mode + espelhos A4 de DANFE e DANFSe (NT 008/2026)
└── tests/
    ├── fixtures/              # XMLs 100% fictícios (válidos e com defeitos propositais)
    └── ...
```

---

## 4. Modelo de Dados (Pydantic v2)

Use `Decimal` para todos os campos monetários e de quantidade (evitando erros de ponto flutuante na validação financeira).

| Entidade | Campos Principais |
| :--- | :--- |
| **`DocumentoFiscal`** | `tipo` (`NFE`, `NFCE`, `NFSE`, `RESUMO`, `EVENTO`), `modelo` (`55`, `65`, `NFSE`), `serie` (`str`), `numero` (`str`), `chave_acesso` (`str` 44 ou 50 posições), `data_emissao` (`datetime`), `ambiente` (`Literal[1, 2]`), `protocolo` (`str \| None`), `situacao` (`AUTORIZADA`, `CANCELADA`, `SEM_PROTOCOLO`, `RESUMO`), `natureza_operacao` (`str \| None`), `emitente` (`Parte`), `destinatario` (`Parte \| None`), `itens` (`list[Item]`), `totais` (`Totais`), `pagamentos` (`list[Pagamento]`), `informacoes_complementares` (`str \| None`). |
| **`Parte`** | `documento` (`str` — CNPJ 14 alfanumérico, CPF 11 numérico ou ID estrangeiro), `tipo_pessoa` (`PJ`, `PF`, `ESTRANGEIRO`, `NAO_IDENTIFICADO`), `razao_social` (`str`), `nome_fantasia` (`str \| None`), `inscricao_estadual` (`str \| None`), `inscricao_municipal` (`str \| None`), `uf` (`str \| None`), `municipio` (`str \| None`), `codigo_ibge` (`str \| None`), `endereco` (`str \| None`), `cep` (`str \| None`), `telefone` (`str \| None`), `email` (`str \| None`). |
| **`ImpostoItem`** | `cst_ou_csosn` (`str \| None`), `icms_bc` (`Decimal`), `icms_aliquota` (`Decimal`), `icms_valor` (`Decimal`), `icms_st_valor` (`Decimal`), `ipi_aliquota` (`Decimal`), `ipi_valor` (`Decimal`), `pis_valor` (`Decimal`), `cofins_valor` (`Decimal`), `iss_aliquota` (`Decimal`), `iss_valor` (`Decimal`), `iss_retido` (`bool`). |
| **`Item`** | `numero_item` (`int`), `codigo` (`str`), `descricao` (`str`), `ncm` (`str \| None`), `cfop` (`str \| None`), `codigo_tributacao_nacional` (`str \| None` — para NFS-e), `unidade` (`str`), `quantidade` (`Decimal`), `valor_unitario` (`Decimal`), `valor_desconto` (`Decimal`), `valor_frete` (`Decimal`), `valor_outros` (`Decimal`), `valor_total` (`Decimal`), `imposto` (`ImpostoItem`), `info_adicional` (`str \| None`). |
| **`ImpostoTotal`** | `icms_bc` (`Decimal`), `icms` (`Decimal`), `icms_st_bc` (`Decimal`), `icms_st` (`Decimal`), `ipi` (`Decimal`), `pis` (`Decimal`), `cofins` (`Decimal`), `iss` (`Decimal`), `retencoes_federais` (`Decimal`), `ibs_cbs` (`Decimal`), `tributos_aproximados` (`Decimal`). |
| **`Totais`** | `valor_produtos_servicos` (`Decimal`), `valor_frete` (`Decimal`), `valor_seguro` (`Decimal`), `valor_desconto` (`Decimal`), `valor_outras_despesas` (`Decimal`), `impostos` (`ImpostoTotal`), `valor_liquido` (`Decimal`), `valor_total_nota` (`Decimal`). |
| **`Pagamento`** | `forma_codigo` (`str`), `forma_descricao` (`str`), `valor` (`Decimal`), `numero_duplicata` (`str \| None`), `vencimento` (`date \| None`). |
| **`AlertaValidacao`** | `codigo` (`str`), `severidade` (`Literal["ERRO", "AVISO"]`), `mensagem` (`str`), `campo` (`str \| None`). |
| **`ResultadoAnalise`** | `arquivo_origem` (`str`), `valido` (`bool`), `documento` (`DocumentoFiscal \| None`), `alertas` (`list[AlertaValidacao]`), `erro_fatal` (`str \| None`). |

---

## 5. Detalhes Técnicos Críticos

1. **CNPJ Alfanumérico e Chave de Acesso (NT 2014.002 v1.40 - Julho/2026):**
   - O CNPJ passa a aceitar números e letras maiúsculas (`[0-9A-Z]`) nas 12 primeiras posições (raiz + ordem), mantendo os 2 dígitos verificadores numéricos.
   - No cálculo do Módulo 11 do CNPJ e da Chave de Acesso de 44 posições (cujas posições 7 a 20 contêm o CNPJ do emitente), o valor de cada caractere é obtido por `ord(char.upper()) - 48` (assim `'0'`=0 ... `'9'`=9, `'A'`=17 ... `'Z'`=42).
   - **Proibido usar `re.sub(r"\D", "", ...)`** para limpar CNPJ ou Chave de Acesso. Use limpeza alfanumérica (`re.sub(r"[^0-9A-Za-z]", "", valor).upper()`), removendo prefixos conhecidos no início (`NFe`, `CTe`, `NFSe`, `NFS`, `DPS`).
2. **Batimento Financeiro Inteligente:**
   - Validar que a soma de `item.valor_total` ($\sum \text{vProd}$) bate com `totais.valor_produtos_servicos` (`vProd` do `ICMSTot`), com tolerância de `Decimal('0.01')` apenas quando expressamente configurado, e validar a equação da nota: `vProd - vDesc + vFrete + vSeg + vOutro + vIPI + vST == vNF`.
3. **Referência de Domínio Local (`projeto-de-referencia`):**
   - Na pasta vizinha `../projeto-de-referencia/utils/` existem implementações de referência já validadas pelo autor (`nfe_extrai.py`, `danfe.py`, `nfse_nacional_extrai.py`, `nfse_dominios.py`, `cert_a1.py`, `nfse_adn.py`). Podem ser consultadas como inspiração de mapeamento de tags XML e layout HTML A4, mas **nunca importe nem altere arquivos do `projeto-de-referencia`** e **nunca copie dados/XMLs reais de lá**.
