/**
 * Drives the two things built for a clinic run by two or three family members:
 * the duty-separation declaration (ADR-041) and the duplicate-patient review
 * queue (ADR-042), in a real Chrome against the real API and PostgreSQL.
 *
 * Both are features that *relax* something — one an approval control, the
 * other the temptation to let matching merge two people — so most of this
 * pass is about limits rather than happy paths:
 *
 *   only the owner may declare it, and only with a reason;
 *   declaring it grants nobody a single extra permission;
 *   a cashier may then close their own short till, and the record says so;
 *   nothing merges two patients without a person confirming it twice.
 *
 * It never destroys data. Every run creates its own staff, patients and
 * till session, banks what it takes, closes the drawer it opened, and leaves
 * the declaration as it found it.
 */
const path = require('path')
const { chromium } = require('playwright')

const WEB = process.env.E2E_WEB || 'http://localhost:5173'
const API = process.env.E2E_API || 'http://localhost:8000/api/v1'
const SHOTS = process.env.E2E_SHOTS || path.join(__dirname, 'shots')

const TENANT = process.env.E2E_TENANT || 'verify-3d'
const OWNER = { email: `owner@${TENANT}.example.com`, password: 'verify-owner-password-1' }
const STAFF_PASSWORD = 'verify-staff-password-1'
const TEMP_PASSWORD = 'verify-temp-password-0'
const RECEPTIONIST = { email: `receptionist@${TENANT}.example.com`, password: STAFF_PASSWORD }
const CASHIER = { email: `cashier@${TENANT}.example.com`, password: STAFF_PASSWORD }

const RUN = Date.now().toString().slice(-6)
const TODAY = new Date().toISOString().slice(0, 10)

/** The pair the whole duplicate queue exists for: same surname, same year. */
const FAMILY = `Oripov${RUN}`
const BIRTH_YEAR = 1990

const problems = []
let step = 'startup'

function note(kind, detail) {
  problems.push(`[${step}] ${kind}: ${detail}`)
}

// --------------------------------------------------------------- the API side

async function token(who) {
  const response = await fetch(`${API}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(who),
  })
  if (!response.ok) throw new Error(`login failed for ${who.email}: ${response.status}`)
  return (await response.json()).access_token
}

function client(accessToken) {
  const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${accessToken}` }
  const call = async (method, pathname, body) => {
    const response = await fetch(`${API}${pathname}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    const text = await response.text()
    let parsed = null
    try {
      parsed = text ? JSON.parse(text) : null
    } catch {
      parsed = text
    }
    return { status: response.status, body: parsed }
  }
  const ok = async (method, pathname, body) => {
    const result = await call(method, pathname, body)
    if (result.status >= 400) {
      throw new Error(`${method} ${pathname} → ${result.status} ${JSON.stringify(result.body)}`)
    }
    return result.body
  }
  return { call, ok }
}

async function ensureStaff(owner) {
  for (const [who, roles, name] of [
    [RECEPTIONIST, ['receptionist'], 'Verify Receptionist'],
    [CASHIER, ['cashier'], 'Verify Cashier'],
  ]) {
    const existing = await fetch(`${API}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(who),
    })
    if (existing.ok) continue
    await owner.ok('POST', '/users', {
      email: who.email,
      full_name: name,
      password: TEMP_PASSWORD,
      role_codes: roles,
      locale: 'uz',
    })
    const fresh = client(await token({ email: who.email, password: TEMP_PASSWORD }))
    await fresh.ok('POST', '/auth/change-password', {
      current_password: TEMP_PASSWORD,
      new_password: who.password,
    })
    console.log(`created ${who.email} as ${roles.join(', ')}`)
  }
}

async function declare(owner, on, reason) {
  return owner.ok('PUT', '/organization/duty-separation', {
    duties_cannot_be_separated: on,
    reason,
  })
}

async function ledger(owner) {
  const report = await owner.ok(
    'GET',
    '/accounting/reports/trial-balance?from_date=2026-01-01&to_date=2026-12-31',
  )
  const balances = {}
  for (const row of report.rows) {
    balances[row.account_code] = Number(row.closing_debit) - Number(row.closing_credit)
  }
  return balances
}

// ----------------------------------------------------------- the browser side

async function shot(page, name) {
  await page.screenshot({ path: path.join(SHOTS, `small-clinic-${name}.png`), fullPage: true })
}

