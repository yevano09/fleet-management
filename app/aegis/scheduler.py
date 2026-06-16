import asyncio
import time
import logging

from app.database import async_session_factory
from app.aegis.engine import AegisEngine

logger = logging.getLogger(__name__)


class AegisScheduler:
    def __init__(self, engine: AegisEngine, interval: int = 15):
        self.engine = engine
        self.interval = interval
        self._stop = False

    def stop(self):
        self._stop = True

    async def run(self):
        logger.info("Aegis scheduler started (interval=%ss)", self.interval)
        while not self._stop:
            start = time.time()
            try:
                async with async_session_factory() as db:
                    await self.engine.run_cycle(db)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Aegis cycle failed")
            elapsed = time.time() - start
            sleep_time = max(0.5, self.interval - elapsed)
            await asyncio.sleep(sleep_time)
        logger.info("Aegis scheduler stopped")
