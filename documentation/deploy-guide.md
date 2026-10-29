# CAPI — Guia de Deploy e Manutenção

**Última atualização:** 2026-05-08  
**Servidor:** `CT0DEVTEFSRV01` (`10.21.157.22`)  
**Responsável inicial:** CtP Hackathon Team

---

## 1. Visão Geral da Arquitetura

```
Usuário (browser)
      │
      ▼
https://ct0devtefsrv01.br.bosch.com/CAPI          ← Frontend (IIS, porta 443)
      │
      ▼  (chamadas de API via JavaScript)
https://ct0devtefsrv01.br.bosch.com:48000          ← Backend (capi_api.exe, porta 48000)
      │
      ▼
SQL Server: 10.21.242.107 / AgieProductionPlan     ← Banco de dados
```

- **Frontend:** Aplicação React (Vite) — arquivos estáticos servidos pelo IIS
- **Backend:** FastAPI + uvicorn empacotado como `capi_api.exe` (PyInstaller)
- **Banco:** SQL Server acessado via pyodbc/SQLAlchemy

---

## 2. Estrutura de Diretórios no Servidor

### Frontend
```
D:\inetpub\Bosch\AplicationHTTPS\CAPI\
├── index.html
├── dashboard.json          ← fallback estático (sobrescrito pela API após simulação)
├── history.json            ← fallback estático
├── livedata.json           ← fallback estático
├── realtime.json           ← fallback estático
└── assets\
    ├── index-[hash].js
    ├── index-[hash].css
    └── Kapi_Icon-[hash].png
```

### Backend
```
D:\inetpub\Bosch\WebCoreAPIHTTPS\CAPI\
├── capi_api.exe            ← servidor da API (PyInstaller)
├── cert.pem                ← certificado SSL (exportado do IIS)
├── key.pem                 ← chave privada SSL
├── web.config              ← configuração IIS (proxy reverso, se necessário)
├── data\
│   ├── changeover_data.csv
│   ├── machines.csv
│   ├── orders.csv
│   └── restrictions.csv
├── outputs\                ← pasta obrigatória (pode estar vazia inicialmente)
│   ├── dashboard.json      ← gerado após simulação
│   ├── history.json
│   ├── livedata.json
│   ├── realtime.json
│   └── sim_ref_dt.json
└── src\
    ├── api.py
    ├── main.py
    ├── simulation.py
    ├── data_gathering.py
    ├── snapshot_accumulator.py
    └── .env                ← ⚠️ credenciais do banco (NÃO commitar no git)
```

---

## 3. Arquivo `.env` (Credenciais do Banco)

Localização: `D:\inetpub\Bosch\WebCoreAPIHTTPS\CAPI\src\.env`

```
SIM_DB_SERVER=10.21.242.107
SIM_DB_NAME=AgieProductionPlan
SIM_DB_USER=AgieProductionPlan
SIM_DB_PASSWORD=<senha>
SIM_DB_DRIVER=ODBC Driver 17 for SQL Server
```

> ⚠️ **Nunca commitar este arquivo no git.** Está listado no `.gitignore`.

---

## 4. Certificados SSL

O uvicorn precisa de certificado SSL para servir HTTPS na porta 48000.  
Os certificados foram exportados do IIS e convertidos para formato PEM.

| Arquivo | Descrição |
|---|---|
| `cert.pem` | Certificado público do domínio `ct0devtefsrv01.br.bosch.com` |
| `key.pem` | Chave privada correspondente |

**Para renovar os certificados (quando expirarem em 2028-05-07):**
1. No servidor, abrir **IIS Manager** (`inetmgr`)
2. Clicar no servidor → **Certificados do Servidor**
3. Botão direito no certificado → **Exportar** → salvar como `.pfx` com senha
4. Converter para PEM usando o script Python abaixo (rodar no PC de desenvolvimento):

```python
from cryptography.hazmat.primitives.serialization import pkcs12, Encoding, PrivateFormat, NoEncryption
from pathlib import Path

pfx_data = Path("CERT.pfx").read_bytes()
private_key, certificate, _ = pkcs12.load_key_and_certificates(pfx_data, b"<senha_do_pfx>")

Path("key.pem").write_bytes(private_key.private_bytes(Encoding.PEM, PrivateFormat.TraditionalOpenSSL, NoEncryption()))
Path("cert.pem").write_bytes(certificate.public_bytes(Encoding.PEM))
print(f"Certificado válido até: {certificate.not_valid_after_utc}")
```

5. Copiar `cert.pem` e `key.pem` para `D:\inetpub\Bosch\WebCoreAPIHTTPS\CAPI\`
6. Reiniciar o serviço/exe da API

---

## 5. Iniciando o Backend

### Comando manual (PowerShell no servidor)
```powershell
cd D:\inetpub\Bosch\WebCoreAPIHTTPS\CAPI
.\capi_api.exe --host 0.0.0.0 --port 48000 --ssl-certfile cert.pem --ssl-keyfile key.pem
```

### Como serviço Windows (recomendado — inicia automaticamente)
Rodar **uma única vez** como Administrador no servidor:
```powershell
New-Service -Name "CAPI_API" `
  -BinaryPathName "D:\inetpub\Bosch\WebCoreAPIHTTPS\CAPI\capi_api.exe --host 0.0.0.0 --port 48000 --ssl-certfile D:\inetpub\Bosch\WebCoreAPIHTTPS\CAPI\cert.pem --ssl-keyfile D:\inetpub\Bosch\WebCoreAPIHTTPS\CAPI\key.pem" `
  -DisplayName "CAPI API Server" `
  -StartupType Automatic

Start-Service -Name "CAPI_API"
```

