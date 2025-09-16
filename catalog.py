import os, re, json, logging, random
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

def tokenize(text: str):
    return re.findall(r"[a-z0-9\.]+", (text or "").lower().replace(",", "."))

def score_offer(q_tokens, offer):
    campos = " ".join([
        offer.get("modelo",""), offer.get("versao",""),
        offer.get("motor",""), offer.get("cambio",""),
        " ".join(offer.get("tags",[])), " ".join(offer.get("publico_alvo",[])),
        " ".join(offer.get("condicoes",[]))
    ]).lower()
    return sum(1 for t in q_tokens if t in campos)

def buscar_oferta(query: str, ofertas: List[Dict]):
    if not ofertas: return None
    q = tokenize(query)
    if not q: return None
    best = max(ofertas, key=lambda o: score_offer(q, o))
    return best if score_offer(q, best) > 0 else None

# --------- Formatação / Intenções ----------
def _pick(opts): return random.choice(opts)

def titulo_oferta(o: dict) -> str:
    return f"{o.get('modelo','').strip()} {o.get('versao','').strip()}".strip()

def link_preferencial(o: dict) -> str:
    return (o.get("link_modelo") or o.get("link_oferta") or "").strip()

def _format_link_line(titulo: str, url: str) -> str:
    return f"👉 {titulo}: {url}"

def montar_texto_oferta(o: dict) -> str:
    """Cartão curto e humano."""
    nome = titulo_oferta(o)
    preco = o.get("preco_por") or o.get("preco_a_partir") or o.get("preco_de")
    preco_label = "por" if o.get("preco_por") else ("a partir de" if o.get("preco_a_partir") else "de")

    linhas = [
        f"{_pick(['Legal!','Boa escolha!','Show!'])} {nome}",
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

def detectar_intencao(msg: str) -> str:
    s = (msg or "").lower()
    if any(k in s for k in ["link", "site", "url"]): return "link"
    if any(k in s for k in ["preço", "preco", "valor", "quanto custa"]): return "preco"
    if any(k in s for k in ["condição", "condicoes", "parcel", "financi", "taxa"]): return "condicoes"
    if any(k in s for k in ["público", "publico", "perfil", "para quem"]): return "publico"
    if any(k in s for k in ["ficha", "detalhe", "detalhes", "resumo", "informação"]): return "detalhes"
    if any(k in s for k in ["oferta", "ofertas", "promo", "promoção", "promocao", "lista", "listar"]): return "lista"
    if "surpreend" in s: return "surpreenda"
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

    # 'detalhes' cai no cartão curto
    return montar_texto_oferta(o)

# --------- Orquestração ----------
def tentar_responder_com_catalogo(mensagem: str, ofertas_path: str):
    """
    Comportamento:
    - 'lista/ofertas': top-3 por preço (cartões curtos).
    - 'surpreenda': 2 opções em faixas de preço bem diferentes.
    - 'link do <modelo>': só o link do modelo.
    - Demais: só responde se houver match claro (senão retorna None p/ IA responder).
    """
    ofertas = load_offers(ofertas_path)
    if not ofertas:
        return None

    intencao = detectar_intencao(mensagem)

    # 1) Lista de ofertas (top-3 por preço)
    if intencao == "lista":
        destaques = sorted(
            ofertas,
            key=lambda o: (o.get("preco_por") or o.get("preco_a_partir") or o.get("preco_de") or 9e9)
        )[:3]
        cards = [montar_texto_oferta(o) for o in destaques]
        prefixo = _pick(["Separei 3 boas opções:", "Olha essas 3 ofertas:", "Trago estas 3 sugestões:"])
        return prefixo + "\n\n" + "\n\n—\n\n".join(cards)

    # 2) Me surpreenda (duas faixas bem distintas)
    if intencao == "surpreenda" and len(ofertas) >= 2:
        ord_price = sorted(ofertas, key=lambda o: (o.get("preco_por") or o.get("preco_a_partir") or o.get("preco_de") or 9e9))
        picks = [ord_price[0], ord_price[-1]]
        cards = [montar_texto_oferta(o) for o in picks]
        return _pick(["Bora fugir do óbvio?", "Top te surpreender!"]) + "\n\n" + "\n\n—\n\n".join(cards)

    # 3) Demais intenções: requer match por modelo
    o = buscar_oferta(mensagem, ofertas)
    if not o:
        return None  # deixa a IA seguir

    # 3.1) Se pediram link explicitamente, só o link
    if intencao == "link":
        return formatar_resposta_por_intencao("link", o)

    # 3.2) Outras intenções (preço/condições/público/detalhes)
    return formatar_resposta_por_intencao(intencao, o)
