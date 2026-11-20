"""Painel local do OfertaBot: cliques, compras e comissão estimada."""
import hmac
import html
import sqlite3
import threading
import time

from flask import Flask, Response, redirect, request
from werkzeug.serving import make_server

app = Flask(__name__)
CFG = {"db": "enviados.db", "senha": "", "taxa": 9.0}


def _db():
    con = sqlite3.connect(CFG["db"])
    con.row_factory = sqlite3.Row
    return con


def iniciar(db_file, senha, taxa, porta, host="0.0.0.0"):
    CFG.update(db=db_file, senha=senha, taxa=taxa)
    con = _db()
    con.executescript("""
        CREATE TABLE IF NOT EXISTS produtos (id TEXT PRIMARY KEY, titulo TEXT, preco REAL, link TEXT, enviado_em REAL);
        CREATE TABLE IF NOT EXISTS cliques (produto_id TEXT, ts REAL);
        CREATE TABLE IF NOT EXISTS vendas (id INTEGER PRIMARY KEY AUTOINCREMENT, produto_id TEXT, valor REAL, comissao REAL, ts REAL);
        CREATE TABLE IF NOT EXISTS enviados (id TEXT PRIMARY KEY, ts REAL);
    """)
    con.commit()
    con.close()
    def servir_painel():
        # Inicia o WSGI local diretamente: Flask.app.run() imprime no registro
        # a advertência de servidor de desenvolvimento e o banner de inicialização.
        servidor = make_server(host, porta, app, threaded=True)
        servidor.serve_forever()

    threading.Thread(target=servir_painel, daemon=True).start()


def registrar_produto(pid, titulo, preco, link):
    con = _db()
    con.execute("INSERT OR REPLACE INTO produtos VALUES (?,?,?,?,?)",
                (pid, titulo, preco, link, time.time()))
    con.commit()
    con.close()


def _autorizado():
    a = request.authorization
    return bool(CFG["senha"]) and a is not None and hmac.compare_digest(a.password or "", CFG["senha"])


def _pedir_login():
    return Response("Acesso restrito", 401, {"WWW-Authenticate": 'Basic realm="Painel"'})


def brl(v):
    return "R$ " + f"{(v or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


@app.route("/r/<pid>")
def clique(pid):
    con = _db()
    row = con.execute("SELECT link FROM produtos WHERE id=?", (pid,)).fetchone()
    if not row:
        con.close()
        return "Produto não encontrado", 404
    ua = (request.headers.get("User-Agent") or "").lower()
    if not any(b in ua for b in ("bot", "crawler", "spider", "preview")):
        con.execute("INSERT INTO cliques VALUES (?,?)", (pid, time.time()))
        con.commit()
    con.close()
    return redirect(row["link"], code=302)


@app.route("/painel/venda", methods=["POST"])
def nova_venda():
    if not _autorizado():
        return _pedir_login()
    try:
        valor = float(request.form["valor"].replace(",", "."))
        com = request.form.get("comissao", "").strip()
        comissao = float(com.replace(",", ".")) if com else None
    except (KeyError, ValueError):
        return "Valor inválido", 400
    con = _db()
    con.execute("INSERT INTO vendas (produto_id, valor, comissao, ts) VALUES (?,?,?,?)",
                (request.form.get("produto_id"), valor, comissao, time.time()))
    con.commit()
    con.close()
    return redirect("/painel")


@app.route("/painel")
def painel():
    if not _autorizado():
        return _pedir_login()
    taxa = CFG["taxa"]
    con = _db()
    linhas = con.execute("""
        SELECT p.id, p.titulo, p.preco,
          (SELECT COUNT(*) FROM cliques c WHERE c.produto_id = p.id) AS cliques,
          (SELECT COUNT(*) FROM vendas v WHERE v.produto_id = p.id) AS compras,
          (SELECT COALESCE(SUM(COALESCE(v.comissao, v.valor * ? / 100.0)), 0)
             FROM vendas v WHERE v.produto_id = p.id) AS comissao
        FROM produtos p INNER JOIN enviados e ON e.id = p.id
        ORDER BY cliques DESC, p.enviado_em DESC LIMIT 100
    """, (taxa,)).fetchall()
    tot = con.execute("SELECT COUNT(*) n, COALESCE(SUM(valor),0) vv, "
                      "COALESCE(SUM(COALESCE(comissao, valor * ? / 100.0)),0) cc FROM vendas",
                      (taxa,)).fetchone()
    cliques = con.execute("SELECT COUNT(*) FROM cliques").fetchone()[0]
    enviados = con.execute("SELECT COUNT(*) FROM enviados").fetchone()[0]
    con.close()

    conv = f"{(tot['n'] / cliques * 100):.1f}%" if cliques else "—"
    opcoes = "".join(
        f"<option value='{html.escape(r['id'], quote=True)}'>{html.escape((r['titulo'] or '')[:60])}</option>"
        for r in linhas
    ) or "<option value=''>Nenhum produto enviado ainda</option>"
    tabela = "".join(
        f"<tr><td><div class='product'><span class='thumb'>🛍️</span><span><b>{html.escape((r['titulo'] or '')[:70])}</b>"
        f"<small>{'Shopee' if str(r['id']).startswith('SHP-') else 'Mercado Livre'} · {html.escape(str(r['id']))}</small></span></div></td>"
        f"<td>{r['cliques']}</td><td>{r['compras']}</td><td>{brl(r['comissao'])}</td></tr>"
        for r in linhas
    ) or "<tr><td colspan='4' class='empty'>As ofertas enviadas aparecerão aqui.</td></tr>"

    return f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="theme-color" content="#f5f5f3">
