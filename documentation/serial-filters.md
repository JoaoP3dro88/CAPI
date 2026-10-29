# Filtros Aplicados aos Seriais — Mapa Completo

> **Objetivo:** listar todos os critérios que descartam ou qualificam um serial (e seus dados) ao longo do pipeline, do banco de dados até o estado inicial da simulação. Indispensável para fazer cara-a-crachá com o Real.

---

## Diagrama do Fluxo de Filtros

```
silver_quality.b2b_machine_status   (raw DB events)
          │
          │  ─── FILTROS SQL ────────────────────────────────────────
          │  F1  SERIAL_NUMBER NOT LIKE '%[a-z]%'
          │  F2  QUANTITY <> 0
          │  F3  DATE >= '2026-04-25'  (trusted window)
          │  F4  STAGE = '03 - Agie'  (ou '02 - Gravadora')
          │  F5  MACHINE <> 'AGIE UNKNOWN' / MACHINE IS NOT NULL
          │  F6  MACHINE NOT LIKE '%SEM AGIE%'  (só buffer)
          │  F7  Ghost timeout 24 h  (entrada sem saída > 24 h → descarta)
          │  F8  Current session (ROW_NUMBER por SERIAL → só o mais recente)
          │  F9  NOT EXISTS evento posterior  (qualquer evento após = tray saiu)
          │  F10 QUANTITY <= 500  (só buffer — erros de entrada de dados)
          │  F11 Current PN session por machine  (ghost de PN diferente = ignorado)
          │
          ▼
  DataFrames (df_machine_status, df_buffer_stock, df_laser_status …)
          │
          │  ─── FILTROS PYTHON (data_gathering.py) ─────────────────
          │  F12 _normalize_optional_text → machine_id / part_number_id vazio → descarta
          │  F13 _machine_to_line → 'Unknown' → descarta
          │  F14 eligible_part_numbers: PN deve ter LASER + EDM nas restrições
          │
          ▼
  live_machine_rows, live_buffer_rows  →  initial_state.py
          │
          │  ─── FILTROS PYTHON (initial_state.py) ──────────────────
          │  F15 Máquina ausente em machine_specs (specs do EDM)
          │  F16 Buffer: PN não elegível (LASER+EDM)
          │  F17 Buffer: linha cheia (≥ stock_capacity_trays)
          │  F18 Buffer: sem ordem aberta para o PN
          │
          ▼
  initial_state → simulation
          │
          │  ─── FILTROS PYTHON (main.py) ───────────────────────────
          │  F19 Orders: PN sem restrição LASER+EDM → ordem removida
          │  F20 --excluded-machines → máquina vira inativa (sem trabalho)
          │  F21 --max-orders / --max-machines (limites de debug)
          │
          ▼
  Simulation result (dashboard.json, history.json …)
```

---

## Detalhamento de Cada Filtro

### F1 — Serial com letras minúsculas
| | |
|---|---|
| **Onde** | `data_gathering.py` → `fetch_machine_status`, `fetch_buffer_stock`, `fetch_laser_status`, `fetch_finished_goods`, `fetch_produced_today` |
| **SQL** | `AND SERIAL_NUMBER NOT LIKE '%[a-z]%'` |
| **Por quê** | Seriais de teste, rework ou lançamentos manuais costumam conter letras minúsculas (e.g. `"123abc456"`). Dados de produção real têm apenas dígitos. |
| **Impacto no Real** | Trays com seriais não-numéricos ficam invisíveis — não entram em nenhum dos contadores de status. |

---

### F2 — Quantidade zero
| | |
|---|---|
| **Onde** | Todos os fetches de `b2b_machine_status` |
| **SQL** | `AND QUANTITY <> 0` |
| **Por quê** | Registros com `QUANTITY = 0` são eventos administrativos/cancelamentos; não representam peças físicas. |
| **Impacto no Real** | Eventos de cancelamento ou correção de lançamento não são contabilizados. |

---

