"""Bot de ofertas Shopee Afiliados -> grupo do Telegram."""
import html
import hashlib
import json
import os
import re
from difflib import SequenceMatcher
import sqlite3
import time
import unicodedata
from urllib.parse import urlparse

import requests

import painel

# ===================== CONFIG =====================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "COLE_O_TOKEN_DO_BOTFATHER")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "-100XXXXXXXXXX")      # ID do grupo (começa com -100...)
CHAT_ID_MIGRADO_CALLBACK = None

# Termos de busca na Shopee
TERMOS_PADRAO = (
    "tênis masculino skate y2k, tênis esportivo retrô masculino, camiseta oversized streetwear, "
    "camiseta masculina y2k sport, camisa de futebol streetwear, jersey futebol y2k, "
    "moletom esportivo masculino streetwear, jaqueta track masculina y2k, "
    "corta vento esportivo streetwear, calça track pants masculina, calça cargo streetwear, "
    "calça baggy y2k, bermuda cargo masculina streetwear, boné hip hop y2k, "
    "óculos esportivo y2k, corrente masculina hip hop, pulseira streetwear"
)
TERMOS = [t.strip() for t in os.environ.get("TERMOS", TERMOS_PADRAO).split(",") if t.strip()]
DESCONTO_MINIMO = int(os.environ.get("DESCONTO_MINIMO", "30"))  # em %
PRODUTOS_POR_TERMO = 30
MAX_ENVIOS_POR_CICLO = 5
INTERVALO_MINUTOS = int(os.environ.get("INTERVALO_MINUTOS", "30"))
DEDUP_JANELA_DIAS = 90  # evita que produtos semelhantes voltem logo após algumas semanas
# Painel (cliques, compras, comissão). BASE_URL = endereço público do seu servidor,
# ex.: https://meubot.up.railway.app  (sem barra no final). Sem ele, os cliques não são contados.
BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")
PAINEL_SENHA = os.environ.get("PAINEL_SENHA", "")   # obrigatória para abrir /painel
TAXA_COMISSAO = float(os.environ.get("TAXA_COMISSAO", "9"))  # % usada na estimativa
PORT = int(os.environ.get("PORT", "8080"))
HOST = os.environ.get("HOST", "0.0.0.0")  # no PC use 127.0.0.1 (só você acessa)
SITE = "MLB"
DB_FILE = os.environ.get("DB_FILE", "enviados.db")  # em servidor, aponte para um volume (ex.: /data/enviados.db)
# ==================================================

SHOPEE_API = "https://open-api.affiliate.shopee.com.br/graphql"
SHOPEE_APP_ID = os.environ.get("SHOPEE_APP_ID", "").strip()
SHOPEE_SECRET = os.environ.get("SHOPEE_SECRET", "").strip()
TERMOS_EM_ESPERA = {}
ATRASOS_RETENTATIVA = (3, 8)


def titulo_bloqueado(titulo):
    texto = unicodedata.normalize("NFKD", str(titulo or "").casefold())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"[^a-z0-9]+", " ", texto).strip()
    bloqueios = (
        r"\bcosplay\w*\b", r"\blingerie\b", r"\bcalcinha\w*\b",
        r"\bsutia\w*\b", r"\bsoutien\w*\b", r"\bcueca\w*\b",
        r"\bunderwear\b", r"\b(roupa|moda|conjunto) intim\w*\b",
        r"\bcamisola\w*\b", r"\bpijama\w*\b", r"\bbaby\s*doll\w*\b",
        r"\bsunga\w*\b", r"\bbiquini\w*\b", r"\bfio dental\b",
        r"\bcinta modeladora\b",
        r"\bbolsa\w*\b", r"\bmochila\w*\b", r"\bpochete\w*\b",
        r"\bcarteira\w*\b", r"\bnecessaire\w*\b", r"\bbackpack\b",
        r"\bhandbag\b", r"\bcrossbody\b", r"\bshoulder bag\b",
        r"\bbags?\b", r"\btote\b", r"\bduffel\b", r"\bpurse\b",
        r"\bfanny pack\b", r"\bsling bag\b", r"\bmini bag\b",
    )
    return any(re.search(padrao, texto) for padrao in bloqueios)


