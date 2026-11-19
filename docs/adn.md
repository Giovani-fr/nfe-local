# ADN Contribuintes (NFS-e Padrão Nacional): o que foi confirmado

Resumo técnico escrito a partir do **Swagger oficial** (`swagger.json`, OpenAPI 3.0.1, título
"API NFS-e - ADN Contribuinte", versão `v1`, baixado em 19/11/2026 da produção restrita). O arquivo
**não** foi copiado para este repositório. Nada abaixo vem de memória: o que não está no Swagger
aparece marcado como **não confirmado**.

Páginas oficiais de origem (a página de APIs do gov.br lista os dois endereços):

- Produção restrita: `https://adn.producaorestrita.nfse.gov.br/contribuintes/docs/index.html`
- Produção: `https://adn.nfse.gov.br/contribuintes/docs/index.html`
- Índice: `https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica/apis-prod-restrita-e-producao`

## URLs

O Swagger **não traz `servers`**; os caminhos são relativos. Como a documentação fica em
`/contribuintes/docs`, a base usada é:

| Ambiente | Base (inferida do endereço da documentação) |
| :--- | :--- |
| Produção restrita (homologação) | `https://adn.producaorestrita.nfse.gov.br/contribuintes` |
| Produção | `https://adn.nfse.gov.br/contribuintes` |

> **Não confirmado:** que o prefixo `/contribuintes` faz parte da URL da API (e não só da
> documentação). A primeira chamada real confirma. Se estiver errado, a URL é configurável sem
> mexer no código: variável `NFE_ADN_URL_BASE` ou `--url-base` na CLI (precisa começar com
> `https://`; a opção vale mais que a variável, que vale mais que o padrão do ambiente).
> A produção foi assumida com o mesmo contrato da produção restrita (o Swagger dela não foi baixado).

## Autenticação

TLS 1.2 ou superior com **autenticação mútua**; certificado ICP-Brasil **A1 ou A3**, CNPJ ou CPF,
com "Autenticação do Cliente". Este projeto usa só A1 (`.pfx`) lido em memória.
Formato das mensagens: JSON. Documentos: XML 1.0, UTF-8, assinado (XMLDSIG).
Compactação dos XMLs: **GZip com representação base64** (`ArquivoXml`).

## Endpoints

### `GET /DFe/{NSU}`: distribuição por NSU

Retorna documentos a partir do NSU informado, **exclusive** (o próximo NSU a pedir é o último
recebido; não há `+1` a somar).

| Parâmetro | Onde | Tipo | Observação |
| :--- | :--- | :--- | :--- |
| `NSU` | path, obrigatório | int64 | Exclusivo. |
| `cnpjConsulta` | query, opcional | string | CNPJ completo, **sempre 14 dígitos**, DV válido. Mesmo CNPJ base (8 primeiros) do certificado é autorizado sem procuração; base diferente exige procuração. |
| `cpfConsulta` | query, opcional | string | 11 dígitos; exige ser o CPF do certificado ou procuração. |
| `lote` | query, opcional | boolean (padrão `true`) | `true`: até o tamanho máximo do lote; `false`: um documento. |

`cnpjConsulta` e `cpfConsulta` **não podem ir juntos** (E2244).
Sem nenhum dos dois, o Swagger diz que são devolvidos os documentos do titular identificado pelo
certificado. Observação de uso real do usuário (nov/2026): sem `cnpjConsulta` houve HTTP 400
`REJEICAO` com `TipoConsulta=InscricaoCertificado` e lista de erros vazia; com ele, HTTP 200
(`TipoConsulta=CnpjBase`). **O cliente sempre envia `cnpjConsulta`.**

Respostas documentadas (o Swagger declara o content-type como `text/plain`, mas o corpo é JSON):

| HTTP | Significado |
| :--- | :--- |
| 200 | `DOCUMENTOS_LOCALIZADOS`: documentos em `LoteDFe`. |
| 400 | `REJEICAO`: motivos em `Erros`. |
| 404 | `NENHUM_DOCUMENTO_LOCALIZADO`: **não é falha**, é o fim da fila (E2220/E2230). **Só** vale se o corpo for JSON válido com esse `StatusProcessamento` ou um desses códigos; 404 vazio, HTML ou JSON de outro tipo é tratado como endereço errado (categoria `ENDERECO`, encerra como `ERRO`). |

Não há resposta documentada para 429/503 nem campo de bloqueio/limite de uso: ver "Divergências".