### F3 — Janela temporal (trusted window)
| | |
|---|---|
| **Onde** | `fetch_machine_status`, `fetch_buffer_stock`, `fetch_laser_status`, `fetch_finished_goods`, `fetch_produced_today`, `fetch_buffer_stock` |
| **SQL** | `AND DATE >= '2026-04-25'` |
| **Por quê** | Antes dessa data os dados de produção não eram confiáveis (migração de sistema, dados sujos). Evita que seriais antigos "fantasmas" apareçam como ativos. |
| **Impacto no Real** | Qualquer tray com última movimentação antes de 25/04/2026 não existe para o sistema. **Se a data-corte não for atualizada periodicamente, trays antigas podem voltar a aparecer como fantasmas quando a window travar no passado.** |

---

### F4 — Estágio correto
| | |
|---|---|
| **Onde** | `fetch_machine_status` → `STAGE = '03 - Agie'`; `fetch_buffer_stock` / `fetch_laser_status` → `STAGE = '02 - Gravadora'` |
| **SQL** | `AND STAGE = '03 - Agie'` (ou `'02 - Gravadora'`) |
| **Por quê** | A tabela `b2b_machine_status` registra eventos de todos os estágios (02, 03, etc.). Cada query filtra apenas o estágio de interesse. |
| **Impacto no Real** | Um serial que passou pelo AGIE mas cujo evento foi gravado com estágio errado não será reconhecido como "na máquina". |

---

### F5 — Máquina desconhecida
| | |
|---|---|
| **Onde** | `fetch_machine_status` |
| **SQL** | `AND MACHINE <> 'AGIE UNKNOWN' AND MACHINE IS NOT NULL` |
| **Por quê** | O campo MACHINE fica `'AGIE UNKNOWN'` ou NULL quando o leitor de código não conseguiu identificar a máquina OU para movimentações do tipo SAÍDA no banco. |
| **Impacto no Real** | Trays lidas sem identificação de máquina são descartadas — não entram no status de nenhuma máquina. |

---

### F6 — Destino "SEM AGIE"
| | |
|---|---|
| **Onde** | `fetch_buffer_stock` |
| **SQL** | `AND MACHINE NOT LIKE '%SEM AGIE%'` |
| **Por quê** | Algumas Saídas da Gravadora têm `MACHINE = 'SEM AGIE'`, indicando que a tray foi marcada para sair da linha sem passar pelo EDM (desvio, descarte, etc.). |
| **Impacto no Real** | Trays desviadas não aparecem no buffer → não afetam a contagem de fila do AGIE. |

---

### F7 — Ghost timeout de 24 horas
| | |
|---|---|
| **Onde** | `fetch_machine_status` (active_candidates), `fetch_laser_status` (active_at_laser) |
| **SQL** | `WHERE ae.event_date >= DATEADD(HOUR, -24, GETDATE())` |
| **Por quê** | Se uma Entrada existir sem Saída por mais de 24 h, assumimos que a Saída não foi registrada (falha no leitor). Sem este filtro, a máquina sempre apareceria como ocupada. |
| **Impacto no Real** | Trays reais que ficam na máquina por mais de 24 h (fim de semana, setup longo) serão descartadas e a máquina aparecerá como IDLE. **Atenção: para o buffer este timeout NÃO existe** — trays no buffer podem esperar legitimamente mais de 24 h (fins de semana). |

---

### F8 — Current session por serial (ROW_NUMBER)
| | |
|---|---|
| **Onde** | `fetch_buffer_stock` (current_session_per_serial), `fetch_laser_status` (current_session_per_serial), `fetch_finished_goods` (last_slot) |
| **SQL** | `ROW_NUMBER() OVER (PARTITION BY SERIAL_NUMBER ORDER BY DATE DESC) AS rn … WHERE rn = 1` |
| **Por quê** | Um mesmo serial pode ser reutilizado em múltiplas passagens (re-queue). Só a passagem mais recente é relevante. |
| **Impacto no Real** | Se o mesmo número de serial foi usado em duas remessas diferentes (o que não deveria acontecer, mas pode), apenas a última entrada conta. |