async function assertNoOverflow(page, where) {
  const over = await page.evaluate(() => {
    const d = document.documentElement
    return d.scrollWidth - d.clientWidth
  })
  if (over > 1) note('horizontal overflow', `${where}: page is ${over}px wider than the viewport`)
}

function watch(page, label) {
  page.on('console', (message) => {
    if (message.type() === 'error') note(`console error${label}`, message.text())
    if (message.type() === 'warning' && !/React Router|Download the React/i.test(message.text())) {
      note(`console warning${label}`, message.text())
    }
  })
  page.on('pageerror', (error) => note(`page error${label}`, error.message))
  page.on('requestfailed', (request) => {
    if (!/favicon/.test(request.url())) {
      note(
        `request failed${label}`,
        `${request.method()} ${request.url()} — ${request.failure()?.errorText}`,
      )
    }
  })
  page.on('response', (response) => {
    if (response.status() >= 500) {
      note(`server error${label}`, `${response.status()} ${response.url()}`)
    }
  })
}

async function signIn(page, who) {
  await page.goto(WEB, { waitUntil: 'networkidle' })
  await page.evaluate(() => {
    try {
      window.localStorage.setItem('erp.locale', 'en')
    } catch {}
  })
  await page.reload({ waitUntil: 'networkidle' })
  await page.getByLabel(/e-?mail/i).fill(who.email)
  await page.locator('input[type="password"]').fill(who.password)
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
  await page.waitForSelector('nav', { timeout: 15000 })
  await page.waitForTimeout(700)
}

async function signOut(page) {
  // Clearing storage is how a pass changes user without depending on where
  // the sign-out control happens to live.
  await page.evaluate(() => {
    try {
      window.localStorage.clear()
      window.sessionStorage.clear()
    } catch {}
  })
}

