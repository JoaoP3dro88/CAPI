# Live Visualization — Documentação Técnica

> Última atualização: 2026-05-04

---

## 1. O que é

A **Live Visualization** é uma aba do frontend (`LiveVisualization.tsx`) que exibe o estado **em tempo real** do chão de fábrica, conectada diretamente ao banco de dados SQL Server via FastAPI. Diferente do Dashboard (playback de simulação), a Live View lê os dados transacionais reais e monta um snapshot do momento atual da produção.

---

## 2. Arquitetura geral

```
SQL Server (AgieProductionPlan)
        │
        ▼
src/data_gathering.py   ←── queries especializadas por estágio
        │
        ▼
src/api.py              ←── FastAPI, endpoint GET /api/livedata
        │                   cache em memória (5 min)
        ▼
src/livedata_export.py  ←── builders de payload JSON
        │
        ▼
Dashboard and Real-Time Playback/
  src/app/data.ts       ←── fetchLiveData(), tipos TypeScript
  src/app/components/
    LiveVisualization.tsx  ←── componente principal, 7 colunas
    ui/SettingsModal.tsx   ←── configurações persistidas no localStorage
```

---

## 3. Fluxo de dados por coluna

O layout é um grid de **14 colunas** (`min-w-[1400px]`), com cada seção ocupando `col-span-2`:

| # | Coluna | Fonte de dados | Função no backend |
|---|--------|----------------|-------------------|
| 1 | **Orders** (Active + Backlog) | `silver_orders.orders_10digit` | `fetch_orders()` |
| 2 | **Hard Stage Market** | `silver_production.inventory` | `fetch_hard_stage_market()` |
| 3 | **Laser Marking** | `silver_quality.b2b_machine_status` | `fetch_laser_status()` |
| 4 | **AGIE Entrance** (Buffer Stock) | `silver_quality.b2b_machine_status` | `fetch_buffer_stock()` |
| 5 | **EDM Lines** | `silver_quality.b2b_machine_status` + `gold.simulation_agie_process_time` | `fetch_machine_status()` + `fetch_machine_oee()` |
| 6 | **HE Market** + **Packaging Market** | `silver_quality.b2b_machine_status` + `silver_orders.orders_10digit` + *routing table (stub)* | `fetch_finished_goods()` + `fetch_destination_routing()` + `_build_final_markets()` |

---

## 4. Lógica de cada seção

### 4.1 Orders (coluna 1)

**Active Orders** — ordens cuja `due_date >= hoje`. Exibidas com borda azul (suficiente corpo no Hard Stage Market) ou vermelha (corpo insuficiente para a quantidade da ordem).

**Backlog Orders** — todas as demais ordens abertas, agrupadas por `due_date`, ordenadas da mais antiga para a mais recente.

**Fonte:** `silver_orders.orders_10digit` — **todas as linhas da tabela são ordens abertas**. Nenhum filtro adicional é aplicado.

**Relação com Hard Stage Market:** cada ordem busca o corpo correspondente em `hardStageMarketItems` pelo `nozzles[]` do corpo. Se `body_qtty < order.quantity`, a ordem aparece em vermelho.

---

### 4.2 Hard Stage Market (coluna 2)

Lista de corpos (`body_partnumber`) com estoque disponível no Hard Stage, ordenada pela **maior quantidade** primeiro.

Cada card exibe:
- `body_partnumber` + `body_qtty` (peças disponíveis)
- Lista de nozzles compatíveis

**Fonte:** `silver_production.inventory` → colunas `TYPE_NUMBER`, `BODY_TYPE_NUMBER`, `QUANTITY_HARDSTAGE`.

---

### 4.3 Laser Marking (coluna 3)

TypeNumbers cuja **última entrada no sistema é uma Entrada na Gravadora** (`STAGE = '02 - Gravadora'`, `STATE = 'Entrada'`). Isso garante que só aparecem trays que entraram na laser e ainda não saíram.

Por TypeNumber:
- `waiting_trays` — quantidade de trays em processamento
- `waiting_pieces` — peças totais

**Filtro de data:** `DATE >= '2026-04-01'`

---

### 4.4 AGIE Entrance / Buffer Stock (coluna 4)

