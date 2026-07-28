#!/usr/bin/env python3
"""
Mock da API Aurora Gás — Fluxo 2ª Via & Atualização Cadastral
Workshop Partner Program · indigo.ai

Servidor stateful, SEM dependências externas (só stdlib). Roda com:
    python3 aurora_mock.py            # porta 8080
    PORT=9000 python3 aurora_mock.py  # outra porta

Para expor publicamente (a plataforma indigo.ai é cloud e não enxerga localhost):
    cloudflared tunnel --url http://localhost:8080     # sem cadastro
    # ou
    ngrok http 8080

Multi-equipe (mock compartilhado): mande o header `X-Workshop-Team: <nome>`.
Cada equipe tem seu próprio estado isolado. Sem o header, usa o namespace "shared".

Endpoints de apoio (fora do swagger, para facilitador/debug):
    GET  /admin/state   → inspeciona o estado da equipe
    POST /admin/reset   → zera o estado da equipe (recarrega os dados-semente)
    GET  /health        → healthcheck
"""

import copy
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# --------------------------------------------------------------------------
# Dados-semente (personas desenhadas para cobrir cada ramo do fluxograma)
# --------------------------------------------------------------------------
SEED = {
    "clientes": {
        # Maria — 1 instalação, 2 contas em aberto → CAMINHO FELIZ da 2ª via
        "CLI-1001": {
            "cpf": "11111111111", "nome": "Maria Souza",
            "telefone_cadastro": "+5511999990001", "email_cadastro": "maria@exemplo.com",
            "instalacoes": ["INST-1001"],
        },
        # João — 1 instalação, NENHUMA conta em aberto → caminho "valor/vencimento / 3 últimas"
        "CLI-2001": {
            "cpf": "22222222222", "nome": "Joao Lima",
            "telefone_cadastro": "+5511999990002", "email_cadastro": "joao@exemplo.com",
            "instalacoes": ["INST-2001"],
        },
        # Ana — 3 instalações → caminho "validar CEP/rua"
        "CLI-3001": {
            "cpf": "33333333333", "nome": "Ana Prado",
            "telefone_cadastro": "+5511999990003", "email_cadastro": "ana@exemplo.com",
            "instalacoes": ["INST-3001", "INST-3002", "INST-3003"],
        },
        # Carlos — 1 instalação, 1 conta aberta. Ao enviar a 2ª via para um telefone
        # DIFERENTE do cadastro, dispara a oferta de atualizar cadastro.
        "CLI-4001": {
            "cpf": "44444444444", "nome": "Carlos Dias",
            "telefone_cadastro": "+5511999990044", "email_cadastro": "carlos@exemplo.com",
            "instalacoes": ["INST-4001"],
        },
    },
    "instalacoes": {
        "INST-1001": {"cliente_id": "CLI-1001", "endereco": "Rua das Acacias, 100 - Sao Paulo/SP",
                      "rua": "Rua das Acacias", "cep": "01000-000"},
        "INST-2001": {"cliente_id": "CLI-2001", "endereco": "Av. Paulista, 2000 - Sao Paulo/SP",
                      "rua": "Avenida Paulista", "cep": "01310-000"},
        "INST-3001": {"cliente_id": "CLI-3001", "endereco": "Rua Azul, 10 - Sao Paulo/SP",
                      "rua": "Rua Azul", "cep": "02000-000"},
        "INST-3002": {"cliente_id": "CLI-3001", "endereco": "Rua Verde, 20 - Sao Paulo/SP",
                      "rua": "Rua Verde", "cep": "03000-000"},
        "INST-3003": {"cliente_id": "CLI-3001", "endereco": "Rua Amarela, 30 - Sao Paulo/SP",
                      "rua": "Rua Amarela", "cep": "04000-000"},
        "INST-4001": {"cliente_id": "CLI-4001", "endereco": "Rua do Sol, 44 - Sao Paulo/SP",
                      "rua": "Rua do Sol", "cep": "05000-000"},
    },
    "contas": {
        "CT-1001A": {"instalacao_id": "INST-1001", "cliente_id": "CLI-1001", "referencia": "03/2026",
                     "valor": 187.40, "vencimento": "2026-03-10", "status": "aberto", "documento_url": None},
        "CT-1001B": {"instalacao_id": "INST-1001", "cliente_id": "CLI-1001", "referencia": "04/2026",
                     "valor": 203.10, "vencimento": "2026-04-10", "status": "aberto", "documento_url": None},
        "CT-2001A": {"instalacao_id": "INST-2001", "cliente_id": "CLI-2001", "referencia": "01/2026",
                     "valor": 150.00, "vencimento": "2026-01-10", "status": "paga", "documento_url": None},
        "CT-2001B": {"instalacao_id": "INST-2001", "cliente_id": "CLI-2001", "referencia": "02/2026",
                     "valor": 165.30, "vencimento": "2026-02-10", "status": "paga", "documento_url": None},
        "CT-2001C": {"instalacao_id": "INST-2001", "cliente_id": "CLI-2001", "referencia": "12/2025",
                     "valor": 142.00, "vencimento": "2025-12-10", "status": "paga", "documento_url": None},
        "CT-3001A": {"instalacao_id": "INST-3001", "cliente_id": "CLI-3001", "referencia": "04/2026",
                     "valor": 198.70, "vencimento": "2026-04-12", "status": "aberto", "documento_url": None},
        "CT-3002A": {"instalacao_id": "INST-3002", "cliente_id": "CLI-3001", "referencia": "04/2026",
                     "valor": 88.20, "vencimento": "2026-04-12", "status": "aberto", "documento_url": None},
        "CT-3003A": {"instalacao_id": "INST-3003", "cliente_id": "CLI-3001", "referencia": "04/2026",
                     "valor": 342.00, "vencimento": "2026-04-12", "status": "aberto", "documento_url": None},
        "CT-4001A": {"instalacao_id": "INST-4001", "cliente_id": "CLI-4001", "referencia": "05/2026",
                     "valor": 210.00, "vencimento": "2026-05-10", "status": "aberto", "documento_url": None},
    },
    "_protocolo_seq": 90000,
}

