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

**Cenas.** Os botões logo abaixo do assistente disparam várias ações de uma
vez ("Modo filme", "Sair de casa"). Para criar uma, deixe os aparelhos como a
cena deve ficar, toque em *+ Cena*, marque quais entram e dê o nome: a central
salva o estado atual deles. Por voz também: "cria a cena modo filme com a TV da
sala no Netflix e a luz em 20%", "ativa o modo filme", "salva a sala assim como
cena jantar". As cenas ficam em `data/homes/<id>/scenes.yaml`.

**Rotinas.** Uma cena disparada sozinha: por horário ("todo dia às 22h, boa
noite", com dias da semana) ou quando um aparelho muda de estado ("quando a TV
da sala ligar, modo filme"). Pelo app, em *+ Cena › Rotinas*, dá para agendar
por horário, pausar e remover; por voz dá para criar qualquer gatilho. O
relógio da central confere a cada 30 s (hora local do computador) e os
gatilhos de estado valem quando o estado muda de fato, vistos nos comandos e
nas consultas do app. Ficam em `data/homes/<id>/routines.yaml`.

**Diagnóstico.** Quando algo atrapalha (TV fora da rede, aparelho offline,
macOS bloqueando a rede local, conta sem renovação, falhas repetidas, cena
apontando para aparelho removido), um aviso aparece acima das cenas; tocar
nele mostra cada ponto com a causa provável e o que fazer. O agente usa o
mesmo diagnóstico para explicar uma falha ("por que a TV não liga?") em vez
de só dizer que não conseguiu. Rota: `GET /health` (`?fresh=true` consulta os
estados de novo).

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

No macOS, o sistema só deixa o terminal falar com a rede local depois de
liberado em *Ajustes do Sistema > Privacidade e Segurança > Rede local*
(ligue o Terminal, iTerm ou VS Code, de onde a central roda). Sem isso toda
busca dá "No route to host"; a central percebe e avisa no app e no log.

Casa com dois roteadores (modem da operadora e um mesh em modo roteador): a
busca só enxerga a rede em que a central está. Se uma TV ligada continua "fora
da nuvem e a central ainda não a viu", o diagnóstico diz o que a última busca
encontrou. Para ver na hora, rode `python -m smarthome.tools.tv_scan` (aceita
outra rede como argumento, ex. `192.168.0.0/24`). Se a TV está no outro Wi-Fi,
o certo é pôr TV e central no mesmo; enquanto isso, `TV_SCAN_NETWORKS` no
`.env` manda a central varrer também essa rede (o acordar por rede, porém, não
atravessa roteadores).

## Contas: entrar com a conta, sem chave nem token

Quem usa a central não cria conta de desenvolvedor nem copia token. O botão
*Contas* do app liga cada fabricante pela própria conta do usuário:

- **SmartThings (Samsung).** *Conectar* abre o login da Samsung e volta para a
  central com a autorização; o token é renovado sozinho. Para isso funcionar,
  quem publica a central registra uma SmartApp uma vez (ver abaixo).
- **Smart Life / Tuya Smart.** No app: *Eu > Configurações > Conta e
  segurança > Código de usuário*. Digite o código em *Contas*, leia o QR code
  que a central mostra com o scanner do app e confirme. Sem conta na
  plataforma Tuya, sem projeto, sem chave. Aparelhos do **Intelbras Izy Smart**
  não podem ser ligados por fora do app da Intelbras: pareie-os de novo no
  Smart Life (mesmo botão de adicionar aparelho) e conecte o Smart Life.
- **LG ThinQ.** A LG só oferece token pessoal por enquanto: gere em
  https://connect-pat.lgthinq.com com a conta do app ThinQ e cole em *Contas*.
  O agente também explica isso quando alguém pede um aparelho LG.

As contas ficam em `data/homes/<id>/accounts/` (fora do git, só o dono lê).
Depois de conectar, *Adicionar* lista os aparelhos da conta; o agente também
cadastra por conversa ("configura a luz nova do quarto").

**Registro da SmartApp (uma vez, por quem publica):** com a
[CLI da SmartThings](https://github.com/SmartThingsCommunity/smartthings-cli),
`smartthings apps:create`, tipo *API_ONLY*, escopos `r:devices:*`,
`x:devices:*` e `r:locations:*`, e redirect
`https://vendra74.github.io/Smarthome/auth/smartthings/` (a *ponte*: a
SmartThings só devolve a autorização para um endereço https público; essa
página estática, em `docs/auth/smartthings/`, só repassa o código para a
central na rede local, usando o endereço guardado no `state`). Copie o *client
id* e o *client secret* para `SMARTTHINGS_CLIENT_ID` e `SMARTTHINGS_CLIENT_SECRET`
no `.env`. Quem hospeda a ponte em outro lugar define `SMARTTHINGS_REDIRECT_URI`;
se a central é aberta por outro endereço, `SMARTHOME_PUBLIC_URL`.
Até isso existir, *Contas* mostra a Samsung como "ainda não liberada" e o
token pessoal (`SMARTTHINGS_TOKEN`) continua valendo para desenvolvimento.

Para desenvolvimento, a Tuya também aceita as chaves de um projeto em
https://iot.tuya.com (`TUYA_ACCESS_ID` e `TUYA_ACCESS_SECRET`, extra
`pip install -e ".[tuya]"`); a conta conectada em *Contas* tem prioridade.

## Uma pasta por casa

A casa do Andre é o caso de referência, mas nada dela fica no código. Cada casa
é uma pasta em `data/homes/<id>/` com `home.yaml` (nome, cômodos, integrações
ativas) e `devices.yaml` (dispositivos). `SMARTHOME_HOME` escolhe a casa
(padrão `casa`) e `SMARTHOME_DATA` a pasta base. O modelo do agente pode ser
trocado com `SMARTHOME_MODEL`.