---

### F9 — Ausência de evento posterior (NOT EXISTS)
| | |
|---|---|
| **Onde** | `fetch_machine_status` (active_candidates), `fetch_buffer_stock` (in_buffer), `fetch_laser_status` (active_at_laser) |
| **SQL** | `NOT EXISTS (SELECT 1 FROM b2b_machine_status s WHERE s.SERIAL_NUMBER = ae.serial_number AND s.QUANTITY <> 0 AND s.DATE > ae.event_date)` |
| **Por quê** | **Principal mecanismo anti-fantasma.** Qualquer evento posterior ao Entrada/Saída de referência (independente de stage) prova que a tray saiu. Captura tanto Saídas normais quanto passagens não-registradas. |
| **Impacto no Real** | Se um evento de saída tiver `QUANTITY = 0` (anulação?), não contará como "saída" → a tray ficará presa como fantasma. |

---

### F10 — Quantidade máxima de buffer (≤ 500 peças)
| | |
|---|---|
| **Onde** | `fetch_buffer_stock` |
| **SQL** | `AND cs.QUANTITY <= 500` |
| **Por quê** | Uma tray de laser normal nunca contém mais de 500 peças. Valores maiores são erros de lançamento (totais cumulativos). Se entrassem na simulação, ultrapassariam instantaneamente a capacidade do buffer. |
| **Impacto no Real** | Trays com quantidade errada não aparecem no buffer → podem gerar divergência entre físico e sistema se houver erros de lançamento recorrentes. |

---

### F11 — Current PN session por máquina
| | |
|---|---|
| **Onde** | `fetch_machine_status` (current_pn_per_machine + INNER JOIN) |
| **SQL** | Somente Entradas cujo `part_number_id` bate com o PN do **Entrada mais recente** daquela máquina são consideradas ativas. |
| **Por quê** | Se uma máquina processou PN_A e depois PN_B, as trays de PN_A que nunca tiveram Saída registrada são "fantasmas de PN diferente" e devem ser ignoradas. |
| **Impacto no Real** | Trays de rodadas antigas (PN diferente do atual) que não tiveram Saída registrada não "ocupam" a máquina. |

---

### F12 — Normalização de campos vazios/nulos
| | |
|---|---|
| **Onde** | `data_gathering.py` → `_normalize_optional_text` + verificação em `fetch_production_seed_rows` |
| **Python** | `if not machine_id or not part_number_id: continue` |
| **Por quê** | Campos `None`, `NaN`, `"nan"`, `"null"`, `""` são todos tratados como ausentes. |
| **Impacto no Real** | Eventos sem máquina ou PN identificado são silenciosamente descartados. |

---

### F13 — Máquina sem corridor AGIE reconhecido
| | |
|---|---|
| **Onde** | `data_gathering.py` → `_machine_to_line` usado em `fetch_production_seed_rows` (buffer rows) |
| **Python** | `if line_id == "Unknown": continue` |
| **Por quê** | O campo MACHINE da Saída da Gravadora é usado para deduzir o corredor AGIE de destino. Se o nome não seguir o padrão `"AGIE XX"`, não é possível mapear para Agie1–Agie5. |
| **Impacto no Real** | Trays com destino AGIE mal registrado (MACHINE = texto livre) não entram no buffer da simulação. |

---

### F14 — Elegibilidade do PN (LASER + EDM obrigatórios)
| | |
|---|---|
| **Onde** | `data_gathering.py` → `get_eligible_part_numbers`; consumido em `fetch_production_seed_rows` e `main.py` |
| **Python** | `if "LASER" in processes and "EDM" in processes` → PN elegível |
| **Por quê** | A simulação só faz sentido para PNs que passam pelos dois processos (Laser Marker + AGIE). PNs com restrição apenas em um dos processos seriam órfãos na simulação. |
| **Impacto no Real** | Trays de PNs que faltam restrição de LASER ou EDM na tabela `nozzle_restrictions` são descartadas em todos os estágios (máquina, buffer, laser status). |

