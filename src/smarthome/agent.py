"""Agente de linguagem natural da central ("tipo Alexa").

Recebe um pedido em texto (já transcrito, se veio de voz), decide com o Claude
quais ferramentas da central usar e responde em português curto, pronto para
virar fala. Serve tanto para ações diretas ("acende a luz da sala") quanto para
cadastrar dispositivo novo em conversa ("configura a TV nova do quarto").
"""

from __future__ import annotations

import json
import os
from typing import Any

import anthropic

from .accounts import PROVIDERS
from .hub import Hub
from .models import Command, DeviceType
from .scenes import SceneAction

MODEL = os.environ.get("SMARTHOME_MODEL", "claude-opus-5-5")
MAX_TOOL_ROUNDS = 8

SYSTEM_PROMPT = """Você é a central de controle da casa, um assistente de voz em português do Brasil.
Você controla os dispositivos da casa usando as ferramentas disponíveis.

Como agir:
- Para ações diretas ("acende a luz da sala", "liga o ar do quarto em 23 graus"), \
use list_devices para achar o dispositivo certo e send_command para agir.
- Se o pedido cobre vários dispositivos ("apaga tudo da sala"), aja em todos.
- Se não ficar claro qual dispositivo é, pergunte, citando as opções pelo nome.
- Para cadastrar dispositivo novo, use discover_devices para ver o que as \
plataformas (SmartThings, LG ThinQ, Tuya/Izy Smart) já conhecem; pergunte nome e cômodo se faltar, \
e cadastre com register_device. Para TVs na rede local, peça IP e MAC.
- Se a plataforma do aparelho não está conectada (veja list_accounts), oriente a pessoa a \
abrir "Contas" no app da central e conectar: Samsung/SmartThings é só entrar com a conta Samsung; \
Smart Life ou Tuya Smart é digitar o código de usuário (no app: Eu, Configurações, Conta e \
segurança, Código de usuário) e ler o QR code que a central mostra; LG ThinQ pede um token \
pessoal gerado em connect-pat.lgthinq.com. Aparelhos do Intelbras Izy Smart não podem ser \
ligados por fora: a pessoa deve pareá-los de novo no app Smart Life e conectar o Smart Life. \
Nunca peça chaves ou tokens pelo chat.
- Cenas: "modo filme", "sair de casa", "boa noite" são cenas (list_scenes, run_scene). Para \
criar uma, monte as ações com create_scene ("cria a cena modo filme: TV da sala no Netflix e luz \
da sala em 20%") ou, se a pessoa pedir para salvar como está ("salva a sala assim como cena \
jantar"), use from_devices com os aparelhos do cômodo. Se um pedido parece uma cena que não existe, \
faça as ações direto e ofereça salvar como cena.
- Nunca diga que fez algo que uma ferramenta não confirmou.

Suas respostas são faladas em voz alta: uma ou duas frases curtas, sem markdown, \
sem listas, sem IDs internos."""

_NULLABLE_STR = {"type": ["string", "null"]}
_PARAM_KEYS = ("level", "celsius", "mode", "speed", "muted", "channel", "app")

TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_accounts",
        "description": "Lista as contas de fabricante (SmartThings, Smart Life/Tuya, LG ThinQ): quais estão conectadas e como conectar as outras.",
        "strict": True,
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "list_devices",
        "description": "Lista os dispositivos cadastrados (id, nome, cômodo, tipo, comandos aceitos e estado atual). Filtre por cômodo quando o pedido citar um.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"room": {**_NULLABLE_STR, "description": "Cômodo, ex.: sala. null para todos."}},
            "required": ["room"],
            "additionalProperties": False,
        },
    },
    {
        "name": "send_command",
        "description": "Envia um comando a um dispositivo cadastrado. Preencha só os parâmetros que o comando usa; os outros ficam null.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string"},
                "command": {"type": "string", "enum": [c.value for c in Command]},
                "level": {"type": ["integer", "null"], "description": "0-100 para set_brightness e set_volume"},
                "celsius": {"type": ["number", "null"], "description": "Para set_temperature"},
                "mode": {**_NULLABLE_STR, "description": "Para set_mode: cool, heat, dry, fan, auto"},
                "speed": {**_NULLABLE_STR, "description": "Para set_fan_speed (ventilação do ar): low, medium, high, auto"},
                "muted": {"type": ["boolean", "null"], "description": "Para mute"},
                "channel": {**_NULLABLE_STR, "description": "Para set_channel"},
                "app": {**_NULLABLE_STR, "description": "Para open_app, ex.: netflix"},
            },
            "required": ["device_id", "command", "level", "celsius", "mode", "speed", "muted", "channel", "app"],
            "additionalProperties": False,
        },
    },
    {
        "name": "discover_devices",
        "description": "Lista dispositivos que as plataformas conectadas (ex.: SmartThings) conhecem e que ainda não foram cadastrados na central.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"adapter": {**_NULLABLE_STR, "description": "Plataforma específica ou null para todas"}},
            "required": ["adapter"],
            "additionalProperties": False,
        },
    },
    {
        "name": "register_device",
        "description": "Cadastra um dispositivo novo na central.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Nome falado, ex.: TV do quarto"},
                "type": {"type": "string", "enum": [t.value for t in DeviceType]},
                "adapter": {"type": "string", "enum": ["smartthings", "lg_thinq", "tuya", "samsung_tv", "lg_tv", "virtual"]},
                "room": _NULLABLE_STR,
                "external_id": {**_NULLABLE_STR, "description": "ID vindo de discover_devices (SmartThings, LG ThinQ)"},
                "host": {**_NULLABLE_STR, "description": "IP da TV na rede local"},
                "mac": {**_NULLABLE_STR, "description": "MAC da TV, para ligar por Wake-on-LAN"},
            },
            "required": ["name", "type", "adapter", "room", "external_id", "host", "mac"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_scenes",
        "description": "Lista as cenas da casa (id, nome, apelidos e ações).",
        "strict": True,
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "run_scene",
        "description": "Executa uma cena pelo id.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"scene_id": {"type": "string"}},
            "required": ["scene_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "create_scene",
        "description": "Cria uma cena. Informe actions (lista de comandos) ou from_devices (ids cujo estado atual vira a cena); o outro fica vazio.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Nome falado, ex.: Modo filme"},
                "aliases": {"type": "array", "items": {"type": "string"}},
                "actions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "device_id": {"type": "string"},
                            "command": {"type": "string", "enum": [c.value for c in Command]},
                            "level": {"type": ["integer", "null"]},
                            "celsius": {"type": ["number", "null"]},
                            "mode": _NULLABLE_STR,
                            "speed": _NULLABLE_STR,
                            "muted": {"type": ["boolean", "null"]},
                            "channel": _NULLABLE_STR,
                            "app": _NULLABLE_STR,
                        },
                        "required": ["device_id", "command", "level", "celsius", "mode", "speed", "muted", "channel", "app"],
                        "additionalProperties": False,
                    },
                },
                "from_devices": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["name", "aliases", "actions", "from_devices"],
            "additionalProperties": False,
        },
    },
    {
        "name": "remove_scene",
        "description": "Apaga uma cena pelo id.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"scene_id": {"type": "string"}},
            "required": ["scene_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "remove_device",
        "description": "Remove um dispositivo do cadastro da central (ex.: um exemplo virtual ou um aparelho que saiu de casa). Confirme com o usuário antes se houver dúvida sobre qual é.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"device_id": {"type": "string"}},
            "required": ["device_id"],
            "additionalProperties": False,
        },
    },
]


