"""Diagnóstico da casa: o que está impedindo a central de controlar algo e o que fazer.

Junta o que a central já observa (estados, erros recentes, TVs vistas na rede,
contas, bloqueio do macOS) numa lista de achados em português, cada um com a
causa provável e a ação sugerida. Alimenta a rota /health, o aviso no app e a
ferramenta `diagnose` do agente, que usa isso para explicar falhas em vez de
só repetir "não consegui".
"""

from __future__ import annotations

import ipaddress
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from .adapters import samsung_lan
from .models import DeviceState, DeviceType

if TYPE_CHECKING:
    from .hub import Hub

Level = Literal["ok", "info", "warning", "critical"]
LEVEL_ORDER = {"critical": 0, "warning": 1, "info": 2, "ok": 3}
ERROR_WINDOW = 3600.0  # falhas da última hora
ERRORS_TO_FLAG = 3


class Finding(BaseModel):
    level: Level
    code: str
    title: str
    detail: str = ""
    action: str = ""
    device_id: str | None = None
    device_ids: list[str] = Field(default_factory=list)  # quando o achado junta vários aparelhos

    def concerns(self, device_id: str) -> bool:
        return device_id == self.device_id or device_id in self.device_ids


class HealthReport(BaseModel):
    checked_at: str
    summary: str
    findings: list[Finding] = Field(default_factory=list)

    @property
    def problems(self) -> list[Finding]:
        return [f for f in self.findings if f.level in ("warning", "critical")]


PROVIDER_LABEL = {"smartthings": "SmartThings (Samsung)", "lg_thinq": "LG ThinQ", "tuya": "Smart Life / Tuya"}


def _label(adapter: str) -> str:
    return PROVIDER_LABEL.get(adapter, adapter)


def _group(findings: list[Finding], code: str, title, detail: str, action: str) -> list[Finding]:
    """Junta achados iguais de vários aparelhos num só, listando os nomes."""
    same = [f for f in findings if f.code == code]
    if len(same) < 2:
        return findings
    names = [f.title.split(" está ")[0].split(" não responde")[0] for f in same]
    merged = Finding(level=same[0].level, code=code, title=title(len(same)),
                     detail=f"{', '.join(names)}. {detail}", action=action,
                     device_ids=[f.device_id for f in same if f.device_id])
    rest = [f for f in findings if f.code != code]
    return rest + [merged]


