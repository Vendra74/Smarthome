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
(`turn_on`, `set_brightness`, `set_temperature`, `open_app` ...). Cada
adaptador traduz esse vocabulário para o protocolo dele. Para suportar um
fabricante novo basta escrever um adaptador.

## Peças

| Peça | Arquivo | Papel |
|---|---|---|
| Modelo | `src/smarthome/models.py` | Dispositivo, tipo, comandos, estado |
| Casa | `src/smarthome/config.py` | Carrega a pasta da casa e monta o Hub dela |
| Cadastro | `src/smarthome/registry.py` | Lista de dispositivos com cômodo e apelidos, salva em YAML |
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
4. **OAuth por casa** com SmartThings e Alexa, em vez de token pessoal.

## Como cada fabricante entra

- **SmartThings (nuvem, API oficial).** Caminho principal para lâmpadas,
  tomadas e ar-condicionado que já estão no app SmartThings, e também para TVs
  Samsung. Token pessoal em `SMARTTHINGS_TOKEN`. A central lista o que existe
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
- **Tuya (nuvem, API oficial via `tinytuya`).** Intelbras Izy Smart, Smart
  Life e Tuya Smart: lâmpadas (liga/desliga, brilho), tomadas e interruptores.
  Chaves de um projeto em iot.tuya.com. Controle local (sem nuvem) com as
  chaves locais dos aparelhos é o próximo passo, no mesmo espírito da
  recuperação da TV.
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
4. **Fase 4:** cenas e rotinas ("modo cinema", "boa noite"), mais adaptadores.

## Segurança

- A API não tem autenticação ainda: rode só na rede local até a fase 3.
- Tokens (SmartThings, chaves de TV) ficam em variáveis de ambiente ou em
  `tokens/`, fora do git.