Trays cuja **última entrada é uma Saída da Gravadora** (`STAGE = '02 - Gravadora'`, `STATE = 'Saída'`). Significa que saíram da laser, mas ainda não entraram em nenhuma AGIE.

Agrupadas por corredor (AGIE 1–10 → Agie1, etc.) com:
- Quantidade total de peças por corredor
- Breakdown por TypeNumber

**Filtro de data:** `DATE >= '2026-04-01'`

Ao clicar no card, abre o `StockDetailModal` com detalhes por bandeja.

---

### 4.5 EDM Lines (coluna 5)

Estado atual das **50 máquinas AGIE** (AGIE 01 a AGIE 50).

**Atividade determinada por:**
- `process` — tray com `Entrada` em `03 - Agie` sem `Saída` correspondente
- `inactive` — sem execução completa nas últimas `off_hours` horas
- `idle` — tray saiu, máquina aguardando próxima

**OEE real:** calculado a partir de pares `Entrada`/`Saída` completados dentro da janela `oee_hours`. Comparado ao tempo ideal (`gold.simulation_agie_process_time`).

**Seletor de corredor:** dropdown permite focar em um corredor específico (filtra também o Buffer Stock e o AGIE Entrance simultaneamente).

Ao clicar em um card de corredor no modo "All corridors", abre `ZoneDetailModal` com todas as máquinas do corredor.

---

### 4.6 HE Market + Packaging Market (coluna 6)

Coluna dividida verticalmente em duas metades — **HE Market** (cima) e **Packaging Market** (baixo).

**Fonte:** peças que tiveram `Saída` de `03 - Agie` e estão no supermarket de saída (`b2b_supermarket_status`).

**Roteamento HE vs. Packaging:** definido por `fetch_destination_routing()` → tabela de relação `part_number_id → destination`. Enquanto a tabela não existe, **todos os TypeNumbers vão para PACKAGING** (comportamento do stub).

**Lógica de conclusão de ordens (`_build_final_markets`):**
1. Agrupa peças por TypeNumber
2. Ordena as ordens abertas do TypeNumber da mais antiga para a mais nova
3. Vai "consumindo" as ordens: se `total_pieces >= order.quantity`, a ordem é removida e o excedente permanece no mercado
4. Exibe as ordens restantes com barra de progresso colorida (vermelho/amarelo/verde)

**Lógica de timeout:**
- Se o TypeNumber **não tem ordens abertas** e a bandeja mais antiga entrou no mercado há mais de `market_timeout_hours` horas → o TypeNumber some do mercado

Por card:
- TypeNumber + total de peças restantes
- Barra de progresso em direção à ordem mais antiga (com `order_id`, meta e peças faltantes)
- Lista compacta de ordens pendentes adicionais

---

## 5. Filtros globais implementados

### 5.1 Filtro de data
Todas as queries que determinam **localização atual** de uma bandeja usam `DATE >= '2026-04-01'`. Isso exclui registros anteriores ao início do controle formal.

Funções afetadas:
- `fetch_laser_status()` — CTE `last_event`
- `fetch_buffer_stock()` — CTE `last_event`
- `fetch_finished_goods()` — CTE `last_machine_event`
- `fetch_machine_status()` — CTE `already_out`
- `fetch_produced_today()` — WHERE direto

**NÃO filtrado:** `fetch_machine_oee()` — usa janela própria de `oee_hours` horas para trás a partir de agora.

### 5.2 Barra de pesquisa global
Campo no header do Live Status Bar. Filtra em tempo real (client-side via `useMemo`) todas as seções simultaneamente:

| Seção | Campo pesquisado |
|-------|-----------------|
| EDM Lines | `machine_id`, `part_number_id`, `serial_number`, `line_id` |
| Laser Marking | `part_number_id` |
| Hard Stage Market | `body_partnumber`, `nozzles[]` |
| Active Orders | `order_id`, `part_number_id` |
| Backlog Orders | `order_id`, `part_number_id` |
| Buffer Stock | `part_number_id` (por corredor; corredor sem match é ocultado) |

Badge com contagem total de resultados. Botão ✕ para limpar.

---

## 6. Configurações persistidas (`SettingsModal`)

Salvas em `localStorage` com chave `live_settings_v1`.

