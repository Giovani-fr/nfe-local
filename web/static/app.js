(() => {
  "use strict";

  const estado = {
    escopoId: "acervo",
    loteId: null,
    resumo: null,
    resultados: [],
    historicoLotes: [],
    subabaAtiva: "notas",
    ordenacaoNotas: { campo: null, direcao: "asc" },
    paginacaoNotas: { pagina: 1, porPagina: 100 },
    notasRecebidas: [],
    ordenacaoRecebidas: { campo: null, direcao: "asc" },
    paginacaoRecebidas: { pagina: 1, porPagina: 100 },
  };

  const ROTULOS_ALERTA = {
    AMBIENTE_HOMOLOGACAO: "HOMOLOGAÇÃO",
    NOTA_SEM_PROTOCOLO: "SEM PROTOCOLO",
    SOMA_ITENS_DIVERGENTE: "SOMA DIVERGENTE",
    EQUACAO_VNF_DIVERGENTE: "TOTAL vNF DIVERGENTE",
    CHAVE_DUPLICADA_LOTE: "CHAVE DUPLICADA",
    CHAVE_TAMANHO_INVALIDO: "CHAVE INVÁLIDA",
    CHAVE_DV_INVALIDO: "DV CHAVE INVÁLIDO",
    DOCUMENTO_PARTE_INVALIDO: "CNPJ/CPF INVÁLIDO",
    LAYOUT_NFSE_MUNICIPAL_DESCONTINUADO: "NFS-e LEGADA",
    LAYOUT_DESCONHECIDO: "LAYOUT DESCONHECIDO",
    XML_MALFORMADO: "XML MALFORMADO",
    ZIP_INVALIDO: "ZIP INVÁLIDO",
    MANIFESTACAO_DESTINATARIO_NECESSARIA: "EXIGE MANIFESTAÇÃO",
  };

  const fmtMoeda = (valor) => {
    const num = Number(valor || 0);
    return num.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  };

  const fmtMoedaRS = (valor) => `R$ ${fmtMoeda(valor)}`;

  const ROTULOS_ORIGEM = { DISTDFE: "SEFAZ", SEFAZ: "SEFAZ", ADN: "ADN", UPLOAD: "Upload" };
  const ROTULOS_PAPEL = {
    EMITIDA: "Emitida",
    RECEBIDA: "Recebida",
    INTERMEDIARIA: "Intermediária",
  };
  const ROTULOS_TIPO_NOTA = { NFE: "NF-e", NFCE: "NFC-e", NFSE: "NFS-e", RESUMO: "NF-e (resumo)" };
  const DICA_PAPEL =
    "Critério: o CNPJ do certificado/consulta é comparado com o emitente (prestador) = Emitida, " +
    "com o destinatário (tomador) = Recebida e com o intermediário (NFS-e) = Intermediária.";

  const rotuloOrigem = (o) => ROTULOS_ORIGEM[String(o || "UPLOAD").toUpperCase()] || String(o);
  const rotuloPapel = (p) => ROTULOS_PAPEL[p] || "—";
  const rotuloTipoNota = (t) => ROTULOS_TIPO_NOTA[t] || String(t || "-");

  const badgeOrigem = (o) => {
    const r = rotuloOrigem(o);
    const classe = r === "SEFAZ" ? "badge-type" : r === "ADN" ? "badge-gold" : "badge-ok";
    return `<span class="badge ${classe}">${escaparHtml(r)}</span>`;
  };

  const badgePapel = (p) => {
    if (!p) return '<span class="muted" title="Sem CNPJ de referência para comparar">—</span>';
    const classe = p === "EMITIDA" ? "badge-ok" : p === "RECEBIDA" ? "badge-type" : "badge-gold";
    return `<span class="badge ${classe}" title="${escaparHtml(DICA_PAPEL)}">${escaparHtml(rotuloPapel(p))}</span>`;
  };

  const fmtDoc = (doc) => {
    if (!doc) return "-";
    const limpo = String(doc).replace(/[^0-9A-Za-z]/g, "").toUpperCase();
    if (limpo.length === 14) {
      return `${limpo.slice(0, 2)}.${limpo.slice(2, 5)}.${limpo.slice(5, 8)}/${limpo.slice(8, 12)}-${limpo.slice(12)}`;
    }
    if (limpo.length === 11 && /^\d+$/.test(limpo)) {
      return `${limpo.slice(0, 3)}.${limpo.slice(3, 6)}.${limpo.slice(6, 9)}-${limpo.slice(9)}`;
    }
    return limpo;
  };

  const escaparHtml = (str) =>
    String(str ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");

  const fmtDataCurta = (iso) => {
    if (!iso) return "-";
    const dt = new Date(iso);
    if (Number.isNaN(dt.getTime())) return "-";
    return dt.toLocaleDateString("pt-BR");
  };

  const fmtDataHoraCompleta = (iso) => {
    if (!iso) return "";
    const dt = new Date(iso);
    if (Number.isNaN(dt.getTime())) return "";
    return dt.toLocaleString("pt-BR");
  };

  const timestampIso = (iso) => {
    if (!iso) return -Infinity;
    const ms = Date.parse(iso);
    return Number.isNaN(ms) ? -Infinity : ms;
  };

  const compararTexto = (a, b) =>
    String(a || "").localeCompare(String(b || ""), "pt-BR", {
      numeric: true,
      sensitivity: "base",
    });

  const extrairNumeroOrdenavel = (valor) => {
    const digitos = String(valor || "").replace(/\D/g, "");
    if (!digitos) return null;
    const n = Number(digitos);
    return Number.isFinite(n) ? n : null;
  };

  const compararNumeroInteligente = (a, b) => {
    const na = extrairNumeroOrdenavel(a);
    const nb = extrairNumeroOrdenavel(b);
    if (na !== null && nb !== null && na !== nb) return na - nb;
    return compararTexto(a, b);
  };

  const pesoStatusValidacao = (res) => {
    if (!res.valido) return 0;
    const avisos = (res.alertas || []).length;
    if (avisos > 0) return 100 - Math.min(avisos, 99);
    return 200;
  };

  const atualizarIndicadoresOrdenacao = (seletorTabela, ord) => {
    document.querySelectorAll(`${seletorTabela} thead th.sortable`).forEach((th) => {
      const ind = th.querySelector(".sort-ind");
      const ativo = th.dataset.sort === ord.campo;
      th.classList.toggle("sort-active", ativo);
      th.setAttribute(
        "aria-sort",
        ativo ? (ord.direcao === "asc" ? "ascending" : "descending") : "none"
      );
      if (ind) {
        ind.textContent = ativo ? (ord.direcao === "asc" ? "▲" : "▼") : "⇅";
      }
    });
  };

  const renderizarChaveComCopia = (chave) => {
    if (!chave) return "";
    const limpa = String(chave).trim();
    if (!limpa) return "";
    const exibicao =
      limpa.length > 20 ? `${limpa.slice(0, 8)}…${limpa.slice(-8)}` : limpa;
    return `
      <div class="chave-row">
        <code class="chave-code" title="${escaparHtml(limpa)}">${escaparHtml(exibicao)}</code>
        <button
          type="button"
          class="btn-copy-chave"
          data-action="copiar-chave"
          data-chave="${escaparHtml(limpa)}"
          title="Copiar chave completa (${limpa.length} caracteres)"
          aria-label="Copiar chave de acesso"
        >📋</button>
      </div>
    `;
  };

  const copiarChaveParaClipboard = async (chave, btnEl) => {
    if (!chave) return;
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(chave);
      } else {
        const tmp = document.createElement("textarea");
        tmp.value = chave;
        tmp.style.position = "fixed";
        tmp.style.opacity = "0";
        document.body.appendChild(tmp);
        tmp.select();
        document.execCommand("copy");
        document.body.removeChild(tmp);
      }
      if (btnEl) {
        const orig = btnEl.textContent;
        btnEl.textContent = "✅";
        setTimeout(() => {
          btnEl.textContent = orig;
        }, 1400);
      }
    } catch (_) {
      // Silencioso caso o navegador bloqueie clipboard em contexto restrito
    }
  };

  const definirStatusUpload = (msg, erro = false) => {
    const el = document.getElementById("upload-status");
    if (!el) return;
    el.textContent = msg;
    el.style.color = erro ? "var(--status-err)" : "var(--accent-gold)";
  };

  const alternarSubaba = (nomeSubaba) => {
    estado.subabaAtiva = nomeSubaba;
    const mapa = {
      notas: ["btn-subtab-notas", "subview-notas"],
      resumo: ["btn-subtab-resumo", "subview-resumo"],
      ia: ["btn-subtab-ia", "subview-ia"],
    };
    Object.entries(mapa).forEach(([chave, [btnId, viewId]]) => {
      const btn = document.getElementById(btnId);
      const view = document.getElementById(viewId);
      if (!btn || !view) return;
      if (chave === nomeSubaba) {
        btn.classList.add("active");
        view.classList.remove("hidden");
      } else {
        btn.classList.remove("active");
        view.classList.add("hidden");
      }
    });
  };

  const popularSeletorEmitentes = (resultados) => {
    const select = document.getElementById("filtro-emitente");
    if (!select) return;
    const valorAtual = select.value;
    const mapaEmitentes = new Map();

    (resultados || []).forEach((res) => {
      const em = res.documento?.emitente;
      if (!em) return;
      const chave = (em.documento || em.razao_social || "").trim();
      if (!chave) return;
      if (!mapaEmitentes.has(chave)) {
        const docFmt = em.documento ? fmtDoc(em.documento) : "";
        const nome = em.razao_social || docFmt;
        const rotulo = docFmt && em.razao_social ? `${em.razao_social} (${docFmt})` : nome;
        mapaEmitentes.set(chave, rotulo);
      }
    });

    const opcoes = ['<option value="">Todos os Emitentes</option>'];
    Array.from(mapaEmitentes.entries())
      .sort((a, b) => a[1].localeCompare(b[1], "pt-BR"))
      .forEach(([chave, rotulo]) => {
        opcoes.push(`<option value="${escaparHtml(chave)}">${escaparHtml(rotulo)}</option>`);
      });

    select.innerHTML = opcoes.join("");
    if (valorAtual && mapaEmitentes.has(valorAtual)) {
      select.value = valorAtual;
    }
  };

  const renderizarSidebarHistorico = () => {
    const listaEl = document.getElementById("lista-historico-lotes");
    const badgeTotal = document.getElementById("badge-total-lotes");
    const btnAcervo = document.getElementById("btn-escopo-acervo");
    const subAcervo = document.getElementById("lbl-acervo-sub");

    if (badgeTotal) {
      const qtd = estado.historicoLotes.length;
      badgeTotal.textContent = `${qtd} ${qtd === 1 ? "lote" : "lotes"}`;
    }

    if (btnAcervo) {
      if (estado.escopoId === "acervo") {
        btnAcervo.classList.add("active");
      } else {
        btnAcervo.classList.remove("active");
      }
    }

    const btnTudo = document.getElementById("btn-escopo-tudo");
    if (btnTudo) btnTudo.classList.toggle("active", estado.escopoId === "tudo");
    const subTudo = document.getElementById("lbl-tudo-sub");
    if (subTudo) {
      if (estado.escopoId === "tudo" && estado.resumo) {
        const ct = estado.resumo.contagem;
        subTudo.textContent = `${ct.total_arquivos} doc(s) únicos • ${fmtMoedaRS(estado.resumo.valor_total_notas)}`;
      } else {
        subTudo.textContent = "Uploads, NF-e da SEFAZ e NFS-e do ADN, sem duplicar chaves";
      }
    }

    if (subAcervo) {
      if (estado.escopoId === "acervo" && estado.resumo) {
        const c = estado.resumo.contagem;
        subAcervo.textContent = `${c.total_arquivos} doc(s) únicos • ${fmtMoedaRS(estado.resumo.valor_total_notas)}`;
      } else {
        const somaArqs = estado.historicoLotes.reduce((acc, l) => acc + (l.total_arquivos || 0), 0);
        subAcervo.textContent =
          somaArqs > 0
            ? `${estado.historicoLotes.length} lote(s) salvos no SQLite`
            : "Todas as notas consolidadas sem duplicar chaves";
      }
    }

    if (!listaEl) return;
    if (estado.historicoLotes.length === 0) {
      listaEl.innerHTML = '<div class="history-empty muted">Nenhum lote importado ainda.</div>';
      return;
    }

    listaEl.innerHTML = estado.historicoLotes
      .map((item) => {
        const ativo = estado.escopoId === item.lote_id ? "active" : "";
        const badgeOrigem =
          item.origem === "demo"
            ? '<span class="badge badge-warn">DEMO</span>'
            : '<span class="badge badge-ok">UPLOAD</span>';
        const dt = item.criado_em ? new Date(item.criado_em).toLocaleString("pt-BR") : "-";
        return `
          <div class="history-item ${ativo}" role="listitem">
            <button type="button" class="history-item-main" data-action="selecionar-lote" data-lote-id="${escaparHtml(item.lote_id)}">
              <div class="history-item-title" title="${escaparHtml(item.nome_rotulo)}">
                ${escaparHtml(item.nome_rotulo)}
              </div>
              <div class="history-item-meta">
                ${badgeOrigem}
                <span>${item.total_arquivos} doc(s)</span>
                <span>•</span>
                <strong>${fmtMoedaRS(item.valor_total_notas)}</strong>
              </div>
              <div class="history-item-meta">
                <span>#${escaparHtml(item.lote_id)} • ${escaparHtml(dt)}</span>
              </div>
            </button>
            <button
              type="button"
              class="history-item-del"
              data-action="excluir-lote"
              data-lote-id="${escaparHtml(item.lote_id)}"
              title="Excluir este lote do SQLite"
            >
              🗑️
            </button>
          </div>
        `;
      })
      .join("");
  };

  const renderizarLote = (dados, rotuloPersonalizado = "") => {
    estado.loteId = dados.lote_id;
    estado.escopoId = dados.lote_id;
    estado.resumo = dados.resumo;
    estado.resultados = dados.resultados || [];

    const painelLote = document.getElementById("painel-lote");
    const estadoVazio = document.getElementById("estado-vazio-lotes");
    const temDados =
      (dados.resultados && dados.resultados.length > 0) || estado.historicoLotes.length > 0;

    if (temDados) {
      painelLote.classList.remove("hidden");
      if (estadoVazio) estadoVazio.classList.add("hidden");
    } else {
      painelLote.classList.add("hidden");
      if (estadoVazio) estadoVazio.classList.remove("hidden");
    }

    const ehAcervo = dados.lote_id === "acervo";
    const ehTudo = dados.lote_id === "tudo";
    const badgeEscopo = document.getElementById("badge-escopo-ativo");
    const tituloEscopo = document.getElementById("lbl-escopo-titulo");
    const lblLoteId = document.getElementById("lbl-lote-id");

    if (ehTudo) {
      if (badgeEscopo) {
        badgeEscopo.className = "badge badge-gold";
        badgeEscopo.textContent = "VISÃO TOTAL";
      }
      if (tituloEscopo) tituloEscopo.textContent = "🧩 Tudo (acervo + SEFAZ + ADN)";
      if (lblLoteId) lblLoteId.textContent = "";
    } else if (ehAcervo) {
      if (badgeEscopo) {
        badgeEscopo.className = "badge badge-gold";
        badgeEscopo.textContent = "ACERVO UNIFICADO";
      }
      if (tituloEscopo) tituloEscopo.textContent = "📚 Todo o Acervo Unificado";
      if (lblLoteId) lblLoteId.textContent = `(${estado.historicoLotes.length} lotes no SQLite)`;
    } else {
      const infoHist = estado.historicoLotes.find((l) => l.lote_id === dados.lote_id);
      const nomeLote = rotuloPersonalizado || infoHist?.nome_rotulo || "Lote Individual";
      if (badgeEscopo) {
        badgeEscopo.className = infoHist?.origem === "demo" ? "badge badge-warn" : "badge badge-ok";
        badgeEscopo.textContent = infoHist?.origem === "demo" ? "LOTE DEMO" : "LOTE INDIVIDUAL";
      }
      if (tituloEscopo) tituloEscopo.textContent = `📦 ${nomeLote}`;
      if (lblLoteId) lblLoteId.textContent = `#${dados.lote_id}`;
    }

    const dt = new Date(dados.criado_em);
    document.getElementById("aviso-escopo-tudo")?.classList.toggle("hidden", !ehTudo);
    document.getElementById("lbl-criado-em").textContent = ehAcervo || ehTudo
      ? `Consolidado do banco SQLite local (última atualização em ${dt.toLocaleString("pt-BR")})`
      : `Processado em ${dt.toLocaleString("pt-BR")}`;

    document.getElementById("link-export-xlsx").href = `/api/lote/${dados.lote_id}/exportar?formato=xlsx`;
    document.getElementById("link-export-csv").href = `/api/lote/${dados.lote_id}/exportar?formato=csv`;
    document.getElementById("link-export-itens-csv").href = `/api/lote/${dados.lote_id}/exportar?formato=itens_csv`;

    const c = dados.resumo.contagem;
    const imp = dados.resumo.impostos_totais;

    document.getElementById("kpi-total-arquivos").textContent = c.total_arquivos;
    const tipos = Object.entries(c.por_tipo || {})
      .map(([k, v]) => `${k}: ${v}`)
      .join(" | ");
    document.getElementById("kpi-por-tipo").textContent = tipos || "Nenhum documento";

    document.getElementById("kpi-validos").textContent = c.validos;
    document.getElementById("kpi-com-avisos").textContent = `${c.com_avisos} com avisos`;
    document.getElementById("kpi-com-erros").textContent = c.com_erros;
    document.getElementById("kpi-erros-fatais").textContent = `${c.erros_fatais_xml} ilegíveis/rejeitados`;

    document.getElementById("kpi-valor-total").textContent = fmtMoedaRS(dados.resumo.valor_total_notas);
    document.getElementById("kpi-valor-liquido").textContent = `Líquido: ${fmtMoedaRS(dados.resumo.valor_liquido_total)}`;

    document.getElementById("tax-icms").textContent = fmtMoedaRS(imp.icms);
    document.getElementById("tax-icms-st").textContent = fmtMoedaRS(imp.icms_st);
    document.getElementById("tax-ipi").textContent = fmtMoedaRS(imp.ipi);
    document.getElementById("tax-pis").textContent = fmtMoedaRS(imp.pis);
    document.getElementById("tax-cofins").textContent = fmtMoedaRS(imp.cofins);
    document.getElementById("tax-iss").textContent = fmtMoedaRS(imp.iss);
    document.getElementById("tax-retencoes").textContent = fmtMoedaRS(imp.retencoes_federais);
    document.getElementById("tax-ibs-cbs").textContent = fmtMoedaRS(imp.ibs_cbs);

    // Emitentes (clicáveis para filtrar a aba Notas Fiscais!)
    const tbodyEmit = document.getElementById("tbody-emitentes");
    tbodyEmit.innerHTML =
      (dados.resumo.por_emitente || [])
        .map((em) => {
          const somaImp =
            Number(em.impostos.icms || 0) +
            Number(em.impostos.ipi || 0) +
            Number(em.impostos.iss || 0);
          const chaveEmit = em.documento || em.razao_social || "";
          return `
            <tr class="clickable-row" data-action="filtrar-emitente" data-emitente="${escaparHtml(chaveEmit)}" title="Clique para ver apenas as notas deste emitente">
              <td><code>${escaparHtml(fmtDoc(em.documento))}</code></td>
              <td><strong>${escaparHtml(em.razao_social || "-")}</strong></td>
              <td>${escaparHtml(em.uf || "-")}</td>
              <td class="num">${em.quantidade_notas}</td>
              <td class="num"><strong>${fmtMoeda(em.valor_total_notas)}</strong></td>
              <td class="num">${fmtMoeda(somaImp)}</td>
            </tr>
          `;
        })
        .join("") ||
      '<tr><td colspan="6" style="text-align:center;" class="muted">Nenhum emitente consolidado.</td></tr>';

    // Períodos
    const tbodyPer = document.getElementById("tbody-periodos");
    tbodyPer.innerHTML =
      (dados.resumo.por_mes || [])
        .map(
          (p) => `
            <tr>
              <td><code>${escaparHtml(p.periodo)}</code></td>
              <td class="num">${p.quantidade_notas}</td>
              <td class="num">${fmtMoeda(p.valor_produtos_servicos)}</td>
              <td class="num"><strong>${fmtMoeda(p.valor_total_notas)}</strong></td>
              <td class="num">${fmtMoeda(p.impostos.ibs_cbs)}</td>
            </tr>
          `
        )
        .join("") ||
      '<tr><td colspan="5" style="text-align:center;" class="muted">Nenhuma competência consolidada.</td></tr>';

    const boxRespLlm = document.getElementById("llm-resposta-box");
    if (boxRespLlm) boxRespLlm.classList.add("hidden");
    atualizarStatusLLM();

    popularSeletorEmitentes(estado.resultados);
    estado.paginacaoNotas.pagina = 1;
    renderizarSidebarHistorico();
    renderizarTabelaNotas();
  };

  const carregarHistoricoLotes = async () => {
    try {
      const resp = await fetch("/api/lotes");
      if (!resp.ok) return [];
      const dados = await resp.json();
      estado.historicoLotes = dados.lotes || [];
      renderizarSidebarHistorico();
      return estado.historicoLotes;
    } catch (_) {
      return [];
    }
  };

  const carregarEscopo = async (escopoId = "acervo") => {
    try {
      const url = escopoId === "acervo" ? "/api/acervo" : `/api/lote/${encodeURIComponent(escopoId)}`; // "tudo" usa /api/lote/tudo
      const resp = await fetch(url);
      if (!resp.ok) return;
      const dados = await resp.json();
      renderizarLote(dados);
    } catch (err) {
      definirStatusUpload(`Erro ao carregar escopo: ${err.message}`, true);
    }
  };

  const excluirLoteDoHistorico = async (loteId) => {
    try {
      const resp = await fetch(`/api/lote/${encodeURIComponent(loteId)}`, { method: "DELETE" });
      if (!resp.ok) return;
      await carregarHistoricoLotes();
      if (estado.historicoLotes.length === 0) {
        estado.loteId = null;
        estado.resultados = [];
        document.getElementById("painel-lote").classList.add("hidden");
        document.getElementById("estado-vazio-lotes")?.classList.remove("hidden");
        definirStatusUpload("Histórico vazio.");
      } else {
        await carregarEscopo("acervo");
        definirStatusUpload(`Lote #${loteId} removido do histórico.`);
      }
    } catch (err) {
      definirStatusUpload(`Erro ao excluir lote: ${err.message}`, true);
    }
  };

  const limparHistorico = async (apenasDemo = false) => {
    try {
      const qs = apenasDemo ? "?apenas_demo=true" : "";
      const resp = await fetch(`/api/lotes${qs}`, { method: "DELETE" });
      if (!resp.ok) return;
      const dados = await resp.json();
      await carregarHistoricoLotes();
      if (estado.historicoLotes.length === 0) {
        estado.loteId = null;
        estado.resultados = [];
        document.getElementById("painel-lote").classList.add("hidden");
        document.getElementById("estado-vazio-lotes")?.classList.remove("hidden");
      } else {
        await carregarEscopo("acervo");
      }
      definirStatusUpload(
        apenasDemo
          ? `${dados.removidos} lote(s) de demonstração removido(s).`
          : `Histórico zerado (${dados.removidos} lote(s) removido(s)).`
      );
    } catch (err) {
      definirStatusUpload(`Erro ao limpar histórico: ${err.message}`, true);
    }
  };

  const atualizarStatusLLM = async () => {
    const badge = document.getElementById("llm-status-badge");
    const quota = document.getElementById("llm-quota-counter");
    if (!badge || !quota) return;

    try {
      const resp = await fetch("/api/llm/status");
      if (!resp.ok) return;
      const st = await resp.json();
      if (st.configurado) {
        badge.className = "badge badge-ok";
        const modo = st.enviar_identificadores
          ? "envia nomes/CNPJ reais"
          : "agregados anonimizados";
        const amostra = st.enviar_amostra ? " + amostra de notas" : "";
        badge.textContent = `Ativo (${st.modelo}) · ${modo}${amostra}`;
      } else {
        badge.className = "badge badge-warn";
        badge.textContent = "Desativado (defina a variável de ambiente LLM_API_KEY)";
      }
      quota.textContent = `Chamadas restantes: ${st.chamadas_restantes}/${st.max_chamadas_hora} por hora`;
      // Sem a amostra de notas o modelo não sabe dizer quais notas têm alertas: esconde o chip.
      document.querySelectorAll("[data-requer-amostra]").forEach((chip) => {
        chip.classList.toggle("hidden", !st.enviar_amostra);
      });
    } catch (_) {
      badge.className = "badge badge-type";
      badge.textContent = "Status indisponível";
    }
  };

  const enviarPerguntaLLM = async (perguntaTexto) => {
    if (!estado.loteId) return;
    const pergunta = (perguntaTexto || "").trim();
    if (pergunta.length < 3) return;

    const btn = document.getElementById("btn-enviar-pergunta-llm");
    const box = document.getElementById("llm-resposta-box");
    const modeloEl = document.getElementById("llm-resposta-modelo");
    const cacheBadge = document.getElementById("llm-badge-cache");
    const textoEl = document.getElementById("llm-resposta-texto");
    const legendaEl = document.getElementById("llm-legenda-codigos");
    const quotaEl = document.getElementById("llm-quota-counter");

    btn.disabled = true;
    box.classList.remove("hidden");
    cacheBadge.classList.add("hidden");
    modeloEl.textContent = "Consultando assistente...";
    textoEl.textContent = "Aguarde...";
    if (legendaEl) {
      legendaEl.classList.add("hidden");
      legendaEl.textContent = "";
    }

    try {
      const resp = await fetch(`/api/lote/${estado.loteId}/perguntar`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pergunta }),
      });
      const dados = await resp.json();
      if (!resp.ok) {
        modeloEl.textContent = `Aviso (${resp.status})`;
        textoEl.textContent = dados.detail || "Não foi possível consultar o assistente LLM.";
        return;
      }

      modeloEl.textContent = `Modelo: ${dados.modelo}`;
      if (dados.em_cache) {
        cacheBadge.classList.remove("hidden");
      } else {
        cacheBadge.classList.add("hidden");
      }
      textoEl.textContent = dados.resposta;
      if (legendaEl && Array.isArray(dados.legenda_codigos) && dados.legenda_codigos.length) {
        const linhas = dados.legenda_codigos
          .map(
            (c) =>
              `<tr><td><strong>${escaparHtml(c.codigo)}</strong></td><td>${escaparHtml(c.razao_social)}</td><td>${escaparHtml(c.documento)}</td></tr>`
          )
          .join("");
        legendaEl.innerHTML =
          '<div class="muted">Tradução local dos códigos (esta tabela nunca foi enviada ao provedor):</div>' +
          `<table><thead><tr><th>Código</th><th>Razão social</th><th>CNPJ/CPF</th></tr></thead><tbody>${linhas}</tbody></table>`;
        legendaEl.classList.remove("hidden");
      }
      if (quotaEl && typeof dados.chamadas_restantes === "number") {
        quotaEl.textContent = `Chamadas restantes: ${dados.chamadas_restantes} na hora atual`;
      }
    } catch (err) {
      modeloEl.textContent = "Erro de comunicação";
      textoEl.textContent = err.message;
    } finally {
      btn.disabled = false;
    }
  };

  const renderizarBadgesNota = (res) => {
    const badges = [];
    if (res.valido && (!res.alertas || res.alertas.length === 0)) {
      badges.push('<span class="badge badge-ok">VÁLIDA</span>');
    } else if (res.valido) {
      badges.push('<span class="badge badge-ok">VÁLIDA COM AVISO</span>');
    } else {
      badges.push('<span class="badge badge-err">INVÁLIDA</span>');
    }

    for (const alerta of res.alertas || []) {
      const classe = alerta.severidade === "ERRO" ? "badge-err" : "badge-warn";
      const rotulo = ROTULOS_ALERTA[alerta.codigo] || alerta.codigo;
      badges.push(
        `<span class="badge ${classe}" title="${escaparHtml(alerta.mensagem)}">${escaparHtml(rotulo)}</span>`
      );
    }
    return badges.join(" ");
  };

  const ordenarItensNotas = (itens) => {
    const { campo, direcao } = estado.ordenacaoNotas;
    if (!campo) return itens;
    const fator = direcao === "desc" ? -1 : 1;

    return [...itens].sort((a, b) => {
      const docA = a.res.documento;
      const docB = b.res.documento;
      let cmp = 0;

      switch (campo) {
        case "arquivo":
          cmp = compararTexto(a.res.arquivo_origem, b.res.arquivo_origem);
          break;
        case "tipo":
          cmp = compararTexto(docA ? docA.tipo : "ERRO_XML", docB ? docB.tipo : "ERRO_XML");
          break;
        case "origem":
          cmp = compararTexto(rotuloOrigem(a.res.origem), rotuloOrigem(b.res.origem));
          break;
        case "numero":
          cmp = compararNumeroInteligente(docA?.numero, docB?.numero);
          break;
        case "data_emissao":
          cmp = timestampIso(docA?.data_emissao) - timestampIso(docB?.data_emissao);
          break;
        case "emitente":
          cmp = compararTexto(docA?.emitente?.razao_social, docB?.emitente?.razao_social);
          break;
        case "destinatario":
          cmp = compararTexto(docA?.destinatario?.razao_social, docB?.destinatario?.razao_social);
          break;
        case "valor_total":
          cmp =
            Number(docA?.totais?.valor_total_nota || 0) -
            Number(docB?.totais?.valor_total_nota || 0);
          break;
        case "status":
          cmp = pesoStatusValidacao(a.res) - pesoStatusValidacao(b.res);
          break;
        default:
          cmp = 0;
      }

      if (cmp !== 0) return cmp * fator;
      return a.idx - b.idx;
    });
  };

  const renderizarTabelaNotas = () => {
    const termo = (document.getElementById("filtro-busca")?.value || "").trim().toLowerCase();
    const filtroTipo = document.getElementById("filtro-tipo")?.value || "";
    const filtroStatus = document.getElementById("filtro-status")?.value || "";
    const filtroEmitente = document.getElementById("filtro-emitente")?.value || "";

    const tbody = document.getElementById("tbody-notas");
    const contadorEl = document.getElementById("lbl-contador-notas");
    if (!tbody) return;

    const totalBruto = estado.resultados.length;
    const itensFiltrados = [];

    estado.resultados.forEach((res, idx) => {
      const doc = res.documento;
      const tipo = doc ? doc.tipo : "ERRO_XML";

      if (filtroTipo && tipo !== filtroTipo) return;
      if (filtroStatus === "validos" && !res.valido) return;
      if (filtroStatus === "erros" && res.valido) return;
      if (
        filtroStatus === "avisos" &&
        !(res.alertas || []).some((a) => a.severidade === "AVISO")
      ) {
        return;
      }

      if (filtroEmitente) {
        const chaveEmit = (doc?.emitente?.documento || doc?.emitente?.razao_social || "").trim();
        if (chaveEmit !== filtroEmitente) return;
      }

      if (termo) {
        const alvo = [
          res.arquivo_origem,
          doc?.chave_acesso,
          doc?.numero,
          doc?.emitente?.documento,
          doc?.emitente?.razao_social,
          doc?.destinatario?.documento,
          doc?.destinatario?.razao_social,
        ]
          .filter(Boolean)
          .join(" ")
          .toLowerCase();
        if (!alvo.includes(termo)) return;
      }

      itensFiltrados.push({ res, idx });
    });

    const itensOrdenados = ordenarItensNotas(itensFiltrados);
    atualizarIndicadoresOrdenacao(".data-table-notas", estado.ordenacaoNotas);

    const totalFiltrado = itensOrdenados.length;
    const porPagina = Number(estado.paginacaoNotas.porPagina || 0);
    const totalPaginas =
      porPagina > 0 ? Math.max(1, Math.ceil(totalFiltrado / porPagina)) : 1;
    if (estado.paginacaoNotas.pagina > totalPaginas) {
      estado.paginacaoNotas.pagina = totalPaginas;
    }
    if (estado.paginacaoNotas.pagina < 1) {
      estado.paginacaoNotas.pagina = 1;
    }

    const inicio = porPagina > 0 ? (estado.paginacaoNotas.pagina - 1) * porPagina : 0;
    const fim = porPagina > 0 ? Math.min(inicio + porPagina, totalFiltrado) : totalFiltrado;
    const itensPagina = itensOrdenados.slice(inicio, fim);

    const linhas = itensPagina.map(({ res, idx }) => {
      const doc = res.documento;
      const tipo = doc ? doc.tipo : "ERRO_XML";
      const numSerie = doc ? `${doc.numero || "-"} / ${doc.serie || "-"}` : "-";
      const dataEmiCurta = fmtDataCurta(doc?.data_emissao);
      const dataEmiCompleta = fmtDataHoraCompleta(doc?.data_emissao);
      const emitenteStr = doc
        ? `<strong class="parte-nome">${escaparHtml(doc.emitente.razao_social || "-")}</strong><code class="doc-code">${escaparHtml(fmtDoc(doc.emitente.documento))}</code>`
        : '<span class="muted">-</span>';
      const destStr =
        doc && doc.destinatario
          ? `<span class="parte-nome">${escaparHtml(doc.destinatario.razao_social || "-")}</span><code class="doc-code">${escaparHtml(fmtDoc(doc.destinatario.documento))}</code>`
          : '<span class="muted">Não identificado</span>';
      const valorTotal = doc ? fmtMoeda(doc.totais.valor_total_nota) : "0,00";

      const chaveValida = (doc?.chave_acesso || "").trim();
      const identUrl =
        chaveValida.length >= 44 ? encodeURIComponent(chaveValida) : String(idx);
      const rotuloEspelho = tipo === "NFSE" ? "📄 DANFSe" : "📄 DANFE";
      const btnEspelho = doc
        ? `<a class="btn btn-sm btn-primary" href="/lote/${estado.loteId}/nota/${identUrl}/espelho" target="_blank" rel="noopener">${rotuloEspelho}</a>`
        : "";

      return `
        <tr>
          <td class="col-arquivo">
            <strong class="arquivo-nome" title="${escaparHtml(res.arquivo_origem)}">${escaparHtml(res.arquivo_origem)}</strong>
            ${doc?.chave_acesso ? renderizarChaveComCopia(doc.chave_acesso) : ""}
          </td>
          <td class="col-tipo"><span class="badge badge-type">${escaparHtml(tipo)}</span></td>
          <td class="col-origem">${badgeOrigem(res.origem)}${res.papel ? ` ${badgePapel(res.papel)}` : ""}</td>
          <td class="col-numero">${escaparHtml(numSerie)}</td>
          <td class="col-emissao-nota" title="${escaparHtml(dataEmiCompleta)}">${escaparHtml(dataEmiCurta)}</td>
          <td class="col-parte">${emitenteStr}</td>
          <td class="col-parte">${destStr}</td>
          <td class="num col-valor"><strong>${valorTotal}</strong></td>
          <td class="col-status"><div class="badges-wrap">${renderizarBadgesNota(res)}</div></td>
          <td class="col-actions">
            <div class="row-actions">
              ${btnEspelho}
              <button type="button" class="btn btn-sm btn-secondary" data-action="detalhes" data-idx="${idx}" title="Ver detalhes, itens e alertas">
                🔍 Detalhes
              </button>
              <a class="btn btn-sm btn-ghost" href="/lote/${estado.loteId}/nota/${identUrl}/xml" download>
                ⬇ XML
              </a>
            </div>
          </td>
        </tr>
      `;
    });

    if (contadorEl) {
      contadorEl.textContent = `Exibindo ${totalFiltrado} de ${totalBruto} documento(s)`;
    }

    const lblPag = document.getElementById("lbl-paginacao-notas");
    const btnAnt = document.getElementById("btn-pag-notas-ant");
    const btnProx = document.getElementById("btn-pag-notas-prox");
    if (lblPag) {
      const a = totalFiltrado === 0 ? 0 : inicio + 1;
      const b = fim;
      lblPag.textContent = `Página ${estado.paginacaoNotas.pagina} de ${totalPaginas} (exibindo ${a}–${b} de ${totalFiltrado} documentos)`;
    }
    if (btnAnt) btnAnt.disabled = estado.paginacaoNotas.pagina <= 1;
    if (btnProx) btnProx.disabled = estado.paginacaoNotas.pagina >= totalPaginas;

    tbody.innerHTML =
      linhas.join("") ||
      '<tr><td colspan="10" style="text-align:center;" class="muted">Nenhum documento corresponde aos filtros aplicados.</td></tr>';
  };

  const abrirModalDetalhes = (idx) => {
    const res = estado.resultados[idx];
    if (!res) return;

    const modal = document.getElementById("modal-detalhes");
    const corpo = document.getElementById("modal-conteudo");
    document.getElementById("modal-titulo").textContent = `Diagnóstico Fiscal & Itens — ${res.arquivo_origem}`;

    const doc = res.documento;
    const cabecalhoHtml = doc
      ? `
        <div class="modal-detail-grid">
          <div class="modal-detail-box">
            <strong>Documento:</strong> ${escaparHtml(doc.tipo)} (Mod. ${escaparHtml(doc.modelo)})<br/>
            <strong>Número / Série:</strong> ${escaparHtml(doc.numero || "-")} / ${escaparHtml(doc.serie || "-")}<br/>
            <strong>Situação:</strong> ${escaparHtml(doc.situacao)}
          </div>
          <div class="modal-detail-box">
            <strong>Emitente:</strong> ${escaparHtml(doc.emitente.razao_social || "-")}<br/>
            <strong>CNPJ/CPF:</strong> <code>${escaparHtml(fmtDoc(doc.emitente.documento))}</code> (${escaparHtml(doc.emitente.uf || "-")})
          </div>
          <div class="modal-detail-box">
            <strong>Valor Produtos/Serviços:</strong> ${fmtMoedaRS(doc.totais.valor_produtos_servicos)}<br/>
            <strong>Valor Total da Nota:</strong> <strong>${fmtMoedaRS(doc.totais.valor_total_nota)}</strong>
          </div>
        </div>
      `
      : "";

    const itens = doc?.itens || [];
    const tabelaItensHtml =
      itens.length > 0
        ? `
          <div style="margin-bottom: 1rem;">
            <h4 style="margin-bottom: 0.45rem;">Itens do Documento (${itens.length})</h4>
            <div class="table-responsive">
              <table class="data-table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Código</th>
                    <th>Descrição</th>
                    <th>NCM / Serv.</th>
                    <th>CFOP</th>
                    <th class="num">Qtd</th>
                    <th class="num">V. Unit</th>
                    <th class="num">V. Total</th>
                  </tr>
                </thead>
                <tbody>
                  ${itens
                    .map(
                      (it) => `
                        <tr>
                          <td>${it.numero_item}</td>
                          <td><code>${escaparHtml(it.codigo || "-")}</code></td>
                          <td>${escaparHtml(it.descricao)}</td>
                          <td><code>${escaparHtml(it.ncm || it.codigo_tributacao_nacional || "-")}</code></td>
                          <td><code>${escaparHtml(it.cfop || "-")}</code></td>
                          <td class="num">${Number(it.quantidade || 0)} ${escaparHtml(it.unidade || "")}</td>
                          <td class="num">${fmtMoeda(it.valor_unitario)}</td>
                          <td class="num"><strong>${fmtMoeda(it.valor_total)}</strong></td>
                        </tr>
                      `
                    )
                    .join("")}
                </tbody>
              </table>
            </div>
          </div>
        `
        : "";

    const alertasHtml =
      (res.alertas || []).length > 0
        ? `<ul style="padding-left: 1.2rem;">${res.alertas
            .map(
              (a) =>
                `<li><strong>[${escaparHtml(a.severidade)}] ${escaparHtml(a.codigo)}:</strong> ${escaparHtml(a.mensagem)}</li>`
            )
            .join("")}</ul>`
        : '<p class="muted">Nenhum erro ou aviso apontado pelos validadores.</p>';

    corpo.innerHTML = `
      ${cabecalhoHtml}
      <div style="margin-bottom: 1rem;">
        <h4 style="margin-bottom: 0.35rem;">Alertas de Validação</h4>
        ${alertasHtml}
      </div>
      ${tabelaItensHtml}
      <details>
        <summary style="cursor:pointer; font-weight:600; margin-bottom:0.45rem;">Ver Estrutura Normalizada Completa (JSON)</summary>
        <pre>${escaparHtml(JSON.stringify(res, null, 2))}</pre>
      </details>
    `;
    modal.classList.remove("hidden");
  };

  const enviarArquivos = async (fileList) => {
    if (!fileList || fileList.length === 0) return;
    definirStatusUpload(`Processando ${fileList.length} arquivo(s)...`);

    const formData = new FormData();
    Array.from(fileList).forEach((f) => formData.append("files", f));

    try {
      const resp = await fetch("/api/lote", { method: "POST", body: formData });
      const dados = await resp.json();
      if (!resp.ok) {
        definirStatusUpload(dados.detail || "Falha ao processar lote.", true);
        return;
      }
      await carregarHistoricoLotes();
      definirStatusUpload(`Lote #${dados.lote_id} salvo no Acervo!`);
      await carregarEscopo("acervo");
    } catch (err) {
      definirStatusUpload(`Erro de comunicação: ${err.message}`, true);
    }
  };

  const carregarLoteDemo = async () => {
    definirStatusUpload("Carregando lote de demonstração sintético...");
    try {
      const resp = await fetch("/api/lote/demo", { method: "POST" });
      const dados = await resp.json();
      if (!resp.ok) {
        definirStatusUpload(dados.detail || "Falha ao carregar demonstração.", true);
        return;
      }
      await carregarHistoricoLotes();
      definirStatusUpload(`Lote Demo #${dados.lote_id} adicionado ao Acervo!`);
      await carregarEscopo("acervo");
    } catch (err) {
      definirStatusUpload(`Erro ao carregar demonstração: ${err.message}`, true);
    }
  };

  // --- Fase 2 & 3: Recebidas da SEFAZ + Cadastro Visual do Certificado A1 ---
  const definirStatusRecebidas = (msg, erro = false) => {
    const el = document.getElementById("recebidas-sync-status");
    if (!el) return;
    el.textContent = msg;
    el.style.color = erro ? "var(--status-err)" : "var(--accent-gold)";
  };

  const alternarAba = (nomeAba) => {
    const abaLotes = document.getElementById("aba-lotes");
    const abaRecebidas = document.getElementById("aba-recebidas");
    const btnLotes = document.getElementById("btn-tab-lotes");
    const btnRecebidas = document.getElementById("btn-tab-recebidas");
    if (!abaLotes || !abaRecebidas || !btnLotes || !btnRecebidas) return;

    if (nomeAba === "recebidas") {
      abaLotes.classList.add("hidden");
      abaRecebidas.classList.remove("hidden");
      btnLotes.classList.remove("active");
      btnRecebidas.classList.add("active");
      carregarStatusRecebidas();
      carregarListaRecebidas();
    } else {
      abaRecebidas.classList.add("hidden");
      abaLotes.classList.remove("hidden");
      btnRecebidas.classList.remove("active");
      btnLotes.classList.add("active");
    }
  };

  const mensagemUltimaSync = (log, origem) => {
    if (!log) return "Nenhuma sincronização registrada.";
    const quando = log.ts ? new Date(log.ts).toLocaleString("pt-BR") : "-";
    let txt = `Última execução (${quando}): `;
    const gravados = `${log.novos_docs} documento(s) gravado(s) em ${log.paginas} página(s)`;
    if (origem === "NFE") {
      if (log.cstat === "138") txt += `${gravados}.`;
      else if (log.cstat === "137") txt += "sem notas novas.";
      else if (log.cstat === "656") txt += "consumo indevido (656): aguarde 1 hora.";
      else txt += `${log.motivo || log.cstat || "sem detalhes"}.`;
    } else if (log.cstat === "DOCUMENTOS_LOCALIZADOS") {
      txt += `${gravados}; ainda pode haver mais notas.`;
    } else if (log.cstat === "NENHUM_DOCUMENTO_LOCALIZADO") {
      txt += log.novos_docs > 0 ? `${gravados}; fila zerada.` : "sem notas novas.";
    } else {
      txt += `${log.motivo || log.cstat || "sem detalhes"}`;
    }
    if (Number(log.tentativas) > 1) txt += ` ${log.tentativas} tentativa(s) de conexão.`;
    if (log.falhas_conexao) txt += ` Falhas de conexão: ${log.falhas_conexao}.`;
    return txt;
  };

  const preencherPainelUnificado = (st) => {
    const passo = document.getElementById("passo-a-passo-cert");
    if (passo) passo.classList.toggle("hidden", !!st.certificado_configurado);

    const lblNfe = document.getElementById("lbl-nfe-ultima-execucao");
    if (lblNfe) lblNfe.textContent = mensagemUltimaSync(st.ultimo_sync_nfe, "NFE");

    const badgeAdn = document.getElementById("adn-badge-ambiente");
    if (badgeAdn) {
      badgeAdn.className = st.ambiente === 1 ? "badge badge-ok" : "badge badge-warn";
      badgeAdn.textContent = st.ambiente === 1 ? "Produção (1)" : "Produção restrita (2)";
    }
    const lblNsu = document.getElementById("lbl-adn-nsu");
    if (lblNsu && st.cursor_adn) lblNsu.textContent = String(st.cursor_adn.ultimo_nsu);
    const lblAdn = document.getElementById("lbl-adn-ultima-execucao");
    if (lblAdn) lblAdn.textContent = mensagemUltimaSync(st.ultimo_sync_nfse, "NFSE");

    const bannerAdn = document.getElementById("banner-bloqueio-adn");
    const btnAdn = document.getElementById("btn-sync-adn");
    if (bannerAdn) {
      if (st.bloqueado_adn) {
        const minutos = Math.max(1, Math.ceil((st.segundos_bloqueio_adn_restantes || 0) / 60));
        bannerAdn.classList.remove("hidden");
        bannerAdn.innerHTML = `⏳ <strong>NFS-e (ADN) em espera:</strong> o ADN pediu para reduzir o ritmo ou ficou indisponível. Nenhuma consulta será feita por ~${minutos} min. Motivo: ${escaparHtml(st.cursor_adn?.ultimo_motivo || "limite de uso")}`;
        if (btnAdn) btnAdn.disabled = true;
      } else {
        bannerAdn.classList.add("hidden");
        if (btnAdn) btnAdn.disabled = false;
      }
    }
  };

  const carregarStatusRecebidas = async () => {
    const badgeCert = document.getElementById("cert-badge-status");
    const detalhesCert = document.getElementById("cert-detalhes");
    const btnRemoverCert = document.getElementById("btn-remover-cert");
    const badgeAmb = document.getElementById("nsu-badge-ambiente");
    const lblUltNsu = document.getElementById("lbl-ult-nsu");
    const lblMaxNsu = document.getElementById("lbl-max-nsu");
    const lblUltRet = document.getElementById("lbl-ultimo-retorno");
    const bannerBloqueio = document.getElementById("banner-bloqueio-656");
    const btnSync = document.getElementById("btn-sync-sefaz");
    const quickAmb = document.getElementById("quick-select-ambiente");
    const quickUf = document.getElementById("quick-select-uf");
    const modalSelectAmb = document.getElementById("select-cert-ambiente");
    const modalSelectUf = document.getElementById("select-cert-uf");

    if (!badgeCert || !detalhesCert) return;

    try {
      const resp = await fetch("/api/recebidas/status");
      if (!resp.ok) return;
      const st = await resp.json();

      if (quickAmb) quickAmb.value = String(st.ambiente || 2);
      if (quickUf && st.uf_autor) quickUf.value = String(st.uf_autor);
      if (modalSelectAmb) modalSelectAmb.value = String(st.ambiente || 2);
      if (modalSelectUf && st.uf_autor) modalSelectUf.value = String(st.uf_autor);

      if (st.certificado_configurado && st.certificado) {
        const c = st.certificado;
        if (c.vencido) {
          badgeCert.className = "badge badge-err";
          badgeCert.textContent = "Certificado Vencido";
        } else {
          badgeCert.className = "badge badge-ok";
          badgeCert.textContent = `Ativo (${c.dias_para_vencer} dias restantes)`;
        }
        const dtValidade = c.valido_ate ? new Date(c.valido_ate).toLocaleDateString("pt-BR") : "-";
        const origemLbl = st.origem_certificado === "interface" ? "Cadastrado na Interface" : "Variável .env";
        detalhesCert.innerHTML = `
          <div><strong>Titular:</strong> ${escaparHtml(c.titular)}</div>
          <div><strong>CNPJ:</strong> <code>${escaparHtml(fmtDoc(c.documento))}</code> | <strong>Válido até:</strong> ${escaparHtml(dtValidade)}</div>
          <div class="muted"><strong>Emissor:</strong> ${escaparHtml(c.emissor)} • <strong>Origem:</strong> ${escaparHtml(origemLbl)}</div>
        `;
        if (btnRemoverCert) {
          if (st.origem_certificado === "interface") {
            btnRemoverCert.classList.remove("hidden");
          } else {
            btnRemoverCert.classList.add("hidden");
          }
        }
      } else {
        badgeCert.className = "badge badge-warn";
        badgeCert.textContent = "Não Configurado";
        detalhesCert.innerHTML = `
          <div>Nenhum certificado A1 ativo. Clique em <strong>"⚙️ Cadastrar / Trocar Certificado (.pfx)"</strong> para selecionar o arquivo do seu computador.</div>
          <div class="muted" style="margin-top:0.2rem;">Dica: use <strong>"🧪 Carregar Recebidas de Demonstração"</strong> para testar os filtros sem certificado.</div>
        `;
        if (btnRemoverCert) btnRemoverCert.classList.add("hidden");
      }

      const avisosCert = document.getElementById("cert-avisos");
      if (avisosCert) {
        const avisos = [];
        if (st.migracao_legada_pendente) {
          avisos.push(`<div class="cert-feedback err">⚠️ Há um certificado no formato antigo (<code>data/certificado/</code>, com a senha em texto puro dentro da pasta do projeto). <button id="btn-migrar-cert" type="button" class="btn btn-sm btn-gold">Mover e proteger agora</button></div>`);
        }
        if (st.pasta_certificado_sincronizada) {
          avisos.push(`<div class="cert-feedback err">⚠️ A pasta do certificado parece estar em uma pasta sincronizada na nuvem (OneDrive/Dropbox/Google Drive). Defina <code>NFE_DATA_DIR</code> para uma pasta local.</div>`);
        }
        if (st.origem_certificado === "interface" && st.protecao_senha === "env") {
          avisos.push(`<div class="muted">Senha lida de <code>NFE_CERT_SENHA</code> (sem DPAPI neste sistema).</div>`);
        }
        avisosCert.innerHTML = avisos.join("");
      }

      if (badgeAmb) {
        badgeAmb.className = st.ambiente === 1 ? "badge badge-ok" : "badge badge-warn";
        badgeAmb.textContent = st.ambiente === 1 ? `Produção (1) • UF ${st.uf_autor || "35"}` : `Homologação (2) • UF ${st.uf_autor || "35"}`;
      }
      if (lblUltNsu && st.cursor) lblUltNsu.textContent = st.cursor.ultimo_nsu;
      if (lblMaxNsu && st.cursor) lblMaxNsu.textContent = st.cursor.max_nsu;

      if (lblUltRet && st.cursor) {
        if (st.cursor.ultimo_cstat) {
          const dtCons = st.cursor.ultima_consulta
            ? new Date(st.cursor.ultima_consulta).toLocaleString("pt-BR")
            : "-";
          lblUltRet.textContent = `Último retorno: cStat ${st.cursor.ultimo_cstat} (${st.cursor.ultimo_motivo || "-"}) em ${dtCons}`;
        } else {
          lblUltRet.textContent = "Nenhuma consulta realizada ainda neste ambiente.";
        }
      }

      if (bannerBloqueio) {
        if (st.bloqueado) {
          const minRest = Math.max(1, Math.ceil((st.segundos_bloqueio_restantes || 0) / 60));
          const dtAte = st.cursor?.bloqueado_ate
            ? new Date(st.cursor.bloqueado_ate).toLocaleTimeString("pt-BR")
            : "";
          bannerBloqueio.classList.remove("hidden");
          bannerBloqueio.innerHTML = `
            ⛔ <strong>Trava obrigatória SEFAZ ativa (cStat 656 — Consumo Indevido):</strong>
            aguarde ~${minRest} min (até ${escaparHtml(dtAte)}) antes de sincronizar novamente para não reiniciar o bloqueio de 1 hora na SEFAZ.
          `;
          if (btnSync) btnSync.disabled = true;
        } else {
          bannerBloqueio.classList.add("hidden");
          if (btnSync) btnSync.disabled = false;
        }
      }
      preencherPainelUnificado(st);
    } catch (_) {
      badgeCert.className = "badge badge-type";
      badgeCert.textContent = "Indisponível";
    }
  };

  const abrirModalCertificado = () => {
    const modal = document.getElementById("modal-certificado");
    const feedback = document.getElementById("cert-modal-feedback");
    if (feedback) {
      feedback.className = "cert-feedback hidden";
      feedback.textContent = "";
    }
    if (modal) modal.classList.remove("hidden");
  };

  const fecharModalCertificado = () => {
    const modal = document.getElementById("modal-certificado");
    if (modal) modal.classList.add("hidden");
  };

  const salvarCertificadoModal = async (e) => {
    e.preventDefault();
    const inputArq = document.getElementById("input-cert-arquivo");
    const inputSenha = document.getElementById("input-cert-senha");
    const selectUf = document.getElementById("select-cert-uf");
    const selectAmb = document.getElementById("select-cert-ambiente");
    const feedback = document.getElementById("cert-modal-feedback");
    const btnSalvar = document.getElementById("btn-salvar-certificado");

    if (!inputArq?.files?.length) return;

    const formData = new FormData();
    formData.append("arquivo", inputArq.files[0]);
    formData.append("senha", inputSenha?.value || "");
    formData.append("uf_autor", selectUf?.value || "35");
    formData.append("ambiente", selectAmb?.value || "2");

    if (btnSalvar) btnSalvar.disabled = true;
    if (feedback) {
      feedback.className = "cert-feedback";
      feedback.textContent = "Validando certificado PKCS#12 em memória...";
      feedback.classList.remove("hidden");
    }

    try {
      const resp = await fetch("/api/recebidas/certificado", {
        method: "POST",
        body: formData,
      });
      const dados = await resp.json();
      if (!resp.ok) {
        if (feedback) {
          feedback.className = "cert-feedback err";
          feedback.textContent = dados.detail || "Falha ao validar o certificado digital.";
        }
        return;
      }

      const c = dados.certificado;
      if (feedback && c) {
        feedback.className = "cert-feedback ok";
        feedback.textContent = `✅ Certificado validado e salvo! Titular: ${c.titular} | CNPJ: ${fmtDoc(c.documento)} | ${c.dias_para_vencer} dias restantes.`;
      }
      if (inputSenha) inputSenha.value = "";
      await carregarStatusRecebidas();
      definirStatusRecebidas(`Certificado A1 (${c?.titular || ""}) configurado com sucesso!`);
      setTimeout(fecharModalCertificado, 1100);
    } catch (err) {
      if (feedback) {
        feedback.className = "cert-feedback err";
        feedback.textContent = `Erro de comunicação: ${err.message}`;
      }
    } finally {
      if (btnSalvar) btnSalvar.disabled = false;
    }
  };

  const salvarQuickConfigSefaz = async () => {
    const quickAmb = document.getElementById("quick-select-ambiente");
    const quickUf = document.getElementById("quick-select-uf");
    const ambiente = Number(quickAmb?.value || 2);
    const uf_autor = quickUf?.value || "35";

    try {
      const resp = await fetch("/api/recebidas/config", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ambiente, uf_autor }),
      });
      const dados = await resp.json();
      if (!resp.ok) {
        definirStatusRecebidas(dados.detail || "Falha ao atualizar configuração SEFAZ.", true);
        return;
      }
      await carregarStatusRecebidas();
      await carregarListaRecebidas();
      definirStatusRecebidas(
        `Configuração SEFAZ atualizada: Ambiente ${dados.ambiente === 1 ? "Produção (1)" : "Homologação (2)"} • UF ${dados.uf_autor}.`
      );
    } catch (err) {
      definirStatusRecebidas(`Erro ao atualizar configuração: ${err.message}`, true);
    }
  };

  const removerCertificadoSalvo = async () => {
    try {
      const resp = await fetch("/api/recebidas/certificado", { method: "DELETE" });
      if (!resp.ok) return;
      await carregarStatusRecebidas();
      definirStatusRecebidas("Certificado digital removido do computador.");
    } catch (err) {
      definirStatusRecebidas(`Erro ao remover certificado: ${err.message}`, true);
    }
  };

  const migrarCertificadoLegado = async () => {
    try {
      const resp = await fetch("/api/recebidas/certificado/migrar", { method: "POST" });
      const dados = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        definirStatusRecebidas(dados.detail || "Falha ao migrar o certificado.", true);
        return;
      }
      await carregarStatusRecebidas();
      definirStatusRecebidas("Certificado movido para a pasta segura; senha protegida e senha.txt antigo apagado.");
    } catch (err) {
      definirStatusRecebidas(`Erro ao migrar certificado: ${err.message}`, true);
    }
  };

  const renderizarBadgesRecebida = (nota) => {
    const badges = [];
    if (nota.situacao === "AUTORIZADA") {
      badges.push('<span class="badge badge-ok">AUTORIZADA</span>');
    } else if (nota.situacao === "CANCELADA") {
      badges.push('<span class="badge badge-err">CANCELADA</span>');
    } else if (nota.situacao === "RESUMO") {
      badges.push('<span class="badge badge-warn">RESUMO (resNFe)</span>');
    } else {
      badges.push(`<span class="badge badge-type">${escaparHtml(nota.situacao)}</span>`);
    }

    if (nota.manifestacao_necessaria) {
      badges.push(
        '<span class="badge badge-warn" title="Registre Ciência da Operação (210210) para baixar o procNFe completo">⚠️ Exige Manifestação (210210)</span>'
      );
    }
    return badges.join(" ");
  };

  const ordenarNotasRecebidas = (notas) => {
    const { campo, direcao } = estado.ordenacaoRecebidas;
    if (!campo) return notas;
    const fator = direcao === "desc" ? -1 : 1;

    return [...notas].sort((a, b) => {
      let cmp = 0;
      switch (campo) {
        case "nsu":
          cmp = compararNumeroInteligente(a.nsu, b.nsu);
          break;
        case "data_emissao":
          cmp = timestampIso(a.data_emissao) - timestampIso(b.data_emissao);
          break;
        case "numero":
          cmp = compararNumeroInteligente(a.numero, b.numero);
          break;
        case "emitente":
          cmp = compararTexto(a.emitente_nome, b.emitente_nome);
          break;
        case "valor_total":
          cmp = Number(a.valor_total || 0) - Number(b.valor_total || 0);
          break;
        case "situacao":
          cmp = compararTexto(a.situacao, b.situacao);
          break;
        case "tipo":
          cmp = compararTexto(a.tipo_documento, b.tipo_documento);
          break;
        case "origem":
          cmp = compararTexto(rotuloOrigem(a.origem), rotuloOrigem(b.origem));
          break;
        case "papel":
          cmp = compararTexto(a.papel_visao || "", b.papel_visao || "");
          break;
        case "destinatario":
          cmp = compararTexto(a.destinatario_nome, b.destinatario_nome);
          break;
        default:
          cmp = 0;
      }
      return cmp * fator;
    });
  };

  const renderizarTabelaRecebidas = () => {
    const tbody = document.getElementById("tbody-recebidas");
    if (!tbody) return;

    atualizarIndicadoresOrdenacao(".data-table-recebidas", estado.ordenacaoRecebidas);
    const notasOrdenadas = ordenarNotasRecebidas(estado.notasRecebidas || []);
    const total = notasOrdenadas.length;
    const porPagina = Number(estado.paginacaoRecebidas.porPagina || 0);
    const totalPaginas = porPagina > 0 ? Math.max(1, Math.ceil(total / porPagina)) : 1;
    if (estado.paginacaoRecebidas.pagina > totalPaginas) {
      estado.paginacaoRecebidas.pagina = totalPaginas;
    }
    if (estado.paginacaoRecebidas.pagina < 1) {
      estado.paginacaoRecebidas.pagina = 1;
    }

    const inicio = porPagina > 0 ? (estado.paginacaoRecebidas.pagina - 1) * porPagina : 0;
    const fim = porPagina > 0 ? Math.min(inicio + porPagina, total) : total;
    const notas = notasOrdenadas.slice(inicio, fim);

    const lblPag = document.getElementById("lbl-paginacao-recebidas");
    const btnAnt = document.getElementById("btn-pag-rec-ant");
    const btnProx = document.getElementById("btn-pag-rec-prox");
    if (lblPag) {
      const a = total === 0 ? 0 : inicio + 1;
      lblPag.textContent = `Página ${estado.paginacaoRecebidas.pagina} de ${totalPaginas} (exibindo ${a}–${fim} de ${total} documentos)`;
    }
    if (btnAnt) btnAnt.disabled = estado.paginacaoRecebidas.pagina <= 1;
    if (btnProx) btnProx.disabled = estado.paginacaoRecebidas.pagina >= totalPaginas;

    if (notas.length === 0) {
      tbody.innerHTML =
        '<tr><td colspan="10" style="text-align:center;" class="muted">Nenhuma nota encontrada para os filtros selecionados. Sincronize ou carregue a demonstração.</td></tr>';
      return;
    }

    tbody.innerHTML = notas
      .map((n) => {
        const dtEmi = fmtDataCurta(n.data_emissao);
        const dtEmiCompleta = fmtDataHoraCompleta(n.data_emissao);
        const ehNfse = n.tipo_documento === "NFSE";
        const numSerie = n.numero ? `${n.numero} / ${n.serie || "-"}` : "Resumo s/ número";
        const chaveUrl = encodeURIComponent(n.chave);
        const rotuloEspelho = ehNfse ? "📄 DANFSe" : "📄 DANFE";
        return `
          <tr>
            <td class="col-tipo-rec"><span class="badge badge-type">${escaparHtml(rotuloTipoNota(n.tipo_documento))}</span></td>
            <td class="col-origem-rec">${badgeOrigem(n.origem)}</td>
            <td class="col-papel-rec">${badgePapel(n.papel_visao)}</td>
            <td class="col-num-chave">
              <strong>${escaparHtml(numSerie)}</strong>
              ${renderizarChaveComCopia(n.chave)}
            </td>
            <td class="col-emissao" title="${escaparHtml(dtEmiCompleta)}">${escaparHtml(dtEmi)}</td>
            <td class="col-parte-rec">
              <strong class="parte-nome">${escaparHtml(n.emitente_nome || "-")}</strong>
              <code class="doc-code">${escaparHtml(fmtDoc(n.emitente_documento))}</code>
            </td>
            <td class="col-parte-rec">
              <strong class="parte-nome">${escaparHtml(n.destinatario_nome || "-")}</strong>
              <code class="doc-code">${escaparHtml(fmtDoc(n.destinatario_documento))}</code>
            </td>
            <td class="num col-valor-rec"><strong>${fmtMoeda(n.valor_total)}</strong></td>
            <td class="col-sit-rec"><div class="badges-wrap">${renderizarBadgesRecebida(n)}</div></td>
            <td class="col-actions">
              <div class="row-actions">
                <a class="btn btn-sm btn-primary" href="/recebidas/${chaveUrl}/espelho" target="_blank" rel="noopener">
                  ${rotuloEspelho}
                </a>
                <a class="btn btn-sm btn-ghost" href="/recebidas/${chaveUrl}/xml" download>
                  ⬇ XML
                </a>
              </div>
            </td>
          </tr>
        `;
      })
      .join("");
  };

  const carregarListaRecebidas = async () => {
    const tbody = document.getElementById("tbody-recebidas");
    if (!tbody) return;

    const params = new URLSearchParams();
    const emitente = (document.getElementById("rec-filtro-emitente")?.value || "").trim();
    const dtInicio = (document.getElementById("rec-filtro-data-inicio")?.value || "").trim();
    const dtFim = (document.getElementById("rec-filtro-data-fim")?.value || "").trim();
    const vMin = (document.getElementById("rec-filtro-valor-min")?.value || "").trim();
    const vMax = (document.getElementById("rec-filtro-valor-max")?.value || "").trim();
    const categoria = (document.getElementById("rec-filtro-categoria")?.value || "").trim();
    const situacao = (document.getElementById("rec-filtro-situacao")?.value || "").trim();
    const tipoF = (document.getElementById("rec-filtro-tipo")?.value || "").trim();
    const origemF = (document.getElementById("rec-filtro-origem")?.value || "").trim();
    const papelF = (document.getElementById("rec-filtro-papel")?.value || "").trim();

    if (tipoF) params.set("tipo", tipoF);
    if (origemF) params.set("origem", origemF);
    if (papelF) params.set("papel", papelF);
    if (emitente) params.set("emitente", emitente);
    if (dtInicio) params.set("data_inicio", dtInicio);
    if (dtFim) params.set("data_fim", dtFim);
    if (vMin) params.set("valor_min", vMin);
    if (vMax) params.set("valor_max", vMax);
    if (categoria) params.set("categoria", categoria);
    if (situacao) params.set("situacao", situacao);

    const qs = params.toString();
    const url = qs ? `/api/recebidas?${qs}` : "/api/recebidas";

    try {
      const resp = await fetch(url);
      if (!resp.ok) return;
      const dados = await resp.json();

      const kpiTotal = document.getElementById("rec-kpi-total");
      const kpiValor = document.getElementById("rec-kpi-valor");
      if (kpiTotal) kpiTotal.textContent = String(dados.total || 0);
      if (kpiValor) kpiValor.textContent = fmtMoedaRS(dados.valor_total_filtrado || 0);

      estado.notasRecebidas = dados.notas || [];
      estado.paginacaoRecebidas.pagina = 1;
      renderizarTabelaRecebidas();
    } catch (err) {
      definirStatusRecebidas(`Erro ao listar notas recebidas: ${err.message}`, true);
    }
  };

  const mostrarPassoACerto = () => {
    document.getElementById("passo-a-passo-cert")?.classList.remove("hidden");
  };

  const executarSincNfe = async () => {
    try {
      const resp = await fetch("/api/recebidas/sincronizar", { method: "POST" });
      const dados = await resp.json();
      if (resp.status === 503) {
        mostrarPassoACerto();
        return { erro: true, texto: "NF-e: cadastre o certificado digital A1 (passo a passo acima)." };
      }
      if (!resp.ok) {
        return { erro: true, texto: `NF-e: ${dados.detail || `falha na sincronização (HTTP ${resp.status}).`}` };
      }
      return {
        erro: false,
        texto: `NF-e (SEFAZ): ${dados.paginas_consultadas} página(s), ${dados.novas_notas} nova(s), ${dados.notas_atualizadas} atualizada(s), ${dados.eventos_registrados} evento(s).`,
      };
    } catch (err) {
      return { erro: true, texto: `NF-e: erro de comunicação (${err.message}).` };
    }
  };

  const executarSincNfse = async () => {
    try {
      const resp = await fetch("/api/recebidas/sincronizar-adn", { method: "POST" });
      const dados = await resp.json();
      if (resp.status === 503) {
        mostrarPassoACerto();
        return { erro: true, texto: "NFS-e: cadastre o certificado digital A1 (passo a passo acima)." };
      }
      if (!resp.ok) {
        return { erro: true, texto: `NFS-e: ${dados.detail || `falha na sincronização (HTTP ${resp.status}).`}` };
      }
      let txt = `NFS-e (ADN): ${dados.paginas_consultadas} página(s), ${dados.novas_notas} nova(s), ${dados.notas_atualizadas} atualizada(s), ${dados.eventos_registrados} evento(s)`;
      const ign = Object.entries(dados.ignorados_por_tipo || {});
      if (ign.length) txt += `; ignorados: ${ign.map(([tp, q]) => `${q} ${tp}`).join(", ")}`;
      if (dados.com_erro_processamento) txt += `; ${dados.com_erro_processamento} com erro de leitura`;
      if (Number(dados.tentativas_usadas) > Number(dados.paginas_consultadas)) {
        txt += `; ${dados.tentativas_usadas} tentativa(s) de conexão`;
      }
      txt += ".";
      if (["ERRO", "REJEICAO", "BLOQUEIO"].includes(dados.encerrado_por)) {
        return { erro: true, texto: `${txt} ${dados.mensagem || ""}`.trim() };
      }
      if (dados.encerrado_por === "LIMITE_PAGINAS") {
        txt += " Ainda há notas: clique de novo para continuar do cursor.";
      }
      return { erro: false, texto: txt };
    } catch (err) {
      return { erro: true, texto: `NFS-e: erro de comunicação (${err.message}).` };
    }
  };

  const depoisDeSincronizar = async () => {
    await carregarStatusRecebidas();
    await carregarListaRecebidas();
    if (estado.escopoId === "tudo") await carregarEscopo("tudo");
  };

  const sincronizarComSefaz = async () => {
    definirStatusRecebidas("Sincronizando NF-e com o Ambiente Nacional da SEFAZ (NFeDistribuicaoDFe)...");
    const r = await executarSincNfe();
    definirStatusRecebidas(r.texto, r.erro);
    await depoisDeSincronizar();
  };

  const sincronizarComAdn = async () => {
    definirStatusRecebidas("Sincronizando NFS-e (emitidas e tomadas) com o ADN Nacional...");
    const r = await executarSincNfse();
    definirStatusRecebidas(r.texto, r.erro);
    await depoisDeSincronizar();
  };

  const sincronizarTudo = async () => {
    definirStatusRecebidas("Sincronizando NF-e (SEFAZ) e depois NFS-e (ADN)...");
    const nfe = await executarSincNfe();
    const nfse = await executarSincNfse();
    definirStatusRecebidas(`${nfe.texto} | ${nfse.texto}`, nfe.erro || nfse.erro);
    await depoisDeSincronizar();
  };

  const renderizarDiagnosticoAdn = (d) => {
    const nomeAmb = d.ambiente === 1 ? "Produção (1)" : "Produção restrita (2)";
    const fmtData = (iso) => (iso ? new Date(iso).toLocaleDateString("pt-BR") : "-");
    const msgs = (lista) =>
      lista && lista.length
        ? lista.map((m) => `${escaparHtml(m.codigo)} - ${escaparHtml(m.descricao)}`).join("<br>")
        : "(nenhum)";
    const itens = Object.entries(d.itens_por_tipo_documento || {});
    const falha = d.falha_conexao || (d.falhas_conexao && d.falhas_conexao.length ? d.falhas_conexao[d.falhas_conexao.length - 1] : null);
    const linhas = [
      ["Ambiente", escaparHtml(nomeAmb)],
      ["URL base", `<code>${escaparHtml(d.url_base)}</code>`],
      ["CNPJ de consulta", `<code>${escaparHtml(d.cnpj_consulta_mascarado)}</code>`],
      [
        "Certificado válido",
        `${fmtData(d.certificado_valido_de)} a ${fmtData(d.certificado_valido_ate)}${d.certificado_vencido ? " (VENCIDO)" : ""}`,
      ],
      ["Tentativas", `${d.tentativas_realizadas} de ${d.tentativas_solicitadas} (${d.tentativas_com_falha} com falha) · timeout ${d.timeout_segundos}s`],
      ["Conexão TLS", escaparHtml(d.conexao_tls)],
    ];
    if (d.tls_versao || d.tls_cifrador) {
      linhas.push(["TLS negociado", `${escaparHtml(d.tls_versao || "-")} / ${escaparHtml(d.tls_cifrador || "-")}`]);
    }
    linhas.push(
      ["HTTP", d.http_status ? String(d.http_status) : "-"],
      ["StatusProcessamento", escaparHtml(d.status_processamento || "-")],
      ["TipoConsulta", escaparHtml(d.tipo_consulta || "-")],
      ["Erros", msgs(d.erros)],
      ["Alertas", msgs(d.alertas)],
      ["Itens no lote", itens.length ? itens.map(([tp, q]) => `${escaparHtml(tp)}: ${q}`).join(" · ") : "0"]
    );
    if (falha) {
      linhas.push(
        ["Falha: etapa", escaparHtml(falha.etapa)],
        ["Falha: exceção raiz", `${escaparHtml(falha.excecao_raiz)}${falha.codigo_so ? ` (${escaparHtml(falha.codigo_so)})` : ""}`],
        ["Falha: servidor encerrou / tempo esgotado", `${falha.servidor_encerrou_conexao ? "SIM" : "NÃO"} / ${falha.tempo_esgotado ? "SIM" : "NÃO"}`]
      );
    }
    const selo = d.sucesso
      ? '<span class="badge badge-ok">Funcionou</span>'
      : '<span class="badge badge-err">Atenção</span>';
    return `
      <div style="display:flex;justify-content:space-between;align-items:center;gap:1rem;">
        <strong>🩺 Diagnóstico do ADN (somente leitura, nada foi gravado)</strong> ${selo}
      </div>
      <p style="margin:0.5rem 0;">${escaparHtml(d.interpretacao || "")}</p>
      <table class="diag-table"><tbody>
        ${linhas.map(([k, v]) => `<tr><th>${k}</th><td>${v}</td></tr>`).join("")}
      </tbody></table>
    `;
  };

  const diagnosticarAdn = async () => {
    const box = document.getElementById("adn-diagnostico");
    if (!box) return;
    box.classList.remove("hidden");
    box.textContent = "Consultando o ADN (uma chamada de leitura)...";
    try {
      const resp = await fetch("/api/recebidas/adn/diagnostico?tentativas=2", { method: "POST" });
      const dados = await resp.json();
      if (resp.status === 503) {
        box.classList.add("hidden");
        mostrarPassoACerto();
        definirStatusRecebidas("Cadastre o certificado digital A1 para usar o diagnóstico (passo a passo acima).", true);
        return;
      }
      if (!resp.ok) {
        box.textContent = dados.detail || `Falha no diagnóstico (HTTP ${resp.status}).`;
        return;
      }
      box.innerHTML = renderizarDiagnosticoAdn(dados);
    } catch (err) {
      box.textContent = `Erro de comunicação: ${err.message}`;
    }
  };

  const carregarRecebidasDemo = async () => {
    definirStatusRecebidas("Carregando NF-e e NFS-e sintéticas no SQLite local...");
    try {
      const resp = await fetch("/api/recebidas/demo", { method: "POST" });
      const dados = await resp.json();
      if (!resp.ok) {
        definirStatusRecebidas(dados.detail || "Falha ao carregar demonstração DistDFe.", true);
        return;
      }
      definirStatusRecebidas(
        `Demonstração carregada: NF-e da SEFAZ (${dados.documentos_processados} docZip) e ${dados.adn_novas_notas || 0} NFS-e sintéticas do ADN (emitidas, tomadas, intermediária e uma cancelada).`
      );
      if (estado.escopoId === "tudo") await carregarEscopo("tudo");
      await carregarStatusRecebidas();
      await carregarListaRecebidas();
    } catch (err) {
      definirStatusRecebidas(`Erro ao carregar demonstração: ${err.message}`, true);
    }
  };

  const limparFiltrosRecebidas = () => {
    [
      "rec-filtro-emitente",
      "rec-filtro-data-inicio",
      "rec-filtro-data-fim",
      "rec-filtro-valor-min",
      "rec-filtro-valor-max",
      "rec-filtro-categoria",
      "rec-filtro-situacao",
      "rec-filtro-tipo",
      "rec-filtro-origem",
      "rec-filtro-papel",
    ].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.value = "";
    });
    marcarPapelRapido("");
    carregarListaRecebidas();
  };

  const marcarPapelRapido = (papel) => {
    document.querySelectorAll("[data-papel-rapido]").forEach((b) => {
      b.classList.toggle("btn-primary", (b.dataset.papelRapido || "") === papel);
      b.classList.toggle("btn-secondary", (b.dataset.papelRapido || "") !== papel);
    });
  };

  const aplicarPapelRapido = (papel) => {
    const sel = document.getElementById("rec-filtro-papel");
    if (sel) sel.value = papel;
    marcarPapelRapido(papel);
    carregarListaRecebidas();
  };

  const limparNotasRecebidas = async () => {
    const confirmado = window.confirm(
      "Remover todas as notas recebidas, eventos e zerar o cursor NSU no banco SQLite local?"
    );
    if (!confirmado) return;

    definirStatusRecebidas("Limpando notas recebidas e zerando cursor NSU...");
    try {
      const resp = await fetch("/api/recebidas", { method: "DELETE" });
      const dados = await resp.json();
      if (!resp.ok) {
        definirStatusRecebidas(dados.detail || "Falha ao limpar notas recebidas.", true);
        return;
      }
      await carregarStatusRecebidas();
      await carregarListaRecebidas();
      definirStatusRecebidas(
        `${dados.removidas} nota(s) recebida(s) removida(s) e cursor NSU zerado.`
      );
    } catch (err) {
      definirStatusRecebidas(`Erro ao limpar recebidas: ${err.message}`, true);
    }
  };

  const resetarBancoCompleto = async () => {
    const confirmado = window.confirm(
      "Zerar completamente o banco SQLite local?\n\n" +
        "Isso apagará todos os lotes importados (Aba 1), todas as notas recebidas da SEFAZ (Aba 2), " +
        "cursores NSU e logs, deixando o sistema pronto para dados reais. " +
        "Seu Certificado Digital A1 configurado não será apagado."
    );
    if (!confirmado) return;

    definirStatusUpload("Zerando banco SQLite local...");
    definirStatusRecebidas("Zerando banco SQLite local...");
    try {
      const resp = await fetch("/api/sistema/resetar-banco", { method: "POST" });
      const dados = await resp.json();
      if (!resp.ok) {
        const msg = dados.detail || "Falha ao zerar o banco SQLite local.";
        definirStatusUpload(msg, true);
        definirStatusRecebidas(msg, true);
        return;
      }

      estado.loteId = null;
      estado.escopoId = "acervo";
      estado.resumo = null;
      estado.resultados = [];
      estado.historicoLotes = [];

      document.getElementById("painel-lote")?.classList.add("hidden");
      document.getElementById("estado-vazio-lotes")?.classList.remove("hidden");
      renderizarSidebarHistorico();
      await carregarStatusRecebidas();
      await carregarListaRecebidas();

      const msgSucesso =
        `Banco SQLite zerado (${dados.lotes_removidos} lote(s) e ` +
        `${dados.recebidas_removidas} recebida(s) removida(s)). Pronto para dados reais!`;
      definirStatusUpload(msgSucesso);
      definirStatusRecebidas(msgSucesso);
    } catch (err) {
      definirStatusUpload(`Erro ao zerar banco: ${err.message}`, true);
      definirStatusRecebidas(`Erro ao zerar banco: ${err.message}`, true);
    }
  };

  document.addEventListener("DOMContentLoaded", async () => {
    const dropzone = document.getElementById("dropzone");
    const fileInput = document.getElementById("file-input");

    const btnTabLotes = document.getElementById("btn-tab-lotes");
    const btnTabRecebidas = document.getElementById("btn-tab-recebidas");
    if (btnTabLotes && btnTabRecebidas) {
      btnTabLotes.addEventListener("click", () => alternarAba("lotes"));
      btnTabRecebidas.addEventListener("click", () => alternarAba("recebidas"));
    }

    // Sub-abas do painel principal
    ["notas", "resumo", "ia"].forEach((sub) => {
      const btn = document.getElementById(`btn-subtab-${sub}`);
      if (btn) btn.addEventListener("click", () => alternarSubaba(sub));
    });

    // Sidebar de Histórico e Escopo
    const btnEscopoAcervo = document.getElementById("btn-escopo-acervo");
    if (btnEscopoAcervo) {
      btnEscopoAcervo.addEventListener("click", () => carregarEscopo("acervo"));
    }

    const listaHistorico = document.getElementById("lista-historico-lotes");
    if (listaHistorico) {
      listaHistorico.addEventListener("click", (e) => {
        const btnDel = e.target.closest("[data-action='excluir-lote']");
        if (btnDel) {
          e.stopPropagation();
          excluirLoteDoHistorico(btnDel.dataset.loteId);
          return;
        }
        const btnSel = e.target.closest("[data-action='selecionar-lote']");
        if (btnSel) {
          carregarEscopo(btnSel.dataset.loteId);
        }
      });
    }

    document.getElementById("btn-limpar-demos")?.addEventListener("click", () => limparHistorico(true));
    document.getElementById("btn-resetar-banco-sidebar")?.addEventListener("click", resetarBancoCompleto);
    document.getElementById("btn-resetar-banco-topo")?.addEventListener("click", resetarBancoCompleto);

    // Clique em um emitente na aba Resumo filtra a aba Notas Fiscais
    document.getElementById("tbody-emitentes")?.addEventListener("click", (e) => {
      const tr = e.target.closest("[data-action='filtrar-emitente']");
      if (!tr) return;
      const chaveEmit = tr.dataset.emitente || "";
      const selectEmit = document.getElementById("filtro-emitente");
      if (selectEmit) {
        selectEmit.value = chaveEmit;
        estado.paginacaoNotas.pagina = 1;
        alternarSubaba("notas");
        renderizarTabelaNotas();
      }
    });

    // Controles SEFAZ e Certificado A1
    document.getElementById("btn-sync-sefaz")?.addEventListener("click", sincronizarComSefaz);
    document.getElementById("btn-sync-adn")?.addEventListener("click", sincronizarComAdn);
    document.getElementById("btn-sync-tudo")?.addEventListener("click", sincronizarTudo);
    document.getElementById("btn-diag-adn")?.addEventListener("click", diagnosticarAdn);
    document.querySelectorAll("[data-papel-rapido]").forEach((b) => {
      b.addEventListener("click", () => aplicarPapelRapido(b.dataset.papelRapido || ""));
    });
    document.getElementById("btn-escopo-tudo")?.addEventListener("click", () => carregarEscopo("tudo"));
    document.getElementById("btn-demo-recebidas")?.addEventListener("click", carregarRecebidasDemo);
    document.getElementById("btn-limpar-recebidas")?.addEventListener("click", limparNotasRecebidas);
    document.getElementById("btn-abrir-modal-cert")?.addEventListener("click", abrirModalCertificado);
    document.getElementById("btn-card-cadastrar-cert")?.addEventListener("click", abrirModalCertificado);
    document.getElementById("btn-fechar-modal-cert")?.addEventListener("click", fecharModalCertificado);
    document.getElementById("form-cadastro-certificado")?.addEventListener("submit", salvarCertificadoModal);
    document.getElementById("btn-salvar-quick-config")?.addEventListener("click", salvarQuickConfigSefaz);
    document.getElementById("btn-remover-cert")?.addEventListener("click", removerCertificadoSalvo);
    document.getElementById("cert-avisos")?.addEventListener("click", (e) => {
      if (e.target.closest("#btn-migrar-cert")) migrarCertificadoLegado();
    });

    const btnRevelarSenha = document.getElementById("btn-revelar-senha-cert");
    const inputSenhaCert = document.getElementById("input-cert-senha");
    if (btnRevelarSenha && inputSenhaCert) {
      btnRevelarSenha.addEventListener("click", () => {
        const ehPassword = inputSenhaCert.type === "password";
        inputSenhaCert.type = ehPassword ? "text" : "password";
        btnRevelarSenha.textContent = ehPassword ? "🙈 Ocultar" : "👁️ Mostrar";
      });
    }

    const formFiltrosRec = document.getElementById("form-filtros-recebidas");
    if (formFiltrosRec) {
      formFiltrosRec.addEventListener("submit", (e) => {
        e.preventDefault();
        carregarListaRecebidas();
      });
    }

    document.getElementById("btn-rec-limpar")?.addEventListener("click", limparFiltrosRecebidas);

    document.getElementById("btn-select-files")?.addEventListener("click", (e) => {
      e.stopPropagation();
      fileInput.click();
    });

    document.getElementById("btn-empty-upload")?.addEventListener("click", () => fileInput.click());
    document.getElementById("btn-empty-demo")?.addEventListener("click", carregarLoteDemo);

    dropzone?.addEventListener("click", (e) => {
      if (e.target.closest("button")) return;
      fileInput.click();
    });

    fileInput?.addEventListener("change", () => enviarArquivos(fileInput.files));

    ["dragenter", "dragover"].forEach((evName) => {
      dropzone?.addEventListener(evName, (e) => {
        e.preventDefault();
        dropzone.classList.add("dragover");
      });
    });

    ["dragleave", "drop"].forEach((evName) => {
      dropzone?.addEventListener(evName, (e) => {
        e.preventDefault();
        dropzone.classList.remove("dragover");
      });
    });

    dropzone?.addEventListener("drop", (e) => {
      if (e.dataTransfer?.files?.length) {
        enviarArquivos(e.dataTransfer.files);
      }
    });

    document.getElementById("btn-demo")?.addEventListener("click", carregarLoteDemo);
    document.getElementById("btn-demo-inline")?.addEventListener("click", (e) => {
      e.stopPropagation();
      carregarLoteDemo();
    });

    document.getElementById("btn-theme")?.addEventListener("click", () => {
      const html = document.documentElement;
      const atual = html.getAttribute("data-theme") || "dark";
      html.setAttribute("data-theme", atual === "dark" ? "light" : "dark");
    });

    ["filtro-busca", "filtro-tipo", "filtro-status", "filtro-emitente"].forEach((id) => {
      const el = document.getElementById(id);
      if (!el) return;
      const onFiltro = () => {
        estado.paginacaoNotas.pagina = 1;
        renderizarTabelaNotas();
      };
      el.addEventListener("input", onFiltro);
      el.addEventListener("change", onFiltro);
    });

    document.getElementById("select-por-pagina-notas")?.addEventListener("change", (e) => {
      estado.paginacaoNotas.porPagina = Number(e.target.value || 100);
      estado.paginacaoNotas.pagina = 1;
      renderizarTabelaNotas();
    });
    document.getElementById("btn-pag-notas-ant")?.addEventListener("click", () => {
      if (estado.paginacaoNotas.pagina > 1) {
        estado.paginacaoNotas.pagina -= 1;
        renderizarTabelaNotas();
      }
    });
    document.getElementById("btn-pag-notas-prox")?.addEventListener("click", () => {
      estado.paginacaoNotas.pagina += 1;
      renderizarTabelaNotas();
    });

    document.getElementById("select-por-pagina-recebidas")?.addEventListener("change", (e) => {
      estado.paginacaoRecebidas.porPagina = Number(e.target.value || 100);
      estado.paginacaoRecebidas.pagina = 1;
      renderizarTabelaRecebidas();
    });
    document.getElementById("btn-pag-rec-ant")?.addEventListener("click", () => {
      if (estado.paginacaoRecebidas.pagina > 1) {
        estado.paginacaoRecebidas.pagina -= 1;
        renderizarTabelaRecebidas();
      }
    });
    document.getElementById("btn-pag-rec-prox")?.addEventListener("click", () => {
      estado.paginacaoRecebidas.pagina += 1;
      renderizarTabelaRecebidas();
    });

    document.querySelector(".data-table-notas thead")?.addEventListener("click", (e) => {
      const th = e.target.closest("th.sortable[data-sort]");
      if (!th) return;
      const campo = th.dataset.sort;
      if (estado.ordenacaoNotas.campo === campo) {
        estado.ordenacaoNotas.direcao =
          estado.ordenacaoNotas.direcao === "asc" ? "desc" : "asc";
      } else {
        estado.ordenacaoNotas.campo = campo;
        estado.ordenacaoNotas.direcao =
          campo === "data_emissao" || campo === "valor_total" ? "desc" : "asc";
      }
      estado.paginacaoNotas.pagina = 1;
      renderizarTabelaNotas();
    });

    document.querySelector(".data-table-recebidas thead")?.addEventListener("click", (e) => {
      const th = e.target.closest("th.sortable[data-sort]");
      if (!th) return;
      const campo = th.dataset.sort;
      if (estado.ordenacaoRecebidas.campo === campo) {
        estado.ordenacaoRecebidas.direcao =
          estado.ordenacaoRecebidas.direcao === "asc" ? "desc" : "asc";
      } else {
        estado.ordenacaoRecebidas.campo = campo;
        estado.ordenacaoRecebidas.direcao =
          campo === "data_emissao" || campo === "valor_total" ? "desc" : "asc";
      }
      estado.paginacaoRecebidas.pagina = 1;
      renderizarTabelaRecebidas();
    });

    document.getElementById("tbody-notas")?.addEventListener("click", (e) => {
      const btnCopiar = e.target.closest("[data-action='copiar-chave']");
      if (btnCopiar) {
        copiarChaveParaClipboard(btnCopiar.dataset.chave, btnCopiar);
        return;
      }
      const btn = e.target.closest("[data-action='detalhes']");
      if (btn) {
        abrirModalDetalhes(Number(btn.dataset.idx));
      }
    });

    document.getElementById("tbody-recebidas")?.addEventListener("click", (e) => {
      const btnCopiar = e.target.closest("[data-action='copiar-chave']");
      if (btnCopiar) {
        copiarChaveParaClipboard(btnCopiar.dataset.chave, btnCopiar);
      }
    });

    document.getElementById("btn-fechar-modal")?.addEventListener("click", () => {
      document.getElementById("modal-detalhes")?.classList.add("hidden");
    });

    const formPergunta = document.getElementById("form-pergunta-llm");
    const inputPergunta = document.getElementById("input-pergunta-llm");
    if (formPergunta && inputPergunta) {
      formPergunta.addEventListener("submit", (e) => {
        e.preventDefault();
        enviarPerguntaLLM(inputPergunta.value);
      });
    }

    document.querySelectorAll(".llm-chip").forEach((chip) => {
      chip.addEventListener("click", () => {
        const p = chip.getAttribute("data-pergunta") || "";
        if (inputPergunta) inputPergunta.value = p;
        enviarPerguntaLLM(p);
      });
    });

    atualizarStatusLLM();

    // Carregamento automático ao abrir a página: se já existirem lotes no SQLite, abre Todo o Acervo!
    const lotesExistentes = await carregarHistoricoLotes();
    if (lotesExistentes.length > 0) {
      await carregarEscopo("acervo");
    }
  });
})();
