"""Testa o laço do agente com um cliente falso, sem chamar a API."""

import asyncio
from types import SimpleNamespace

from smarthome.agent import HomeAgent


def tool_use(id, name, input):
    return SimpleNamespace(type="tool_use", id=id, name=name, input=input)


def text(t):
    return SimpleNamespace(type="text", text=t)


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.responses.pop(0)


NULLS = {"level": None, "celsius": None, "mode": None, "muted": None, "channel": None, "app": None}


def test_turns_on_living_room_light(hub):
    client = FakeClient(
        [
            SimpleNamespace(stop_reason="tool_use", content=[tool_use("t1", "list_devices", {"room": "sala"})]),
            SimpleNamespace(
                stop_reason="tool_use",
                content=[tool_use("t2", "send_command", {"device_id": "luz-sala", "command": "turn_on", **NULLS})],
            ),
            SimpleNamespace(stop_reason="end_turn", content=[text("Pronto, acendi a luz da sala.")]),
        ]
    )
    agent = HomeAgent(hub, client=client)
    reply = asyncio.run(agent.handle("acende a luz da sala"))
    assert reply == "Pronto, acendi a luz da sala."
    assert asyncio.run(hub.get_state("luz-sala")).power is True
    # o resultado de list_devices voltou ao modelo com a luz da sala
    first_result = client.requests[1]["messages"][-1]["content"][0]
    assert "luz-sala" in first_result["content"] and "ar-quarto" not in first_result["content"]


def test_registers_new_device_by_conversation(hub):
    client = FakeClient(
        [
            SimpleNamespace(
                stop_reason="tool_use",
                content=[
                    tool_use(
                        "t1",
                        "register_device",
                        {"name": "TV do quarto", "type": "tv", "adapter": "virtual", "room": "quarto", "external_id": None, "host": "192.168.0.9", "mac": None},
                    )
                ],
            ),
            SimpleNamespace(stop_reason="end_turn", content=[text("Cadastrei a TV do quarto.")]),
        ]
    )
    reply = asyncio.run(HomeAgent(hub, client=client).handle("cadastra a TV do quarto, IP 192.168.0.9"))
    assert reply == "Cadastrei a TV do quarto."
    device = hub.registry.get("tv-do-quarto")
    assert device.config == {"host": "192.168.0.9"}


def test_tool_error_goes_back_to_model(hub):
    client = FakeClient(
        [
            SimpleNamespace(
                stop_reason="tool_use",
                content=[tool_use("t1", "register_device", {"name": "X", "type": "tv", "adapter": "inexistente", "room": None, "external_id": None, "host": None, "mac": None})],
            ),
            SimpleNamespace(stop_reason="end_turn", content=[text("Não consegui cadastrar.")]),
        ]
    )
    asyncio.run(HomeAgent(hub, client=client).handle("cadastra X"))
    result = client.requests[1]["messages"][-1]["content"][0]
    assert result["is_error"] is True


def test_refusal_does_not_poison_history(hub):
    client = FakeClient([SimpleNamespace(stop_reason="refusal", content=[])])
    agent = HomeAgent(hub, client=client)
    asyncio.run(agent.handle("algo"))
    assert agent._conversations["default"] == []
