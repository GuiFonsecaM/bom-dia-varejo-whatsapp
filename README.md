# ☀️ Bom dia varejo — boletim no WhatsApp

Todo dia às ~6h40 (Brasília) um robô no GitHub busca as notícias do dia, a IA do Gemini redige no estilo Bom dia varejo e a mensagem chega no seu WhatsApp. Tudo grátis.

**O que vem no boletim**
- 🛒 Varejo e atacarejo (concorrentes, Mart Minas, Minas Gerais, setor)
- 📊 Economia que mexe com o varejo (inflação, juros, consumo, dólar)
- 🌎 Mundo
- 💡 Curiosidade do varejo
- Domingo: resumo da semana
- Nunca repete notícia nem curiosidade (fica registrado em `historico.json`)

---

## Configuração (uma vez só, ~10 min)

### 1. Ativar o CallMeBot (envio para o WhatsApp)
1. Adicione o número **+34 694 29 84 96** nos contatos do celular (confira o número atual em callmebot.com → WhatsApp API).
2. Mande para ele pelo WhatsApp: `I allow callmebot to send me messages`
3. Você recebe uma mensagem com a sua **APIKEY**. Guarde.
   *Se não chegar em 2 minutos, tente de novo em 24h.*

### 2. Pegar a chave grátis do Gemini
1. Entre em **aistudio.google.com** com sua conta Google.
2. Clique em **Get API key → Create API key**. Guarde.

### 3. Subir no GitHub
1. Crie um repositório novo (ex.: `bom-dia-varejo-whatsapp`), **privado**.
2. Suba todos os arquivos desta pasta, incluindo `.github/workflows/`.

### 4. Cadastrar os segredos
No repositório: **Settings → Secrets and variables → Actions → New repository secret**

| Nome | Valor |
|---|---|
| `GEMINI_API_KEY` | chave do passo 2 |
| `WHATSAPP_PHONE` | seu número, ex.: `+5531999999999` |
| `CALLMEBOT_APIKEY` | chave do passo 1 |

### 5. Testar
**Actions → Bom dia varejo no WhatsApp → Run workflow**
- Marque *Só testar* para ver a mensagem só no log.
- Desmarque para receber no WhatsApp na hora.

Pronto. A partir daí roda sozinho todo dia.

---

## Ajustes opcionais
Em **Settings → Secrets and variables → Actions → aba Variables**:

| Variável | Efeito |
|---|---|
| `INCLUIR_LINKS` = `1` | coloca o link de cada notícia |
| `GEMINI_MODEL` | força um modelo do Gemini (se o padrão sair do ar) |

- **Mudar horário:** edite a linha `cron` no arquivo `.github/workflows/bom-dia-whatsapp.yml` (horário em UTC = Brasília + 3h).
- **Mudar temas:** edite o bloco `BUSCAS` no `noticias_whatsapp.py`.

## Se algo falhar
- Se o Gemini estiver fora, o boletim sai mesmo assim, só com as manchetes.
- O log de cada dia fica em **Actions**, com a mensagem completa que foi gerada.

---
Criado por Guilherme Fonseca