# Estado por equipe (namespace isolado). Preenchido on-demand a partir do SEED.
STORE = {}


def store_for(team):
    if team not in STORE:
        STORE[team] = copy.deepcopy(SEED)
    return STORE[team]


def find_cliente(store, cpf=None, codigo_instalacao=None):
    if cpf:
        cpf_norm = re.sub(r"\D", "", cpf)
        for cid, c in store["clientes"].items():
            if c["cpf"] == cpf_norm:
                return cid
    if codigo_instalacao:
        # Aceita "INST-1001" ou só "1001"
        code = codigo_instalacao.upper().replace("INST-", "")
        for iid, inst in store["instalacoes"].items():
            if iid.replace("INST-", "") == code:
                return inst["cliente_id"]
    return None


def cliente_payload(store, cliente_id):
    c = store["clientes"][cliente_id]
    insts = []
    for iid in c["instalacoes"]:
        inst = store["instalacoes"][iid]
        insts.append({"instalacao_id": iid, "endereco": inst["endereco"],
                      "rua": inst["rua"], "cep": inst["cep"]})
    return {
        "cliente_id": cliente_id, "nome": c["nome"],
        "telefone_cadastro": c["telefone_cadastro"], "email_cadastro": c["email_cadastro"],
        "qtd_instalacoes": len(insts), "instalacoes": insts,
    }


def conta_payload(cid, conta):
    return {"conta_id": cid, "referencia": conta["referencia"], "valor": conta["valor"],
            "vencimento": conta["vencimento"], "status": conta["status"]}


# --------------------------------------------------------------------------
# Handlers (retornam (status_code, dict))
# --------------------------------------------------------------------------
def h_identificar(store, m, body):
    cpf = body.get("cpf")
    codigo = body.get("codigo_instalacao")
    if not cpf and not codigo:
        return 400, {"erro": "Informe cpf ou codigo_instalacao."}
    cid = find_cliente(store, cpf, codigo)
    if not cid:
        return 404, {"erro": "Cliente nao encontrado. Verifique o CPF ou o codigo de instalacao."}
    return 200, cliente_payload(store, cid)


