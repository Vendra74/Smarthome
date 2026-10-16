# Arquitetura da central

## Visão geral

```
  App (web/celular)   Voz local (microfone)   Alexa (Echo)
          \                  |                    /
           \           [STT -> texto]     [Smart Home Skill]
            \                |                  /
             +---------- API HTTP (FastAPI) ---+
                    |                  |
               /devices ...         /agent  <-- agente (Claude + ferramentas)
                    \                  /
                     +------ Hub ------+
                            |
                  Casa: home.yaml + devices.yaml
                            |
   +-------------+----------+------------+--------+---------+
   | SmartThings | LG ThinQ | TV Samsung | TV LG  | Virtual |  <- adaptadores
   | (nuvem)     | (nuvem)  | (rede)     | (rede) | (teste) |
   +-------------+----------+------------+--------+---------+
```

**Uma regra só:** tudo passa pelo `Hub`. App, voz e Alexa nunca falam direto
com fabricante; falam com o Hub usando o vocabulário único de comandos
(`turn_on`, `set_brightness`, `set_temperature`, `set_fan_speed`, `open_app` ...). Cada
adaptador traduz esse vocabulário para o protocolo dele. Para suportar um
fabricante novo basta escrever um adaptador.

## Peças

| Peça | Arquivo | Papel |
|---|---|---|
| Modelo | `src/smarthome/models.py` | Dispositivo, tipo, comandos, estado |
| Casa | `src/smarthome/config.py` | Carrega a pasta da casa e monta o Hub dela |
| Contas | `src/smarthome/accounts.py` | Contas de fabricante ligadas pelo usuário (OAuth, QR, token), guardadas na pasta da casa |
| Cadastro | `src/smarthome/registry.py` | Lista de dispositivos com cômodo e apelidos, salva em YAML |
| Cenas | `src/smarthome/scenes.py` | Cena = nome falado + lista de ações (aparelho, comando, parâmetros); o Hub executa, aparelhos em paralelo e ações do mesmo aparelho em ordem |
| Diagnóstico | `src/smarthome/diagnostics.py` | Achados (gravidade, causa, ação) a partir de estados, erros recentes, rede local, contas, cenas e rotinas; alimenta /health, o aviso no app e a ferramenta `diagnose` do agente |
| Rotinas | `src/smarthome/routines.py` | Gatilho (horário com dias, ou estado de um aparelho com detecção de borda) que dispara uma cena; relógio em segundo plano a cada 30 s |
| Hub | `src/smarthome/hub.py` | Valida e despacha comandos, descobre e cadastra dispositivos |
| Adaptadores | `src/smarthome/adapters/` | SmartThings, LG ThinQ, TV Samsung, TV LG, virtual |
| Agente | `src/smarthome/agent.py` | Entende pedidos em português e usa o Hub via ferramentas |
| API | `src/smarthome/api.py` | REST para o app, a voz e a Alexa |
| App web | `src/smarthome/web/` | Tela por cômodo, voz no navegador, cadastro; servida pela própria API |

## Pensada para várias casas

A casa do Andre é o caso de referência, mas o objetivo é virar um app usado
em outras casas. Por isso, desde já:

- **Nada da casa no código.** Cômodos, dispositivos e integrações ficam em
  `data/homes/<id>/home.yaml` e `devices.yaml`. Uma casa nova é uma pasta nova
  (exemplo em `examples/homes/casa`).
- **Um Hub por casa.** Cada casa tem seu cadastro, seus adaptadores e suas
  credenciais (o `home.yaml` diz de qual variável vem o token do SmartThings).
- **Cadastro pelo próprio app ou por voz.** O usuário final não edita YAML: a
  descoberta (`/discover`) e o cadastro por conversa já existem para isso.

Para virar produto, o caminho previsto é:

1. **Instalação local por casa** (hoje): um mini PC ou Raspberry Pi na rede de
   cada casa roda a central. Isso é necessário de qualquer forma, porque TVs
   e Wake-on-LAN só são alcançáveis de dentro da rede local.