<title>OfertaBot — Painel de ofertas</title><link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700;800&family=Manrope:wght@500;600;700;800&display=swap" rel="stylesheet">
<style>
:root{{--bg:#f5f5f3;--paper:#fff;--ink:#20221f;--muted:#777a72;--line:#e8e9e4;--green:#177653;--soft:#eaf3eb;--lime:#dbf6ad;--shadow:0 8px 28px #242b2010}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px 'DM Sans',sans-serif}}button,input,select{{font:inherit}}
.layout{{min-height:100vh;display:grid;grid-template-columns:235px 1fr}}.side{{background:#fff;border-right:1px solid var(--line);padding:25px 17px;display:flex;flex-direction:column;gap:30px}}
.brand{{display:flex;align-items:center;gap:10px;padding:0 9px;font:800 18px Manrope}}.mark{{background:var(--green);color:#fff;border-radius:12px;width:36px;height:36px;display:grid;place-items:center;font-size:18px}}
.brand small{{display:block;color:#9a9c96;font:500 10px 'DM Sans';letter-spacing:.9px;text-transform:uppercase;margin-top:1px}}.navtitle{{color:#a1a39d;font-weight:700;font-size:10px;letter-spacing:1.3px;padding:0 11px;margin:4px 0 6px}}
.nav{{display:grid;gap:4px}}.nav a{{text-decoration:none;padding:11px;border-radius:10px;color:#61645e;font-weight:600;display:flex;gap:11px;align-items:center}}.nav a.active{{background:#edf5ef;color:var(--green)}}
.ico{{font-size:17px;width:20px;text-align:center}}.sidefoot{{margin-top:auto;border:1px solid var(--line);padding:14px;border-radius:13px;background:#fafbf9}}.sidefoot strong{{font-size:12px}}.sidefoot p{{font-size:11px;color:var(--muted);line-height:1.55;margin:7px 0 0}}
.main{{padding:27px 36px 42px;max-width:1500px;width:100%;margin:auto}}.top{{display:flex;justify-content:space-between;align-items:center;margin-bottom:29px}}.crumb{{font-size:12px;color:var(--muted)}}.crumb b{{color:#42443e}}
.badge{{border:1px solid var(--line);background:#fff;border-radius:20px;padding:8px 12px;font-size:11px;color:#676a63}}.heading{{display:flex;align-items:flex-end;justify-content:space-between;margin-bottom:23px}}
.eyebrow{{color:var(--green);font-weight:800;letter-spacing:1.6px;font-size:10px;text-transform:uppercase}}h1{{font:800 30px Manrope;margin:6px 0 4px;letter-spacing:-1.1px}}.heading p{{margin:0;color:var(--muted)}}
.stats{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:18px}}.stat,.panel{{background:#fff;border:1px solid var(--line);border-radius:15px;box-shadow:var(--shadow)}}.stat{{padding:17px 19px}}
.label{{color:var(--muted);font-size:12px}}.num{{font:800 25px Manrope;margin:9px 0 5px;letter-spacing:-.8px}}.hint{{font-size:11px;color:#888b84}}
.grid{{display:grid;grid-template-columns:minmax(0,1.7fr) minmax(280px,.8fr);gap:17px}}.panelhead{{display:flex;justify-content:space-between;align-items:center;padding:17px 19px;border-bottom:1px solid var(--line)}}
.panelhead h2{{font:800 15px Manrope;margin:0}}.panelhead span{{color:var(--muted);font-size:11px}}.tablewrap{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;min-width:510px}}th{{text-align:left;color:#8a8c85;font-size:10px;letter-spacing:.6px;text-transform:uppercase;font-weight:700;padding:12px 18px;border-bottom:1px solid #f0f1ed}}td{{padding:13px 18px;border-bottom:1px solid #f0f1ed;font-size:12px}}tr:last-child td{{border-bottom:0}}
.product{{display:flex;align-items:center;gap:10px;max-width:420px}}.thumb{{height:39px;width:39px;background:#f4f5f1;border-radius:10px;display:grid;place-items:center;font-size:19px;flex:none}}.product b{{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:350px;font-size:11px}}.product small{{display:block;color:#92948e;margin-top:4px;font-size:10px}}.empty{{text-align:center!important;color:#92948e;padding:25px!important}}
.right{{display:grid;align-content:start;gap:16px}}.formbox{{padding:17px 19px}}.formbox p{{font-size:11px;line-height:1.55;color:var(--muted);margin:0 0 13px}}label{{display:block;font-size:11px;color:#656860;font-weight:700;margin:10px 0 6px}}input,select{{width:100%;border:1px solid #dfe1da;border-radius:9px;padding:10px 11px;background:#fff;outline:0}}input:focus,select:focus{{border-color:#68a382;box-shadow:0 0 0 3px #17765314}}.row{{display:grid;grid-template-columns:1fr 1fr;gap:9px}}
.primary{{margin-top:13px;width:100%;border:0;border-radius:10px;background:var(--green);color:white;padding:11px 15px;font-weight:700;box-shadow:0 5px 12px #17765325;cursor:pointer}}.primary:hover{{background:#116746}}.note{{margin-top:12px;padding:12px 13px;border:1px solid #e7e9df;border-radius:11px;background:#fbfcf8;color:#777a72;font-size:10px;line-height:1.55}}.note b{{color:#4d5149}}
@media(max-width:1000px){{.layout{{grid-template-columns:70px 1fr}}.side{{padding:20px 10px;align-items:center}}.brandtxt,.navtitle,.nav a span:not(.ico),.sidefoot{{display:none}}.brand{{padding:0}}.nav{{width:100%}}.nav a{{justify-content:center;padding:12px 0}}.main{{padding:22px}}.grid{{grid-template-columns:1fr}}.right{{grid-template-columns:1fr 1fr}}.stats{{grid-template-columns:repeat(2,1fr)}}}}
@media(max-width:650px){{.layout{{display:block}}.side{{display:none}}.main{{padding:18px 13px}}.heading{{align-items:flex-start;gap:12px;flex-direction:column}}h1{{font-size:25px}}.top{{margin-bottom:19px}}.stats{{gap:8px}}.stat{{padding:13px}}.num{{font-size:21px}}.right{{grid-template-columns:1fr}}.panelhead{{padding:14px}}}}
</style></head><body><div class="layout"><aside class="side"><div class="brand"><div class="mark">O</div><div class="brandtxt">oferta bot<small>painel de ofertas</small></div></div>
<div style="width:100%"><div class="navtitle">MENU PRINCIPAL</div><nav class="nav"><a class="active" href="/painel"><span class="ico">▦</span><span>Painel</span></a><a href="#produtos"><span class="ico">◉</span><span>Ofertas enviadas</span></a><a href="#venda"><span class="ico">＋</span><span>Registrar compra</span></a></nav></div>
<div class="sidefoot"><strong>Suas recomendações em um só lugar</strong><p>Acompanhe cliques, vendas e comissões estimadas das ofertas enviadas ao Telegram.</p></div></aside>
<main class="main"><div class="top"><div class="crumb">Workspace&nbsp; / &nbsp;<b>Painel</b></div><div class="badge">●&nbsp; Operação local</div></div>
<div class="heading"><div><div class="eyebrow">CENTRAL DE OFERTAS · TELEGRAM</div><h1>Seu painel de ofertas</h1><p>Acompanhe o desempenho dos produtos que você compartilha.</p></div></div>
<section class="stats"><div class="stat"><div class="label">Produtos enviados</div><div class="num">{enviados}</div><div class="hint">na sua lista</div></div><div class="stat"><div class="label">Cliques</div><div class="num">{cliques}</div><div class="hint">acessos aos links rastreados</div></div><div class="stat"><div class="label">Compras registradas</div><div class="num">{tot['n']}</div><div class="hint">lançadas por você</div></div><div class="stat"><div class="label">Comissão estimada</div><div class="num">{brl(tot['cc'])}</div><div class="hint">conversão: {conv}</div></div></section>
<div class="grid"><section class="panel" id="produtos"><div class="panelhead"><div><h2>Desempenho por produto</h2><span>Ofertas enviadas para seus grupos</span></div><span>{enviados} itens</span></div><div class="tablewrap"><table><thead><tr><th>Produto</th><th>Cliques</th><th>Compras</th><th>Comissão est.</th></tr></thead><tbody>{tabela}</tbody></table></div></section>
<div class="right"><section class="panel" id="venda"><div class="panelhead"><div><h2>Registrar compra</h2><span>Atualize seus resultados</span></div><span>＋</span></div><div class="formbox"><p>Informe uma venda confirmada no painel da sua plataforma de afiliados.</p><form method="post" action="/painel/venda"><label>Produto</label><select name="produto_id">{opcoes}</select><div class="row"><div><label>Valor da compra</label><input name="valor" inputmode="decimal" placeholder="Ex.: 199,90" required></div><div><label>Comissão real</label><input name="comissao" inputmode="decimal" placeholder="Opcional"></div></div><button class="primary" type="submit">Salvar compra</button></form><div class="note"><b>Como funciona:</b> sem comissão real informada, o painel estima {taxa:g}% do valor da venda. Confira os valores oficiais nos portais de afiliados.</div></div></section>
<section class="panel"><div class="panelhead"><div><h2>Sobre os dados</h2><span>Atualização manual</span></div><span>i</span></div><div class="formbox"><p>Os cliques são contados quando alguém abre um link rastreado pelo painel. Registre as compras após conferir os relatórios do Mercado Livre ou da Shopee.</p><div class="note"><b>Privacidade:</b> este painel fica disponível apenas neste computador.</div></div></section></div></div></main></div></body></html>"""
