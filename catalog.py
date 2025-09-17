import os, re, json, logging
from typing import List, Dict

log = logging.getLogger("fiat-whatsapp")

# --------- Load ----------
def load_offers(offers_path: str) -> List[Dict]:
    if not os.path.exists(offers_path):
        return []
    try:
        with open(offers_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception as e:
        log.error(f"Erro lendo {offers_path}: {e}")
        return []

# --------- Utils ----------
def fmt_brl(valor) -> str:
    if valor is None: return "indisponível"
    s = f"{float(valor):,.2f}"
    return "R$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")

_token_re = re.compile(r"[a-z0-9\.]+", re.IGNORECASE)

def tokenize(text: str):
    return _token_re.findall((text or "").lower().replace(",", "."))

def _tokens_set(text: str):
    return set(tokenize(text))

# --------- Heurísticas de ativação (evitar saudar) ----------
_MODEL_KEYWORDS = {
    "pulse","toro","strada","mobi","argo","fastback","cronos","fiorino","ducato","uno","weekend","siena"
}
_QUERY_KEYWORDS = {
    "carro","carros","modelo","modelos","versao","versão","foto","fotos",
    "preco","preço","valor","link","site","oferta","ofertas","promo","promoção","promocao",
    "condicao","condição","condicoes","condições","taxa","parcel","financi",
    "dispon","estoque","cores","test","testdrive","test drive","agendar","agenda"
}
_GREET_RE = re.compile(r"\b(oi|olá|ola|bom dia|boa tarde|boa noite|salve|eai|e aí)\b", re.IGNORECASE)

def _looks_like_vehicle_query(msg: str) -> bool:
    s = (msg or "").lower()
    if _GREET_RE.search(s):  # cumprimentos não devem disparar catálogo
        return False
    # precisa citar algum termo de consulta OU um modelo
    has_model = any(m in s for m in _MODEL_KEYWORDS)
    has_query = any(k in s for k in _QUERY_KEYWORDS)
    return has_model or has_query

# --------- Scoring conservador (match por token EXATO) ----------
def score_offer(q_tokens: List[str], offer: Dict]) -> int:
    campos = " ".join([
        offer.get("modelo",""), offer.get("versao",""),
        offer.get("motor",""), offer.get("cambio",""),
        " ".join(offer.get("tags",[])), " ".join(offer.get("publico_alvo",[])),
        " ".join(offer.get("condicoes",[]))
    ]).lower()
    offer_tokens = _tokens_set(campos)
    return len(set(q_tokens) & offer_tokens)  # interseção por token exato

def buscar_oferta(query: str, ofertas: List[Dict]):
    if not ofertas: return None
    q = tokenize(query)
    if not q: return None
    best = max(ofertas, key=lambda o: score_offer(q, o))
    return best if score_offer(q, best) > 0 else None

# --------- Formatação / Intenções ----------
def titulo_oferta(o: dict) -> str:
    return f"{o.get('modelo','').strip()} {o.get('versao','').strip()}".strip()

def link_preferencial(o: dict) -> str:
    return (o.get("link_modelo") or o.get("link_oferta") or "").strip()

def montar_texto_oferta(o: dict) -> str:
    preco = o.get("preco_por") or o.get("preco_a_partir") or o.get("preco_de")
    preco_label = "por" if o.get("preco_por") else ("a partir de" if o.get("preco_a_partir") else "de")
    linhas = [titulo_oferta(o), f"Preço {preco_label}: {fmt_brl(preco)}"]

    extras = []
    if o.get("motor"): extras.append(f"Motor {o['motor']}")
    if o.get("cambio"): extras.append(f"Câmbio {o['cambio']}")
    if o.get("combustivel"): extras.append(o["combustivel"])
    if extras: linhas.append(", ".join(extras))

    if o.get("condicoes"):    linhas.append("Condições: " + "; ".join(o["condicoes"]))
    if o.get("publico_alvo"): linhas.append("Público-alvo: " + ", ".join(o["publico_alvo"]))

    lp = link_preferencial(o)
    if lp: linhas.append(f"Link: {lp}")

    linhas.append("Quer consultar cores, disponibilidade e agendar um test drive?")
    return "\n".join(linhas)

def detectar_intencao(msg: str) -> str:
    s = (msg or "").lower()
    if any(k in s for k in ["link", "site", "url"]): return "link"
    if any(k in s for k in ["preço", "preco", "valor", "quanto custa"]): return "preco"
    if any(k in s for k in ["condição", "condicoes", "condições", "parcel", "financi", "taxa"]): return "condicoes"
    if any(k in s for k in ["público", "publico", "perfil", "para quem"]): return "publico"
    if any(k in s for k in ["ficha", "detalhe", "detalhes", "resumo", "informação"]): return "detalhes"
    if any(k in s for k in ["oferta", "ofertas", "promo", "promoção", "promocao", "lista", "listar"]): return "lista"
    return "detalhes"

def formatar_resposta_por_intencao(intencao: str, o: dict):
    if not o:
        return None

    tit = titulo_oferta(o)
    lp  = link_preferencial(o)
    preco = o.get("preco_por") or o.get("preco_a_partir") or o.get("preco_de")
    preco_label = "por" if o.get("preco_por") else ("a partir de" if o.get("preco_a_partir") else "de")

    if intencao == "link":
        return f"{tit}\n{lp}" if lp else f"{tit}\nLink indisponível."

    if intencao == "preco":
        return f"{tit}\nPreço {preco_label}: {fmt_brl(preco)}" + (f"\n{lp}" if lp else "")

    if intencao == "condicoes":
        cond = "; ".join(o.get("condicoes", [])) or "Não informado."
        return f"{tit}\nCondições: {cond}" + (f"\n{lp}" if lp else "")

    if intencao == "publico":
        pub = ", ".join(o.get("publico_alvo", [])) or "Não informado."
        return f"{tit}\nPúblico-alvo: {pub}" + (f"\n{lp}" if lp else "")

    return montar_texto_oferta(o)

def tentar_responder_com_catalogo(mensagem: str, ofertas_path: str):
    """
    CONSERVADOR:
    - Responde lista quando pedir explicitamente.
    - Para outras intenções, só ativa se a mensagem parecer consulta de veículos.
    - Match por token exato (evita 'dia' == 'dias').
    - Cumprimentos simples não disparam catálogo.
    """
    ofertas = load_offers(ofertas_path)
    if not ofertas:
        return None

    intencao = detectar_intencao(mensagem)

    # Lista de ofertas quando a pessoa pede explicitamente
    if intencao == "lista":
        destaques = sorted(
            ofertas,
            key=lambda o: (o.get("preco_por") or o.get("preco_a_partir") or o.get("preco_de") or 9e9)
        )[:3]
        cards = [montar_texto_oferta(o) for o in destaques]
        return "Algumas ofertas em destaque:\n\n" + "\n\n---\n\n".join(cards)

    # Para qualquer outra intenção, só continua se parecer consulta de carro
    if not _looks_like_vehicle_query(mensagem):
        return None

    # Tenta casar um modelo específico
    o = buscar_oferta(mensagem, ofertas)
    if not o:
        return None  # deixa a IA responder de forma natural

    return formatar_resposta_por_intencao(intencao, o)