---

### F15 — Máquina ausente em machine_specs (initial_state)
| | |
|---|---|
| **Onde** | `initial_state.py` → `build_production_seeded_initial_state` |
| **Python** | `spec = edm_machine_specs.get(machine_id); if spec is None: skipped["machine_not_in_specs"] += 1; continue` |
| **Por quê** | A simulação só conhece máquinas presentes em `machine_specs`. Se o DB reportar uma máquina que não está nas specs (e.g. AGIE 51 fictício), ela é ignorada. |

---

### F16 — Buffer: PN não elegível (initial_state)
| | |
|---|---|
| **Onde** | `initial_state.py` |
| **Python** | `if part_number_id not in eligible_parts: skipped["buffer_non_eligible"] += 1; continue` |
| **Por quê** | Tray no buffer físico cujo PN não tem caminho LASER+EDM seria incapaz de progredir na simulação. |

---

### F17 — Buffer: linha cheia (initial_state)
| | |
|---|---|
| **Onde** | `initial_state.py` |
| **Python** | `if len(edm_stock[line_id]) >= stock_capacity_trays: skipped["buffer_line_full"] += 1; continue` |
| **Por quê** | Cada corredor AGIE tem capacidade máxima de trays no buffer. Trays excedentes são descartadas para evitar estado inicial inválido. |
| **Impacto no Real** | Se o físico tiver mais trays no buffer do que `stock_capacity_trays`, as mais antigas (por ordem de chegada no loop) são perdidas. |

---

### F18 — Buffer: sem ordem aberta correspondente (initial_state)
| | |
|---|---|
| **Onde** | `initial_state.py` |
| **Python** | `match = _consume_order_tray(...); if not match: skipped["buffer_no_order"] += 1; continue` |
| **Por quê** | A tray está no buffer mas não existe ordem aberta no sistema para aquele PN. Não há como associar a tray a um objetivo de produção. |
| **Impacto no Real** | Trays de ordens já fechadas / PNs sem ordem ativa no DB não entram na simulação. |

---

### F19 — Ordens sem PN elegível (main.py)
| | |
|---|---|
| **Onde** | `main.py` |
| **Python** | `filtered_orders = [order for order in orders if order.part_number_id in eligible_parts]` |
| **Por quê** | Filtragem final antes de passar as ordens ao simulador. Qualquer ordem cujo PN não tenha LASER+EDM é removida. |

---

### F20 — Máquinas excluídas (--excluded-machines)
| | |
|---|---|
| **Onde** | `main.py` (parâmetro CLI / parâmetro de API `excluded_machines`) |
| **Python** | Remove entradas de `restrictions.allowed` onde `entry[2] in excluded_set` |
| **Por quê** | Máquinas em manutenção ou desligadas permanentemente podem ser passadas como parâmetro para a simulação. Elas aparecem no dashboard como `INACTIVE` e nunca recebem trabalho. |

---

### F21 — `fetch_orders`: só PNs na tabela de restrições
| | |
|---|---|
| **Onde** | `data_gathering.py` → `fetch_orders` |
| **SQL** | `WHERE MATERIAL IN (SELECT DISTINCT TYPE_NUMBER FROM silver_type_number.nozzle_restrictions)` |
| **Por quê** | O ERP (`silver_orders.orders_10digit`) contém ordens de *todos* os produtos Bosch. Só são importadas ordens de PNs que existem na tabela de restrições de nozzle (i.e., PNs de injetores que passam por AGIE/Laser). |
| **Impacto no Real** | PNs que existem no ERP mas não têm restrição cadastrada são completamente ignorados. |

---

## Tabela de Referência Rápida

