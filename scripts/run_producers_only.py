import asyncio
import logging

from app.streams.producer_manager import manager


LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


async def _run_forever() -> None:
    manager.ensure_loop()
    while True:
        try:
            await manager.reconcile_all()
        except Exception as exc:
            LOG.info("producer reconcile failed err=%s", exc)
        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(_run_forever())