### `GET /NFSe/{ChaveAcesso}/Eventos`

Eventos vinculados a uma chave (200, 400, 404). Resposta sem `TipoConsulta`. **Não usado nesta
parte**; os eventos chegam também pelo fluxo por NSU.

## Corpo da resposta de `/DFe/{NSU}` (`LoteDistribuicaoNSUResponseConsulta`)

| Campo | Tipo | Observação |
| :--- | :--- | :--- |
| `TipoConsulta` | enum | `InscricaoCertificado`, `CnpjBase`, `Procuracao` (nas rejeições vem o padrão `InscricaoCertificado`). |
| `StatusProcessamento` | enum | `REJEICAO`, `NENHUM_DOCUMENTO_LOCALIZADO`, `DOCUMENTOS_LOCALIZADOS`. |
| `LoteDFe` | array\|null | Itens abaixo. |
| `Alertas`, `Erros` | array\|null | Cada item: `Codigo`, `Descricao`, `Complemento` (todos opcionais). |
| `TipoAmbiente` | enum | `PRODUCAO`, `HOMOLOGACAO`. |
| `VersaoAplicativo` | string | |
| `DataHoraProcessamento` | date-time | |

Item de `LoteDFe` (`DistribuicaoNSU`):

| Campo | Tipo | Observação |
| :--- | :--- | :--- |
| `NSU` | int64\|null | |
| `ChaveAcesso` | string\|null | |
| `TipoDocumento` | enum | `NENHUM`, `DPS`, `PEDIDO_REGISTRO_EVENTO`, `NFSE`, `EVENTO`, `CNC`. |
| `TipoEvento` | enum\|null | `CANCELAMENTO`, `SOLICITACAO_CANCELAMENTO_ANALISE_FISCAL`, `CANCELAMENTO_POR_SUBSTITUICAO`, `CANCELAMENTO_DEFERIDO_ANALISE_FISCAL`, `CANCELAMENTO_INDEFERIDO_ANALISE_FISCAL`, `CONFIRMACAO_PRESTADOR`, `REJEICAO_PRESTADOR`, `CONFIRMACAO_TOMADOR`, `REJEICAO_TOMADOR`, `CONFIRMACAO_INTERMEDIARIO`, `REJEICAO_INTERMEDIARIO`, `CONFIRMACAO_TACITA`, `ANULACAO_REJEICAO`, `CANCELAMENTO_POR_OFICIO`, `BLOQUEIO_POR_OFICIO`, `DESBLOQUEIO_POR_OFICIO`, `TRIBUTOS_NFSE_RECOLHIDOS`, `INCLUSAO_NFSE_DAN`. |
| `ArquivoXml` | string\|null | XML em GZip + base64. |
| `DataHoraGeracao` | date-time\|null | |

## Códigos de erro documentados (`Erros[].Codigo`)

| Código | HTTP | Descrição oficial (resumida) |
| :--- | :--- | :--- |
| E2210 | 400 | Certificado de transmissão inválido. |
| E2211 | 400 | Certificado revogado. |
| E2212 | 400 | Erro na cadeia de certificação. |
| E2213 | 400 | Certificado expirado. |
| E2214 | 400 | Erro de acesso à LCR. |
| E2220 | 404 | Nenhum documento a partir do NSU informado. |
| E2230 | 404 | Nenhum documento com o NSU informado. |
| E2240 | 404 | Nenhum documento para a chave informada. |
| E2241 | 404 | Chave da NFS-e não referencia o contribuinte solicitante. |
| E2243 | 400 | `cnpjConsulta` deve ter o mesmo CNPJ base do solicitante. |
| E2244 | 400 | Informar só um entre `cnpjConsulta` e `cpfConsulta`. |
| E2245 | 400 | Não há procuração da inscrição `{0}` para a inscrição `{1}`. |
| E2246 | 400 | `cnpjConsulta` inválido (dígito verificador). |
| E2247 | 400 | `cpfConsulta` inválido (dígito verificador). |
| E999 | 400 | Falha não tratada; a descrição traz um identificador para acionar o suporte. |

## Divergências entre o pedido original e o Swagger (vale o Swagger)

1. **"Sem documentos"** é **HTTP 404** com `NENHUM_DOCUMENTO_LOCALIZADO` (E2220), não um 200 vazio.
   Tratado como fim normal da fila, sem erro.