**Comandos úteis para o serviço:**
```powershell
Get-Service -Name "CAPI_API"          # verificar status
Start-Service -Name "CAPI_API"        # iniciar
Stop-Service -Name "CAPI_API"         # parar
Restart-Service -Name "CAPI_API"      # reiniciar
Remove-Service -Name "CAPI_API"       # remover (para recriar)
```

---

## 6. Verificando se o Backend está Funcionando

Acessar no browser ou via curl:
```
https://ct0devtefsrv01.br.bosch.com:48000/api/health
```

Resposta esperada:
```json
{"status": "ok", "db_connected": true, "timestamp": "..."}
```

---

## 7. Acessando o Frontend

```
https://ct0devtefsrv01.br.bosch.com/CAPI
```

> ⚠️ **Importante:** Na primeira vez que acessar em um browser novo, é necessário aceitar o certificado SSL da porta 48000:
> 1. Acessar `https://ct0devtefsrv01.br.bosch.com:48000/api/health`
> 2. Clicar em **Avançado** → **Continuar mesmo assim** (Chrome) ou **Aceitar o risco** (Firefox)
> 3. Voltar para `https://ct0devtefsrv01.br.bosch.com/CAPI`
>
> Isso só precisa ser feito uma vez por browser/máquina.

---

## 8. Rebuild do Frontend (quando houver atualização de código)

Rodar no **PC de desenvolvimento** (`C:\Users\klj1ct\Desktop\CAPI\CtP-Hackathon`):

```powershell
# 1. Confirmar que o .env.local está correto
cat "Dashboard and Real-Time Playback\.env.local"
# Deve conter:
# VITE_SIMULATION_API_BASE_URL=https://ct0devtefsrv01.br.bosch.com:48000

# 2. Buildar
cd "Dashboard and Real-Time Playback"
npm install
npm run build

# 3. Copiar dist\ para o servidor
# Copiar todo o conteúdo de dist\ para \\10.21.157.22\inetpub\Bosch\AplicationHTTPS\CAPI\
```

---

## 9. Rebuild do Backend / `capi_api.exe` (quando houver atualização de código)

Rodar no **PC de desenvolvimento**:

```powershell
cd C:\Users\klj1ct\Desktop\CAPI\CtP-Hackathon
pyinstaller capi_api.spec --clean
```

O novo exe é gerado em `dist\capi_api.exe`.

Copiar para o servidor:
```powershell
copy dist\capi_api.exe \\10.21.157.22\inetpub\Bosch\WebCoreAPIHTTPS\CAPI\capi_api.exe
```

Depois reiniciar o serviço:
```powershell
# No servidor (PowerShell como Admin):
Restart-Service -Name "CAPI_API"
```

---

## 10. Comportamento Esperado no Primeiro Uso

Ao subir o servidor pela primeira vez (sem nenhuma simulação rodada):

```
[api] DB engine created successfully.
[api] History refresh background thread started (interval=1800s).
[snapshot] Skipping — sim_ref_dt.json not found (run a simulation first).
[api] Startup snapshot skipped (no sim_ref_dt or DB unavailable).
```

Isso é **normal** — o sistema aguarda a primeira simulação ser rodada pelo usuário via interface.

---

## 11. Processo de Atualização de Dados (automático)

| Evento | O que acontece |
|---|---|
| Usuário roda simulação | `outputs/` é atualizado com novos JSONs; linha "Real" inicial é populada |
| A cada 30 minutos | Background thread busca estado atual do DB e atualiza `history.json` (linha "Real") |
| Servidor reinicia | API sobe, lê último `sim_ref_dt.json`, reconstrói snapshot do último estado conhecido |

---

## 12. Troubleshooting

### API não responde
1. Verificar se o exe está rodando: `Get-Service -Name "CAPI_API"` (ou verificar processos)
2. Testar localmente no servidor: `curl https://localhost:48000/api/health`
3. Verificar firewall: porta 48000 TCP deve estar liberada

### Frontend mostra "Failed to fetch"
1. Verificar se o backend está rodando (item acima)
2. Verificar se o certificado foi aceito no browser (item 7)
3. Abrir DevTools (F12) → Console → ver qual URL está falhando

### Simulação não inicia / volta para estado inicial
1. Verificar logs no terminal do `capi_api.exe`
2. Checar se a pasta `outputs\` existe em `D:\inetpub\Bosch\WebCoreAPIHTTPS\CAPI\`
3. Checar conexão com o banco: `https://ct0devtefsrv01.br.bosch.com:48000/api/health` deve mostrar `"db_connected": true`

### Erro de pool de conexões (`QueuePool limit reached`)
Muitos usuários acessando `/api/livedata` ao mesmo tempo. O pool está configurado para 30 conexões + 50 overflow em `src/data_gathering.py`. Se persistir, aumentar `pool_size` e recompilar o exe.

### Certificado expirado (após 2028-05-07)
Seguir o processo do item 4 para renovar.

---

## 13. Contatos e Repositório

- **Repositório:** *(adicionar URL do git aqui)*
- **PC de desenvolvimento:** `C:\Users\klj1ct\Desktop\CAPI\CtP-Hackathon`
- **Servidor:** `CT0DEVTEFSRV01` — acesso via pasta de rede `\\10.21.157.22\inetpub\Bosch\`