2. **Nuvem de contas:** login do usuário, casas por conta, membros da família
   com permissões, e a central local se conecta à nuvem por um túnel de saída
   (sem abrir porta no roteador). App e Alexa falam com a nuvem, que repassa
   para a central da casa certa.
3. **Banco de dados** no lugar dos YAML quando houver muitas casas; a
   interface do cadastro (`DeviceRegistry`) já isola essa troca.
4. **OAuth por casa** com a Alexa (a SmartThings já entra por conta, ver
   "Contas").

## Como cada fabricante entra

- **SmartThings (nuvem, API oficial).** Caminho principal para lâmpadas,
  tomadas e ar-condicionado que já estão no app SmartThings, e também para TVs
  Samsung. O usuário entra com a conta Samsung (OAuth da SmartApp registrada
  pela empresa; `accounts.py` renova o token sozinho). Token pessoal em
  `SMARTTHINGS_TOKEN` só para desenvolvimento. A central lista o que existe
  na conta (`/discover`) e o usuário escolhe o que cadastrar.
- **LG ThinQ (nuvem, API oficial ThinQ Connect).** Eletrodomésticos do app
  LG ThinQ, pelo SDK oficial `thinqconnect`. Ar-condicionado com liga/desliga,
  modo e temperatura; os demais aparelhos aparecem com estado e ganham
  comandos conforme a necessidade. Token pessoal em `LG_THINQ_TOKEN`. TVs LG
  não fazem parte dessa API e seguem pela rede local.
- **TV Samsung (rede local).** `samsungtvws`: desligar, mudo, canal, abrir app.
  Ligar é por Wake-on-LAN (precisa do MAC). Volume absoluto só via SmartThings.
- **Recuperação pela rede local (`recovery.py`).** Quando a nuvem diz que não
  alcança o aparelho (`DeviceUnreachable`), o Hub tenta pela rede antes de
  desistir: acha a TV por SSDP + `http://<ip>:8001/api/v2/` (`samsung_lan.py`),
  guarda IP e MAC, acorda por Wake-on-LAN e repete o comando pela nuvem; se a
  TV está ligada na rede mas invisível na nuvem, usa o adaptador local. É o
  motivo de a central existir dentro da casa: a nuvem sozinha não acorda a TV.
- **TV LG webOS (rede local).** `aiowebostv`: desligar, volume, mudo, abrir app.
  Ligar por Wake-on-LAN. Pareamento uma vez na TV gera a `client_key`.
- **Tuya pela conta do app (`tuya_sharing.py`).** Smart Life e Tuya Smart
  pelo SDK de compartilhamento de dispositivos (o caminho do Home Assistant):
  o usuário lê um QR code no app e pronto. Lâmpadas (liga/desliga, brilho),
  tomadas e interruptores. O adaptador por chaves de projeto (`tuya.py`,
  `tinytuya`) continua para desenvolvimento; os dois usam o nome `tuya` e o
  mesmo `device_id`, então o cadastro vale nos dois. Intelbras Izy Smart não
  aceita vínculo externo: os aparelhos são pareados de novo no Smart Life.
  Controle local (sem nuvem) com as chaves locais é o próximo passo.
- **Outros (próximas fases).** Philips Hue, ar-condicionado por infravermelho
  (Broadlink), Matter. Cada um vira um adaptador.

## O agente "tipo Alexa"

O agente recebe texto e conversa com o Claude, que tem quatro ferramentas:
`list_devices`, `send_command`, `discover_devices` e `register_device`. Assim
ele cobre os dois tipos de pedido:

- **Ação direta:** "acende a luz da sala" -> lista a sala -> `turn_on`.
- **Configuração por conversa:** "configura a TV nova do quarto" -> descobre o
  que o SmartThings já vê, pergunta o que faltar, cadastra.

A conversa fica guardada por `session_id`, então perguntas e respostas em
sequência funcionam. O agente só diz que fez algo quando a ferramenta confirmou.

## Voz

Três caminhos, que convivem porque todos terminam em `POST /agent`:

1. **No app (pronto na fase 2):** o botão do microfone usa o reconhecimento
   de voz do próprio navegador em pt-BR, manda o texto ao agente e lê a
   resposta em voz alta. Não precisa de hardware extra; exige HTTPS fora do
   `localhost` (ver README).
