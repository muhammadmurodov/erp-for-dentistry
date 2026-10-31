"""Per-organization document numbering.

Numbers are allocated under a row lock rather than from a PostgreSQL
sequence. A sequence is not transactional: a rolled-back posting would burn a
number and leave a gap, and an accountant reading a ledger with missing entry
numbers has to prove nothing was deleted. Gapless numbering is worth the lock
at this volume.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.accounting.models import NumberSequence

JOURNAL_ENTRY_SEQUENCE = "journal_entry"


async def next_number(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    name: str = JOURNAL_ENTRY_SEQUENCE,
    prefix: str = "JE-",
    padding: int = 6,
) -> str:
    """Take the next number, creating the sequence on first use.

    `with_for_update()` serialises concurrent callers: the second waits for
    the first to commit or roll back, so two postings cannot be handed the
    same number.
    """
    stmt = (
        select(NumberSequence)
        .where(
            NumberSequence.organization_id == organization_id,
            NumberSequence.name == name,
        )
        .with_for_update()
    )
    sequence = (await session.execute(stmt)).scalar_one_or_none()

    if sequence is None:
        sequence = NumberSequence(
            organization_id=organization_id,
            name=name,
            prefix=prefix,
            next_value=1,
            padding=padding,
        )
        session.add(sequence)
        await session.flush()

    value = sequence.next_value
    sequence.next_value = value + 1
    await session.flush()
    return f"{sequence.prefix}{value:0{sequence.padding}d}"