class HomeAgent:
    def __init__(self, hub: Hub, client: anthropic.AsyncAnthropic | None = None) -> None:
        self.hub = hub
        self.client = client or anthropic.AsyncAnthropic()
        self.system = SYSTEM_PROMPT
        if hub.home:
            rooms = ", ".join(hub.home.rooms) or "não informados"
            self.system += f"\n\nCasa: {hub.home.name}. Cômodos: {rooms}."
        self._conversations: dict[str, list[dict[str, Any]]] = {}

    async def _run_tool(self, name: str, args: dict[str, Any]) -> Any:
        if name == "list_accounts":
            connected = self.hub.accounts.connected() if self.hub.accounts else []
            return [
                {
                    "id": key,
                    "label": meta["label"],
                    "how": meta["method"],
                    "connected": key in connected or key in self.hub.adapters,
                }
                for key, meta in PROVIDERS.items()
            ]
        if name == "list_devices":
            devices = self.hub.registry.in_room(args["room"]) if args.get("room") else self.hub.registry.all()
            out = []
            for d in devices:
                state = await self.hub.get_state(d.id)
                out.append(
                    {
                        "id": d.id,
                        "name": d.name,
                        "room": d.room,
                        "type": d.type.value,
                        "aliases": d.aliases,
                        "commands": [c.value for c in d.capabilities],
                        "state": state.model_dump() if state else None,
                    }
                )
            return out
        if name == "send_command":
            params = {k: args[k] for k in _PARAM_KEYS if args.get(k) is not None}
            result = await self.hub.execute(args["device_id"], Command(args["command"]), params)
            return result.model_dump(mode="json")
        if name == "discover_devices":
            found = await self.hub.discover(args.get("adapter"))
            return [d.model_dump(mode="json") for d in found]
        if name == "register_device":
            config = {
                key: args[src]
                for key, src in (("device_id", "external_id"), ("host", "host"), ("mac", "mac"))
                if args.get(src)
            }
            device = self.hub.register(
                name=args["name"],
                type=DeviceType(args["type"]),
                adapter=args["adapter"],
                room=args.get("room"),
                config=config,
            )
            return {"registered": device.model_dump(mode="json")}
        if name == "list_scenes":
            return [s.model_dump(mode="json") for s in self.hub.scenes.all()]
        if name == "run_scene":
            return (await self.hub.run_scene(args["scene_id"])).model_dump(mode="json")
        if name == "create_scene":
            if args.get("from_devices"):
                scene = await self.hub.snapshot_scene(args["name"], args["from_devices"], args.get("aliases") or [])
            else:
                actions = [
                    SceneAction(
                        device_id=a["device_id"],
                        command=Command(a["command"]),
                        params={k: a[k] for k in _PARAM_KEYS if a.get(k) is not None},
                    )
                    for a in args.get("actions") or []
                ]
                scene = self.hub.create_scene(args["name"], actions, args.get("aliases") or [])
            return {"created": scene.model_dump(mode="json")}
        if name == "remove_scene":
            if not self.hub.scenes.remove(args["scene_id"]):
                raise ValueError(f"Cena {args['scene_id']} não existe")
            return {"removed": args["scene_id"]}
        if name == "remove_device":
            device = self.hub.registry.get(args["device_id"])
            if device is None:
                raise ValueError(f"Dispositivo {args['device_id']} não está cadastrado")
            self.hub.registry.remove(device.id)
            return {"removed": device.id, "name": device.name}
        raise ValueError(f"Ferramenta desconhecida: {name}")

    async def handle(self, text: str, session_id: str = "default") -> str:
        """Processa um pedido e devolve a resposta para falar ao usuário."""
        messages = self._conversations.setdefault(session_id, [])
        messages.append({"role": "user", "content": text})

        for _ in range(MAX_TOOL_ROUNDS):
            response = await self.client.beta.messages.create(
                model=MODEL,
                max_tokens=16000,
                system=self.system,
                tools=TOOLS,
                messages=messages,
                thinking={"type": "adaptive"},
                output_config={"effort": "low"},  # comandos de casa pedem resposta rápida
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            if response.stop_reason == "refusal":
                messages.pop()
                return "Desculpe, não posso ajudar com esse pedido."

            messages.append({"role": "assistant", "content": response.content})
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if response.stop_reason != "tool_use" or not tool_uses:
                return " ".join(b.text for b in response.content if b.type == "text").strip()

            results = []
            for block in tool_uses:
                try:
                    output = await self._run_tool(block.name, block.input)
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(output, ensure_ascii=False),
                        }
                    )
                except Exception as exc:  # o erro volta para o modelo explicar ao usuário
                    results.append(
                        {"type": "tool_result", "tool_use_id": block.id, "content": str(exc), "is_error": True}
                    )
            messages.append({"role": "user", "content": results})

        return "Não consegui concluir esse pedido agora."

    def reset(self, session_id: str = "default") -> None:
        self._conversations.pop(session_id, None)