async def diagnose(hub: Hub, fresh: bool = False) -> HealthReport:
    findings: list[Finding] = []
    devices = hub.registry.all()
    now = time.time()

    # 1. Bloqueio do macOS: nada na rede local funciona
    if samsung_lan.lan_blocked():
        findings.append(Finding(
            level="critical", code="lan_blocked", title="A central não consegue falar com a rede local",
            detail="Todos os pacotes para a rede de casa são recusados pelo sistema; as TVs não são encontradas nem acordadas.",
            action=samsung_lan.MACOS_HINT,
        ))

    # 2. Aparelhos cuja plataforma não está ativa
    by_adapter: dict[str, list] = {}
    for d in devices:
        by_adapter.setdefault(d.adapter, []).append(d)
    for adapter, group in by_adapter.items():
        if adapter not in hub.adapters:
            names = ", ".join(d.name for d in group[:4]) + (" e outros" if len(group) > 4 else "")
            findings.append(Finding(
                level="critical", code="adapter_inactive", title=f"{_label(adapter)} não está conectada",
                detail=f"{len(group)} aparelho(s) dependem dela: {names}.",
                action="Abra Contas no app e conecte a conta." if adapter in PROVIDER_LABEL else f"Ative '{adapter}' em adapters no home.yaml.",
            ))

    # 3. Erros de descoberta por plataforma
    for adapter, error in hub.discovery_errors.items():
        findings.append(Finding(
            level="warning", code="discovery_error", title=f"{_label(adapter)} não respondeu à busca de aparelhos",
            detail=error, action="Se persistir, desconecte e conecte a conta de novo em Contas.",
        ))

    # 4. Estados: offline, TV sem IP/MAC, rede diferente
    states: dict[str, DeviceState | None] = {}
    for d in devices:
        if fresh or d.id not in hub.last_states:
            states[d.id] = await hub.get_state(d.id)
        else:
            states[d.id] = hub.last_states[d.id][1]
    central_net = samsung_lan.local_network()
    for d in devices:
        state = states.get(d.id)
        if state is None or d.adapter not in hub.adapters:
            continue
        host = d.config.get("host")
        if host and central_net:
            try:
                if ipaddress.ip_address(host) not in central_net:
                    findings.append(Finding(
                        level="warning", code="other_subnet", device_id=d.id,
                        title=f"{d.name} está em outra rede ({host})",
                        detail=f"A central está em {central_net}; o pacote de acordar e o controle direto não atravessam redes.",
                        action="Ligue a central e o aparelho no mesmo roteador, ou ponha o segundo roteador em modo ponto de acesso.",
                    ))
            except ValueError:
                pass
        if state.online:
            continue
        if d.adapter in ("samsung_tv", "lg_tv"):
            # TV controlada pela rede local: offline quer dizer desligada/em espera
            if d.config.get("mac"):
                findings.append(Finding(
                    level="info", code="tv_standby", device_id=d.id, title=f"{d.name} está em espera",
                    detail="Está desligada ou em espera; a central acorda pela rede ao ligar.",
                ))
            else:
                findings.append(Finding(
                    level="warning", code="tv_unknown_lan", device_id=d.id,
                    title=f"{d.name} não responde na rede e a central não tem o MAC dela",
                    action="Cadastre o MAC da TV (em Adicionar, campo MAC) para a central poder acordá-la.",
                ))
            continue
        if d.type == DeviceType.TV and hub.recovery.applies(d):
            if d.config.get("mac"):
                findings.append(Finding(
                    level="info", code="tv_standby", device_id=d.id, title=f"{d.name} está em espera",
                    detail="A nuvem não a vê, mas a central sabe o IP e o MAC e acorda pela rede ao ligar.",
                ))
            else:
                findings.append(Finding(
                    level="warning", code="tv_unknown_lan", device_id=d.id,
                    title=f"{d.name} está fora da nuvem e a central ainda não a viu na rede",
                    detail="Sem IP e MAC, não dá para acordá-la quando a SmartThings a perde.",
                    action="Ligue a TV uma vez pelo controle com a central rodando; ela aprende sozinha. " + hub.recovery_hint(),
                ))
        else:
            findings.append(Finding(
                level="warning", code="offline", device_id=d.id, title=f"{d.name} está sem conexão",
                detail=f"A {_label(d.adapter)} informa o aparelho offline.",
                action="Confira se está na tomada e no Wi-Fi; se o Wi-Fi mudou, refaça o pareamento no app do fabricante.",
            ))

    # Várias TVs na mesma situação viram um achado só
    findings = _group(findings, "tv_unknown_lan",
                      lambda n: f"{n} TVs estão fora da nuvem e a central ainda não as viu na rede",
                      "Provavelmente estão desligadas. Sem IP e MAC, não dá para acordá-las quando a SmartThings as perde.",
                      "Ligue cada uma pelo controle uma vez com a central rodando; ela aprende sozinha. " + hub.recovery_hint())
    findings = _group(findings, "offline", lambda n: f"{n} aparelhos estão sem conexão",
                      "O fabricante informa os aparelhos offline.",
                      "Confira tomada e Wi-Fi de cada um; se o Wi-Fi mudou, refaça o pareamento no app do fabricante.")

    # 5. Falhas repetidas na última hora
    recent = [e for e in hub.recent_errors if now - e["time"] < ERROR_WINDOW]
    per_device: dict[str, list[dict[str, Any]]] = {}
    for e in recent:
        per_device.setdefault(e["device_id"], []).append(e)
    for device_id, errors in per_device.items():
        if len(errors) < ERRORS_TO_FLAG:
            continue
        device = hub.registry.get(device_id)
        name = device.name if device else device_id
        last = errors[-1]["message"]
        existing = next((f for f in findings if f.concerns(device_id) and f.code in ("offline", "tv_unknown_lan", "tv_standby")), None)
        if existing is not None:
            # já está explicado como offline/em espera; só acrescenta o motivo que a nuvem deu
            existing.detail = (existing.detail + " " if existing.detail else "") + f"{name} falhou {len(errors)} vezes na última hora: {last}"
            continue
        if "409" in last or "rate" in last.lower() or "429" in last:
            action = "A nuvem está limitando ou sem contato com o aparelho; espere alguns minutos. Para TVs, a central tenta pela rede local."
        else:
            action = "Veja o último motivo; se for sempre o mesmo, o aparelho pode precisar de pareamento ou da conta reconectada."
        findings.append(Finding(
            level="warning", code="repeated_failures", device_id=device_id,
            title=f"{name} falhou {len(errors)} vezes na última hora", detail=f"Último motivo: {last}", action=action,
        ))

    # 6. Contas: como estão autenticadas
    if hub.accounts is not None:
        st = hub.accounts.load("smartthings")
        if st and not st.get("refresh_token"):
            findings.append(Finding(
                level="warning", code="no_refresh", title="A conta SmartThings não tem renovação automática",
                detail="O acesso vai expirar e os aparelhos Samsung param de responder.",
                action="Em Contas, desconecte e conecte a SmartThings de novo.",
            ))
        if "lg_thinq" in hub.adapters and not hub.accounts.load("lg_thinq"):
            findings.append(Finding(
                level="info", code="env_token", title="LG ThinQ usa chave fixa da central",
                detail="Funciona, mas a LG não renova esse token sozinha.",
                action="Se os ares LG pararem de responder, gere um token novo em connect-pat.lgthinq.com e cole em Contas.",
            ))

    # 7. Cenas e rotinas apontando para o que não existe mais
    device_ids = {d.id for d in devices}
    for scene in hub.scenes.all():
        missing = sorted({a.device_id for a in scene.actions if a.device_id not in device_ids})
        if missing:
            findings.append(Finding(
                level="warning", code="scene_missing_device", title=f"A cena {scene.name} usa aparelho que não existe mais",
                detail=f"Ids: {', '.join(missing)}.", action="Refaça a cena ou remova essas ações.",
            ))
    for routine in hub.routines.all():
        if hub.scenes.get(routine.scene_id) is None:
            findings.append(Finding(
                level="warning", code="routine_missing_scene", title=f"A rotina {routine.name} aponta para uma cena apagada",
                action="Remova a rotina ou crie a cena de novo.",
            ))

    findings.sort(key=lambda f: LEVEL_ORDER[f.level])
    problems = [f for f in findings if f.level in ("warning", "critical")]
    if not findings:
        summary = "Tudo em ordem: todos os aparelhos respondem."
    elif not problems:
        summary = "Tudo em ordem; " + "; ".join(f.title.lower() for f in findings) + "."
    else:
        summary = f"{len(problems)} ponto(s) de atenção: " + "; ".join(f.title for f in problems[:3]) + ("..." if len(problems) > 3 else ".")
    return HealthReport(checked_at=datetime.now().isoformat(timespec="seconds"), summary=summary, findings=findings)
