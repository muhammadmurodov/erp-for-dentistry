"""Authentication and access-management workflows.

Everything here is a service method taking an explicit session and audit
recorder. No global state, so each rule below is directly testable.

Design notes worth knowing before changing this file:

* `authenticate` keeps a uniform failure message and runs a password
  verification even for an unknown email, so response content and timing do
  not reveal which addresses are registered.
* Refresh tokens rotate on every use. Presenting an already-rotated token
  revokes the whole chain — the signature of a stolen token being replayed.
* Role grants are re-validated against `check_role_permission_set`, so a
  segregation-of-duties conflict cannot be introduced by combining two
  individually valid roles on one membership.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.errors import (
    AuthenticationFailed,
    Conflict,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from app.core.logging import get_logger
from app.core.permissions import (
    SEGREGATION_OF_DUTIES,
    check_role_permission_set,
    one_person_may_do_and_approve,
)
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    password_needs_rehash,
    verify_password,
)
from app.modules.audit import events
from app.modules.audit.service import AuditRecorder
from app.modules.identity.models import (
    Membership,
    MembershipRole,
    RefreshToken,
    Role,
    RolePermission,
    User,
)
from app.modules.organization.service import OrganizationService

log = get_logger(__name__)

#: One message for every authentication failure. Which of "no such user",
#: "wrong password" or "inactive account" applied is recorded in the audit
#: trail, not returned to the caller.
GENERIC_AUTH_FAILURE = "Email or password is incorrect"

#: Verified against on an unknown email so that the work done — and therefore
#: the response time — matches the work done for a real account.
_DUMMY_HASH = (
    "$argon2id$v=19$m=19456,t=2,p=1$c29tZXNhbHRzb21lc2FsdA$Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4Zm9vYmFy"
)


class AuthenticatedSession:
    """Everything the API needs after a successful sign-in or refresh."""

    __slots__ = ("access_token", "expires_at", "membership", "permissions", "refresh_token", "user")

    def __init__(
        self,
        *,
        user: User,
        membership: Membership | None,
        permissions: frozenset[str],
        access_token: str,
        expires_at: datetime,
        refresh_token: str,
    ) -> None:
        self.user = user
        self.membership = membership
        self.permissions = permissions
        self.access_token = access_token
        self.expires_at = expires_at
        self.refresh_token = refresh_token


class IdentityService:
    def __init__(self, session: AsyncSession, audit: AuditRecorder) -> None:
        self._session = session
        self._audit = audit

    # ------------------------------------------------------------------ reads

    async def get_user_by_email(self, email: str) -> User | None:
        stmt = select(User).where(User.email == email.lower().strip())
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_user(self, user_id: uuid.UUID) -> User:
        user = await self._session.get(User, user_id)
        if user is None:
            raise NotFound("User not found")
        return user

    async def list_memberships(self, user_id: uuid.UUID) -> list[Membership]:
        stmt = (
            select(Membership)
            .where(Membership.user_id == user_id, Membership.is_active.is_(True))
            .options(selectinload(Membership.roles))
            .order_by(Membership.created_at)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def get_membership(
        self, *, user_id: uuid.UUID, organization_id: uuid.UUID
    ) -> Membership | None:
        stmt = (
            select(Membership)
            .where(
                Membership.user_id == user_id,
                Membership.organization_id == organization_id,
            )
            .options(selectinload(Membership.roles))
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def _granted_role_ids(self, membership: Membership) -> list[uuid.UUID]:
        """Role ids on a membership, read from the link table.

        Deliberately not `membership.roles`: that relationship is declared
        `raise_on_sql`, and a membership created moments ago has no loaded
        collection to walk. One query works for both cases.
        """
        stmt = select(MembershipRole.role_id).where(
            MembershipRole.membership_id == membership.id,
            MembershipRole.organization_id == membership.organization_id,
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def resolve_permissions(self, membership: Membership) -> frozenset[str]:
        """Union of the permissions granted by every role on this membership."""
        role_ids = await self._granted_role_ids(membership)
        if not role_ids:
            return frozenset()
        stmt = select(RolePermission.permission_code).where(RolePermission.role_id.in_(role_ids))
        codes = (await self._session.execute(stmt)).scalars().all()
        return frozenset(codes)

    async def role_codes(self, membership: Membership) -> list[str]:
        role_ids = await self._granted_role_ids(membership)
        if not role_ids:
            return []
        stmt = select(Role.code).where(Role.id.in_(role_ids)).order_by(Role.code)
        return list((await self._session.execute(stmt)).scalars().all())

    # --------------------------------------------------------- authentication

    async def authenticate(
        self,
        *,
        email: str,
        password: str,
        organization_id: uuid.UUID | None,
        user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> AuthenticatedSession:
        settings = get_settings()
        email = email.lower().strip()
        user = await self.get_user_by_email(email)

        if user is None:
            # Spend comparable time so an unknown address is not faster.
            verify_password(password, _DUMMY_HASH)
            self._audit.record(
                events.LOGIN_FAILED,
                outcome="failure",
                actor_label=email,
                target_type="app_user",
                reason="no such user",
            )
            raise AuthenticationFailed(GENERIC_AUTH_FAILURE)

        now = datetime.now(UTC)
        if user.locked_until and user.locked_until > now:
            self._audit.record(
                events.LOGIN_BLOCKED,
                outcome="denied",
                actor_user_id=user.id,
                actor_label=email,
                target_type="app_user",
                target_id=user.id,
                reason="account temporarily locked after repeated failures",
            )
            raise AuthenticationFailed("This account is temporarily locked. Try again later.")

        if not verify_password(password, user.password_hash):
            user.failed_login_count += 1
            if user.failed_login_count >= settings.login_max_failed_attempts:
                user.locked_until = now + timedelta(minutes=settings.login_lockout_minutes)
                user.failed_login_count = 0
            self._audit.record(
                events.LOGIN_FAILED,
                outcome="failure",
                actor_user_id=user.id,
                actor_label=email,
                target_type="app_user",
                target_id=user.id,
                reason="wrong password",
            )
            raise AuthenticationFailed(GENERIC_AUTH_FAILURE)

        if not user.is_active:
            self._audit.record(
                events.LOGIN_FAILED,
                outcome="denied",
                actor_user_id=user.id,
                actor_label=email,
                target_type="app_user",
                target_id=user.id,
                reason="user deactivated",
            )
            raise AuthenticationFailed(GENERIC_AUTH_FAILURE)

        # Password is correct from here on.
        user.failed_login_count = 0
        user.locked_until = None
        user.last_login_at = now
        if password_needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)

        membership = await self._select_membership(user, organization_id)
        permissions = await self.resolve_permissions(membership) if membership else frozenset()

        self._audit.bind(
            actor_user_id=user.id,
            actor_label=user.email,
            organization_id=membership.organization_id if membership else None,
        )
        self._audit.record(
            events.LOGIN_SUCCEEDED,
            target_type="app_user",
            target_id=user.id,
            target_label=user.email,
            new_values={
                "organization_id": str(membership.organization_id) if membership else None,
                "permission_count": len(permissions),
            },
        )
        return await self._issue_session(
            user=user,
            membership=membership,
            permissions=permissions,
            user_agent=user_agent,
            ip_address=ip_address,
        )

    async def _select_membership(
        self, user: User, organization_id: uuid.UUID | None
    ) -> Membership | None:
        memberships = await self.list_memberships(user.id)
        if organization_id is not None:
            for membership in memberships:
                if membership.organization_id == organization_id:
                    return membership
            # Do not disclose whether the organization exists.
            self._audit.record(
                events.PERMISSION_DENIED,
                outcome="denied",
                actor_user_id=user.id,
                actor_label=user.email,
                target_type="organization",
                target_id=organization_id,
                reason="user has no active membership in the requested organization",
            )
            raise PermissionDenied("You do not have access to that organization")
        if len(memberships) == 1:
            return memberships[0]
        if not memberships:
            # A superuser with no membership can still sign in, to bootstrap.
            if user.is_superuser:
                return None
            raise PermissionDenied("This account is not a member of any organization yet")
        raise ValidationFailed(
            "Select an organization to sign in to",
            details={
                "field": "organization_id",
                "options": [{"id": str(m.organization_id)} for m in memberships],
            },
        )

    async def _issue_session(
        self,
        *,
        user: User,
        membership: Membership | None,
        permissions: frozenset[str],
        user_agent: str | None,
        ip_address: str | None,
        replaces: RefreshToken | None = None,
    ) -> AuthenticatedSession:
        settings = get_settings()
        access_token, expires_at = create_access_token(
            user_id=user.id,
            email=user.email,
            organization_id=membership.organization_id if membership else None,
            membership_id=membership.id if membership else None,
            permissions=permissions,
            is_superuser=user.is_superuser,
        )
        plaintext, digest = generate_refresh_token()
        refresh = RefreshToken(
            user_id=user.id,
            token_hash=digest,
            membership_id=membership.id if membership else None,
            expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_ttl_days),
            user_agent=(user_agent or None),
            ip_address=ip_address,
        )
        self._session.add(refresh)
        await self._session.flush()
        if replaces is not None:
            replaces.replaced_by_id = refresh.id
        return AuthenticatedSession(
            user=user,
            membership=membership,
            permissions=permissions,
            access_token=access_token,
            expires_at=expires_at,
            refresh_token=plaintext,
        )

    async def refresh(
        self,
        *,
        refresh_token: str,
        user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> AuthenticatedSession:
        digest = hash_refresh_token(refresh_token)
        stmt = select(RefreshToken).where(RefreshToken.token_hash == digest)
        token = (await self._session.execute(stmt)).scalar_one_or_none()
        if token is None:
            raise AuthenticationFailed("Refresh token is not recognised")

        now = datetime.now(UTC)
        if token.replaced_by_id is not None or token.revoked_at is not None:
            # A rotated or revoked token came back. Treat the chain as
            # compromised and end every session for this user.
            await self._revoke_all_for_user(token.user_id, reason="token_replay")
            self._audit.record(
                events.TOKEN_REPLAY_DETECTED,
                outcome="denied",
                actor_user_id=token.user_id,
                target_type="refresh_token",
                target_id=token.id,
                reason="a refresh token was presented after it had been rotated or revoked",
            )
            raise AuthenticationFailed("Session is no longer valid. Sign in again.")

        if token.expires_at <= now:
            raise AuthenticationFailed("Session has expired. Sign in again.")

        user = await self.get_user(token.user_id)
        if not user.is_active:
            raise AuthenticationFailed("Session is no longer valid. Sign in again.")

        membership = None
        permissions: frozenset[str] = frozenset()
        if token.membership_id is not None:
            membership = await self._session.get(
                Membership,
                token.membership_id,
                options=[selectinload(Membership.roles)],
            )
            if membership is None or not membership.is_active:
                raise AuthenticationFailed(
                    "Your access to this organization has changed. Sign in again."
                )
            permissions = await self.resolve_permissions(membership)

        token.revoked_at = now
        token.revoked_reason = "rotated"
        self._audit.bind(
            actor_user_id=user.id,
            actor_label=user.email,
            organization_id=membership.organization_id if membership else None,
        )
        self._audit.record(
            events.TOKEN_REFRESHED,
            target_type="refresh_token",
            target_id=token.id,
        )
        return await self._issue_session(
            user=user,
            membership=membership,
            permissions=permissions,
            user_agent=user_agent,
            ip_address=ip_address,
            replaces=token,
        )

    async def logout(self, *, refresh_token: str) -> None:
        digest = hash_refresh_token(refresh_token)
        stmt = select(RefreshToken).where(RefreshToken.token_hash == digest)
        token = (await self._session.execute(stmt)).scalar_one_or_none()
        if token is None or token.revoked_at is not None:
            # Already gone. Report success: a sign-out is idempotent and the
            # response must not confirm whether a token was valid.
            return
        token.revoked_at = datetime.now(UTC)
        token.revoked_reason = "logout"
        self._audit.record(events.LOGOUT, target_type="refresh_token", target_id=token.id)

    async def _revoke_all_for_user(self, user_id: uuid.UUID, *, reason: str) -> None:
        stmt = select(RefreshToken).where(
            RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)
        )
        now = datetime.now(UTC)
        for token in (await self._session.execute(stmt)).scalars().all():
            token.revoked_at = now
            token.revoked_reason = reason

    async def switch_organization(
        self,
        *,
        user: User,
        organization_id: uuid.UUID,
        refresh_token: str,
        user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> AuthenticatedSession:
        """Re-issue tokens bound to another of this user's memberships.

        The current refresh token is consumed, so a user ends up with one
        session per sign-in rather than one per organization they visited.
        """
        membership = await self.get_membership(user_id=user.id, organization_id=organization_id)
        if membership is None or not membership.is_active:
            self._audit.record(
                events.PERMISSION_DENIED,
                outcome="denied",
                actor_user_id=user.id,
                actor_label=user.email,
                target_type="organization",
                target_id=organization_id,
                reason="switch requested into an organization without active membership",
            )
            raise PermissionDenied("You do not have access to that organization")

        digest = hash_refresh_token(refresh_token)
        stmt = select(RefreshToken).where(RefreshToken.token_hash == digest)
        token = (await self._session.execute(stmt)).scalar_one_or_none()
        if token is None or token.user_id != user.id or token.revoked_at is not None:
            raise AuthenticationFailed("Session is no longer valid. Sign in again.")
        token.revoked_at = datetime.now(UTC)
        token.revoked_reason = "organization_switch"

        permissions = await self.resolve_permissions(membership)
        self._audit.bind(
            actor_user_id=user.id,
            actor_label=user.email,
            organization_id=organization_id,
        )
        self._audit.record(
            events.LOGIN_SUCCEEDED,
            target_type="membership",
            target_id=membership.id,
            target_label=user.email,
            new_values={
                "organization_id": str(organization_id),
                "via": "organization_switch",
            },
        )
        return await self._issue_session(
            user=user,
            membership=membership,
            permissions=permissions,
            user_agent=user_agent,
            ip_address=ip_address,
            replaces=token,
        )

    # --------------------------------------------------------------- password

    async def change_password(
        self, *, user: User, current_password: str, new_password: str
    ) -> None:
        if not verify_password(current_password, user.password_hash):
            self._audit.record(
                events.PASSWORD_CHANGED,
                outcome="failure",
                target_type="app_user",
                target_id=user.id,
                reason="current password did not match",
            )
            raise AuthenticationFailed("Current password is incorrect")
        if current_password == new_password:
            raise ValidationFailed("New password must differ from the current one")
        user.password_hash = hash_password(new_password)
        user.must_change_password = False
        # Every other session of this user ends: a password change is also the
        # remedy for a suspected compromise.
        await self._revoke_all_for_user(user.id, reason="password_changed")
        self._audit.record(events.PASSWORD_CHANGED, target_type="app_user", target_id=user.id)

    # ------------------------------------------------------- user management

    async def create_user_in_organization(
        self,
        *,
        organization_id: uuid.UUID,
        email: str,
        full_name: str,
        password: str,
        role_codes: list[str],
        phone: str | None = None,
        locale: str = "uz",
        job_title: str | None = None,
        must_change_password: bool = True,
    ) -> tuple[User, Membership]:
        # Reached through the organization module's service rather than its
        # models: a module owns its own tables, and this keeps the dependency
        # a service call instead of a reach-in.
        await OrganizationService(self._session, self._audit).get(organization_id)

        existing = await self.get_user_by_email(email)
        if existing is not None:
            # The account exists globally; add a membership instead of a user.
            user = existing
            if await self.get_membership(user_id=user.id, organization_id=organization_id):
                raise Conflict("This person is already a member of the organization")
        else:
            user = User(
                email=email.lower().strip(),
                password_hash=hash_password(password),
                full_name=full_name.strip(),
                phone=phone,
                locale=locale,
                must_change_password=must_change_password,
            )
            self._session.add(user)
            await self._session.flush()
            self._audit.record(
                events.USER_CREATED,
                target_type="app_user",
                target_id=user.id,
                target_label=user.email,
                new_values={"email": user.email, "full_name": user.full_name},
            )

        membership = Membership(
            organization_id=organization_id,
            user_id=user.id,
            job_title=job_title,
        )
        self._session.add(membership)
        await self._session.flush()
        self._audit.record(
            events.MEMBERSHIP_CREATED,
            target_type="membership",
            target_id=membership.id,
            target_label=user.email,
            new_values={"organization_id": str(organization_id), "job_title": job_title},
        )
        if role_codes:
            await self.set_membership_roles(membership=membership, role_codes=role_codes)
        return user, membership

    async def set_membership_roles(
        self, *, membership: Membership, role_codes: list[str]
    ) -> list[Role]:
        """Replace this membership's roles, refusing conflicting combinations.

        Unless the organization has declared it cannot separate duties, in
        which case one person holding both sides of a control is the point:
        a clinic with three people needs the receptionist to take money on a
        Saturday, and refusing the grant does not create a fourth person
        (ADR-041). The declaration is read from the organization rather than
        passed in, so a caller cannot ask for the relaxation.
        """
        requested = sorted(set(role_codes))
        roles = await self._load_roles(organization_id=membership.organization_id, codes=requested)

        # The effective permission set is the union across granted roles, so a
        # conflict can appear from a combination that each role allows alone.
        combined: set[str] = set()
        for role in roles:
            combined |= {link.permission_code for link in role.permissions}
        granted_codes = {r.code for r in roles}
        organization = await OrganizationService(self._session, self._audit).get(
            membership.organization_id
        )
        problems: list[str] = []
        if not one_person_may_do_and_approve(
            duties_cannot_be_separated=organization.duties_cannot_be_separated,
            actor_is_owner="owner" in granted_codes,
        ):
            for left, right, reason in SEGREGATION_OF_DUTIES:
                if left in combined and right in combined:
                    problems.append(f"{reason} ({left} + {right})")
        if problems:
            raise ValidationFailed(
                "This combination of roles breaks a segregation-of-duties control",
                details={"problems": problems, "roles": requested},
            )

        previous = await self.role_codes(membership)
        if membership.is_primary_owner and "owner" not in granted_codes:
            raise Conflict("The primary owner must keep the owner role. Transfer ownership first.")

        await self._session.execute(
            delete(MembershipRole).where(
                MembershipRole.membership_id == membership.id,
                MembershipRole.organization_id == membership.organization_id,
            )
        )
        await self._session.flush()
        for role in roles:
            self._session.add(
                MembershipRole(
                    organization_id=membership.organization_id,
                    membership_id=membership.id,
                    role_id=role.id,
                    granted_by_user_id=self._audit.actor_user_id,
                )
            )
        await self._session.flush()

        added = sorted(granted_codes - set(previous))
        removed = sorted(set(previous) - granted_codes)
        if added:
            self._audit.record(
                events.ROLE_GRANTED,
                target_type="membership",
                target_id=membership.id,
                old_values={"role_codes": previous},
                new_values={"role_codes": requested, "added": added},
            )
        if removed:
            self._audit.record(
                events.ROLE_REVOKED,
                target_type="membership",
                target_id=membership.id,
                old_values={"role_codes": previous},
                new_values={"role_codes": requested, "removed": removed},
            )
        return roles

    async def _load_roles(self, *, organization_id: uuid.UUID, codes: list[str]) -> list[Role]:
        if not codes:
            return []
        stmt = (
            select(Role)
            .where(
                Role.code.in_(codes),
                (Role.organization_id == organization_id) | (Role.organization_id.is_(None)),
            )
            .options(selectinload(Role.permissions))
        )
        roles = list((await self._session.execute(stmt)).scalars().all())
        # Prefer an organization's own role over the global one of the same code.
        by_code: dict[str, Role] = {}
        for role in roles:
            if role.code not in by_code or role.organization_id is not None:
                by_code[role.code] = role
        missing = sorted(set(codes) - set(by_code))
        if missing:
            raise ValidationFailed(
                f"Unknown role(s): {', '.join(missing)}",
                details={"unknown_roles": missing},
            )
        for role in by_code.values():
            problems = check_role_permission_set(
                role.code, {link.permission_code for link in role.permissions}
            )
            if problems:
                raise Conflict(
                    f"Role {role.code!r} is itself misconfigured and cannot be granted",
                    details={"problems": problems},
                )
        return [by_code[c] for c in codes]

    async def set_membership_active(
        self, *, membership: Membership, is_active: bool, reason: str | None = None
    ) -> None:
        if membership.is_primary_owner and not is_active:
            raise Conflict(
                "The primary owner's membership cannot be deactivated. Transfer ownership first."
            )
        before = membership.is_active
        membership.is_active = is_active
        self._audit.record(
            events.MEMBERSHIP_UPDATED,
            target_type="membership",
            target_id=membership.id,
            old_values={"is_active": before},
            new_values={"is_active": is_active},
            reason=reason,
        )
