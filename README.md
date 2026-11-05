# ☀️ Bom dia varejo — boletim no WhatsApp

Todo dia às ~6h40 (Brasília) um robô no GitHub busca as notícias do dia, a IA do Gemini checa os fatos no Google e redige no estilo Bom dia varejo, e o boletim chega **inteiro numa mensagem só** no seu WhatsApp e no grupo.

**O que vem no boletim**
- 🛒 Varejo e atacarejo (concorrentes, Mart Minas, Minas Gerais, setor)
- 📊 Economia que mexe com o varejo (inflação, juros, consumo, dólar)
- 🌎 Mundo
- 💡 Curiosidade do varejo
- 🔗 Link original de cada notícia (direto do portal, sem encurtador)
- Domingo: resumo da semana
- Nunca repete notícia nem curiosidade (fica registrado em `historico.json`)

**Envio:** Green-API (mensagem única, direto em grupos). Se ele falhar, o CallMeBot entra como reserva automática.

---

## Configuração

### 1. Gemini (redação + checagem)
1. Entre em **aistudio.google.com** → ícone de chave 🔑 → **Create API key**.
2. Guarde a chave.

### 2. Green-API (envio)
1. Crie a conta em **green-api.com** e entre no **console**.
2. Crie uma instância no plano **Developer** (grátis).
3. Escaneie o **QR Code** com o WhatsApp que vai enviar o boletim
   (WhatsApp → ⋮ / Configurações → **Aparelhos conectados** → Conectar aparelho).
   > Recomendado: usar um **segundo número** como "robô do Bom dia varejo" e adicioná-lo ao grupo.
4. Na página da instância, copie: **apiUrl**, **idInstance** e **apiTokenInstance**.
5. Ainda na instância, deixe um intervalo de envio (*Message sending delay*) de alguns segundos.

### 3. Secrets no GitHub
**Settings → Secrets and variables → Actions → aba Secrets → New repository secret**

| Nome | Valor |
|---|---|
| `GEMINI_API_KEY` | chave do passo 1 |
| `GREEN_API_URL` | apiUrl (ex.: `https://7105.api.greenapi.com`) |
| `GREEN_ID_INSTANCE` | idInstance |
| `GREEN_API_TOKEN` | apiTokenInstance |
| `WHATSAPP_DESTINOS` | quem recebe, separado por vírgula (ver passo 4) |

Reserva (opcional, se quiser manter o CallMeBot): `WHATSAPP_PHONE` e `CALLMEBOT_APIKEY`.

### 4. Descobrir o ID do grupo
1. **Actions → Bom dia varejo no WhatsApp → Run workflow**
2. Marque **"Só listar meus grupos e seus IDs"** e rode.
3. No log aparece cada grupo assim: `Nome do grupo → 120363000000000000@g.us`
4. Monte o secret `WHATSAPP_DESTINOS` com seu número e o ID do grupo:
   ```
   553199999999, 120363000000000000@g.us
   ```
   O número pode ser com ou sem o 9 extra: o script descobre o formato certo sozinho.

### 5. Testar
**Run workflow** → marque *Só testar* para ver só no log, ou desmarque para receber de verdade.

---

## Ajustes opcionais
**Settings → Secrets and variables → Actions → aba Variables**

| Variável | Efeito |
|---|---|
| `INCLUIR_LINKS` = `0` | tira os links das notícias |
| `GEMINI_MODEL` | força um modelo do Gemini (ex.: `gemini-3.8-flash`) |

- **Mudar horário:** linha `cron` em `.github/workflows/bom-dia-whatsapp.yml` (UTC = Brasília + 3h).
- **Mudar temas:** bloco `BUSCAS` no `noticias_whatsapp.py`.
- **Reenviar as notícias de hoje:** troque o conteúdo de `historico.json` por `{"noticias": [], "curiosidades": []}`.

## Plano grátis do Green-API
- 3 conversas (contatos ou grupos): ex.: você + o grupo.
- Envio de mensagens sem limite de quantidade.
- Não é API oficial da Meta (funciona como WhatsApp Web) — por isso o segundo número é recomendado.

## Se algo falhar
O log de cada execução fica em **Actions**. Linhas com `✓` deram certo; com `!` mostram o motivo do erro.

---
Criado por Guilherme Fonseca
