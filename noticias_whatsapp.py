"""
Bom dia varejo — boletim diário no WhatsApp
Criado por Guilherme Fonseca

Fluxo:
 1. Busca as manchetes do dia no Google News (RSS, grátis)
 2. Tira tudo que já foi enviado antes (historico.json)
 3. A IA gratuita do Gemini escolhe e redige no estilo Bom dia varejo
 4. Envia pelo Green-API (mensagem única, número e/ou grupo);
    se falhar, usa o CallMeBot como reserva

Variáveis de ambiente (GitHub Secrets):
  GEMINI_API_KEY      chave grátis do Google AI Studio
  GREEN_API_URL       apiUrl da instância (console do Green-API)
  GREEN_ID_INSTANCE   idInstance
  GREEN_API_TOKEN     apiTokenInstance
  WHATSAPP_DESTINOS   números e/ou IDs de grupo separados por vírgula
                      ex.: 553199999999, 120363000000000000@g.us
Reserva (CallMeBot):
  WHATSAPP_PHONE      seu número, ex.: +553199999999
  CALLMEBOT_APIKEY    chave recebida do CallMeBot
Opcionais:
  GEMINI_MODEL        força um modelo específico do Gemini
  INCLUIR_LINKS=0     tira os links das notícias
  DRY_RUN=1           só mostra a mensagem no log, não envia
  LISTAR_GRUPOS=1     só lista os grupos e seus IDs
"""

import datetime as dt
import html
import json
import os
import re
import sys
import time
import unicodedata
from pathlib import Path
from urllib.parse import quote_plus

import feedparser
import requests

# --------------------------------------------------------------------------
# Configuração
# --------------------------------------------------------------------------
BASE = Path(__file__).resolve().parent
HISTORICO = BASE / "historico.json"
FUSO = dt.timezone(dt.timedelta(hours=-3))  # Brasília
AGORA = dt.datetime.now(FUSO)
DOMINGO = AGORA.weekday() == 6

JANELA_HORAS = 7 * 24 if DOMINGO else 30   # domingo = resumo da semana
MAX_POR_SECAO = {"varejo": 6, "economia": 4, "mundo": 3}
INCLUIR_LINKS = (os.getenv("INCLUIR_LINKS") or "1").strip() != "0"   # links ligados por padrão
LIMITE_MSG = 650  # o CallMeBot corta mensagens a partir de ~750 caracteres
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"

MODELOS_GEMINI = [m for m in [
    os.getenv("GEMINI_MODEL"),
    "gemini-flash-latest",
    "gemini-3-flash-preview",
    "gemini-flash-lite-latest",
    "gemini-3.1-flash-lite",
] if m]

GN = "https://news.google.com/rss/search?q={q}&hl=pt-BR&gl=BR&ceid=BR:pt-419"
JANELA_GN = "when:7d" if DOMINGO else "when:1d"

BUSCAS = {
    "varejo": [
        "varejo OR supermercado OR atacarejo",
        'Atacadão OR Assaí OR Carrefour OR "Grupo Mateus" OR "Pão de Açúcar"',
        '"Mart Minas" OR "Supermercados BH" OR "DMA Distribuidora" OR "Verdemar"',
        "Abras supermercados OR ABAD atacado distribuidor",
        "varejo Minas Gerais",
    ],
    "economia": [
        "inflação alimentos IPCA",
        "Selic Copom juros consumo",
        "IBGE vendas no varejo",
        "dólar preço alimentos commodities",
        "consumo das famílias crédito inadimplência",
    ],
    "mundo": [],
}
FEED_MUNDO = "https://news.google.com/rss/headlines/section/topic/WORLD?hl=pt-BR&gl=BR&ceid=BR:pt-419"


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------
def normaliza(texto: str) -> str:
    t = unicodedata.normalize("NFKD", texto.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]", "", t).strip()


def carrega_historico() -> dict:
    if HISTORICO.exists():
        try:
            return json.loads(HISTORICO.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"noticias": [], "curiosidades": []}


