import os, re, json, logging
from typing import List, Dict

log = logging.getLogger("fiat-whatsapp")

# =========================
# Carregamento do catálogo
# =========================
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

# =========================
# Utilitários
# =========================
def fmt_brl(valor) -> str:
    if valor is None:
        return "indisponível"
    s = f"{float(valor):,.2f}"
    return "R$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")

def tokenize(text: str):
    """
    Normaliza e separa termos. Mantém números de motor (1.0, 1.3), e
    aproxima 'automático' -> 'automatico' pra facilitar match.
    """
    base = (text or "").lower()
    base = base.replace("automático", "automatico")
    return re.findall(r"[a-z0-9\.]+", base)

def score_offer(q_tokens: List[str], offer: Dict) -> int:
    """
    Score básico + pesos para atributos-chave (câmbio/motor).
    Campos possíveis do item:
      modelo, versao, motor, cambio, tags[], publico_alvo[], condicoes[]
    """
    campos = " ".join([
        offer.get("modelo",""), offer.get("versao",""),
        offer.get("motor",""), offer.get("cambio",""),
        " ".join(offer.get("tags",[])),
        " ".join(offer.get("publico_alvo",[])),
        " ".join(offer.get("condicoes",[]))
    ]).lower()

    score = 0
    for t in q_tokens:
        if t in campos:
            score += 1

    cambio = (offer.get("cambio","") or "").lower()
    motor  = (offer.get("motor","")  or "").lower() + " " + " ".join(offer.get("tags",[])).lower()

    # Preferência por automático quando pedido
    if any(t in ("automatico","auto","cvt","at") for t in q_tokens):
        if "autom" in cambio or "cvt" in cambio or "at" in cambio:
            score += 3

    # Preferência por manual quando pedido explicitamente
    if "manual" in q_tokens and "manual" in cambio:
        score += 2

    # Peso por termos de motor
    motor_tokens = {"1.0","1.3","1.8","turbo","diesel","flex"}
    score += sum(1 for t in q_tokens if t in motor_tokens and t in motor)

    return score

def buscar_oferta(query: str, ofertas: List[Dict]) -> Dict | None:
    if not ofertas:
        return None
    q = tokenize(query)
    if not q:
        return None
    best = max(ofertas, key=lambda o: score_offer(q, o))
    return best if score_offer(q, best) > 0 else None

# =========================
# Formatação
# =========================
def titulo_oferta(o: dict) -> str:
    return f"{o.get('modelo','').strip()} {o.get('versao','').strip()}".strip()

def link_preferencial(o: dict) -> str:
    return (o.get("link_modelo") or o.get("link_oferta") or "").strip()

def montar_texto_oferta(o: dict) -> str:
    preco = o.get("preco_por") or o.get("preco_a_partir") or o.get("preco_de")
    preco_label = "por" if o.get("preco_por") else ("a partir de" if o.get("preco_a_partir") else "de")
    linhas = [titulo_oferta(o), f"Preço {preco_label}: {fmt_brl(preco)}"]

    extras = []
    if o.get("motor"):  extras.append(f"Motor {o['motor']}")
    if o.get("cambio"): extras.append(f"Câmbio {o['cambio']}")
    if o.get("combustivel"): extras.append(o["combustivel"])
    if extras:
        linhas.append(", ".join(extras))

    if o.get("condicoes"):
        linhas.append("Condições: " + "; ".join(o["condicoes"]))
    if o.get("publico_alvo"):
        linhas.append("Público-alvo: " + ", ".join(o["publico_alvo"]))

    lp = link_preferencial(o)
    if lp:
        linhas.append(f"Link: {lp}")

    linhas.append("Quer consultar cores, disponibilidade e agendar um test drive?")
    return "\n".join(linhas)

# =========================
# Intenções e respostas
# =========================
def detectar_intencao(msg: str) -> str:
    s = (msg or "").lower()
    if any(k in s for k in ["foto", "fotos", "imagem", "imagens", "galeria"]): return "fotos"
    if any(k in s for k in ["link", "site", "url"]): return "link"
    if any(k in s for k in ["preço", "preco", "valor", "quanto custa"]): return "preco"
    if any(k in s for k in ["condição", "condicoes", "condição", "parcel", "financi", "taxa"]): return "condicoes"
    if any(k in s for k in ["público", "publico", "perfil", "para quem"]): return "publico"
    if any(k in s for k in ["ficha", "detalhe", "detalhes", "resumo", "informação"]): return "detalhes"
    if any(k in s for k in ["produto", "produtos", "modelos", "oferta", "ofertas", "promo", "lista", "vendem"]): return "lista"
    return "detalhes"

def formatar_resposta_por_intencao(intencao: str, o: dict):
    if not o:
        return None

    tit = titulo_oferta(o)
    lp  = link_preferencial(o)
    preco = o.get("preco_por") or o.get("preco_a_partir") or o.get("preco_de")
    preco_label = "por" if o.get("preco_por") else ("a partir de" if o.get("preco_a_partir") else "de")

    if intencao == "fotos":
        # suporta 'galeria' ou 'fotos' no JSON (lista de URLs)
        galerias = o.get("galeria") or o.get("fotos") or []
        if isinstance(galerias, list) and galerias:
            head = " ".join(galerias[:2])  # 1–2 para não poluir
            tail = (f"\nMais fotos: {lp}" if lp else "")
            return f"{tit}\nAlgumas fotos: {head}{tail}".strip()
        return f"{tit}\nVeja fotos e cores no site: {lp}" if lp else f"{tit}\nPosso te enviar fotos por aqui. Tem alguma cor em mente?"

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

    # padrão: cartão curto
    return montar_texto_oferta(o)

# =========================
# Orquestração (entrada do catálogo)
# =========================
def tentar_responder_com_catalogo(mensagem: str, ofertas_path: str):
    """
    Conservador:
      - Se pedir 'lista/produtos/ofertas', devolve lista curta de modelos.
      - Caso contrário, só responde se houver match claro (buscar_oferta).
      - Se não houver, retorna None (IA assume).
    """
    ofertas = load_offers(ofertas_path)
    if not ofertas:
        return None

    intencao = detectar_intencao(mensagem)

    if intencao == "lista":
        # lista seca de modelos (curta) + pergunta
        nomes = list({titulo_oferta(x) for x in ofertas})
        nomes.sort()
        resumo = ", ".join(nomes[:6])
        return f"Trabalhamos com: {resumo}. Algum deles te interessa?"

    # Para as outras intenções, exige match
    o = buscar_oferta(mensagem, ofertas)
    if not o:
        return None

    return formatar_resposta_por_intencao(intencao, o)