def h_consultar_contas(store, m, body, query):
    iid = m.group("iid")
    if iid not in store["instalacoes"]:
        return 404, {"erro": "Instalacao nao encontrada."}
    status = (query.get("status") or "aberto").lower()
    limit = int(query.get("limit") or 10)
    contas = [(k, v) for k, v in store["contas"].items() if v["instalacao_id"] == iid]
    if status == "aberto":
        contas = [(k, v) for k, v in contas if v["status"] == "aberto"]
    contas.sort(key=lambda kv: kv[1]["vencimento"], reverse=True)
    contas = contas[:limit]
    return 200, {"instalacao_id": iid, "total": len(contas),
                 "contas": [conta_payload(k, v) for k, v in contas]}


def h_buscar_conta(store, m, body):
    iid = m.group("iid")
    if iid not in store["instalacoes"]:
        return 404, {"erro": "Instalacao nao encontrada."}
    valor = body.get("valor")
    venc = body.get("vencimento")
    if valor is None and not venc:
        return 400, {"erro": "Informe valor e/ou vencimento para localizar a conta."}
    out = []
    for k, v in store["contas"].items():
        if v["instalacao_id"] != iid:
            continue
        ok = True
        if valor is not None and abs(float(v["valor"]) - float(valor)) > 0.01:
            ok = False
        if venc and v["vencimento"] != venc:
            ok = False
        if ok:
            out.append(conta_payload(k, v))
    return 200, {"instalacao_id": iid, "total": len(out), "contas": out}


def h_validar_endereco(store, m, body):
    cid = m.group("cid")
    if cid not in store["clientes"]:
        return 404, {"erro": "Cliente nao encontrado."}
    cep = re.sub(r"\D", "", body.get("cep", "") or "")
    rua = (body.get("rua") or "").strip().lower()
    for iid in store["clientes"][cid]["instalacoes"]:
        inst = store["instalacoes"][iid]
        cep_ok = cep and re.sub(r"\D", "", inst["cep"]) == cep
        rua_ok = rua and rua in inst["rua"].lower()
        if cep_ok or rua_ok:
            return 200, {"match": True, "instalacao_id": iid, "endereco": inst["endereco"]}
    return 404, {"match": False, "erro": "Nenhuma instalacao bateu com o CEP/rua informados."}


def h_segunda_via(store, m, body):
    ct = m.group("ct")
    conta = store["contas"].get(ct)
    if not conta:
        return 404, {"erro": "Conta nao encontrada."}
    url = f"https://aurora.example/2via/{ct}.pdf"
    conta["documento_url"] = url
    return 200, {"conta_id": ct, "tipo": "2via", "documento_url": url,
                 "valor": conta["valor"], "vencimento": conta["vencimento"]}


def h_codigo_barras(store, m, body):
    ct = m.group("ct")
    conta = store["contas"].get(ct)
    if not conta:
        return 404, {"erro": "Conta nao encontrada."}
    linha = "83660000000-1 " + ct.replace("CT-", "")
    return 200, {"conta_id": ct, "tipo": "codigo_barras",
                 "linha_digitavel": linha, "codigo_barras": "8366" + "0" * 20,
                 "valor": conta["valor"], "vencimento": conta["vencimento"]}


def h_enviar(store, m, body):
    canal = (body.get("canal") or "").lower()
    destino = (body.get("destino") or "").strip()
    conta_id = body.get("conta_id")
    tipo = body.get("tipo") or "2via"
    if canal not in ("whatsapp", "email") or not destino or not conta_id:
        return 400, {"erro": "Informe canal (whatsapp|email), destino e conta_id."}
    conta = store["contas"].get(conta_id)
    if not conta:
        return 404, {"erro": "Conta nao encontrada."}
    store["_protocolo_seq"] += 1
    protocolo = f"PROT-{store['_protocolo_seq']}"
    cliente = store["clientes"][conta["cliente_id"]]
    telefone_diverge = False
    if canal == "whatsapp":
        norm = lambda s: re.sub(r"\D", "", s)
        telefone_diverge = norm(destino) != norm(cliente["telefone_cadastro"])
    return 200, {"protocolo": protocolo, "status": "enviado", "canal": canal,
                 "destino": destino, "tipo": tipo, "conta_id": conta_id,
                 "telefone_diverge": telefone_diverge}