def titulo_relevante(titulo):
    """Aceita apenas vestuário streetwear e acessórios usados no corpo."""
    if titulo_bloqueado(titulo):
        return False
    texto = unicodedata.normalize("NFKD", str(titulo or "").casefold())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"[^a-z0-9]+", " ", texto).strip()
    categorias = (
        r"\btenis\w*\b", r"\bsneaker\w*\b",
        r"\bcamiseta\w*\b", r"\bt shirt\b", r"\bshirt\b", r"\bcamisa\w*\b",
        r"\bjersey\w*\b", r"\bregata\w*\b", r"\bfutebol\b", r"\bsoccer\b",
        r"\bmoletom\w*\b", r"\bhoodie\w*\b",
        r"\bcalca\w*\b", r"\bjeans\b", r"\btrack pants\b",
        r"\bjaqueta\w*\b", r"\bbomber\b", r"\bcorta vento\b", r"\btrack jacket\b",
        r"\bbermuda\w*\b", r"\bshorts?\b", r"\bbone\w*\b", r"\bcap\b", r"\bgorro\w*\b",
        r"\bchapeu\w*\b", r"\bcorrente\w*\b", r"\bcolar\w*\b",
        r"\bpulseira\w*\b", r"\bbracelete\w*\b", r"\banel\w*\b",
        r"\brelogio\w*\b", r"\boculos\w*\b", r"\bcinto\w*\b",
        r"\bbrinco\w*\b", r"\bpiercing\w*\b", r"\bmeia\w*\b",
        r"\bconjunto esportiv\w*\b",
    )
    marcadores_streetwear = (
        r"\bstreetwear\w*\b", r"\bstreet\w*\b", r"\burban\w*\b",
        r"\burbano\w*\b", r"\bskate\w*\b", r"\bskater\w*\b",
        r"\bhip hop\b", r"\boversized\b", r"\bcargo\b", r"\bbaggy\b",
        r"\bwide leg\b", r"\bjordan\b", r"\bdunk\b", r"\bair force\b",
        r"\byeezy\b", r"\bgrunge\b", r"\btrap\b", r"\bpunk\b",
        r"\by2k\b", r"\bold school\b", r"\bsport life\b", r"\bsportlife\b",
        r"\bsportswear\w*\b", r"\besportiv\w*\b", r"\bsport\b",
        r"\btrack\b", r"\bfutebol\b", r"\bsoccer\b", r"\bjersey\w*\b",
        r"\bretro\b", r"\bretr[oô]\b",
    )
    return (
        any(re.search(padrao, texto) for padrao in categorias)
        and any(re.search(padrao, texto) for padrao in marcadores_streetwear)
    )


def categoria_produto(titulo):
    texto = unicodedata.normalize("NFKD", str(titulo or "").casefold())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"[^a-z0-9]+", " ", texto).strip()
    # A palavra "tênis" também aparece em pulseira de tênis; classifique
    # acessórios de corpo antes de tentar identificar calçados.
    if re.search(r"\b(pulseira\w*|corrente\w*|colar\w*|anel\w*|brinco\w*|oculos|bone\w*|chapeu\w*|cinto\w*|relogio\w*)\b", texto):
        return "acessorios"
    if re.search(r"\b(tenis\w*|sneaker\w*)\b", texto):
        return "tenis"
    if re.search(r"\b(calca\w*|jeans|bermuda\w*|shorts?|wide leg)\b", texto):
        return "calcas"
    if re.search(r"\b(camiseta\w*|t shirt|shirt|camisa\w*|moletom\w*|hoodie\w*|jaqueta\w*|bomber|corta vento)\b", texto):
        return "roupas"
    return "acessorios"


