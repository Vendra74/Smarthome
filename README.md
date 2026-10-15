# Central da casa inteligente

Central para controlar luzes, TVs, ar-condicionado e outros dispositivos da
casa por um app, por voz e pela Alexa. A arquitetura e o roteiro estão em
[docs/ARQUITETURA.md](docs/ARQUITETURA.md).

## Rodando

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"            # extras: .[samsung] .[lg]
mkdir -p data/homes && cp -r examples/homes/casa data/homes/casa

cp .env.example .env                # e preencha as chaves (veja abaixo)

uvicorn smarthome.api:app --reload  # app em http://localhost:8000, API em /docs
python -m smarthome.cli             # conversar com a central pelo terminal
pytest                              # testes
```

## Chaves

A central lê as chaves de um arquivo `.env` na pasta de onde ela é iniciada
(modelo em `.env.example`). O `.env` está no `.gitignore` e nunca deve ir para
o git. Variáveis já definidas no ambiente têm prioridade sobre o arquivo.

- **`ANTHROPIC_API_KEY`** (agente de voz e texto): entre em
  https://console.anthropic.com, crie uma chave em *Settings > API Keys* e cole
  no `.env`. O uso é cobrado por consumo na conta da Anthropic; vale definir um
  limite de gasto mensal em *Settings > Limits*. Sem a chave, os botões do app
  funcionam e só os pedidos em linguagem natural ficam indisponíveis.
- **`SMARTTHINGS_TOKEN`** (opcional): crie em
  https://account.smartthings.com/tokens com acesso a dispositivos.
- **`LG_THINQ_TOKEN`** (opcional): entre com a conta do app LG ThinQ em
  https://connect-pat.lgthinq.com, crie um token com todas as permissões e
  instale o extra com `pip install -e ".[lg]"`.

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

## TV Samsung que "some" da SmartThings

Em standby a TV Samsung costuma sumir da nuvem (a SmartThings responde `409`
e a mostra offline). A central está na mesma rede que a TV e resolve sozinha:
acha a TV na rede local (guarda IP e MAC no `devices.yaml`), acorda por
Wake-on-LAN e repete o comando quando a TV volta; se a TV está ligada mas a
nuvem não a vê, manda o comando direto pela rede. Para isso funcionar, ative
na TV *Configurações > Geral > Gerenciador de dispositivos externos > Ligar com
dispositivo móvel* e ligue a TV pelo controle uma vez com a central rodando,
para ela aprender o IP e o MAC. Na primeira vez que a central falar direto com
a TV, a TV pede autorização na tela; aceite.

## Intelbras Izy Smart (Tuya)

Os aparelhos do Izy Smart (e de Smart Life / Tuya Smart) entram pela API de
nuvem da Tuya. Uma vez só:

1. Crie uma conta em https://iot.tuya.com e, em *Cloud > Development*, um
   projeto ("Smart Home", data center *Western America* para o Brasil).
2. No projeto, aba *Devices > Link Tuya App Account*, escolha *Add App Account*
   e leia o QR code com o app Izy Smart (*Eu > ícone de leitura no canto*).
   Os aparelhos do app aparecem na lista do projeto.
3. Copie *Access ID* e *Access Secret* da visão geral do projeto para o `.env`
   (`TUYA_ACCESS_ID` e `TUYA_ACCESS_SECRET`), instale com
   `pip install -e ".[tuya]"` e reinicie a central.

Pronto: *Adicionar* lista os aparelhos do Izy Smart. Lâmpadas ganham
liga/desliga e brilho; tomadas e interruptores, liga/desliga. O plano
gratuito da Tuya vence a cada seis meses e é renovado com um clique no site.

## Uma pasta por casa

A casa do Andre é o caso de referência, mas nada dela fica no código. Cada casa
é uma pasta em `data/homes/<id>/` com `home.yaml` (nome, cômodos, integrações
ativas) e `devices.yaml` (dispositivos). `SMARTHOME_HOME` escolhe a casa
(padrão `casa`) e `SMARTHOME_DATA` a pasta base. O modelo do agente pode ser
trocado com `SMARTHOME_MODEL`.
