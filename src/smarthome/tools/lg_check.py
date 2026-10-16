"""Checagem da conta LG ThinQ: o que a LG lista e o que responde.

Uso (na pasta do projeto, com o .env da central):
    python -m smarthome.tools.lg_check

Para cada aparelho da conta mostra apelido, modelo, tipo e se o status responde;
depois mostra a qual deles cada ar cadastrado na central aponta. Não imprime tokens.
"""

from __future__ import annotations

import asyncio
import logging

from ..config import build_hub


async def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    hub = build_hub()
    adapter = hub.adapters.get("lg_thinq")
    if adapter is None:
        print("LG ThinQ não está ativa nesta central (sem token em Contas nem no .env).")
        return
    api = await adapter._get_api()
    items = await api.async_get_device_list() or []
    print(f"A LG lista {len(items)} aparelho(s) na conta:\n")
    status_by_id: dict[str, str] = {}
    for item in items:
        info = item.get("deviceInfo") or {}
        device_id = item.get("deviceId", "?")
        try:
            status = await api.async_get_device_status(device_id)
            power = status.get("operation", {}).get("airConOperationMode") if isinstance(status, dict) else None
            result = f"responde (power={power})" if power else "responde"
        except Exception as exc:  # mostra o código da LG
            result = f"NÃO responde: {str(exc).split(' - ')[0][:80]}"
        status_by_id[device_id] = result
        print(f"- {info.get('alias', '?')!r}  modelo={info.get('modelName', '?')}  tipo={info.get('deviceType', '?')}")
        print(f"    id=...{device_id[-8:]}  {result}")
    print("\nAres cadastrados na central:")
    for device in hub.registry.all():
        if device.adapter != "lg_thinq":
            continue
        ext = device.config.get("device_id", "")
        print(f"- {device.name!r} -> id=...{ext[-8:]}  {status_by_id.get(ext, 'id não está na lista da LG!')}")
    if adapter._session:
        await adapter._session.close()


if __name__ == "__main__":
    asyncio.run(main())