def genero_produto(titulo):
    texto = unicodedata.normalize("NFKD", str(titulo or "").casefold())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"[^a-z0-9]+", " ", texto).strip()
    if re.search(r"\b(masculin\w*|homem|homens|mens?|man)\b", texto):
        return "masculino"
    if re.search(r"\b(feminin\w*|mulher\w*|women|woman|girl)\b", texto):
        return "feminino"
    return "unissex"


def ordenar_candidatos_diversos(candidatos):
    """Intercala categorias e dá prioridade aos produtos masculinos."""
    categorias = ("tenis", "calcas", "roupas", "acessorios")
    generos = ("masculino", "unissex", "feminino")
    grupos = {categoria: {genero: [] for genero in generos} for categoria in categorias}
    for candidato in candidatos:
        titulo = candidato[1].get("title")
        grupos[categoria_produto(titulo)][genero_produto(titulo)].append(candidato)
    for por_genero in grupos.values():
        for grupo in por_genero.values():
            grupo.sort(key=lambda item: item[0], reverse=True)

    ordenados = []
    while True:
        progresso = False
        for categoria in categorias:
            for genero in generos:
                if grupos[categoria][genero]:
                    ordenados.append(grupos[categoria][genero].pop(0))
                    progresso = True
                    break
        if not progresso:
            break
    return ordenados


def shopee_graphql(query, variables=None):
    """Faz uma chamada assinada à Open API oficial de afiliados da Shopee BR."""
    if not SHOPEE_APP_ID or not SHOPEE_SECRET:
        return {}
    body = json.dumps({"query": query, "variables": variables or {}},
                      ensure_ascii=False, separators=(",", ":"))
    timestamp = str(int(time.time()))
    assinatura = hashlib.sha256(
        (SHOPEE_APP_ID + timestamp + body + SHOPEE_SECRET).encode("utf-8")
    ).hexdigest()
    headers = {"Content-Type": "application/json",
               "Authorization": f"SHA256 Credential={SHOPEE_APP_ID}, Timestamp={timestamp}, Signature={assinatura}"}
    r = None
    for tentativa in range(3):
        try:
            r = requests.post(SHOPEE_API, data=body.encode("utf-8"),
                              headers=headers, timeout=25)
        except requests.RequestException:
            if tentativa == 2:
                raise
            time.sleep(ATRASOS_RETENTATIVA[tentativa])
            continue
        if not r.ok:
            break
        try:
            dados = r.json()
        except ValueError:
            raise RuntimeError("Shopee respondeu em formato inesperado (JSON inválido).")
        erros = dados.get("errors") or []
        erro_sistema = any(str((e.get("extensions") or {}).get("code")) == "10000"
                           or "10000" in str(e.get("message", "")) for e in erros)
        if erro_sistema and tentativa < 2:
            time.sleep(ATRASOS_RETENTATIVA[tentativa])
            continue
        break
    if not r.ok:
        try:
            resposta_erro = r.json()
            detalhe = resposta_erro.get("message") or resposta_erro.get("error") or ""
        except ValueError:
            detalhe = ""
        raise RuntimeError(f"Shopee HTTP {r.status_code}" + (f": {detalhe}" if detalhe else ""))
    if dados.get("errors"):
        detalhes = []
        for erro in dados["errors"]:
            extensoes = erro.get("extensions") or {}
            codigo = extensoes.get("code")
            mensagem = erro.get("message", "erro")
            detalhe = f"Shopee API{f' [{codigo}]' if codigo is not None else ''}: {mensagem}"
            if str(codigo) == "10000":
                detalhe += " (erro interno da Shopee; confira se o App está ativo e autorizado para a Open API e tente novamente mais tarde)"
            elif str(codigo) == "10035":
                detalhe += " (esta conta ainda não tem acesso à Open API de Afiliados)"
            elif str(codigo) == "10020":
                detalhe += " (confira App ID, Secret, horário do Windows e assinatura)"
            detalhes.append(detalhe)
        raise RuntimeError("; ".join(detalhes))
    return dados.get("data", {})


