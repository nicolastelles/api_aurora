# Kit Técnico — Workshop Partner Program · Bot Aurora Gás 2ª Via & Cadastro

Tudo que o Dia 2 (build) precisa para os participantes construírem o assistente da Aurora Gás
batendo numa API que se comporta como o backend real, sem depender do sistema da Aurora Gás.

## Arquivos

| Arquivo | O que é |
|---|---|
| `aurora_mock.py` | API mock stateful (só stdlib, roda com `python3`). Simula billing/cadastro. |
| `aurora-2via-openapi.json` | Swagger (OpenAPI 3.0) das 8 tools, pronto para importar na plataforma. |
| `aurora-2via-openapi.BUGGED.json` | Versão com bug plantado para o Bloco 6 (debug). Ver "Gabarito do bug". |
| `README.md` | Este guia. |

---

## 1. Subir o mock e expor publicamente

A plataforma indigo.ai é cloud e **não enxerga `localhost`**. Precisa de uma URL pública.

```bash
# 1. sobe o mock (porta 8080)
python3 aurora_mock.py

# 2. em outro terminal, expõe publicamente (sem cadastro):
cloudflared tunnel --url http://localhost:8080
#   ou, se preferir ngrok:
# ngrok http 8080
```

O túnel imprime uma URL `https://...`. Copie e cole no campo `servers[0].url` do
`aurora-2via-openapi.json` (hoje está `https://SEU-MOCK-PUBLICO.exemplo`).

**Dois modos de operação:**
- **1 mock por dupla (recomendado para certificação):** cada dupla roda seu próprio mock + túnel. Estado isolado, zero colisão.
- **1 mock compartilhado:** o facilitador sobe um só. Para isolar o estado de cada dupla, configure a plataforma para enviar o header estático `X-Workshop-Team: <nome-da-dupla>` nas tools. Sem o header, todos caem no namespace `shared`.

Endpoints de apoio (não estão no swagger, são para debug):
- `GET /health` — está no ar?
- `GET /admin/state` — dump do estado atual da equipe.
- `POST /admin/reset` — recarrega os dados-semente (útil entre demos).

---

## 2. Importar as tools na plataforma

1. Ajuste `servers[0].url` com a URL do túnel.
2. Importe `aurora-2via-openapi.json` como conjunto de tools do assistente.
3. Auth: o mock aceita chamadas sem chave. **Não** há header de auth no swagger de propósito — na plataforma os headers de autenticação são gerenciados fora da tool. (No dia, é o gancho para explicar como auth entraria num backend real.)

As 8 tools:

| operationId | Método | Passo do fluxo Aurora Gás |
|---|---|---|
| `identificar_cliente` | POST /identificar | 2.1 / 2.2 — identificação por CPF ou código de instalação |
| `consultar_contas` | GET /instalacoes/{id}/contas | 2.2.1.2 — contas em aberto (ou 3 últimas) |
| `buscar_conta` | POST .../contas/buscar | 2.2.1.2.2 — sem conta aberta, achar por valor/vencimento |
| `validar_endereco_instalacao` | POST /clientes/{id}/validar-endereco | 2.2.1.3 — >1 instalação, validar CEP/rua |
| `emitir_segunda_via` | POST /contas/{id}/segunda-via | emissão da 2ª via (PDF) |
| `emitir_codigo_barras` | POST /contas/{id}/codigo-barras | emissão do código de barras |
| `enviar_documento` | POST /envios | 2.2.1.2.1.1 — envio WhatsApp/e-mail + protocolo |
| `atualizar_cadastro` | PATCH /clientes/{id}/cadastro | 2.2.1.4 — atualização cadastral |

---

## 3. Personas de teste (dados-semente)

Desenhadas para cada dupla cair num ramo diferente do fluxograma:

| CPF | Código inst. | Cliente | Cenário | Exercita |
|---|---|---|---|---|
| `11111111111` | `1001` | Maria Souza | 1 instalação, **2 contas em aberto** | Caminho feliz da 2ª via |
| `22222222222` | `2001` | João Lima | 1 instalação, **0 conta em aberto** | Buscar por valor/vencimento / 3 últimas |
| `33333333333` | `3001/2/3` | Ana Prado | **3 instalações** | Validar CEP/rua antes de seguir |
| `44444444444` | `4001` | Carlos Dias | 1 instalação, 1 conta aberta | **Telefone diverge → oferecer atualizar cadastro** |
| `00000000000` | — | (inexistente) | não encontrado (404) | Loop de identificação / escape |