def h_atualizar_cadastro(store, m, body):
    cid = m.group("cid")
    c = store["clientes"].get(cid)
    if not c:
        return 404, {"erro": "Cliente nao encontrado."}
    email = body.get("email")
    telefone = body.get("telefone")
    if not email and not telefone:
        return 400, {"erro": "Informe email e/ou telefone para atualizar."}
    if email:
        c["email_cadastro"] = email
    if telefone:
        c["telefone_cadastro"] = telefone
    return 200, {"status": "atualizado", "cliente": cliente_payload(store, cid)}


# (method, compiled_regex, handler, needs_query)
ROUTES = [
    ("POST", re.compile(r"^/identificar$"), lambda s, m, b, q: h_identificar(s, m, b)),
    ("GET",  re.compile(r"^/instalacoes/(?P<iid>[^/]+)/contas$"), lambda s, m, b, q: h_consultar_contas(s, m, b, q)),
    ("POST", re.compile(r"^/instalacoes/(?P<iid>[^/]+)/contas/buscar$"), lambda s, m, b, q: h_buscar_conta(s, m, b)),
    ("POST", re.compile(r"^/clientes/(?P<cid>[^/]+)/validar-endereco$"), lambda s, m, b, q: h_validar_endereco(s, m, b)),
    ("POST", re.compile(r"^/contas/(?P<ct>[^/]+)/segunda-via$"), lambda s, m, b, q: h_segunda_via(s, m, b)),
    ("POST", re.compile(r"^/contas/(?P<ct>[^/]+)/codigo-barras$"), lambda s, m, b, q: h_codigo_barras(s, m, b)),
    ("POST", re.compile(r"^/envios$"), lambda s, m, b, q: h_enviar(s, m, b)),
    ("PATCH", re.compile(r"^/clientes/(?P<cid>[^/]+)/cadastro$"), lambda s, m, b, q: h_atualizar_cadastro(s, m, b)),
]


class Handler(BaseHTTPRequestHandler):
    server_version = "Aurora GásMock/1.0"

    def _team(self):
        return self.headers.get("X-Workshop-Team", "shared")

    def _send(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PATCH,OPTIONS")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _split(self):
        path = self.path.split("?", 1)
        p = path[0].rstrip("/") or "/"
        query = {}
        if len(path) > 1:
            for pair in path[1].split("&"):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    query[k] = v
        return p, query

    def do_OPTIONS(self):
        self._send(204, {})

    def do_GET(self):
        p, q = self._split()
        if p == "/health":
            return self._send(200, {"status": "ok", "service": "aurora-mock"})
        if p == "/admin/state":
            return self._send(200, store_for(self._team()))
        self._dispatch("GET", p, q, {})

    def do_POST(self):
        p, q = self._split()
        if p == "/admin/reset":
            STORE.pop(self._team(), None)
            return self._send(200, {"status": "reset", "team": self._team()})
        self._dispatch("POST", p, q, self._read_body())

    def do_PATCH(self):
        p, q = self._split()
        self._dispatch("PATCH", p, q, self._read_body())

    def _dispatch(self, method, path, query, body):
        store = store_for(self._team())
        for meth, rgx, fn in ROUTES:
            if meth != method:
                continue
            m = rgx.match(path)
            if m:
                try:
                    code, payload = fn(store, m, body, query)
                except Exception as e:  # noqa
                    code, payload = 500, {"erro": f"Erro interno: {e}"}
                return self._send(code, payload)
        self._send(404, {"erro": f"Rota nao encontrada: {method} {path}"})

    def log_message(self, fmt, *args):
        print("[mock]", self.address_string(), "-", fmt % args)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    print(f"Aurora Gás Mock rodando em http://localhost:{port}")
    print("Personas: CPF 11111111111 (Maria/feliz) · 22222222222 (Joao/sem conta aberta) · "
          "33333333333 (Ana/3 instalacoes) · 44444444444 (Carlos/telefone diverge)")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
