"""Operator commands: seed demo data, create an organization, inspect config.

Run with ``python -m app.cli --help``.

Everything this module writes is fictional. No name, patient, amount or bank
detail from the reference workbook is reproduced here — see
docs/SECURITY.md on why the legacy business data stays out of fixtures.
"""

from __future__ import annotations

import asyncio
from datetime import date

import typer

from app.core.config import get_settings
from app.core.db import get_session_factory
from app.core.logging import configure_logging, get_logger
from app.modules.audit.service import AuditRecorder
from app.modules.identity.service import IdentityService
from app.modules.provisioning.service import ProvisioningService

app = typer.Typer(help="Business ERP operator commands", no_args_is_help=True)
log = get_logger(__name__)

# --- deterministic demo fixture --------------------------------------------
# A fictional dental clinic. The shape mirrors a small Uzbek MChJ so that the
# demo exercises the same code paths as the real tenant will, without carrying
# any of its data.
DEMO_ORG_CODE = "demo-clinic"
DEMO_ORG = {
    "code": DEMO_ORG_CODE,
    "legal_name": '"Namuna Stomatologiya" MChJ',
    "short_name": "Namuna Stomatologiya",
    "tax_number": "100200300",
    "legal_form": "MChJ",
    "activity_description": "Stomatologiya xizmatlari ko'rsatish (demo)",
    "address": "O'zbekiston, Toshkent sh., Namuna ko'chasi 1 (demo)",
    "base_currency": "UZS",
    "timezone": "Asia/Tashkent",
    "default_locale": "uz",
    "registered_on": date(2020, 1, 15),
}
DEMO_OWNER = {
    "email": "owner@demo.example.com",
    "full_name": "Demo Owner",
    "password": "demo-owner-password-1",
}
#: One account per role so each permission set can be exercised by hand.
#: `example.com` is reserved by RFC 2606, so these can never be real addresses.
DEMO_STAFF = [
    ("accountant@demo.example.com", "Demo Accountant", ["accountant"], "Bosh buxgalter"),
    ("cashier@demo.example.com", "Demo Cashier", ["cashier"], "Kassir"),
    ("reception@demo.example.com", "Demo Receptionist", ["receptionist"], "Administrator"),
    ("dentist@demo.example.com", "Demo Dentist", ["dentist"], "Stomatolog"),
    ("auditor@demo.example.com", "Demo Auditor", ["auditor"], "Auditor"),
]
DEMO_STAFF_PASSWORD = "demo-staff-password-1"


@app.command("show-config")
def show_config() -> None:
    """Print the effective configuration with secrets masked."""
    settings = get_settings()
    typer.echo(f"environment      : {settings.app_env}")
    typer.echo(
        f"database         : {settings.postgres_user}@"
        f"{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}"
    )
    typer.echo(f"redis            : {settings.redis_url}")
    typer.echo(f"secret key set   : {'yes' if settings.secret_key else 'NO'}")
    typer.echo(f"cors origins     : {', '.join(settings.cors_origin_list)}")
    typer.echo(f"access token ttl : {settings.access_token_ttl_minutes} min")


@app.command("seed-demo")
def seed_demo() -> None:
    """Create a fictional demo organization with one account per role.

    Idempotent by skipping: if the demo organization is already present the
    command reports it and does nothing. There is deliberately no --force,
    because the demo organization cannot be deleted — audit records reference
    it with ON DELETE RESTRICT, which is the guarantee that makes the trail
    trustworthy. To start over, drop the database and re-run the migrations.
    """
    settings = get_settings()
    configure_logging(settings.log_level, "console")
    if settings.is_production:
        raise typer.BadParameter("Refusing to seed demo data in production")
    asyncio.run(_seed_demo())


async def _seed_demo() -> None:
    from sqlalchemy import select

    from app.modules.organization.models import Organization

    factory = get_session_factory()
    async with factory() as session:
        existing = (
            await session.execute(select(Organization).where(Organization.code == DEMO_ORG_CODE))
        ).scalar_one_or_none()
        if existing is not None:
            typer.echo(
                f"Demo organization {DEMO_ORG_CODE!r} already exists "
                f"(id={existing.id}). Nothing to do."
            )
            typer.echo(
                "To rebuild it: drop the database, then run "
                "`alembic upgrade head` and this command again."
            )
            return

        audit = AuditRecorder(session, actor_label="cli:seed-demo")
        provisioning = ProvisioningService(session, audit)
        organization, owner, _ = await provisioning.create_organization_with_owner(
            owner_email=DEMO_OWNER["email"],
            owner_full_name=DEMO_OWNER["full_name"],
            owner_password=DEMO_OWNER["password"],
            **DEMO_ORG,
        )

        identity = IdentityService(session, audit)
        for email, full_name, roles, job_title in DEMO_STAFF:
            await identity.create_user_in_organization(
                organization_id=organization.id,
                email=email,
                full_name=full_name,
                password=DEMO_STAFF_PASSWORD,
                role_codes=roles,
                job_title=job_title,
                must_change_password=False,
            )
        await session.commit()

    typer.echo(f"Created organization {organization.code!r} (id={organization.id})")
    typer.echo(f"  owner : {owner.email} / {DEMO_OWNER['password']}")
    for email, _, roles, _ in DEMO_STAFF:
        typer.echo(f"  {roles[0]:<13}: {email} / {DEMO_STAFF_PASSWORD}")
    typer.echo(
        "\nThese are development credentials for fictional accounts. "
        "Never seed them into an environment reachable from the internet."
    )


@app.command("create-organization")
def create_organization(
    code: str = typer.Option(..., help="Short handle, lowercase"),
    legal_name: str = typer.Option(..., help="Registered legal name"),
    owner_email: str = typer.Option(..., help="Email of the primary owner"),
    owner_name: str = typer.Option(..., help="Full name of the primary owner"),
    tax_number: str | None = typer.Option(None, help="STIR / INN, 9-14 digits"),
) -> None:
    """Create a real organization and its primary owner.

    The owner's password is prompted for, never passed on the command line
    where it would land in shell history.
    """
    password = typer.prompt("Owner password", hide_input=True, confirmation_prompt=True)
    configure_logging(get_settings().log_level, "console")
    asyncio.run(
        _create_organization(
            code=code.lower(),
            legal_name=legal_name,
            owner_email=owner_email.lower(),
            owner_name=owner_name,
            owner_password=password,
            tax_number=tax_number,
        )
    )


async def _create_organization(
    *,
    code: str,
    legal_name: str,
    owner_email: str,
    owner_name: str,
    owner_password: str,
    tax_number: str | None,
) -> None:
    factory = get_session_factory()
    async with factory() as session:
        audit = AuditRecorder(session, actor_label="cli:create-organization")
        provisioning = ProvisioningService(session, audit)
        organization, owner, _ = await provisioning.create_organization_with_owner(
            code=code,
            legal_name=legal_name,
            tax_number=tax_number,
            owner_email=owner_email,
            owner_full_name=owner_name,
            owner_password=owner_password,
        )
        await session.commit()
    typer.echo(f"Created {organization.code!r} (id={organization.id}); owner {owner.email}")


if __name__ == "__main__":
    app()