def salva_historico(hist: dict) -> None:
    limite = (AGORA - dt.timedelta(days=45)).date().isoformat()
    hist["noticias"] = [n for n in hist["noticias"] if n.get("data", "") >= limite][-800:]
    hist["curiosidades"] = hist["curiosidades"][-365:]
    HISTORICO.write_text(json.dumps(hist, ensure_ascii=False, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------
# 1. Coleta
# --------------------------------------------------------------------------
def le_feed(url: str):
    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0 (BomDiaVarejoBot)"})
        r.raise_for_status()
        return feedparser.parse(r.content).entries
    except Exception as e:
        print(f"  ! falha no feed: {e}")
        return []


def coleta() -> list[dict]:
    limite = AGORA - dt.timedelta(hours=JANELA_HORAS)
    itens, vistos = [], set()

    fontes = [(sec, GN.format(q=quote_plus(f"{q} {JANELA_GN}"))) for sec, qs in BUSCAS.items() for q in qs]
    fontes.append(("mundo", FEED_MUNDO))

    for secao, url in fontes:
        for e in le_feed(url)[:25]:
            titulo_bruto = html.unescape(e.get("title", "")).strip()
            fonte = (e.get("source") or {}).get("title", "")
            titulo = titulo_bruto
            if fonte and titulo.endswith(f" - {fonte}"):
                titulo = titulo[: -len(f" - {fonte}")]
            elif " - " in titulo:
                titulo, fonte = titulo.rsplit(" - ", 1)

            pub = e.get("published_parsed")
            if pub:
                quando = dt.datetime(*pub[:6], tzinfo=dt.timezone.utc)
                if quando < limite:
                    continue

            chave = normaliza(titulo)[:80]
            if not chave or chave in vistos:
                continue
            vistos.add(chave)
            itens.append({
                "secao": secao,
                "titulo": titulo.strip(),
                "fonte": fonte.strip(),
                "link": e.get("link", ""),
                "resumo": re.sub(r"<[^>]+>", " ", html.unescape(e.get("summary", "")))[:300],
            })
    return itens


def remove_repetidas(itens: list[dict], hist: dict) -> list[dict]:
    links = {n["link"] for n in hist["noticias"]}
    titulos = {normaliza(n["titulo"])[:80] for n in hist["noticias"]}
    return [i for i in itens if i["link"] not in links and normaliza(i["titulo"])[:80] not in titulos]


# --------------------------------------------------------------------------
# 2. Redação com Gemini
# --------------------------------------------------------------------------
INSTRUCOES = """Você é o editor do "Bom dia varejo", boletim matinal sobre varejo, atacarejo e supermercados.
Escreva em português do Brasil, tom jornalístico, direto e com credibilidade.

Regras de estilo (obrigatórias):
- Títulos são manchetes informativas, estilo portal de notícias. Nada de frases de efeito, trocadilhos ou clickbait.
- Cada notícia é detalhada, para o leitor entender o assunto sem abrir o link:
  • "resumo": parágrafo de 2 a 3 frases no estilo lead jornalístico (o quê, quem, quando, onde e por quê).
  • "topicos": 3 a 5 tópicos (até 30 palavras cada) com números, contexto, comparações (ano anterior,
    concorrentes, média do setor), causas e próximos passos. Não repita o que já está no resumo.
- Não escreva "Para o varejo" nem seções de impacto ou análise separadas.
- Tópicos sem numeração e sem marcador no início do texto (o marcador é colocado depois).
- Os tópicos são texto puro, sem numeração (nada de "1.", "2)") e sem marcador no início — o marcador é colocado depois.
- Nunca use a expressão "leitura editorial".
- Não invente fatos nem números: use só o que está nas manchetes fornecidas ou o que foi confirmado em pesquisa.
- Junte em um item só notícias diferentes sobre o mesmo fato.
- Ignore notícias que repetem assuntos da lista "JÁ ENVIADOS".
- Priorize: concorrentes (Atacadão, Assaí, Carrefour, Grupo Mateus, Pão de Açúcar, Supermercados BH, DMA), Mart Minas, Minas Gerais, preços de alimentos, juros, consumo e comportamento do consumidor.
- Na seção "mundo", escolha os fatos internacionais mais relevantes do dia (não precisam ser de varejo).
- Crie uma curiosidade verdadeira e verificável sobre varejo/atacarejo/supermercados, diferente das já usadas,
  com 3 a 4 frases (o fato, o contexto e por que importa hoje).
{extra}

Responda SOMENTE com JSON neste formato:
{{
  "abertura": "1 a 2 frases neutras resumindo os principais assuntos do dia",
  "varejo":   [{{"ids": [1, 4], "titulo": "...", "resumo": "...", "topicos": ["...", "...", "..."]}}],
  "economia": [{{"ids": [7],    "titulo": "...", "resumo": "...", "topicos": ["...", "...", "..."]}}],
  "mundo":    [{{"ids": [12],   "titulo": "...", "resumo": "...", "topicos": ["...", "...", "..."]}}],
  "curiosidade": {{"titulo": "...", "texto": "3 a 4 frases"}}
}}
Limites: até {mv} itens em varejo, {me} em economia, {mm} em mundo. Use apenas ids da lista."""


def extrai_json(texto: str) -> dict:
    texto = re.sub(r"```(json)?", "", texto).strip()
    ini, fim = texto.find("{"), texto.rfind("}")
    return json.loads(texto[ini:fim + 1])


def chama_gemini(prompt: str) -> dict | None:
    chave = os.getenv("GEMINI_API_KEY")
    if not chave:
        print("  ! GEMINI_API_KEY ausente — usando modo simples")
        return None

    # 1º tenta COM Busca Google (checa os fatos); se não der, tenta sem.
    modos = [
        ("com busca Google", {
            "contents": [{"role": "user", "parts": [{"text": prompt + CHECAGEM}]}],
            "tools": [{"google_search": {}}],
            "generationConfig": {"temperature": 0.2},
        }),
        ("sem busca", {
            "contents": [{"role": "user", "parts": [{"text": prompt + SEM_BUSCA}]}],
            "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
        }),
    ]
    for nome_modo, corpo in modos:
        for modelo in MODELOS_GEMINI:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
            for tentativa in range(3):
                try:
                    r = requests.post(url, headers={"x-goog-api-key": chave}, json=corpo, timeout=180)
                    if r.status_code in (401, 403):
                        print(f"  ! chave recusada ({r.status_code}): {r.text[:200]}")
                        return None
                    if r.status_code in (400, 404):
                        print(f"  ! {modelo} ({nome_modo}) indisponível: {r.status_code}")
                        break
                    if r.status_code in (429, 500, 503):
                        time.sleep(15 * (tentativa + 1))
                        continue
                    r.raise_for_status()
                    partes = r.json()["candidates"][0]["content"]["parts"]
                    texto = "".join(p.get("text", "") for p in partes)
                    dados = extrai_json(texto)
                    print(f"  ✓ redigido com {modelo} ({nome_modo})")
                    return dados
                except Exception as e:
                    print(f"  ! {modelo} ({nome_modo}) tentativa {tentativa + 1}: {e}")
                    time.sleep(5)
    return None


CHECAGEM = """

CHECAGEM DE FATOS (obrigatória):
- Antes de escrever cada notícia, pesquise no Google e confirme os fatos, números e datas.
- Os tópicos devem vir do que a busca confirmou, não de memória.
- Se não conseguir confirmar uma notícia, descarte-a.
- Confirme também a curiosidade.
Responda apenas com o JSON, sem texto antes ou depois."""

SEM_BUSCA = """

ATENÇÃO: hoje você está sem acesso à pesquisa. Use apenas o que está nas manchetes fornecidas.
Se não houver informação suficiente, escreva um resumo curto e menos tópicos — nunca complete de memória."""


def redige(itens: list[dict], hist: dict) -> dict | None:
    lista = "\n".join(
        f'[{n}] ({i["secao"]}) {i["titulo"]} — {i["fonte"]} | {i["resumo"][:180]}'
        for n, i in enumerate(itens, 1)
    )
    ja = "\n".join(f"- {n['titulo']}" for n in hist["noticias"][-80:]) or "(nenhum)"
    curios = "\n".join(f"- {c}" for c in hist["curiosidades"][-60:]) or "(nenhuma)"
    extra = ("- HOJE É DOMINGO: faça o RESUMO DA SEMANA, com os fatos mais importantes dos últimos 7 dias."
             if DOMINGO else "- Use apenas notícias de hoje ou de ontem.")
    prompt = (
        INSTRUCOES.format(extra=extra, mv=MAX_POR_SECAO["varejo"], me=MAX_POR_SECAO["economia"],
                          mm=MAX_POR_SECAO["mundo"])
        + f"\n\nMANCHETES DISPONÍVEIS:\n{lista}\n\nJÁ ENVIADOS:\n{ja}\n\nCURIOSIDADES JÁ USADAS:\n{curios}"
    )
    return chama_gemini(prompt)


def modo_simples(itens: list[dict]) -> dict:
    """Plano B se o Gemini falhar: manchetes diretas, sem redação da IA."""
    out = {"abertura": "", "curiosidade": None}
    for sec, mx in MAX_POR_SECAO.items():
        out[sec] = [{"ids": [n], "titulo": i["titulo"], "topicos": []}
                    for n, i in enumerate(itens, 1) if i["secao"] == sec][:mx]
    return out


# --------------------------------------------------------------------------
# 3. Formatação WhatsApp
# --------------------------------------------------------------------------
DIAS = ["Segunda-feira", "Terça-feira", "Quarta-feira", "Quinta-feira", "Sexta-feira", "Sábado", "Domingo"]
SECOES = [
    ("varejo", "🛒 *VAREJO E ATACAREJO*"),
    ("economia", "📊 *ECONOMIA QUE MEXE COM O VAREJO*"),
    ("mundo", "🌎 *MUNDO*"),
]


def monta_blocos(boletim: dict, itens: list[dict]) -> list[str]:
    """Devolve a mensagem em blocos (cada bloco = uma notícia ou um cabeçalho)."""
    cab = "☀️ *Bom dia varejo*"
    if DOMINGO:
        cab += "\n📅 _Resumo da semana_"
    cab += f"\n_{DIAS[AGORA.weekday()]}, {AGORA:%d/%m/%Y}_"
    if boletim.get("abertura"):
        cab += f"\n\n{boletim['abertura'].strip()}"
    blocos = [cab]

    for chave, titulo_secao in SECOES:
        noticias = boletim.get(chave) or []
        if not noticias:
            continue
        cabecalho = titulo_secao  # vai grudado na 1ª notícia, para não ficar sozinho
        for n in noticias:
            ids = [i for i in n.get("ids", []) if isinstance(i, int) and 1 <= i <= len(itens)]
            fontes = sorted({itens[i - 1]["fonte"] for i in ids if itens[i - 1]["fonte"]})
            txt = f"🟠 *{n['titulo'].strip()}*"
            if (n.get("resumo") or "").strip():
                txt += f"\n{n['resumo'].strip()}"
            # tira numeração/marcador que a IA às vezes coloca ("1.", "2)", "-", "•")
            # ("1. x" sai; "1º trimestre", "1.500 lojas" e "2026 foi" ficam intactos)
            topicos = [re.sub(r"^\s*(?:\d{1,2}\s*[.):\-–]\s+|[-•*–·▪●]\s*)", "", t).strip()
                       for t in n.get("topicos", []) if t and t.strip()]
            topicos = [t for t in topicos if t and not re.match(r"^para o varejo", t, re.I)]
            if topicos:
                txt += "\n" + "".join(f"\n• {t}" for t in topicos)
            if fontes:
                txt += f"\n\n_Fonte: {', '.join(fontes[:2])}_"
            if INCLUIR_LINKS and ids:
                link = link_original(itens[ids[0] - 1]["link"])
                if link:
                    txt += f"\n🔗 {link}"
            if cabecalho:
                txt, cabecalho = f"{cabecalho}\n\n{txt}", ""
            blocos.append(txt)

    cur = boletim.get("curiosidade")
    if cur and cur.get("titulo"):
        blocos.append(f"💡 *CURIOSIDADE DO VAREJO*\n*{cur['titulo'].strip()}*\n{cur.get('texto', '').strip()}")

    blocos.append("#BomDiaVarejo")
    return blocos


RASTREIO = re.compile(r"^(utm_|fbclid|gclid|ocid|cmpid|ref$|xtor|at_|origin$|source$)", re.I)


def limpa_url(url: str) -> str:
    """Tira parâmetros de rastreamento (utm_ etc.) e o que vem depois de #."""
    base, _, query = url.split("#")[0].partition("?")
    params = [p for p in query.split("&") if p and not RASTREIO.match(p.split("=")[0])]
    return base + ("?" + "&".join(params) if params else "")


def link_original(url: str) -> str | None:
    """Troca o link do Google News pelo link do próprio portal da notícia."""
    if not url:
        return None
    if "news.google.com" not in url:
        return limpa_url(url)
    try:
        from googlenewsdecoder import gnewsdecoder
        r = gnewsdecoder(url, interval=1)
        if (r.get("success") or r.get("status")) and r.get("decoded_url", "").startswith("http"):
            return limpa_url(r["decoded_url"])
        print(f"  ! link original não encontrado: {str(r.get('message', ''))[:80]}")
    except Exception as e:
        print(f"  ! erro ao buscar link original: {e}")
    return None  # sem link é melhor que um link gigante do Google


def quebra_bloco(bloco: str, limite: int) -> list[str]:
    """Se um bloco sozinho passar do limite, divide por linhas."""
    if len(bloco) <= limite:
        return [bloco]
    partes, atual = [], ""
    for linha in bloco.split("\n"):
        while len(linha) > limite:          # linha gigante: corta por palavra
            corte = linha.rfind(" ", 0, limite)
            corte = corte if corte > 0 else limite
            if atual:
                partes.append(atual)
                atual = ""
            partes.append(linha[:corte])
            linha = linha[corte:].lstrip()
        if atual and len(atual) + len(linha) + 1 > limite:
            partes.append(atual)
            atual = linha
        else:
            atual = f"{atual}\n{linha}" if atual else linha
    if atual:
        partes.append(atual)
    return partes


def agrupa(blocos: list[str], limite: int = LIMITE_MSG) -> list[str]:
    """Junta blocos em mensagens que caibam no envio do CallMeBot, sem cortar notícia."""
    msgs, atual = [], ""
    for bloco in blocos:
        for b in quebra_bloco(bloco, limite):
            if atual and len(atual) + len(b) + 2 > limite:
                msgs.append(atual)
                atual = b
            else:
                atual = f"{atual}\n\n{b}" if atual else b
    if atual:
        msgs.append(atual)
    return msgs


# --------------------------------------------------------------------------
# 4. Envio
# --------------------------------------------------------------------------
ERROS_CALLMEBOT = ("apikey is invalid", "invalid apikey", "not valid", "you need to get", "not activated",
                   "error", "blocked", "too many")
_NUMERO_OK: str | None = None  # versão do número que funcionou nesta execução


def limpa_numero(bruto: str) -> str:
    digitos = re.sub(r"\D", "", bruto)
    if not digitos.startswith("55") and len(digitos) in (10, 11):
        digitos = "55" + digitos  # digitou só DDD + número
    return "+" + digitos


def variantes_numero(phone: str) -> list[str]:
    """Números do Brasil podem estar no WhatsApp com ou sem o 9 extra."""
    d = phone.lstrip("+")
    vs = [phone]
    if d.startswith("55") and len(d) == 13 and d[4] == "9":
        vs.append("+" + d[:4] + d[5:])          # tira o 9
    elif d.startswith("55") and len(d) == 12:
        vs.append("+" + d[:4] + "9" + d[4:])    # põe o 9
    return vs


def mascara(phone: str) -> str:
    return f"{phone[:5]}•••••{phone[-4:]} ({len(phone) - 1} dígitos)"


def _tenta_envio(phone: str, key: str, texto: str) -> tuple[bool, bool]:
    """Devolve (enviou, chave_ou_numero_invalido)."""
    for _ in range(3):
        try:
            r = requests.get("https://api.callmebot.com/whatsapp.php",
                             params={"phone": phone, "text": texto, "apikey": key}, timeout=60)
            resposta = re.sub(r"\s+", " ", re.sub("<[^>]+>", " ", r.text))
            # A página ecoa o texto enviado; o status do envio vem depois dele
            final = resposta.split("Text to send:")[-1][-300:].lower()
            if 200 <= r.status_code < 300 and not any(e in final for e in ERROS_CALLMEBOT):
                print(f"  ✓ CallMeBot ({r.status_code}) para {mascara(phone)}: {final[-110:].strip()}")
                return True, False
            print(f"  ! CallMeBot ({r.status_code}) para {mascara(phone)}: {final[-110:].strip()}")
            if 200 <= r.status_code < 300:
                return False, True  # erro de chave/número: repetir não adianta
        except Exception as e:
            print(f"  ! erro no envio: {e}")
        time.sleep(20)
    return False, False


def envia_whatsapp(texto: str) -> bool:
    global _NUMERO_OK
    bruto_phone = os.getenv("WHATSAPP_PHONE", "")
    bruto_key = os.getenv("CALLMEBOT_APIKEY", "")
    phone = limpa_numero(bruto_phone)
    key = re.sub(r"[^A-Za-z0-9]", "", bruto_key)
    if len(phone) < 12 or not key:
        print(f"  ! WHATSAPP_PHONE: {'VAZIO' if not bruto_phone.strip() else f'{len(phone) - 1} dígitos'}"
              f" | CALLMEBOT_APIKEY: {'VAZIO' if not bruto_key.strip() else f'{len(key)} caracteres'}")
        return False

    for numero in ([_NUMERO_OK] if _NUMERO_OK else variantes_numero(phone)):
        enviou, invalido = _tenta_envio(numero, key, texto)
        if enviou:
            if numero != phone and _NUMERO_OK is None:
                print(f"  ℹ funcionou com {mascara(numero)} — use esse formato no secret WHATSAPP_PHONE")
            _NUMERO_OK = numero
            return True
        if not invalido:
            return False
    print(f"  ℹ chave com {len(key)} dígitos; número testado: "
          + " e ".join(mascara(n) for n in variantes_numero(phone)))
    return False


# --------------------------------------------------------------------------
# 4b. Envio pelo Green-API (mensagem única, direto em grupos)
# --------------------------------------------------------------------------
GREEN_URL = (os.getenv("GREEN_API_URL") or "https://api.green-api.com").strip().rstrip("/")
GREEN_ID = re.sub(r"\D", "", os.getenv("GREEN_ID_INSTANCE", ""))
GREEN_TOKEN = os.getenv("GREEN_API_TOKEN", "").strip()
USA_GREEN = bool(GREEN_ID and GREEN_TOKEN)
LIMITE_GREEN = 15000  # o Green-API aceita até 20 mil caracteres por mensagem


def green(metodo: str, corpo: dict | None = None) -> requests.Response:
    url = f"{GREEN_URL}/waInstance{GREEN_ID}/{metodo}/{GREEN_TOKEN}"
    if corpo is None:
        return requests.get(url, timeout=60)
    return requests.post(url, json=corpo, timeout=60)


def mascara_chat(chat_id: str) -> str:
    if chat_id.endswith("@g.us"):
        return f"grupo …{chat_id.split('@')[0][-6:]}"
    return mascara("+" + chat_id.split("@")[0])


def resolve_numero_green(numero: str) -> str:
    """Descobre o formato do número no WhatsApp (com ou sem o 9 extra)."""
    for variante in variantes_numero(limpa_numero(numero)):
        digitos = variante.lstrip("+")
        try:
            r = green("checkWhatsapp", {"phoneNumber": int(digitos)})
            if r.status_code == 200 and r.json().get("existsWhatsapp"):
                return f"{digitos}@c.us"
        except Exception:
            break  # cota da checagem esgotada ou instabilidade: usa como está
    return f"{limpa_numero(numero).lstrip('+')}@c.us"


def destinos_green() -> list[str]:
    """WHATSAPP_DESTINOS: números e/ou IDs de grupo separados por vírgula."""
    bruto = os.getenv("WHATSAPP_DESTINOS") or os.getenv("WHATSAPP_PHONE", "")
    destinos = []
    for d in re.split(r"[,;\n]+", bruto):
        d = d.strip()
        if not d:
            continue
        destinos.append(d if "@" in d else resolve_numero_green(d))
    return destinos


def estado_green() -> str:
    try:
        return green("getStateInstance").json().get("stateInstance", "?")
    except Exception as e:
        return f"erro: {e}"


def envia_green(chat_id: str, texto: str) -> bool:
    corpo = {"chatId": chat_id, "message": texto, "linkPreview": False}
    for tentativa in range(3):
        try:
            r = green("sendMessage", corpo)
            if r.status_code == 200 and "idMessage" in r.text:
                print(f"  ✓ Green-API para {mascara_chat(chat_id)}")
                return True
            print(f"  ! Green-API ({r.status_code}) para {mascara_chat(chat_id)}: {r.text[:200]}")
            if r.status_code in (400, 401, 403, 466):
                return False  # erro de configuração ou cota: repetir não adianta
        except Exception as e:
            print(f"  ! erro no Green-API: {e}")
        time.sleep(15 * (tentativa + 1))
    return False


def lista_grupos() -> int:
    """Mostra os grupos do WhatsApp conectado e o ID de cada um."""
    print(f"Estado da conexão: {estado_green()}")
    try:
        contatos = green("getContacts").json()
    except Exception as e:
        print(f"  ! não consegui listar: {e}")
        return 1
    grupos = [c for c in contatos if str(c.get("id", "")).endswith("@g.us")]
    if not grupos:
        print("  Nenhum grupo encontrado. Confira se o número conectado já está no grupo.")
    for g in grupos:
        print(f"  {g.get('name') or g.get('contactName') or '(sem nome)'}  →  {g['id']}")
    return 0


def envia_boletim(blocos: list[str]) -> bool:
    """Green-API se configurado; se falhar (ou não houver), CallMeBot."""
    if USA_GREEN:
        print(f"  Green-API: conexão {estado_green()}")
        msgs = agrupa(blocos, LIMITE_GREEN)
        ok_algum = False
        for destino in destinos_green():
            ok = all([envia_green(destino, m) for m in msgs])
            ok_algum = ok_algum or ok
            time.sleep(3)
        if ok_algum:
            return True
        print("  ! Green-API falhou — tentando pelo CallMeBot")

    if os.getenv("CALLMEBOT_APIKEY", "").strip():
        msgs = agrupa(blocos, LIMITE_MSG)
        enviados = 0
        for m in msgs:
            enviados += envia_whatsapp(m)
            time.sleep(8)  # CallMeBot pede intervalo entre mensagens
        print(f"  CallMeBot: {enviados}/{len(msgs)} mensagens enviadas")
        return enviados > 0
    if not USA_GREEN:
        print("  ! nenhum envio configurado (Green-API ou CallMeBot)")
    return False


# --------------------------------------------------------------------------
def main() -> int:
    if os.getenv("LISTAR_GRUPOS", "0") == "1":
        return lista_grupos()

    print(f"Bom dia varejo — {AGORA:%d/%m/%Y %H:%M} {'(resumo da semana)' if DOMINGO else ''}")
    hist = carrega_historico()

    itens = remove_repetidas(coleta(), hist)
    print(f"  {len(itens)} manchetes novas encontradas")
    if not itens:
        print("  nada novo hoje — nenhuma mensagem enviada")
        return 0

    boletim = redige(itens, hist) or modo_simples(itens)
    blocos = monta_blocos(boletim, itens)

    print("\n" + "\n\n".join(blocos) + "\n")
    if DRY_RUN:
        print("  DRY_RUN=1 — não enviado, histórico não alterado")
        return 0

    if not envia_boletim(blocos):
        return 1

    hoje = AGORA.date().isoformat()
    for chave, _ in SECOES:
        for n in boletim.get(chave) or []:
            for i in n.get("ids", []):
                if isinstance(i, int) and 1 <= i <= len(itens):
                    hist["noticias"].append({"titulo": itens[i - 1]["titulo"], "link": itens[i - 1]["link"], "data": hoje})
            hist["noticias"].append({"titulo": n["titulo"], "link": "", "data": hoje})
    if (boletim.get("curiosidade") or {}).get("titulo"):
        hist["curiosidades"].append(boletim["curiosidade"]["titulo"])
    salva_historico(hist)
    return 0


if __name__ == "__main__":
    sys.exit(main())
