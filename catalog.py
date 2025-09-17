# catalog.py
import os
import re
import json
import logging
import unicodedata
from typing import List, Dict, Any, Optional

log = logging.getLogger("fiat-whatsapp")

# -----------------------------------------------------------------------------
# Cache simples para evitar reler o arquivo a cada mensagem
# -----------------------------------------------------------------------------
_OFFERS_CACHE = {"path": None, "mtime": 0.0, "data": []}


def _load_offers_cached(offers_path: str) -> List[Dict[str, Any]]:
    """
    Lê e cacheia o JSON de ofertas. Se o arquivo mudar (mtime), recarrega.
    Nunca levanta exceção para cima: em erro, retorna [].
    """
    try:
        if not offers_path or not os.path.exists(offers_path):
            return []

        mtime = os.path.getmtime(offers_path)
        if _OFFERS_CACHE["path"] == offers_path and _OFFERS_CACHE["mtime"] == mtime:
            return _OFFERS_CACHE["data"]

        with open(offers_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, list):
            log.warning("ofertas.json não é uma lista, ignorando.")
            data = []

        _OFFERS_CACHE.update({"path": offers_path, "mtime": mtime, "data": data})
        return data
    except Exception:
        log.exception("Erro lendo catálogo de ofertas")
        return []


# -----------------------------------------------------------------------------
# Utils gerais
# -----------------------------------------------------------------------------
def _strip_accents(s: str) -> str:
    """Remove acentos para facilitar match simples."""
    try:
        return "".join(
            c for c in unicodedata.normalize("NFD", s or "")
            if unicodedata.category(c) != "Mn"
        )
    except Exception:
        return s or ""


def _safe_str(x: Any) -> str:
    try:
        return str(x or "").strip()
    except Exception:
        return ""


def _as_list(x: Any) -> List[str]:
    """
    Garante lista de strings. Aceita lista, string única ou None.
    Ex.: "A; B" -> ["A; B"] (decisão de design: não split aqui)
    """
    if x is None:
        return []
    if isinstance(x, list):
        return [ _safe_str(i) for i in x if _safe_str(i) ]
    return [ _safe_str(x) ]


def fmt_brl(valor: Any) -> str:
    """Formata valor para BRL. Se não der, devolve 'indisponível'."""
    try:
        if valor in (None, "", "indisponível"):
            return "indisponível"
        # Remove separadores comuns que possam vir como string
        if isinstance(valor, str):
            v = valor.replace("R$", "").replace(".", "").replace(" ", "").replace(",", ".")
            valor = float(v)
        return "R$ " + f"{float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "indisponível"


def tokenize(text: str) -> List[str]:
    """
    Tokeniza em minúsculas, sem acentos. Mantém números e letras.
    """
    s = _strip_accents((text or "").lower())
    return re.findall(r"[a-z0-9\.]+", s)


def _campo_txt(o: Dict[str, Any], key: str) -> str:
    return _safe_str(o.get(key))


def _campos_match(o: Dict[str, Any]) -> str:
    """
    Junta os campos mais relevantes para scoring simples de busca.
    """
    partes = [
        _campo_txt(o, "modelo"),
        _campo_txt(o, "versao"),
        _campo_txt(o, "motor"),
        _campo_txt(o, "cambio"),
        " ".join(_as_list(o.get("tags"))),
        " ".join(_as_list(o.get("publico_alvo"))),
        " ".join(_as_list(o.get("condicoes"))),
    ]
    return " ".join(p for p in partes if p).lower()


def score_offer(q_tokens: List[str], offer: Dict[str, Any]) -> int:
    blob = _strip_accents(_campos_match(offer))
    return sum(1 for t in q_tokens if t in blob)