| Campo | Padrão | Descrição |
|-------|--------|-----------|
| `oeeHours` | 168h | Janela para cálculo de OEE (execuções completas) |
| `offHours` | 168h | Máquinas sem execução neste período aparecem como OFF |
| `marketTimeoutHours` | 24h | Horas para ocultar TypeNumber sem ordens do mercado final |
| `manualOff` | `[]` | Lista de `machine_id` forçados para OFF manualmente |

---

## 7. Como iniciar

```powershell
# Na raiz do repositório
cd "c:\Users\klj1ct\Desktop\CAPI\CtP-Hackathon"

# 1. Iniciar a API (backend)
C:/Users/klj1ct/AppData/Local/Programs/Python/Python312/python.exe `
  -m uvicorn src.api:app --host 127.0.0.1 --port 8001

# 2. Em outro terminal, iniciar o frontend
cd "Dashboard and Real-Time Playback"
npm run dev
```

O frontend conecta automaticamente em `http://127.0.0.1:8001/api/livedata` se `VITE_LIVEDATA_JSON_URL` estiver configurado, ou lê `public/livedata.json` como fallback.

**Variável de ambiente para apontar para a API:**
```
VITE_LIVEDATA_JSON_URL=http://127.0.0.1:8001/api/livedata
```

---

## 8. O que falta implementar

### 8.1 ⚠️ Roteamento HE vs. Packaging — STUB

**Arquivo:** `src/data_gathering.py` → função `fetch_destination_routing(engine)`

**Status:** retorna DataFrame vazio. Todos os TypeNumbers vão para **PACKAGING** por padrão.

**O que fazer:** substituir a query stub pela query real quando a tabela de relação estiver disponível no banco:

```python
# Substituir dentro de fetch_destination_routing():
query = """
SELECT
    TYPE_NUMBER   AS part_number_id,
    DESTINATION   AS destination     -- 'HE' ou 'PACKAGING'
FROM <schema>.<tabela_de_destino>
"""
```

A função `_build_final_markets()` já está completa e consumirá o `routing_map` automaticamente.

---

### 8.2 Pesquisa global — HE Market e Packaging Market não filtrados

**Arquivo:** `LiveVisualization.tsx`

**Status:** `heMarket` e `packagingMarket` não têm variantes `filteredHeMarket` / `filteredPackagingMarket` no `useMemo` de pesquisa. O badge de contagem também não os inclui.

**O que fazer:** adicionar dois `useMemo` de filtro por `part_number_id` e incluir os counts em `searchMatchCount`.

---

### 8.3 AGIE EXIT legado ainda presente no backend

**Arquivo:** `src/data_gathering.py` → `fetch_current_state()`

**Status:** o campo `finished_goods` (agrupamento por corredor, legado) ainda é retornado no snapshot. Ele foi substituído visualmente pelas colunas HE/Packaging, mas continua sendo calculado e enviado na API, consumindo recursos.

**O que fazer:** após validar o funcionamento dos mercados finais em produção, remover `fetch_finished_goods()` do `fetch_current_state()` e o campo `finished_goods` do payload.

---

### 8.4 Orders — separação de Active vs. Backlog

**Arquivo:** `src/data_gathering.py` + `LiveVisualization.tsx`

**Status:** `Active Orders` filtra ordens com `due_date >= hoje` (client-side). Ordens sem `due_date` aparecem como Active por padrão.

**O que fazer:** validar com o negócio se a distinção "Active = due_date hoje ou futuro próximo" está correta, ou se deve ser baseada em outro campo (status de produção, prioridade etc.).

---

### 8.5 Timeout do mercado final — sem feedback visual de quando vai expirar

**Status:** TypeNumbers próximos do timeout desaparecem sem aviso.

**O que fazer:** adicionar indicador visual (ex: badge amarelo "expira em Xh") quando `now - oldest_entry_date > 0.75 * market_timeout_hours`.

---

### 8.6 Sem paginação nos mercados finais

**Status:** `FinalMarketColumn` usa scroll simples. Com muitos TypeNumbers pode ficar lento.

**O que fazer:** usar `VirtualList` (já disponível no projeto) quando o número de itens ultrapassar ~50.
