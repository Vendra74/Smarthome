# Central da casa inteligente

Central para controlar luzes, TVs, ar-condicionado e outros dispositivos da
casa por um app, por voz e pela Alexa. A arquitetura e o roteiro estão em
[docs/ARQUITETURA.md](docs/ARQUITETURA.md).

## Rodando

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"            # extras: .[samsung] .[lg]
cp config/devices.example.yaml config/devices.yaml

export ANTHROPIC_API_KEY=...        # para o agente de linguagem natural
export SMARTTHINGS_TOKEN=...        # opcional: https://account.smartthings.com/tokens

uvicorn smarthome.api:app --reload  # API em http://localhost:8000/docs
python -m smarthome.cli             # conversar com a central pelo terminal
pytest                              # testes
```

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

O modelo do agente pode ser trocado com `SMARTHOME_MODEL`.