def buscar_oferta(query: str, ofertas: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Retorna a oferta com melhor score, se houver match (>0). Caso contrário, None.
    """
    if not ofertas:
        return None
    q_tokens = tokenize(query)
    if not q_tokens:
        return None
    try:
        best = max(ofertas, key=lambda o: score_offer(q_tokens, o))
        return best if score_offer(q_tokens, best) > 0 else None
    except Exception:
        log.exception("Erro em buscar_oferta()")
        return None


# -----------------------------------------------------------------------------
# Formatação de respostas
# -----------------------------------------------------------------------------
def titulo_oferta(o: Dict[str, Any]) -> str:
    return f"{_campo_txt(o, 'modelo')} {_campo_txt(o, 'versao')}".strip()


def link_preferencial(o: Dict[str, Any]) -> str:
    """
    Prioriza link_modelo; se vazio, cai para link_oferta.
    """
    return _safe_str(o.get("link_modelo") or o.get("link_oferta"))


def montar_texto_oferta(o: Dict[str, Any]) -> str:
    """
    Card detalhado, porém enxuto, para envio no WhatsApp.
    """
    try:
        preco = o.get("preco_por") or o.get("preco_a_partir") or o.get("preco_de")
        if o.get("preco_por"):
            preco_label = "por"
        elif o.get("preco_a_partir"):
            preco_label = "a partir de"
        else:
            preco_label = "de"

        linhas: List[str] = [
            titulo_oferta(o),
            f"Preço {preco_label}: {fmt_brl(preco)}"
        ]

        extras = []
        if _campo_txt(o, "motor"):
            extras.append(f"Motor {_campo_txt(o, 'motor')}")
        if _campo_txt(o, "cambio"):
            extras.append(f"Câmbio {_campo_txt(o, 'cambio')}")
        if _campo_txt(o, "combustivel"):
            extras.append(_campo_txt(o, "combustivel"))
        if extras:
            linhas.append(", ".join(extras))

        condicoes = _as_list(o.get("condicoes"))
        publico   = _as_list(o.get("publico_alvo"))
        if condicoes:
            linhas.append("Condições: " + "; ".join(condicoes))
        if publico:
            linhas.append("Público-alvo: " + ", ".join(publico))

        lp = link_preferencial(o)
        if lp:
            linhas.append(f"Link: {lp}")

        linhas.append("Quer consultar cores, disponibilidade e agendar um test drive?")
        return "\n".join([l for l in linhas if _safe_str(l)])
    except Exception:
        log.exception("Erro ao montar texto da oferta")
        return _safe_str(titulo_oferta(o)) or "Oferta disponível."


# -----------------------------------------------------------------------------
# Intenções simples (heurística)
# -----------------------------------------------------------------------------
def detectar_intencao(msg: str) -> str:
    s = (_strip_accents(msg or "").lower())
    try:
        if any(k in s for k in ["lista", "ofertas", "promo", "promocao", "promoção", "catalogo", "catalogue"]):
            return "lista"
        if any(k in s for k in ["link", "site", "url"]):
            return "link"
        if any(k in s for k in ["preco", "preço", "valor", "quanto custa", "a partir"]):
            return "preco"
        if any(k in s for k in ["condicao", "condicoes", "condição", "parcel", "financi", "taxa"]):
            return "condicoes"
        if any(k in s for k in ["publico", "público", "perfil", "para quem"]):
            return "publico"
        if any(k in s for k in ["ficha", "detalhe", "detalhes", "resumo", "informacao", "informação"]):
            return "detalhes"
        return "detalhes"
    except Exception:
        return "detalhes"


def formatar_resposta_por_intencao(intencao: str, o: Dict[str, Any]) -> str:
    """
    Resumo mais direto dependendo da intenção.
    """
    if not o:
        return ""

    try:
        tit = titulo_oferta(o)
        lp  = link_preferencial(o)
        preco = o.get("preco_por") or o.get("preco_a_partir") or o.get("preco_de")
        if o.get("preco_por"):
            preco_label = "por"
        elif o.get("preco_a_partir"):
            preco_label = "a partir de"
        else:
            preco_label = "de"

        if intencao == "link":
            return f"{tit}\n{lp}" if lp else f"{tit}\nLink indisponível."

        if intencao == "preco":
            base = f"{tit}\nPreço {preco_label}: {fmt_brl(preco)}"
            return base + (f"\n{lp}" if lp else "")

        if intencao == "condicoes":
            cond = "; ".join(_as_list(o.get("condicoes"))) or "Não informado."
            base = f"{tit}\nCondições: {cond}"
            return base + (f"\n{lp}" if lp else "")

        if intencao == "publico":
            pub = ", ".join(_as_list(o.get("publico_alvo"))) or "Não informado."
            base = f"{tit}\nPúblico-alvo: {pub}"
            return base + (f"\n{lp}" if lp else "")

        # "detalhes" e fallback
        return montar_texto_oferta(o)
    except Exception:
        log.exception("Erro formatando resposta por intenção")
        return montar_texto_oferta(o)


# -----------------------------------------------------------------------------
# Função pública usada pelo routes.py
# -----------------------------------------------------------------------------
def tentar_responder_com_catalogo(mensagem: str, ofertas_path: str, max_cards: int = 3) -> Optional[str]:
    """
    Estratégia CONSERVADORA:
      - Se pedir 'lista/ofertas', devolve até max_cards (mais baratos).
      - Senão, tenta achar um modelo pelo texto; se não achar, devolve None (IA assume).
      - Quando achar, responde conforme intenção (link/preço/condições/etc).
    Nunca levanta exceção; em qualquer erro retorna None.
    """
    try:
        ofertas = _load_offers_cached(ofertas_path)
        if not ofertas:
            return None

        intencao = detectar_intencao(mensagem)

        # 1) lista / ofertas
        if intencao == "lista":
            # Ordena por preço quando possível
            def _preco(o: Dict[str, Any]) -> float:
                val = o.get("preco_por") or o.get("preco_a_partir") or o.get("preco_de")
                try:
                    if isinstance(val, str):
                        v = val.replace("R$", "").replace(".", "").replace(" ", "").replace(",", ".")
                        return float(v)
                    return float(val)
                except Exception:
                    return 9e12

            destaques = sorted(ofertas, key=_preco)[: max(1, int(max_cards))]
            cards = [montar_texto_oferta(o) for o in destaques]
            return "Algumas ofertas em destaque:\n\n" + "\n\n---\n\n".join(cards)

        # 2) busca por modelo / versão
        o = buscar_oferta(mensagem, ofertas)
        if not o:
            return None  # IA conversa normalmente

        return formatar_resposta_por_intencao(intencao, o)

    except Exception:
        log.exception("Erro em tentar_responder_com_catalogo()")
        return None


__all__ = ["tentar_responder_com_catalogo"]