2. **Satélite sempre ouvindo (próximo passo):** palavra de ativação
   (openWakeWord) + transcrição (Whisper) + síntese de fala (Piper), num
   Raspberry Pi ou mini PC com microfone em cada ambiente. Tudo local, sem
   depender do navegador.
3. **Pelos Echo da Alexa:** ver abaixo.

## Alexa

Fase 3. Dois níveis de integração:

1. **Smart Home Skill (recomendado).** A central publica seus dispositivos para
   a Alexa ("Alexa, acende a luz da sala" funciona sem nome de skill). Precisa
   de uma função AWS Lambda que repassa as diretivas da Alexa para a API da
   central, de acesso externo seguro à central (Cloudflare Tunnel ou similar)
   e de login da conta (OAuth). A tradução das diretivas (`PowerController`,
   `BrightnessController`, `ThermostatController` ...) para os comandos do
   Hub é direta.
2. **Custom Skill para o agente.** "Alexa, pede para a central configurar a TV
   nova" repassa a frase inteira para `POST /agent`.

**Checar a configuração na Alexa:** a Alexa não tem API pública para ler as
rotinas e dispositivos da conta. O que dá para fazer com segurança é a
central ser a fonte da verdade: a skill reporta os dispositivos da central
(discovery) e a central confere quais foram reconhecidos. Se for preciso ler
o app Alexa de fato, isso exigiria automação de navegador e fica para avaliar.

## Roteiro

1. **Fase 1 (este PR):** núcleo, pasta por casa, cadastro, adaptadores SmartThings/Samsung/LG,
   API REST, agente por texto, testes.
2. **Fase 2:** app web (por cômodo, controles, cadastro, instalável no
   celular) com voz no navegador. Depois, o satélite de voz sempre ouvindo.
3. **Fase 3:** Smart Home Skill da Alexa e acesso externo seguro.
4. **Fase 4:** cenas ("modo cinema", "boa noite": feitas, no app e por voz, inclusive
   "salvar como está") e rotinas por horário/evento (feitas); mais adaptadores.

## Configuração zero para o usuário final

Decisão de produto (15/10/2026): tudo que exige conta de desenvolvedor, token
pessoal ou chave no `.env` é trabalho de quem publica o app, feito uma vez.
O usuário final só faz "entrar com a conta" ou lê um QR code; o resto é do
agente. O que isso exige, por ordem:

1. **Login por conta, não por chave (pronto: tela "Contas").** SmartThings
   por OAuth (SmartApp registrada pela empresa; o usuário clica em "Conectar"
   e entra na Samsung; `GET /auth/smartthings/callback` recebe o retorno).
   Tuya por QR code lido com Smart Life ou Tuya Smart, sem conta na plataforma
   Tuya (`POST /accounts/tuya/qr` gera o QR, `/accounts/tuya/poll` espera a
   confirmação). LG ThinQ só oferece token pessoal por enquanto: o agente guia
   a pessoa e o token é colado em "Contas". Apps de marca fechados (Intelbras
   Izy Smart) não permitem vínculo externo: o agente orienta a parear no Smart
   Life. Conectar uma conta liga o adaptador na hora, sem reiniciar.
2. **Matter como padrão para aparelho novo.** A central vira controlador
   Matter (`python-matter-server`): lâmpadas, tomadas e sensores entram pelo
   QR da caixa, sem nuvem de fabricante.
3. **O agente cuida da configuração.** Onboarding por conversa ("quais apps
   você usa em casa?"), descoberta e nomeação automáticas, e autodiagnóstico
   de rede: duas redes em casa, roteador em modo errado, TV fora do alcance,
   limite de consultas da nuvem. Tudo que hoje é feito à mão vira verificação
   com explicação em português.
4. **Central num aparelho fixo.** Mini computador ligado por cabo no
   roteador, para não sair da rede quando o usuário circula pela casa.

## Segurança

- A API não tem autenticação ainda: rode só na rede local até a fase 3.
- Tokens (SmartThings, chaves de TV) ficam em variáveis de ambiente ou em
  `tokens/`, fora do git.