def buscar_shopee(termo):
    query = """query ProductOffers($keyword: String!, $page: Int!, $limit: Int!) {
      productOfferV2(keyword: $keyword, page: $page, limit: $limit, sortType: 1) {
        nodes { itemId productName priceMin priceDiscountRate imageUrl offerLink productLink }
        pageInfo { hasNextPage }
      }
    }"""
    resultado = shopee_graphql(query, {"keyword": termo, "page": 1, "limit": 20})
    ofertas = resultado.get("productOfferV2", {}).get("nodes", [])
    itens = []
    for oferta in ofertas:
        try:
            preco = float(oferta.get("priceMin") or 0)
            pct = round(float(str(oferta.get("priceDiscountRate") or 0).replace("%", "")))
            if not preco or pct < DESCONTO_MINIMO:
                continue
            original = round(preco / (1 - pct / 100), 2) if pct < 100 else preco
            link_afiliado = (oferta.get("offerLink") or "").strip()
            link_produto = (oferta.get("productLink") or "").strip()
            if not link_afiliado and not link_produto:
                continue
            titulo = oferta.get("productName") or "Produto Shopee"
            if not titulo_relevante(titulo):
                continue
            identificador = oferta.get("itemId") or oferta.get("productId") or link_produto
            itens.append(({"id": f"SHP-{identificador}",
                           "title": titulo,
                           "price": preco, "original_price": original,
                           "thumbnail": oferta.get("imageUrl"),
                           "permalink": link_afiliado or link_produto,
                           "_needs_affiliate_link": not bool(link_afiliado),
                           "shipping": {"free_shipping": False}, "marketplace": "Shopee"}, pct))
        except (TypeError, ValueError):
            continue
    return itens


def gerar_link_afiliado(url_produto, identificador=""):
    """Converte um link de produto Shopee em link rastreado pela conta afiliada."""
    url = str(url_produto or "").strip()
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {"shopee.com.br", "www.shopee.com.br"}:
        raise ValueError("A Shopee não forneceu um link de produto válido para converter.")
    sub_id = str(identificador or "produto").replace("SHP-", "")[:32]
    query = (
        "mutation { generateShortLink(input: {"
        f"originUrl: {json.dumps(url, ensure_ascii=False)} "
        f"subIds: [\"ofertabot\", \"telegram\", {json.dumps(sub_id)}]"
        "}) { shortLink } }"
    )
    resultado = shopee_graphql(query)
    link = ((resultado.get("generateShortLink") or {}).get("shortLink") or "").strip()
    if not link:
        raise RuntimeError("A Shopee não retornou o link curto de afiliado.")
    return link


def brl(v):
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def montar_legenda(item, pct, link):
    loja = item.get("marketplace", "Shopee")
    linhas = [f"🔥 <b>{html.escape(item['title'])}</b>", f"🏬 {loja}", ""]
    if item.get("original_price"):
        linhas.append(f"❌ De: <s>{brl(item['original_price'])}</s>")
    linhas.append(f"✅ Por: <b>{brl(item['price'])}</b> ({pct}% OFF)")
    if item.get("shipping", {}).get("free_shipping"):
        linhas.append("🚚 Frete grátis")
    linhas += ["", f"🛒 <a href=\"{link}\">Comprar agora</a>"]
    return "\n".join(linhas)