| # | Filtro | Arquivo | Localização | Remove |
|---|---|---|---|---|
| F1 | Serial com letras minúsculas | data_gathering.py | SQL `NOT LIKE '%[a-z]%'` | Seriais de teste/rework |
| F2 | Quantidade zero | data_gathering.py | SQL `QUANTITY <> 0` | Eventos de cancelamento |
| F3 | Trusted window `2026-04-25` | data_gathering.py | SQL `DATE >= '2026-04-25'` | Dados históricos antes da migração |
| F4 | Estágio correto | data_gathering.py | SQL `STAGE = '03 - Agie'` etc. | Eventos de outros estágios |
| F5 | AGIE UNKNOWN / NULL | data_gathering.py | SQL `<> 'AGIE UNKNOWN'` | Leituras sem máquina identificada |
| F6 | SEM AGIE | data_gathering.py | SQL `NOT LIKE '%SEM AGIE%'` | Trays desviadas da linha |
| F7 | Ghost timeout 24 h | data_gathering.py | SQL `DATEADD(HOUR,-24,GETDATE())` | Entradas fantasmas sem saída |
| F8 | Current session (ROW_NUMBER) | data_gathering.py | SQL `rn = 1` | Passagens antigas do mesmo serial |
| F9 | NOT EXISTS evento posterior | data_gathering.py | SQL subquery `DATE > ae.event_date` | Trays que já saíram (saída registrada ou não) |
| F10 | Quantidade ≤ 500 (buffer) | data_gathering.py | SQL `QUANTITY <= 500` | Erros de lançamento de quantidade |
| F11 | Current PN session por máquina | data_gathering.py | SQL INNER JOIN | Trays de PN anterior sem saída |
| F12 | Campos vazios/nulos | data_gathering.py | Python `_normalize_optional_text` | Eventos sem máquina ou PN |
| F13 | Linha AGIE desconhecida | data_gathering.py | Python `_machine_to_line == 'Unknown'` | Buffer com destino não mapeado |
| F14 | PN sem LASER+EDM | data_gathering.py / main.py | Python `get_eligible_part_numbers` | PNs incompletos nas restrições |
| F15 | Máquina fora de machine_specs | initial_state.py | Python spec lookup | Máquinas desconhecidas no DB |
| F16 | Buffer PN não elegível | initial_state.py | Python `not in eligible_parts` | PNs sem LASER+EDM no buffer |
| F17 | Buffer linha cheia | initial_state.py | Python `>= stock_capacity_trays` | Excesso de trays no buffer |
| F18 | Buffer sem ordem aberta | initial_state.py | Python `_consume_order_tray` | Trays de ordens já fechadas |
| F19 | Ordens PN não elegível | main.py | Python `filtered_orders` | Ordens sem caminho LASER+EDM |
| F20 | Máquinas excluídas | main.py | Python `excluded_machines` param | Máquinas em manutenção |
| F21 | Ordens só para PNs com restrição | data_gathering.py | SQL `MATERIAL IN (SELECT TYPE_NUMBER ...)` | PNs sem restrição cadastrada |

---

## Consequências para o Cara-a-Crachá com o Real

Ao comparar o que a CAPI mostra com o que é observado fisicamente na linha, verifique:

1. **Serial tem letras minúsculas?** → F1 invisibiliza esse serial.
2. **Evento antes de 25/04/2026?** → F3 invisibiliza.
3. **Tray está na máquina há mais de 24 h sem saída?** → F7 a reportará como IDLE.
4. **PN não tem linha LASER ou linha EDM cadastrada em `nozzle_restrictions`?** → F14/F21 removem a tray do sistema inteiro.
5. **Saída da Gravadora com `MACHINE = 'SEM AGIE'` ou texto livre?** → F6/F13 descartam a tray do buffer.
6. **Quantidade da tray > 500?** → F10 descarta do buffer.
7. **Tem ordem aberta no ERP para esse PN?** → F18/F19 descartam se não tiver.
8. **Evento de saída lançado com `QUANTITY = 0`?** → F9 não o enxerga como "saída" → tray fica fantasma.