2. **Bloqueio, limite de uso e indisponibilidade:** o Swagger **não documenta** código de bloqueio
   (equivalente ao `cStat 656`), 429 nem `Retry-After`. O cliente trata de forma genérica e
   conservadora: HTTP 429/503/5xx ou falha de conexão registram o motivo em `sync_log` e travam
   novas consultas por um intervalo (usa `Retry-After` quando existir; senão 1 hora para 429 e
   10 minutos para 5xx). Esse intervalo é uma **escolha do projeto**, não uma regra do ADN.
3. **Código de rejeição de certificado** é uma família documentada (E2210 a E2214), mas a
   rejeição observada em uso real (HTTP 400 com `Erros` vazio) **não tem código**: a mensagem
   não culpa o certificado.
4. **`TipoDocumento`** tem seis valores. Guardados: `NFSE` e `EVENTO`. Ignorados e contados:
   `DPS`, `CNC`, `PEDIDO_REGISTRO_EVENTO` e `NENHUM`.
5. **Cancelamento:** o Swagger entrega `TipoEvento` já classificado. A nota vira `CANCELADA` para
   `CANCELAMENTO`, `CANCELAMENTO_POR_SUBSTITUICAO`, `CANCELAMENTO_DEFERIDO_ANALISE_FISCAL` e
   `CANCELAMENTO_POR_OFICIO`. `SOLICITACAO_CANCELAMENTO_ANALISE_FISCAL` **não** cancela (é só um
   pedido).
6. **Papel (prestador/tomador/intermediário):** o Swagger não diz nada sobre isso; é calculado a
   partir do XML da NFS-e (`prest`, `toma`, `interm` do layout nacional, **não confirmado aqui**) e
   comparado com o CNPJ **consultado** (por padrão o do certificado; com `NFE_CNPJ_CONSULTA`
   diferente, o papel é relativo a ele).
7. `cpfConsulta` existe no contrato; esta parte consulta apenas por CNPJ.
8. A faixa de NSU é `int64` (inteiro, sem zeros à esquerda), diferente dos 15 dígitos do
   `NFeDistribuicaoDFe`; por isso o ADN tem **cursor próprio**.

## Como testar a chamada real (não feito aqui)

Ver o resumo da tarefa 27 no `docs/BACKLOG.md`.

## Diagnóstico (`nfe-local sincronizar-adn --diagnostico`)

Faz **uma** consulta somente leitura a `GET /DFe/0` com o certificado cadastrado. Não abre nem cria
o banco, não altera o cursor e não imprime XML, senha nem bytes do `.pfx`. Aceita `--ambiente`,
`--url-base` e `--json`. Mostra: ambiente, URL base, CNPJ de consulta mascarado (2 primeiros e 2
últimos), validade do certificado, resultado da conexão TLS, HTTP status, `StatusProcessamento`,
`TipoConsulta`, `Codigo`/`Descricao` de erros e alertas (CNPJ/CPF dentro das descrições são
mascarados) e a contagem de itens por `TipoDocumento`. Saída 0 quando a consulta funcionou
(documentos ou fila vazia) e 1 nos demais casos. Use-o para separar problema de URL, de TLS/certificado
e de CNPJ de consulta antes de sincronizar.

### Falhas de conexão no diagnóstico

Se a conexão falhar antes de haver resposta HTTP, o diagnóstico percorre a cadeia de causas da
exceção (`__cause__`, `__context__`, `.reason` e `args`) e mostra, **sem URL com parâmetros, CNPJ,
caminho do certificado, senha nem mensagens brutas**: a etapa (resolução de nome, conexão TCP,
handshake TLS ou leitura da resposta), a classe da exceção raiz e a cadeia de classes, o código do
sistema operacional (`WinError`/`errno`), o motivo TLS quando for um código curto, se o servidor
encerrou a conexão (reset, `RemoteDisconnected`), se houve tempo esgotado e uma orientação em
português. A etapa "handshake TLS (provável)" aparece quando houve reset sem resposta HTTP: a
biblioteca não distingue reset no handshake de reset logo depois; com `RemoteDisconnected` o
handshake completou (a requisição foi enviada). Quando há resposta, mostra a versão do TLS e o
cifrador negociados (melhor esforço; pode não estar disponível).

`--tentativas N` (1 a 3, padrão 1) repete só em falha de conexão, com espera de 2 s, e informa
quantas falharam; `--timeout SEGUNDOS` (padrão 30) vale para cada chamada, inclusive na
sincronização.
