"""Platform metadata: which modules exist and how finished each one is."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.deps import Ctx
from app.core.permissions import PERMISSIONS
from app.modules.platform.registry import MODULES, ModuleStatus

router = APIRouter(prefix="/platform", tags=["platform"])


class ModuleStatusResponse(BaseModel):
    key: str
    phase: int
    status: ModuleStatus
    required_permission: str | None
    available_features: list[str]
    missing_features: list[str]
    #: False when the caller lacks `required_permission`; the navigation entry
    #: is then hidden rather than shown and refused.
    visible_to_caller: bool


class PermissionCatalogEntry(BaseModel):
    code: str
    module: str
    description: str
    is_sensitive: bool


@router.get("/modules", response_model=list[ModuleStatusResponse])
async def list_modules(context: Ctx) -> list[ModuleStatusResponse]:
    """Implementation status of every module, for honest navigation.

    The frontend must not hard-code this list. A module reported as `planned`
    is rendered as an explicit placeholder, never as an empty screen that
    looks broken or a polished one that looks finished.
    """
    return [
        ModuleStatusResponse(
            key=info.key,
            phase=info.phase,
            status=info.status,
            required_permission=info.required_permission,
            available_features=list(info.available_features),
            missing_features=list(info.missing_features),
            visible_to_caller=(
                info.required_permission is None or context.has(info.required_permission)
            ),
        )
        for info in MODULES
    ]


@router.get("/permissions", response_model=list[PermissionCatalogEntry])
async def list_permissions(context: Ctx) -> list[PermissionCatalogEntry]:
    """The full permission catalogue, for the role editor."""
    context.require("identity.role.read")
    return [
        PermissionCatalogEntry(
            code=p.code,
            module=p.module,
            description=p.description,
            is_sensitive=p.sensitive,
        )
        for p in PERMISSIONS
    ]
