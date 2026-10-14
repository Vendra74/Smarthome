# Central da casa inteligente

Central para controlar luzes, TVs, ar-condicionado e outros dispositivos da
casa por um app, por voz e pela Alexa. A arquitetura e o roteiro estão em
[docs/ARQUITETURA.md](docs/ARQUITETURA.md).

## Rodando

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"            # extras: .[samsung] .[lg]
mkdir -p data/homes && cp -r examples/homes/casa data/homes/casa

export ANTHROPIC_API_KEY=...        # para o agente de linguagem natural
export SMARTTHINGS_TOKEN=...        # opcional: https://account.smartthings.com/tokens

uvicorn smarthome.api:app --reload  # app em http://localhost:8000, API em /docs
python -m smarthome.cli             # conversar com a central pelo terminal
pytest                              # testes
```

## App e voz

Abra http://localhost:8000 no navegador: os dispositivos aparecem por cômodo,
com liga/desliga, brilho, temperatura, modo do ar, volume e apps da TV. O
campo do topo aceita pedidos em português, digitados ou falados pelo botão do
microfone; a resposta aparece na tela e é falada. Dá para instalar na tela
inicial do celular ("Adicionar à tela de início").

O microfone do navegador só funciona em `localhost` ou com HTTPS. Para usar no
celular pela rede de casa, gere um certificado local com
[mkcert](https://github.com/FiloSottile/mkcert) e suba a central com ele:

```bash
mkcert -install && mkcert -key-file tokens/key.pem -cert-file tokens/cert.pem 192.168.0.10 central.local
uvicorn smarthome.api:app --host 0.0.0.0 --ssl-keyfile tokens/key.pem --ssl-certfile tokens/cert.pem
```

Troque `192.168.0.10` pelo IP do computador da central e abra
`https://192.168.0.10:8000` no celular. Para o celular confiar no certificado,
instale nele a CA do mkcert (`mkcert -CAROOT` mostra onde ela fica).

## Exemplos

```bash
# acender a luz da sala
curl -X POST localhost:8000/devices/luz-sala/commands \
  -H 'content-type: application/json' -d '{"command": "turn_on"}'

# pedir em linguagem natural
curl -X POST localhost:8000/agent \
  -H 'content-type: application/json' -d '{"text": "liga o ar do quarto em 23 graus"}'

# ver o que o SmartThings conhece e ainda não foi cadastrado
curl localhost:8000/discover
```

## Uma pasta por casa

A casa do Andre é o caso de referência, mas nada dela fica no código. Cada casa
é uma pasta em `data/homes/<id>/` com `home.yaml` (nome, cômodos, integrações
ativas) e `devices.yaml` (dispositivos). `SMARTHOME_HOME` escolhe a casa
(padrão `casa`) e `SMARTHOME_DATA` a pasta base. O modelo do agente pode ser
trocado com `SMARTHOME_MODEL`.
