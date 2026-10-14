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
                  Cadastro de dispositivos (YAML)
                            |
   +-------------+--------------+-------------+-----------+
   | SmartThings | TV Samsung   | TV LG       | Virtual   |  <- adaptadores
   | (nuvem)     | (rede local) | (rede local)| (testes)  |
   +-------------+--------------+-------------+-----------+
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
| Cadastro | `src/smarthome/registry.py` | Lista de dispositivos com cômodo e apelidos, salva em YAML |
| Hub | `src/smarthome/hub.py` | Valida e despacha comandos, descobre e cadastra dispositivos |
| Adaptadores | `src/smarthome/adapters/` | SmartThings, Samsung, LG, virtual |
| Agente | `src/smarthome/agent.py` | Entende pedidos em português e usa o Hub via ferramentas |
| API | `src/smarthome/api.py` | REST para o app, a voz e a Alexa |

## Como cada fabricante entra

- **SmartThings (nuvem, API oficial).** Caminho principal para lâmpadas,
  tomadas e ar-condicionado que já estão no app SmartThings, e também para TVs
  Samsung. Token pessoal em `SMARTTHINGS_TOKEN`. A central lista o que existe
  na conta (`/discover`) e o usuário escolhe o que cadastrar.
- **TV Samsung (rede local).** `samsungtvws`: desligar, mudo, canal, abrir app.
  Ligar é por Wake-on-LAN (precisa do MAC). Volume absoluto só via SmartThings.
- **TV LG webOS (rede local).** `aiowebostv`: desligar, volume, mudo, abrir app.
  Ligar por Wake-on-LAN. Pareamento uma vez na TV gera a `client_key`.
- **Outros (próximas fases).** Tuya/Smart Life, Philips Hue, ar-condicionado
  por infravermelho (Broadlink), Matter. Cada um vira um adaptador.

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

Fase 2. Dois caminhos, que podem conviver:

1. **Voz local:** palavra de ativação (openWakeWord) + transcrição (Whisper)
   + síntese de fala (Piper), num Raspberry Pi ou mini PC com microfone. O texto
   transcrito vai para `POST /agent`; a resposta volta em fala.
2. **Pelos Echo da Alexa:** ver abaixo.

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

1. **Fase 1 (este PR):** núcleo, cadastro, adaptadores SmartThings/Samsung/LG,
   API REST, agente por texto, testes.
2. **Fase 2:** voz local e um app web simples (lista por cômodo, botões).
3. **Fase 3:** Smart Home Skill da Alexa e acesso externo seguro.
4. **Fase 4:** cenas e rotinas ("modo cinema", "boa noite"), mais adaptadores.

## Segurança

- A API não tem autenticação ainda: rode só na rede local até a fase 3.
- Tokens (SmartThings, chaves de TV) ficam em variáveis de ambiente ou em
  `config/tokens/`, fora do git.