def _enviar_telegram_metodo(base, metodo, dados_envio):
    global TELEGRAM_CHAT_ID
    for tentativa in range(2):
        resposta = requests.post(f"{base}/{metodo}", data=dados_envio, timeout=20)
        try:
            dados = resposta.json()
        except ValueError:
            return False, f"Telegram HTTP {resposta.status_code}: resposta inválida"

        migracao = (dados.get("parameters") or {}).get("migrate_to_chat_id")
        if migracao and tentativa == 0 and str(migracao) != str(TELEGRAM_CHAT_ID):
            antigo = str(TELEGRAM_CHAT_ID)
            TELEGRAM_CHAT_ID = str(migracao)
            os.environ["TELEGRAM_CHAT_ID"] = TELEGRAM_CHAT_ID
            callback = CHAT_ID_MIGRADO_CALLBACK
            if callback:
                callback(TELEGRAM_CHAT_ID)
            print(f"[Telegram] O grupo virou supergrupo. ID atualizado e salvo ({antigo} → {TELEGRAM_CHAT_ID}); repetindo o envio.")
            dados_envio = {**dados_envio, "chat_id": TELEGRAM_CHAT_ID}
            continue

        if resposta.ok and dados.get("ok") is True:
            return True, ""
        return False, dados.get("description") or f"Telegram HTTP {resposta.status_code}"
    return False, "O Telegram não confirmou o envio após atualizar o ID do grupo."


def enviar_telegram(item, pct, link):
    foto = (item.get("thumbnail") or "").replace("http://", "https://")
    foto = foto.replace("-I.jpg", "-O.jpg")  # versão maior da imagem
    base = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
    legenda = montar_legenda(item, pct, link)
    falha_foto = ""
    if foto:
        try:
            enviado, falha_foto = _enviar_telegram_metodo(
                base, "sendPhoto",
                {"chat_id": TELEGRAM_CHAT_ID, "photo": foto,
                 "caption": legenda, "parse_mode": "HTML"},
            )
            if enviado:
                return True, ""
        except requests.RequestException as erro:
            falha_foto = str(erro) or "Falha ao enviar a foto"

    try:
        enviado, falha_texto = _enviar_telegram_metodo(
            base, "sendMessage",
            {"chat_id": TELEGRAM_CHAT_ID, "text": legenda,
             "parse_mode": "HTML", "disable_web_page_preview": False},
        )
        if enviado:
            return True, ""
    except requests.RequestException as erro:
        falha_texto = str(erro) or "Falha ao enviar a mensagem"
    detalhe = f"Mensagem: {falha_texto}"
    if falha_foto:
        detalhe = f"Foto: {falha_foto}; {detalhe}"
    return False, detalhe


def db():
    con = sqlite3.connect(DB_FILE)
    con.execute("CREATE TABLE IF NOT EXISTS enviados (id TEXT PRIMARY KEY, ts REAL)")
    # A execução agendada não inicia o painel; crie a tabela compartilhada
    # antes de registrar produtos encontrados no ciclo.
    con.execute("CREATE TABLE IF NOT EXISTS produtos (id TEXT PRIMARY KEY, titulo TEXT, preco REAL, link TEXT, enviado_em REAL)")
    con.execute("CREATE TABLE IF NOT EXISTS enviados_fingerprints (fingerprint TEXT PRIMARY KEY, id TEXT, ts REAL)")
    # Aprende os títulos de ofertas que já foram confirmadas no Telegram antes
    # desta versão, para evitar repeti-las após a atualização.
    try:
        antigos = con.execute("""
            SELECT p.id, p.titulo, e.ts
            FROM produtos p INNER JOIN enviados e ON e.id = p.id
        """).fetchall()
    except sqlite3.OperationalError:
        antigos = []
    for pid, titulo, ts in antigos:
        chave = fingerprint_produto(titulo)
        if chave:
            con.execute("INSERT OR IGNORE INTO enviados_fingerprints VALUES (?, ?, ?)",
                        (chave, pid, ts))
    con.commit()
    return con


