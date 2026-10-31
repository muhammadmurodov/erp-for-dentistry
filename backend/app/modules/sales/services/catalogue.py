"""The service catalogue and its price history."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.money import quantize
from app.modules.audit.service import AuditRecorder
from app.modules.sales.enums import ServiceCategory
from app.modules.sales.models import Customer, Service, ServicePrice


class CatalogueService:
    def __init__(self, session: AsyncSession, audit: AuditRecorder) -> None:
        self._session = session
        self._audit = audit

    # ------------------------------------------------------------- services

    async def list_services(
        self,
        *,
        organization_id: uuid.UUID,
        active_only: bool = True,
        search: str | None = None,
    ) -> list[Service]:
        stmt = select(Service).where(Service.organization_id == organization_id)
        if active_only:
            stmt = stmt.where(Service.is_active.is_(True))
        if search:
            needle = f"%{search.strip()}%"
            stmt = stmt.where(Service.code.ilike(needle) | Service.name.ilike(needle))
        return list((await self._session.execute(stmt.order_by(Service.code))).scalars().all())

    async def get_service(self, *, organization_id: uuid.UUID, service_id: uuid.UUID) -> Service:
        stmt = select(Service).where(
            Service.organization_id == organization_id, Service.id == service_id
        )
        service = (await self._session.execute(stmt)).scalar_one_or_none()
        if service is None:
            raise NotFound("Service not found")
        return service

    async def create_service(
        self,
        *,
        organization_id: uuid.UUID,
        code: str,
        name: str,
        category: ServiceCategory = ServiceCategory.TREATMENT,
        price: Decimal | None = None,
        effective_from: date | None = None,
        currency: str = "UZS",
        **attributes: object,
    ) -> Service:
        code = code.strip()
        existing = (
            await self._session.execute(
                select(Service).where(
                    Service.organization_id == organization_id, Service.code == code
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise Conflict(f"A service with code {code!r} already exists")

        service = Service(
            organization_id=organization_id,
            code=code,
            name=name.strip(),
            category=category,
            **attributes,  # type: ignore[arg-type]
        )
        self._session.add(service)
        await self._session.flush()

        if price is not None:
            await self.set_price(
                organization_id=organization_id,
                service_id=service.id,
                amount=price,
                effective_from=effective_from or date.today(),
                currency=currency,
            )

        self._audit.record(
            "sales.service.created",
            organization_id=organization_id,
            target_type="service",
            target_id=service.id,
            target_label=code,
            new_values={"code": code, "name": name, "price": price},
        )
        return service

    # --------------------------------------------------------------- prices

    async def set_price(
        self,
        *,
        organization_id: uuid.UUID,
        service_id: uuid.UUID,
        amount: Decimal,
        effective_from: date,
        currency: str = "UZS",
    ) -> ServicePrice:
        """Start a new price from a date, closing the previous one.

        Prices are never overwritten. An invoice raised last March must stay
        explicable by the price list that applied last March.
        """
        service = await self.get_service(organization_id=organization_id, service_id=service_id)
        amount = quantize(amount, currency)
        if amount < Decimal("0"):
            raise ValidationFailed("A price must not be negative")

        current = await self.price_on(
            organization_id=organization_id, service_id=service_id, on=effective_from
        )
        if current is not None:
            if current.effective_from == effective_from:
                raise Conflict(
                    f"A price for {service.code} already starts on {effective_from.isoformat()}",
                    details={"effective_from": effective_from.isoformat()},
                )
            # Close the previous price the day before this one starts, so
            # exactly one price answers for any date.
            current.effective_to = effective_from - timedelta(days=1)

        price = ServicePrice(
            organization_id=organization_id,
            service_id=service_id,
            amount=amount,
            currency=currency.upper(),
            effective_from=effective_from,
        )
        self._session.add(price)
        await self._session.flush()

        self._audit.record(
            "sales.service.price_changed",
            organization_id=organization_id,
            target_type="service",
            target_id=service_id,
            target_label=service.code,
            new_values={
                "amount": amount,
                "currency": currency,
                "effective_from": effective_from,
            },
        )
        return price

    async def price_on(
        self, *, organization_id: uuid.UUID, service_id: uuid.UUID, on: date
    ) -> ServicePrice | None:
        """The price in force on a date, or None if the service had no price."""
        stmt = (
            select(ServicePrice)
            .where(
                ServicePrice.organization_id == organization_id,
                ServicePrice.service_id == service_id,
                ServicePrice.effective_from <= on,
                (ServicePrice.effective_to.is_(None)) | (ServicePrice.effective_to >= on),
            )
            .order_by(ServicePrice.effective_from.desc())
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_prices(
        self, *, organization_id: uuid.UUID, service_id: uuid.UUID
    ) -> list[ServicePrice]:
        stmt = (
            select(ServicePrice)
            .where(
                ServicePrice.organization_id == organization_id,
                ServicePrice.service_id == service_id,
            )
            .order_by(ServicePrice.effective_from.desc())
        )
        return list((await self._session.execute(stmt)).scalars().all())

    # ------------------------------------------------------------ customers

    async def list_customers(
        self,
        *,
        organization_id: uuid.UUID,
        active_only: bool = True,
        search: str | None = None,
    ) -> list[Customer]:
        stmt = select(Customer).where(Customer.organization_id == organization_id)
        if active_only:
            stmt = stmt.where(Customer.is_active.is_(True))
        if search:
            needle = f"%{search.strip()}%"
            stmt = stmt.where(Customer.code.ilike(needle) | Customer.display_name.ilike(needle))
        return list(
            (await self._session.execute(stmt.order_by(Customer.display_name))).scalars().all()
        )

    async def get_customer(self, *, organization_id: uuid.UUID, customer_id: uuid.UUID) -> Customer:
        stmt = select(Customer).where(
            Customer.organization_id == organization_id, Customer.id == customer_id
        )
        customer = (await self._session.execute(stmt)).scalar_one_or_none()
        if customer is None:
            raise NotFound("Customer not found")
        return customer

    async def create_customer(
        self,
        *,
        organization_id: uuid.UUID,
        code: str,
        display_name: str,
        **attributes: object,
    ) -> Customer:
        code = code.strip()
        existing = (
            await self._session.execute(
                select(Customer).where(
                    Customer.organization_id == organization_id, Customer.code == code
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise Conflict(f"A customer with code {code!r} already exists")

        customer = Customer(
            organization_id=organization_id,
            code=code,
            display_name=display_name.strip(),
            **attributes,  # type: ignore[arg-type]
        )
        self._session.add(customer)
        await self._session.flush()
        self._audit.record(
            "sales.customer.created",
            organization_id=organization_id,
            target_type="customer",
            target_id=customer.id,
            target_label=code,
            # The name is business data, not clinical data, so it is safe to
            # record here. Clinical detail never enters this module.
            new_values={"code": code, "display_name": display_name},
        )
        return customer
