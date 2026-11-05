"""
Bom dia varejo — boletim diário no WhatsApp
Criado por Guilherme Fonseca

Fluxo:
 1. Busca as manchetes do dia no Google News (RSS, grátis)
 2. Tira tudo que já foi enviado antes (historico.json)
 3. A IA gratuita do Gemini escolhe e redige no estilo Bom dia varejo
 4. Envia para o seu WhatsApp pelo CallMeBot (grátis, uso pessoal)

Variáveis de ambiente (GitHub Secrets):
  GEMINI_API_KEY     chave grátis do Google AI Studio
  WHATSAPP_PHONE     seu número no formato internacional, ex.: +5531999999999
  CALLMEBOT_APIKEY   chave recebida do CallMeBot
Opcionais:
  GEMINI_MODEL       força um modelo específico do Gemini
  INCLUIR_LINKS=1    adiciona o link de cada notícia
  DRY_RUN=1          só mostra a mensagem no log, não envia
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
INCLUIR_LINKS = os.getenv("INCLUIR_LINKS", "0") == "1"
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
- Cada notícia tem 2 ou 3 tópicos curtos (máx. 20 palavras cada) com os fatos e números principais.
- Sempre que fizer sentido, o último tópico explica o impacto prático para o varejo/atacarejo, começando com "Para o varejo:".
- Nunca use a expressão "leitura editorial".
- Não invente fatos nem números: use só o que está nas manchetes e resumos fornecidos.
- Junte em um item só notícias diferentes sobre o mesmo fato.
- Ignore notícias que repetem assuntos da lista "JÁ ENVIADOS".
- Priorize: concorrentes (Atacadão, Assaí, Carrefour, Grupo Mateus, Pão de Açúcar, Supermercados BH, DMA), Mart Minas, Minas Gerais, preços de alimentos, juros, consumo e comportamento do consumidor.
- Na seção "mundo", escolha os fatos internacionais mais relevantes do dia (não precisam ser de varejo).
- Crie uma curiosidade verdadeira e verificável sobre varejo/atacarejo/supermercados, diferente das já usadas.
{extra}

Responda SOMENTE com JSON neste formato:
{{
  "abertura": "1 frase neutra resumindo o principal assunto do dia",
  "varejo":   [{{"ids": [1, 4], "titulo": "...", "topicos": ["...", "..."]}}],
  "economia": [{{"ids": [7],    "titulo": "...", "topicos": ["...", "..."]}}],
  "mundo":    [{{"ids": [12],   "titulo": "...", "topicos": ["...", "..."]}}],
  "curiosidade": {{"titulo": "...", "texto": "2 ou 3 frases"}}
}}
Limites: até {mv} itens em varejo, {me} em economia, {mm} em mundo. Use apenas ids da lista."""


def chama_gemini(prompt: str) -> dict | None:
    chave = os.getenv("GEMINI_API_KEY")
    if not chave:
        print("  ! GEMINI_API_KEY ausente — usando modo simples")
        return None
    corpo = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.4, "responseMimeType": "application/json"},
    }
    for modelo in MODELOS_GEMINI:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
        for tentativa in range(3):
            try:
                r = requests.post(url, params={"key": chave}, json=corpo, timeout=120)
                if r.status_code == 404:
                    print(f"  ! modelo {modelo} indisponível")
                    break
                if r.status_code in (429, 500, 503):
                    time.sleep(15 * (tentativa + 1))
                    continue
                r.raise_for_status()
                texto = r.json()["candidates"][0]["content"]["parts"][0]["text"]
                texto = re.sub(r"^```(json)?|```$", "", texto.strip()).strip()
                print(f"  ✓ redigido com {modelo}")
                return json.loads(texto)
            except Exception as e:
                print(f"  ! {modelo} tentativa {tentativa + 1}: {e}")
                time.sleep(5)
    return None


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
        blocos.append(titulo_secao)
        for n in noticias:
            ids = [i for i in n.get("ids", []) if isinstance(i, int) and 1 <= i <= len(itens)]
            fontes = sorted({itens[i - 1]["fonte"] for i in ids if itens[i - 1]["fonte"]})
            txt = f"🟠 *{n['titulo'].strip()}*"
            for t in n.get("topicos", []):
                txt += f"\n• {t.strip()}"
            if fontes:
                txt += f"\n_Fonte: {', '.join(fontes[:2])}_"
            if INCLUIR_LINKS and ids:
                txt += f"\n{itens[ids[0] - 1]['link']}"
            blocos.append(txt)

    cur = boletim.get("curiosidade")
    if cur and cur.get("titulo"):
        blocos.append(f"💡 *CURIOSIDADE DO VAREJO*\n*{cur['titulo'].strip()}*\n{cur.get('texto', '').strip()}")

    blocos.append("#BomDiaVarejo")
    return blocos


def agrupa(blocos: list[str], limite: int = 1400) -> list[str]:
    """Junta blocos em mensagens que caibam no envio do CallMeBot."""
    msgs, atual = [], ""
    for b in blocos:
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
def envia_whatsapp(texto: str) -> bool:
    phone = os.getenv("WHATSAPP_PHONE", "").strip()
    key = os.getenv("CALLMEBOT_APIKEY", "").strip()
    if not phone or not key:
        print("  ! WHATSAPP_PHONE / CALLMEBOT_APIKEY ausentes")
        return False
    for tentativa in range(3):
        try:
            r = requests.get("https://api.callmebot.com/whatsapp.php",
                             params={"phone": phone, "text": texto, "apikey": key}, timeout=60)
            ok = r.status_code == 200 and "error" not in r.text.lower()
            if ok:
                return True
            print(f"  ! CallMeBot respondeu {r.status_code}: {re.sub('<[^>]+>', ' ', r.text)[:200]}")
        except Exception as e:
            print(f"  ! erro no envio: {e}")
        time.sleep(20)
    return False


# --------------------------------------------------------------------------
def main() -> int:
    print(f"Bom dia varejo — {AGORA:%d/%m/%Y %H:%M} {'(resumo da semana)' if DOMINGO else ''}")
    hist = carrega_historico()

    itens = remove_repetidas(coleta(), hist)
    print(f"  {len(itens)} manchetes novas encontradas")
    if not itens:
        print("  nada novo hoje — nenhuma mensagem enviada")
        return 0

    boletim = redige(itens, hist) or modo_simples(itens)
    msgs = agrupa(monta_blocos(boletim, itens))

    print("\n" + "\n\n——— (próxima mensagem) ———\n\n".join(msgs) + "\n")
    if DRY_RUN:
        print("  DRY_RUN=1 — não enviado, histórico não alterado")
        return 0

    enviados = 0
    for m in msgs:
        if envia_whatsapp(m):
            enviados += 1
        time.sleep(8)  # CallMeBot pede intervalo entre mensagens
    print(f"  {enviados}/{len(msgs)} mensagens enviadas")
    if enviados == 0:
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
