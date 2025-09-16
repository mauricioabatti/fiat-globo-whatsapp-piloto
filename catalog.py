import os, re, json, logging, random
from typing import List, Dict, Tuple

log = logging.getLogger("fiat-whatsapp")

# ------------ Config de matching ------------
MODELOS = ["toro","pulse","strada","mobi","argo","fastback","cronos","fiorino","ducato"]
STOPWORDS = {
    "de","da","do","dos","das","e","ou","o","a","os","as","um","uma","uns","umas",
    "que","tem","mais","algum","alguma","quais","qual","outro","outra","modelo",
    "modelos","me","manda","por","favor","pra","para","sobre","daqui","dessa","desse",
    "isso","esse","essa","aí","ai","no","na","nos","nas","em","com"
}

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

def _low(s: str) -> str:
    return (s or "").lower()

def tokenize(text: str) -> List[str]:
    # mantém letras/números; filtra stopwords e palavras < 3 letras
    raw = re.findall(r"[a-z0-9]+", _low(text))
    return [t for t in raw if len(t) >= 3 and t not in STOPWORDS]

def extract_model_mentioned(text: str) -> str | None:
    s = _low(text)
    for m in MODELOS:
        if re.search(rf"\b{re.escape(m)}\b", s):
            return m
    return None

def _offer_fields_blob(o: Dict) -> str:
    return " ".join([
        _low(o.get("modelo","")), _low(o.get("versao","")),
        _low(o.get("motor","")),  _low(o.get("cambio","")),
        " ".join(map(_low, o.get("tags",[]))),
        " ".join(map(_low, o.get("publico_alvo",[]))),
        " ".join(map(_low, o.get("condicoes",[])))
    ])

def score_offer(q_tokens: List[str], offer: Dict) -> int:
    """
    Ranking simples com pesos. Palavras de intenção (diesel, 4x4, automático etc.)
    e nomes de modelo valem mais.
    """
    blob = _offer_fields_blob(offer)

    weights = {
        # boosts por características comuns
        "automatico": 2, "automático": 2, "manual": 2, "diesel": 3, "4x4": 3, "turbo": 2, "hybrid": 2, "híbrido": 2,
    }
    # dá um boost alto se o token for um modelo
    for m in MODELOS: weights[m] = 6

    score = 0
    for t in q_tokens:
        if t in blob:
            score += weights.get(t, 1)
    return score

def buscar_oferta(query: str, ofertas: List[Dict]):
    if not ofertas: return None
    q_tokens = tokenize(query)
    if not q_tokens: return None

    modelo = extract_model_mentioned(query)

    # Se citar modelo, restringe ao modelo
    cand = ofertas
    if modelo:
        cand = [o for o in ofertas if modelo in _offer_fields_blob(o)]
        # se não achar nada (ex.: catálogo não tem), volta pro full
        if not cand:
            cand = ofertas

    best = max(cand, key=lambda o: score_offer(q_tokens, o))
    return best if score_offer(q_tokens, best) > 0 else None

# --------- Formatação ----------
def _pick(opts): return random.choice(opts)

def titulo_oferta(o: dict) -> str:
    return f"{o.get('modelo','').strip()} {o.get('versao','').strip()}".strip()

def link_preferencial(o: dict) -> str:
    return (o.get("link_modelo") or o.get("link_oferta") or "").strip()

def _format_link_line(titulo: str, url: str) -> str:
    return f"👉 {titulo}: {url}"

def montar_texto_oferta(o: dict) -> str:
    nome = titulo_oferta(o)
    preco = o.get("preco_por") or o.get("preco_a_partir") or o.get("preco_de")
    preco_label = "por" if o.get("preco_por") else ("a partir de" if o.get("preco_a_partir") else "de")

    linhas = [
        f"{_pick(['Show!','Boa escolha!','Legal!'])} {nome}",
        f"Preço {preco_label}: {fmt_brl(preco)}",
    ]

    extras = []
    if o.get("motor"): extras.append(f"Motor {o['motor']}")
    if o.get("cambio"): extras.append(f"Câmbio {o['cambio']}")
    if o.get("combustivel"): extras.append(o["combustivel"])
    if extras:
        linhas.append(" • " + " | ".join(extras))

    lp = link_preferencial(o)
    if lp:
        linhas.append(_format_link_line("Link", lp))

    return "\n".join(linhas)