def fingerprint_produto(titulo):
    texto = unicodedata.normalize("NFKD", str(titulo or "").casefold())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"[^a-z0-9]+", " ", texto).strip()
    if len(texto) < 12:
        return None
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def tokens_produto(titulo):
    """Retorna palavras que identificam modelo, marca ou corte do produto."""
    texto = unicodedata.normalize("NFKD", str(titulo or "").casefold())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    aliases = {
        "sneaker": "tenis", "sneakers": "tenis", "tshirt": "camiseta",
        "tshirts": "camiseta", "calcas": "calca", "bermudas": "bermuda",
        "camisetas": "camiseta", "camisas": "camisa", "jaquetas": "jaqueta",
        "joggers": "jogger", "pulseiras": "pulseira", "shirt": "camiseta", "shirts": "camiseta",
        "tee": "camiseta", "hoodie": "moletom", "pants": "calca",
        "trousers": "calca", "shorts": "bermuda",
    }
    genericos = {
        "novo", "nova", "novos", "novas", "original", "promocao", "oferta",
        "lancamento", "frete", "gratis", "desconto", "imperdivel", "masculino",
        "masculina", "feminino", "feminina", "unissex", "homem", "mulher",
        "adulto", "adulta", "moda", "fashion", "streetwear", "urbano", "urbana",
        "casual", "premium", "kit", "com", "para", "de", "do", "da", "em",
        "e", "o", "a", "os", "as", "unidade", "unidades", "produto", "loja",
        "y2k", "sport", "sports", "sportswear", "skate", "retro", "vintage",
        "hiphop", "hip", "hop", "estilo", "tendencia", "novoestilo",
        "preto", "preta", "pretos", "pretas", "branco", "branca", "brancos",
        "brancas", "azul", "azuis", "marinho", "marinha", "escuro", "escura", "vermelho", "vermelha", "vermelhos",
        "vermelhas", "verde", "verdes", "rosa", "rosas", "cinza", "cinzas",
        "bege", "marrom", "roxo", "roxa", "bolso", "bolsos",
        "algodao", "sarja", "tactel", "tecido", "manga", "curta", "curto", "longa", "longo", "amarelo", "amarela", "p", "m",
        "g", "gg", "xg", "xl", "xxl", "tamanho", "cor", "cores",
    }
    tokens = {aliases.get(t, t) for t in re.findall(r"[a-z0-9]+", texto)}
    return {token for token in tokens
            if len(token) > 1 and token not in genericos and not token.isdigit()}


def produtos_parecidos(titulo_a, titulo_b):
    """Bloqueia o mesmo produto mesmo com variações de título, cor ou tamanho."""
    if categoria_produto(titulo_a) != categoria_produto(titulo_b):
        return False
    tokens_a, tokens_b = tokens_produto(titulo_a), tokens_produto(titulo_b)
    if not tokens_a or not tokens_b:
        return False
    if tokens_a == tokens_b:
        return True
    em_comum = len(tokens_a & tokens_b)
    proporcao_menor = em_comum / min(len(tokens_a), len(tokens_b))
    proporcao_total = em_comum / len(tokens_a | tokens_b)
    similares_por_nome = SequenceMatcher(
        None, " ".join(sorted(tokens_a)), " ".join(sorted(tokens_b))
    ).ratio()
    return (
        (em_comum >= 2 and proporcao_menor >= 0.67 and proporcao_total >= 0.40)
        or (em_comum >= 3 and proporcao_menor >= 0.60 and proporcao_total >= 0.35)
        or similares_por_nome >= 0.82
    )


def titulos_enviados_recentes(con):
    desde = time.time() - DEDUP_JANELA_DIAS * 24 * 60 * 60
    try:
        return [row[0] for row in con.execute("""
            SELECT DISTINCT p.titulo
            FROM produtos p INNER JOIN enviados e ON e.id = p.id
            WHERE e.ts >= ? AND p.titulo IS NOT NULL
        """, (desde,)).fetchall()]
    except sqlite3.OperationalError:
        return []


