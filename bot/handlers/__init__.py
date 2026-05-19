from __future__ import annotations

from aiogram import Router

from .admin import router as admin_router
from .client import router as client_router
from .common import router as common_router


def setup_routers() -> Router:
    root = Router()
    # Порядок важливий: спершу common (/start), потім інші callback-и
    root.include_router(common_router)
    root.include_router(admin_router)
    root.include_router(client_router)
    return root