# --------- Intenções ----------
def detectar_intencao(msg: str) -> str:
    s = _low(msg)
    if any(k in s for k in ["link", "site", "url"]): return "link"
    if any(k in s for k in ["preço", "preco", "valor", "quanto custa"]): return "preco"
    if any(k in s for k in ["condição", "condicoes", "parcel", "financi", "taxa"]): return "condicoes"
    if any(k in s for k in ["público", "publico", "perfil", "para quem"]): return "publico"
    if any(k in s for k in ["ficha", "detalhe", "detalhes", "resumo", "informação"]): return "detalhes"
    if any(k in s for k in ["oferta", "ofertas", "promo", "promoção", "promocao", "lista", "listar"]): return "lista"
    if "surpreend" in s: return "surpreenda"

    # “o que tem da toro?”, “tem mais algum modelo da toro?”, “quais versões da toro?”
    if extract_model_mentioned(s) and any(p in s for p in ["o que tem", "tem mais", "mais algum", "quais", "versões", "versao", "versões", "versoes"]):
        return "lista_modelo"

    return "detalhes"

def formatar_resposta_por_intencao(intencao: str, o: dict):
    if not o:
        return None
    tit = titulo_oferta(o)
    lp  = link_preferencial(o)
    preco = o.get("preco_por") or o.get("preco_a_partir") or o.get("preco_de")
    preco_label = "por" if o.get("preco_por") else ("a partir de" if o.get("preco_a_partir") else "de")

    if intencao == "link":
        return _format_link_line(tit, lp) if lp else f"{tit}\nLink indisponível."

    if intencao == "preco":
        return f"{tit}\nPreço {preco_label}: {fmt_brl(preco)}" + (f"\n{_format_link_line('Link', lp)}" if lp else "")

    if intencao == "condicoes":
        cond = "; ".join(o.get("condicoes", [])) or "Não informado."
        return f"{tit}\nCondições: {cond}" + (f"\n{_format_link_line('Link', lp)}" if lp else "")

    if intencao == "publico":
        pub = ", ".join(o.get("publico_alvo", [])) or "Não informado."
        return f"{tit}\nPúblico-alvo: {pub}" + (f"\n{_format_link_line('Link', lp)}" if lp else "")

    # detalhes => cartão curto
    return montar_texto_oferta(o)

# --------- Listagens ----------
def _ordenar_por_preco(ofertas: List[Dict]) -> List[Dict]:
    return sorted(ofertas, key=lambda o: (o.get("preco_por") or o.get("preco_a_partir") or o.get("preco_de") or 9e9))

def _listar_por_modelo(ofertas: List[Dict], modelo: str, k: int = 3) -> List[Dict]:
    blob = lambda o: _offer_fields_blob(o)
    filtradas = [o for o in ofertas if modelo in blob(o)]
    return _ordenar_por_preco(filtradas)[:k]

# --------- Orquestração ----------
def tentar_responder_com_catalogo(mensagem: str, ofertas_path: str):
    ofertas = load_offers(ofertas_path)
    if not ofertas:
        return None

    intencao = detectar_intencao(mensagem)

    # 1) Lista geral (top-3 por preço)
    if intencao == "lista":
        destaques = _ordenar_por_preco(ofertas)[:3]
        cards = [montar_texto_oferta(o) for o in destaques]
        prefixo = _pick(["Separei 3 boas opções:", "Olha essas 3 ofertas:", "Trago estas 3 sugestões:"])
        return prefixo + "\n\n" + "\n\n—\n\n".join(cards)

    # 2) Lista por modelo (ex.: “o que tem da toro?”)
    if intencao == "lista_modelo":
        modelo = extract_model_mentioned(mensagem)
        if modelo:
            itens = _listar_por_modelo(ofertas, modelo, k=3)
            if itens:
                cards = [montar_texto_oferta(o) for o in itens]
                prefixo = _pick([f"Opções da {modelo.title()}:", f"Variações da {modelo.title()}:", f"O que temos de {modelo.title()}:"])
                return prefixo + "\n\n" + "\n\n—\n\n".join(cards)

    # 3) Me surpreenda (duas faixas distintas)
    if intencao == "surpreenda" and len(ofertas) >= 2:
        ord_price = _ordenar_por_preco(ofertas)
        picks = [ord_price[0], ord_price[-1]]
        cards = [montar_texto_oferta(o) for o in picks]
        return _pick(["Bora fugir do óbvio?", "Top te surpreender!"]) + "\n\n" + "\n\n—\n\n".join(cards)

    # 4) Intenções específicas → precisa de um match
    o = buscar_oferta(mensagem, ofertas)
    if not o:
        return None  # deixa a IA responder

    if intencao == "link":
        return formatar_resposta_por_intencao("link", o)

    return formatar_resposta_por_intencao(intencao, o)