def ciclo():
    con = db()
    candidatos = []
    for termo in TERMOS:
        restante = TERMOS_EM_ESPERA.get(termo, 0) - time.monotonic()
        if restante > 0:
            continue
        try:
            for item, pct in buscar_shopee(termo):
                candidatos.append((pct, item))
            TERMOS_EM_ESPERA.pop(termo, None)
        except Exception as e:
            detalhe = str(e)
            if "10000" in detalhe or "System Error" in detalhe:
                TERMOS_EM_ESPERA[termo] = time.monotonic() + max(1800, INTERVALO_MINUTOS * 60)
                print(f"[Shopee] Busca '{termo}' temporariamente indisponível; será tentada novamente depois. As outras buscas continuam.")
            else:
                print(f"[erro Shopee busca '{termo}']: {detalhe}")

    candidatos = ordenar_candidatos_diversos(candidatos)
    enviados = 0
    acessorios_enviados = 0
    masculinos_enviados = 0
    ja_enviados = {row[0] for row in con.execute(
        "SELECT fingerprint FROM enviados_fingerprints").fetchall()}
    titulos_conhecidos = titulos_enviados_recentes(con)
    vistos_neste_ciclo = set()
    semelhantes_ignorados = 0
    for pct, item in candidatos:
        if enviados >= MAX_ENVIOS_POR_CICLO:
            break
        if not titulo_relevante(item.get("title")):
            continue
        categoria = categoria_produto(item.get("title"))
        if categoria == "acessorios" and acessorios_enviados >= 1:
            continue
        if con.execute("SELECT 1 FROM enviados WHERE id=?", (item["id"],)).fetchone():
            continue
        fingerprint = fingerprint_produto(item.get("title"))
        if fingerprint and (fingerprint in ja_enviados or fingerprint in vistos_neste_ciclo):
            continue
        if any(produtos_parecidos(item.get("title"), titulo) for titulo in titulos_conhecidos):
            semelhantes_ignorados += 1
            continue
        if fingerprint:
            vistos_neste_ciclo.add(fingerprint)
        afiliado = item["permalink"]
        if item.get("_needs_affiliate_link"):
            try:
                afiliado = gerar_link_afiliado(afiliado, item["id"])
                item["permalink"] = afiliado
                print(f"[Shopee] Link de afiliado gerado para {item['title'][:60]}.")
            except Exception as erro:
                print(f"[erro link de afiliado '{item['title'][:60]}']: {erro}")
                continue
        link = f"{BASE_URL}/r/{item['id']}" if BASE_URL else afiliado
        painel.registrar_produto(item["id"], item["title"], item["price"], afiliado)
        sucesso, erro_envio = enviar_telegram(item, pct, link)
        if sucesso:
            con.execute("INSERT INTO enviados VALUES (?, ?)", (item["id"], time.time()))
            if fingerprint:
                con.execute("INSERT OR IGNORE INTO enviados_fingerprints VALUES (?, ?, ?)",
                            (fingerprint, item["id"], time.time()))
            con.commit()
            if fingerprint:
                ja_enviados.add(fingerprint)
            titulos_conhecidos.append(item.get("title", ""))
            enviados += 1
            if categoria == "acessorios":
                acessorios_enviados += 1
            if genero_produto(item.get("title")) == "masculino":
                masculinos_enviados += 1
            print(f"[ok] {item['title'][:60]} ({pct}%)")
            time.sleep(3)  # evita limite de envio do Telegram
        else:
            print(f"[não enviado ao Telegram] {item['title'][:60]}: {erro_envio}")
    con.close()
    print(
        f"Ciclo concluído: {enviados} enviados "
        f"({enviados - acessorios_enviados} roupas/calçados, "
        f"{acessorios_enviados} acessórios, {masculinos_enviados} masculinos; "
        f"{semelhantes_ignorados} ofertas parecidas ignoradas)."
    )


def main():
    if not SHOPEE_APP_ID or not SHOPEE_SECRET:
        raise RuntimeError("Preencha o App ID e o Secret da Open API de Afiliados Shopee.")
    if os.environ.get("RUN_ONCE", "").strip() == "1":
        ciclo()
        return
    painel.iniciar(DB_FILE, PAINEL_SENHA, TAXA_COMISSAO, PORT, HOST)
    while True:
        try:
            ciclo()
        except Exception as e:
            print(f"[erro no ciclo]: {e}")
        time.sleep(INTERVALO_MINUTOS * 60)


if __name__ == "__main__":
    main()