Dica para o Carlos: manda a 2ª via para um WhatsApp **diferente** de `+5511999990044` → o envio volta `telefone_diverge: true`, disparando a oferta de atualização cadastral.

Cheat-sheet de curl para conferir o mock antes do workshop:

```bash
B=http://localhost:8080
curl -s -X POST $B/identificar -d '{"cpf":"111.111.111-11"}'
curl -s "$B/instalacoes/INST-1001/contas?status=aberto"
curl -s -X POST $B/contas/CT-1001A/segunda-via
curl -s -X POST $B/envios -d '{"canal":"whatsapp","destino":"+5511911112222","conta_id":"CT-4001A","tipo":"2via"}'
curl -s -X PATCH $B/clientes/CLI-4001/cadastro -d '{"telefone":"+5511911112222"}'
```

---

## 4. Definition of Done por bloco

- **Bloco 1 (tools de leitura):** o bot identifica Maria pelo CPF e diz "você tem 2 contas em aberto".
- **Bloco 2 (tools de ação):** o bot emite a 2ª via e a entrega no WhatsApp/e-mail com o número de protocolo.
- **Bloco 3 (workflow):** caminho feliz ponta a ponta — identificar → classificar motivo → consultar → emitir → enviar.
- **Bloco 4 (loops/guardrail/escape):** atualização de cadastro com até 3 tentativas de confirmação; guardrail "sem identificação não emite 2ª via"; regra única de escape (derivar/encerrar/migrar).
- **Bloco 5 (MCP vs OpenAPI):** justificar por que a consulta de billing é OpenAPI e onde um MCP entraria; conectar um MCP de exemplo.
- **Bloco 6 (debug):** achar, pelo trace, por que o bot chamou a tool errada (ver gabarito) e corrigir.
- **Bloco 7 (certificação):** rodar as conversas de teste das personas e bater as métricas do brief.

---

## 5. Stretch goals (para as duplas que voam)

1. **Multi-instalação (Ana):** desambiguar por CEP/rua com `validar_endereco_instalacao` antes de emitir.
2. **Sem conta em aberto (João):** implementar o caminho valor/vencimento com `buscar_conta` e as "3 últimas".
3. **Código de barras:** oferecer 2ª via **ou** código de barras e rotear para a tool certa.
4. **Atualização proativa (Carlos):** tratar `telefone_diverge` e completar o loop de atualização cadastral.
5. **Regra de escape parametrizável:** transformar os 5 pontos de "Derivação/Encerramento/Migração" numa única política (por horário/fila/motivo) — recomendação do próprio blueprint da Aurora Gás.
6. **Contestação:** motivo fora de escopo → deflexão educada + encaminhamento (o blueprint cita mas não detalha).
7. **Instrumentação:** logar taxa de sucesso de identificação e de validação de CEP/rua (métrica que o blueprint pede).

---

## 6. Gabarito do bug (Bloco 6) — só para o facilitador

**Arquivo:** `aurora-2via-openapi.BUGGED.json`.

**O que foi plantado:** `emitir_segunda_via` e `emitir_codigo_barras` receberam a **mesma descrição
genérica** ("gera o documento da fatura..."), sem roteamento negativo. As duas tools ficam
indistinguíveis para o modelo.

**Sintoma:** o cliente pede "a 2ª via" e, aleatoriamente, o bot chama `emitir_codigo_barras`
(ou vice-versa). Intermitente — às vezes acerta, o que confunde e ensina a olhar o trace.

**O que os participantes acham no trace:** a tool call registrada é `emitir_codigo_barras`
mesmo com o usuário pedindo 2ª via — ou seja, o roteamento errou na *descrição*, não no prompt.

**Correção esperada:** reescrever as descrições distinguindo as duas e adicionando roteamento
negativo ("NÃO use quando... -> use a outra"), exatamente como está na versão boa
(`aurora-2via-openapi.json`). Lição: **a descrição da tool é prompt** — tool mal descrita = agente
que chama errado.
