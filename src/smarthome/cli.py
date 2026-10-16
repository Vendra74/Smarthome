"""Conversa com a central pelo terminal: python -m smarthome.cli"""

from __future__ import annotations

import asyncio

from .agent import HomeAgent
from .config import build_hub


async def main() -> None:
    agent = HomeAgent(build_hub())
    print("Central da casa. Digite um pedido (Ctrl+D para sair).")
    while True:
        try:
            text = input("> ").strip()
        except EOFError:
            break
        if text:
            print(await agent.handle(text))


if __name__ == "__main__":
    asyncio.run(main())