async function openOrganizationSettings(page) {
  await page.goto(`${WEB}/settings/organization`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(1000)
}

async function openDuplicates(page) {
  await page.goto(`${WEB}/clinic/duplicates`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(1200)
}

async function registerPatient(page, { family, given, birthYear, phone }) {
  await page.goto(`${WEB}/clinic/patients`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(900)
  await page.getByRole('button', { name: 'Register patient' }).first().click()
  await page.waitForTimeout(400)
  await page.locator('#patient-family').fill(family)
  await page.locator('#patient-given').fill(given)
  await page.locator('#patient-birth-year').fill(String(birthYear))
  if (phone) await page.locator('#patient-phone').fill(phone)
  await page.getByRole('button', { name: 'Create', exact: true }).click()
  await page.waitForTimeout(1400)
  const duplicates = await page
    .getByText('This person may already be on file')
    .isVisible()
    .catch(() => false)
  return duplicates
}

/**
 * Open a session on this register, whatever the drawer currently holds.
 *
 * A float must equal what the previous session counted — cash does not move
 * between sessions on its own (ADR-031) — so the amount is not something a
 * pass can assume. Asking for nothing and reading the refusal is how the
 * script learns what the drawer is holding without reimplementing the rule.
 */
async function openSession(actor, register) {
  const first = await actor.call('POST', '/cash/sessions', {
    register_id: register.id,
    business_date: TODAY,
    opening_float: '0',
  })
  if (first.status < 400) return first.body
  const expected = first.body?.details?.expected_float
  if (!expected) {
    throw new Error(`opening a session failed: ${JSON.stringify(first.body)}`)
  }
  return actor.ok('POST', '/cash/sessions', {
    register_id: register.id,
    business_date: TODAY,
    opening_float: expected,
  })
}

// ------------------------------------------------------------------- the pass

async function run() {
  step = '0 setup over the API'
  const owner = client(await token(OWNER))
  await ensureStaff(owner)

  const before = await owner.ok('GET', '/organization')
  const wasDeclared = before.duties_cannot_be_separated
  console.log(`tenant ${TENANT}; duties_cannot_be_separated was ${wasDeclared}`)
  // Start from the strict state whatever the tenant was left in, so the
  // "refused by default" steps mean something.
  if (wasDeclared) await declare(owner, false, 'e2e: start from the strict state')

  const browser = await chromium.launch({ channel: 'chrome', headless: true })
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  const page = await context.newPage()
  watch(page, '')

  // ============================================ A. the declaration is the owner's

  step = '1 the cashier is not offered the declaration'
  await signIn(page, CASHIER)
  await openOrganizationSettings(page)
  await shot(page, '01-cashier-settings')
  if (await page.getByText('Separation of duties').isVisible().catch(() => false)) {
    note('authority', 'a cashier is shown the duty-separation card')
  }
  if (
    await page
      .getByText('This organization has too few people to separate duties')
      .isVisible()
      .catch(() => false)
  ) {
    note('authority', 'a cashier is shown the duty-separation checkbox')
  }

  step = '2 the API refuses the declaration to a cashier'
  const cashier = client(await token(CASHIER))
  const refusedDeclare = await cashier.call('PUT', '/organization/duty-separation', {
    duties_cannot_be_separated: true,
    reason: 'I would like this please',
  })
  if (refusedDeclare.status !== 403) {
    note('authority', `a cashier declaring duty separation got ${refusedDeclare.status}, not 403`)
  }

  step = '3 the owner is offered it'
  await signOut(page)
  await signIn(page, OWNER)
  await openOrganizationSettings(page)
  await assertNoOverflow(page, 'organization settings')
  await shot(page, '02-owner-settings')
  const card = page.getByText('Separation of duties').first()
  if (!(await card.isVisible().catch(() => false))) {
    note('declaration', 'the owner is not shown the duty-separation card')
  }

  step = '4 the reason is mandatory'
  const checkbox = page.getByRole('checkbox', {
    name: /too few people to separate duties/i,
  })
  await checkbox.check()
  await page.waitForTimeout(500)
  await assertNoOverflow(page, 'organization settings with the box ticked')
  await shot(page, '03-ticked-no-reason')
  const save = page.getByRole('button', { name: 'Save', exact: true }).last()
  if (await save.isEnabled().catch(() => true)) {
    note('declaration', 'Save is enabled with no reason given')
  }
  const reasonBox = page.locator('#duty-separation-reason')
  if (!(await reasonBox.isVisible().catch(() => false))) {
    note('declaration', 'no reason field appears when the setting is changed')
  }

  step = '5 the API refuses a declaration with no reason'
  const noReason = await owner.call('PUT', '/organization/duty-separation', {
    duties_cannot_be_separated: true,
    reason: '',
  })
  if (noReason.status !== 422) {
    note('declaration', `an empty reason got ${noReason.status}, not 422`)
  }

  step = '6 the owner declares it from the screen'
  await reasonBox.fill('Three of us work here: two sisters and their father')
  await page.waitForTimeout(300)
  if (!(await save.isEnabled().catch(() => false))) {
    note('declaration', 'Save is still disabled with a reason given')
  }
  await save.click()
  await page.waitForTimeout(1500)
  await shot(page, '04-declared')
  const afterDeclare = await owner.ok('GET', '/organization')
  if (afterDeclare.duties_cannot_be_separated !== true) {
    note('declaration', 'the screen saved but the organization does not report it')
  }
  if (!afterDeclare.duties_declared_at) {
    note('declaration', 'declared, but with no date recorded')
  }

  step = '7 the declaration is in the audit trail with its reason'
  const logs = await owner.ok('GET', '/audit/logs?action=organization.duties.declared&limit=5')
  const entry = (logs.items ?? logs)[0]
  if (!entry) {
    note('audit', 'declaring duty separation produced no audit record')
  } else {
    if (entry.new_values?.duties_cannot_be_separated !== true) {
      note('audit', `the audit record does not show the new value: ${JSON.stringify(entry.new_values)}`)
    }
    if (!String(entry.new_values?.reason || '').includes('Three of us')) {
      note('audit', 'the audit record does not carry the reason that was typed')
    }
    if (!entry.actor_label && !entry.actor_user_id) {
      note('audit', 'the audit record does not say who declared it')
    }
  }

  // ============================================ B. it grants nobody anything

  step = '8 the declaration grants the receptionist no new authority'
  const receptionist = client(await token(RECEPTIONIST))
  const forbidden = [
    ['GET', '/cash/registers', 'read cash registers'],
    ['GET', '/accounting/accounts', 'read the chart of accounts'],
    ['GET', '/audit/logs', 'read the audit trail'],
  ]
  for (const [method, pathname, what] of forbidden) {
    const attempt = await receptionist.call(method, pathname)
    if (attempt.status !== 403) {
      note(
        'permission leak',
        `a receptionist could ${what} (${attempt.status}) after the declaration`,
      )
    }
  }

  step = '9 the declaration does not let the cashier register a patient'
  const cashierRegister = await cashier.call('POST', '/clinic/patients', {
    family_name: `Leak${RUN}`,
    given_name: 'Should not exist',
  })
  if (cashierRegister.status !== 403) {
    note('permission leak', `a cashier registered a patient (${cashierRegister.status})`)
  }

  step = '10 an auditor is still read-only'
  const auditorRoles = await owner.ok('GET', '/roles')
  const auditor = auditorRoles.find((r) => r.code === 'auditor')
  const writes = (auditor?.permission_codes ?? []).filter(
    (c) => c.endsWith('.manage') || c.endsWith('.post') || c.endsWith('.approve'),
  )
  if (writes.length > 0) {
    note('permission leak', `the auditor role carries write permissions: ${writes.join(', ')}`)
  }

  // ============================================ C. combined responsibilities

  step = '11 director is no longer offered, and the rest still are'
  const roles = await owner.ok('GET', '/roles')
  const offered = Object.fromEntries(roles.map((r) => [r.code, r.offered_for_assignment]))
  if (offered.director !== false) {
    note('roles', 'director is still offered for assignment after the declaration')
  }
  for (const code of ['cashier', 'receptionist', 'dentist', 'accountant']) {
    if (offered[code] !== true) note('roles', `${code} is no longer offered for assignment`)
  }

  step = '12 the role picker does not show director'
  await page.goto(`${WEB}/settings/users`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(1200)
  await assertNoOverflow(page, 'users settings')
  await shot(page, '05-users')
  // The cashier's row, not the first one: the primary owner's roles cannot be
  // changed and the control on that row is correctly disabled.
  const cashierRow = page.locator('tbody tr', { hasText: CASHIER.email }).first()
  const rolesTrigger = cashierRow.getByRole('button', { name: /roles/i }).first()
  if (await rolesTrigger.isEnabled().catch(() => false)) {
    await rolesTrigger.click()
    await page.waitForTimeout(800)
    await shot(page, '06-role-picker')
    const dialog = page.locator('dialog[open]')
    const directorOffered = await dialog
      .getByText('Director', { exact: false })
      .first()
      .isVisible()
      .catch(() => false)
    if (directorOffered) {
      note('roles', 'the role picker still offers Director after the declaration')
    }
    const cashierOffered = await dialog
      .getByText('Cashier', { exact: false })
      .first()
      .isVisible()
      .catch(() => false)
    if (!cashierOffered) note('roles', 'the role picker no longer offers Cashier')
    await page.keyboard.press('Escape')
    await page.waitForTimeout(400)
  } else {
    note('roles', 'no control to edit a member’s roles was found on the users screen')
  }

  step = '13 one employee may hold combined responsibilities'
  const members = await owner.ok('GET', '/users')
  const cashierMember = members.find((m) => m.email === CASHIER.email)
  if (!cashierMember) {
    note('roles', 'the cashier account is not listed among the members')
  } else {
    const combined = await owner.call('PUT', `/users/${cashierMember.membership_id}/roles`, {
      role_codes: ['cashier', 'receptionist'],
    })
    if (combined.status >= 400) {
      note('roles', `reception + cashier on one person was refused: ${JSON.stringify(combined.body)}`)
    }
    const conflicting = await owner.call('PUT', `/users/${cashierMember.membership_id}/roles`, {
      role_codes: ['cashier', 'director'],
    })
    if (conflicting.status >= 400) {
      note(
        'roles',
        `cashier + director was refused with the declaration in force: ${JSON.stringify(conflicting.body)}`,
      )
    }
    // Back to just the cashier, so the pass leaves the tenant as it found it
    // and the self-approval step below tests the relaxation, not a grant.
    await owner.ok('PUT', `/users/${cashierMember.membership_id}/roles`, {
      role_codes: ['cashier'],
    })
  }

  // ============================================ D. closing a short till alone

  step = '14 a cashier closes their own short till'
  const registers = await owner.ok('GET', '/cash/registers')
  const register =
    registers.find((row) => row.code === 'TILL-E2E') ??
    (await owner.ok('POST', '/cash/registers', {
      code: 'TILL-E2E',
      name: 'Verification till',
      account_code: '5010',
    }))
  const openAlready = await owner.ok('GET', '/cash/sessions?session_status=OPEN')
  if (openAlready.length > 0) {
    console.log('a drawer is already open: the short-till steps are skipped')
  } else {
    const ledgerBefore = await ledger(owner)
    const session = await openSession(cashier, register)
    const customers = await owner.ok('GET', '/sales/customers')
    const customer = customers[0]
    await cashier.ok('POST', `/cash/sessions/${session.id}/payments`, {
      customer_id: customer.id,
      amount: '100000',
      received_on: TODAY,
    })
    // Bank most of it, then count 5 000 short of whatever is left: the
    // shortage is derived from what the drawer says it expects, not assumed,
    // because the float carried over from the previous session.
    await cashier.ok('POST', `/cash/sessions/${session.id}/deposits`, {
      amount: '90000',
      route: 'STAFF',
      description: 'e2e banking',
    })
    const expectedNow = Number(
      (await cashier.ok('GET', `/cash/sessions/${session.id}`)).totals.expected,
    )
    const counted = expectedNow - 5000
    await cashier.ok('POST', `/cash/sessions/${session.id}/count`, {
      counted_amount: String(counted),
      reason: 'e2e: 5 000 short, no explanation found',
    })
    const closedByCashier = await cashier.call('POST', `/cash/sessions/${session.id}/close`)
    if (closedByCashier.status >= 400) {
      note(
        'self-approval',
        `the cashier could not close their own short till with the declaration in force: ${closedByCashier.status} ${JSON.stringify(closedByCashier.body)}`,
      )
    }

    step = '15 the close is recorded as self-approved'
    const closeLogs = await owner.ok('GET', '/audit/logs?action=cash.session.closed&limit=5')
    const closeEntry = (closeLogs.items ?? closeLogs)[0]
    if (!closeEntry) {
      note('audit', 'closing the session produced no audit record')
    } else if (closeEntry.new_values?.self_approved !== true) {
      note(
        'audit',
        `the close is not marked self_approved: ${JSON.stringify(closeEntry.new_values)}`,
      )
    }

    step = '16 the shortage still reaches the ledger'
    const ledgerAfter = await ledger(owner)
    const shortage = (ledgerAfter['9430'] ?? 0) - (ledgerBefore['9430'] ?? 0)
    if (shortage !== 5000) {
      note('ledger', `the 5 000 shortage posted ${shortage} to 9430, not 5000`)
    }
    // The invariant that matters: relaxing *who approves* must not change
    // what the books say. A cash account may hold only what was counted.
    const cashHeld = ledgerAfter['5010'] ?? 0
    if (cashHeld !== counted) {
      note('ledger', `the cash account holds ${cashHeld}, but ${counted} was counted`)
    }
  }

  step = '17 without the declaration the same close is refused'
  await declare(owner, false, 'e2e: back to strict for the refusal check')
  const stillOpen = await owner.ok('GET', '/cash/sessions?session_status=OPEN')
  if (stillOpen.length === 0) {
    const session = await openSession(cashier, register)
    const expected = Number(
      (await cashier.ok('GET', `/cash/sessions/${session.id}`)).totals.expected,
    )
    const shortCount = expected - 1000
    await cashier.ok('POST', `/cash/sessions/${session.id}/count`, {
      counted_amount: String(shortCount),
      reason: 'e2e: an unexplained 1 000 that should not be closable alone',
    })
    const refusedClose = await cashier.call('POST', `/cash/sessions/${session.id}/close`)
    if (refusedClose.status !== 403) {
      note(
        'control',
        `without the declaration a cashier closed a short till: ${refusedClose.status}`,
      )
    }
    // The owner clears it, which is what a clinic would do, and leaves the
    // drawer closed for the next run.
    await owner.ok('POST', `/cash/sessions/${session.id}/close`)
    const held = (await ledger(owner))['5010'] ?? 0
    if (held !== shortCount) {
      note('ledger', `the cash account holds ${held}, but the owner closed on ${shortCount}`)
    }
  }
  await declare(owner, true, 'e2e: the duplicate steps run as the clinic really is')

  // ============================================ E. the duplicate queue

  step = '18 reception registers a patient'
  await signOut(page)
  await signIn(page, RECEPTIONIST)
  const firstDuplicates = await registerPatient(page, {
    family: FAMILY,
    given: 'Jahongir',
    birthYear: BIRTH_YEAR,
    phone: `+99890${RUN}1`,
  })
  await shot(page, '07-first-patient')
  if (firstDuplicates) {
    note('duplicates', 'a patient nobody resembles was reported as a possible duplicate')
  }

  step = '19 a second of the same name and birth year is reported, not refused'
  // The same *full* name: the detector requires the given name to match too,
  // which is what keeps two brothers apart. Step 19b checks that rule holds.
  const secondDuplicates = await registerPatient(page, {
    family: FAMILY,
    given: 'Jahongir',
    birthYear: BIRTH_YEAR,
    phone: `+99890${RUN}2`,
  })
  await shot(page, '08-duplicate-reported')
  if (!secondDuplicates) {
    note('duplicates', 'a second patient of the same name and birth year raised no question')
  }
  const registered = await receptionist.ok(`GET`, `/clinic/patients?query=${FAMILY}`)
  if (registered.length !== 2) {
    note('duplicates', `expected both records to exist, found ${registered.length}`)
  }
  if (registered.some((p) => p.merged_into_patient_id)) {
    note('duplicates', 'a record was merged by matching alone — nothing should have merged')
  }
  if (!registered.every((p) => p.is_active)) {
    note('duplicates', 'a record was deactivated by matching alone')
  }

  step = '19b two brothers are not reported as duplicates'
  const brotherDuplicates = await registerPatient(page, {
    family: FAMILY,
    given: 'Bahodir',
    birthYear: BIRTH_YEAR,
    phone: `+99890${RUN}3`,
  })
  if (brotherDuplicates) {
    note(
      'duplicates',
      'a brother — same surname and birth year, different given name — was reported as a possible duplicate',
    )
  }

  step = '19c a name containing digits is not matched against telephone numbers'
  const byName = await receptionist.ok(
    'GET',
    `/clinic/patients?query=${encodeURIComponent(FAMILY)}`,
  )
  const strangers = byName.filter((p) => !p.family_name.startsWith('Oripov'))
  if (strangers.length > 0) {
    note(
      'search',
      `searching a surname returned ${strangers.length} unrelated patient(s) — matched on telephone digits`,
    )
  }

  step = '20 the queue shows both records side by side'
  await openDuplicates(page)
  await assertNoOverflow(page, 'duplicate queue')
  await shot(page, '09-queue')
  const queueRow = page.locator('li', { hasText: FAMILY }).first()
  if (!(await queueRow.isVisible().catch(() => false))) {
    note('duplicates', 'the pair just registered is not in the queue')
  } else {
    await queueRow.getByRole('button', { name: 'Compare' }).click()
    await page.waitForTimeout(900)
    await assertNoOverflow(page, 'comparison dialog')
    await shot(page, '10-comparison')
    const dialog = page.locator('dialog[open]')
    for (const label of ['Surname', 'Given name', 'Year of birth', 'Telephone']) {
      if (!(await dialog.getByText(label, { exact: false }).first().isVisible().catch(() => false))) {
        note('comparison', `the comparison does not show the ${label} row`)
      }
    }
    // Both telephone numbers must be on screen: with the names identical,
    // the number is the only thing distinguishing the two records, and it is
    // what a person is being asked to judge.
    for (const tail of [`${RUN}1`, `${RUN}2`]) {
      if (!(await dialog.getByText(tail, { exact: false }).first().isVisible().catch(() => false))) {
        note('comparison', `the number ending ${tail} does not appear in the comparison`)
      }
    }
    if (!(await dialog.getByText('would be kept').first().isVisible().catch(() => false))) {
      note('comparison', 'the comparison does not say which record a merge would keep')
    }
    if (!(await dialog.getByText('Both records are untouched').first().isVisible().catch(() => false))) {
      note('comparison', 'the comparison does not say that nothing has been merged yet')
    }

    step = '21 a decision needs a note'
    const distinct = dialog.getByRole('button', { name: 'Different people' })
    if (await distinct.isEnabled().catch(() => true)) {
      note('duplicates', '“Different people” is enabled with no note given')
    }
    const mergeButton = dialog.getByRole('button', { name: /Same person/ })
    if (await mergeButton.isEnabled().catch(() => true)) {
      note('duplicates', '“Same person” is enabled with no note given')
    }

    step = '22 two people of the same name are not merged by one click'
    await dialog.locator('#duplicate-review-note').fill('Two brothers; their mother confirmed')
    await page.waitForTimeout(300)
    await mergeButton.click()
    await page.waitForTimeout(800)
    await shot(page, '11-merge-confirmation')
    const confirm = page.locator('dialog[open]', { hasText: 'Confirm the merge?' })
    if (!(await confirm.isVisible().catch(() => false))) {
      note(
        'duplicates',
        'pressing “Same person” did not ask for confirmation — one click away from merging two people',
      )
    } else {
      if (!(await confirm.getByText('Record kept').first().isVisible().catch(() => false))) {
        note('confirmation', 'the confirmation does not name the record being kept')
      }
      if (!(await confirm.getByText('Record filed away').first().isVisible().catch(() => false))) {
        note('confirmation', 'the confirmation does not name the record being filed away')
      }
      // Back out. These two really are different people.
      await confirm.getByRole('button', { name: 'Cancel' }).click()
      await page.waitForTimeout(600)
    }
    const afterCancel = await receptionist.ok(`GET`, `/clinic/patients?query=${FAMILY}`)
    if (afterCancel.some((p) => p.merged_into_patient_id)) {
      note('duplicates', 'cancelling the confirmation merged the records anyway')
    }

    step = '23 marking them distinct keeps both records'
    const reopened = page.locator('dialog[open]')
    await reopened
      .locator('#duplicate-review-note')
      .fill('Two brothers; their mother confirmed')
    await page.waitForTimeout(300)
    await reopened.getByRole('button', { name: 'Different people' }).click()
    await page.waitForTimeout(1500)
    await shot(page, '12-after-distinct')
    // Three records share this surname by now: the two Jahongirs under review
    // and the brother registered in 19b. All three must still be live.
    const afterDistinct = await receptionist.ok(
      'GET',
      `/clinic/patients?query=${encodeURIComponent(FAMILY)}`,
    )
    if (afterDistinct.length !== 3 || !afterDistinct.every((p) => p.is_active)) {
      note(
        'duplicates',
        `marking two patients distinct did not leave every record live: ${afterDistinct.length} live of 3`,
      )
    }
    if (afterDistinct.some((p) => p.merged_into_patient_id)) {
      note('duplicates', 'marking two patients distinct merged them')
    }
    const queueNow = await receptionist.ok('GET', '/clinic/duplicate-reviews')
    if (queueNow.some((r) => r.patient.family_name === FAMILY)) {
      note('duplicates', 'a pair settled as distinct is still in the pending queue')
    }
  }

  step = '24 a real duplicate is merged, and the original is preserved'
  // The same person typed twice: same name, same year, same number.
  const twiceFamily = `Karimova${RUN}`
  await registerPatient(page, {
    family: twiceFamily,
    given: 'Nilufar',
    birthYear: 1985,
    phone: `+99891${RUN}7`,
  })
  await registerPatient(page, {
    family: twiceFamily,
    given: 'Nilufar',
    birthYear: 1985,
    phone: `+99891${RUN}7`,
  })
  const pair = await receptionist.ok(
    'GET',
    `/clinic/patients?query=${encodeURIComponent(twiceFamily)}`,
  )
  if (pair.length !== 2) {
    note('duplicates', `expected two records for the same person, found ${pair.length}`)
  }
  // Captured before the merge: afterwards the superseded record is correctly
  // excluded from the search, so there would be nothing left to look it up by.
  const queueBeforeMerge = await receptionist.ok('GET', '/clinic/duplicate-reviews')
  const realReview = queueBeforeMerge.find((r) => r.patient.family_name === twiceFamily)
  if (!realReview) note('duplicates', 'the real duplicate raised no review')
  await openDuplicates(page)
  const realRow = page.locator('li', { hasText: twiceFamily }).first()
  if (!(await realRow.isVisible().catch(() => false))) {
    note('duplicates', 'the duplicate just created is not in the queue')
  } else {
    await realRow.getByRole('button', { name: 'Compare' }).click()
    await page.waitForTimeout(900)
    await shot(page, '13-real-duplicate')
    const dialog = page.locator('dialog[open]')
    if (
      !(await dialog
        .getByText('Nothing in the two records contradicts')
        .first()
        .isVisible()
        .catch(() => false))
    ) {
      note('comparison', 'two identical records are not reported as uncontradicted')
    }
    await dialog
      .locator('#duplicate-review-note')
      .fill('Same person; she was registered twice on the same day')
    await page.waitForTimeout(300)
    await dialog.getByRole('button', { name: /Same person/ }).click()
    await page.waitForTimeout(800)
    const confirm = page.locator('dialog[open]', { hasText: 'Confirm the merge?' })
    await confirm.getByRole('button', { name: 'Yes, merge' }).click()
    await page.waitForTimeout(1800)
    await shot(page, '14-after-merge')

    const listed = await receptionist.ok(
      'GET',
      `/clinic/patients?query=${encodeURIComponent(twiceFamily)}`,
    )
    if (listed.length !== 1) {
      note('merge', `after the merge the search offers ${listed.length} records, not 1`)
    }
    if (listed[0] && listed[0].id !== realReview?.survivor_id) {
      note('merge', 'the record left in the search is not the one the queue said would be kept')
    }

    // The superseded record: kept, marked, and still readable by id, because
    // treatments were recorded against it.
    const supersededId =
      realReview?.patient.id === realReview?.survivor_id
        ? realReview?.candidate.id
        : realReview?.patient.id
    const fetched = await receptionist.call('GET', `/clinic/patients/${supersededId}`)
    if (fetched.status >= 400) {
      note('merge', `the superseded record can no longer be read: ${fetched.status}`)
    } else {
      const kept = fetched.body.patient ?? fetched.body
      if (!kept.merged_into_patient_id) {
        note('merge', 'the superseded record is not marked as merged')
      }
      if (kept.merged_into_patient_id !== realReview?.survivor_id) {
        note('merge', 'the superseded record does not point at the survivor')
      }
      if (kept.is_active) note('merge', 'the superseded record is still active')
    }

    step = '25 the merge is in the audit trail with its reason'
    const mergeLogs = await owner.ok('GET', '/audit/logs?action=clinic.patient.merged&limit=5')
    const mergeEntry = (mergeLogs.items ?? mergeLogs)[0]
    if (!mergeEntry) {
      note('audit', 'the merge produced no audit record')
    } else {
      if (!String(mergeEntry.reason || '').includes('registered twice')) {
        note('audit', `the merge audit record does not carry the reason: ${mergeEntry.reason}`)
      }
      if (!mergeEntry.actor_label && !mergeEntry.actor_user_id) {
        note('audit', 'the merge audit record does not say who did it')
      }
    }
  }

  step = '26 the queue is not offered to somebody who may not settle one'
  await signOut(page)
  await signIn(page, CASHIER)
  await page.goto(`${WEB}/clinic`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(1200)
  await shot(page, '15-cashier-clinic')
  const cashierTab = page.locator('nav a[href="/clinic/duplicates"]')
  if (await cashierTab.isVisible().catch(() => false)) {
    note('authority', 'the cashier, who may not settle a duplicate, is offered the queue tab')
  }
  await openDuplicates(page)
  await shot(page, '16-cashier-queue')
  if (!(await page.getByText('do not have permission').first().isVisible().catch(() => false))) {
    note('authority', 'the cashier reaching the queue by URL is not told they may not settle one')
  }
  const cashierQueue = await cashier.call('GET', '/clinic/duplicate-reviews')
  if (cashierQueue.status !== 403) {
    note('authority', `a cashier read the duplicate queue over the API (${cashierQueue.status})`)
  }

  step = '27 all three languages, and phone width'
  await signOut(page)
  await signIn(page, RECEPTIONIST)
  for (const locale of ['uz', 'ru', 'en']) {
    await page.evaluate((code) => {
      try {
        window.localStorage.setItem('erp.locale', code)
      } catch {}
    }, locale)
    await openDuplicates(page)
    await assertNoOverflow(page, `duplicate queue in ${locale}`)
    await shot(page, `17-queue-${locale}`)
  }
  await page.setViewportSize({ width: 390, height: 844 })
  await openDuplicates(page)
  await assertNoOverflow(page, 'duplicate queue at phone width')
  await shot(page, '18-queue-phone')
  const phoneRow = page.locator('li', { hasText: 'Karimova' }).first()
  if (await phoneRow.isVisible().catch(() => false)) {
    await phoneRow.getByRole('button').first().click()
    await page.waitForTimeout(900)
    await assertNoOverflow(page, 'comparison dialog at phone width')
    await shot(page, '19-comparison-phone')
  }
  await page.setViewportSize({ width: 1440, height: 900 })

  // ============================================ leave the tenant as found

  step = '28 tidy up'
  await browser.close()
  await declare(
    owner,
    wasDeclared,
    'e2e: restoring the declaration to what this run found',
  )
  const finalState = await owner.ok('GET', '/organization')
  if (finalState.duties_cannot_be_separated !== wasDeclared) {
    note('tidy', 'the declaration was not restored to the state this run found')
  }
  const leftOpen = await owner.ok('GET', '/cash/sessions?session_status=OPEN')
  if (leftOpen.length > 0) note('tidy', 'this run left a cash session open')

  console.log('\n=== problems ===')
  if (problems.length === 0) console.log('none')
  else problems.forEach((p) => console.log(' -', p))
  console.log(`\n${problems.length} problem(s)`)
  if (problems.length > 0) process.exitCode = 1
}

run().catch((error) => {
  console.log(`\nFAILED at step: ${step}`)
  console.log(error.message)
  console.log('\n=== problems so far ===')
  problems.forEach((p) => console.log(' -', p))
  process.exit(1)
})
