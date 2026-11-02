/**
 * Giving money back, in a real Chrome.
 *
 * The clinic's commonest refund is not a reversal: a patient hands over more
 * cash than the bill and wants the difference. This pass drives that from the
 * cash screen as the cashier — reads what the dialog says is refundable,
 * tries to take more than there is, hands back part of it twice, and then
 * checks three things no page can tell you on its own: that the till's
 * expected cash fell by exactly what went out, that the ledger agrees, and
 * that the bill the patient really did pay is still settled.
 *
 * It never destroys data: it creates its own patient and service, and counts
 * and closes the drawer it opened.
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
const CASHIER = { email: `cashier@${TENANT}.example.com`, password: STAFF_PASSWORD }
const RECEPTIONIST = { email: `receptionist@${TENANT}.example.com`, password: STAFF_PASSWORD }

const RUN = Date.now().toString().slice(-6)
const TODAY = new Date().toISOString().slice(0, 10)
const BILL = '600000'
const HANDED_OVER = '800000'

const problems = []
let step = 'startup'
const note = (kind, detail) => problems.push(`[${step}] ${kind}: ${detail}`)
const num = (text) => Number((text || '0').replace(/[^\d.]/g, '')) || 0

async function token(who) {
  const r = await fetch(`${API}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(who),
  })
  if (!r.ok) throw new Error(`login failed for ${who.email}: ${r.status}`)
  return (await r.json()).access_token
}

function client(accessToken) {
  const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${accessToken}` }
  const call = async (method, pathname, body) => {
    const r = await fetch(`${API}${pathname}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    const text = await r.text()
    let parsed = null
    try {
      parsed = text ? JSON.parse(text) : null
    } catch {
      parsed = text
    }
    return { status: r.status, body: parsed }
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
  }
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

const moved = (before, after, code) => (after[code] ?? 0) - (before[code] ?? 0)

async function openSession(actor, register) {
  // Reuse the till if it is already open: this pass is about refunds, and a
  // drawer somebody left open is the ordinary state of a clinic.
  const existing = await actor.ok('GET', '/cash/sessions?session_status=OPEN')
  const mine = existing.find((s) => s.register_id === register.id)
  if (mine) return mine
  const first = await actor.call('POST', '/cash/sessions', {
    register_id: register.id,
    business_date: TODAY,
    opening_float: '0',
  })
  if (first.status < 400) return first.body
  const expected = first.body?.details?.expected_float
  if (!expected) throw new Error(`opening a session failed: ${JSON.stringify(first.body)}`)
  return actor.ok('POST', '/cash/sessions', {
    register_id: register.id,
    business_date: TODAY,
    opening_float: expected,
  })
}

const shot = (page, name) =>
  page.screenshot({ path: path.join(SHOTS, `refund-${name}.png`), fullPage: true })

function watch(page) {
  page.on('console', (m) => {
    if (m.type() === 'error') note('console error', m.text())
  })
  page.on('pageerror', (e) => note('page error', e.message))
  page.on('response', (r) => {
    if (r.status() >= 500) note('server error', `${r.status()} ${r.url()}`)
  })
}

async function signIn(page, who) {
  await page.goto(WEB, { waitUntil: 'networkidle' })
  await page.evaluate(() => {
    try {
      localStorage.clear()
      sessionStorage.clear()
    } catch {}
  })
  await page.goto(WEB, { waitUntil: 'networkidle' })
  await page.evaluate(() => {
    try {
      localStorage.setItem('erp.locale', 'en')
    } catch {}
  })
  await page.reload({ waitUntil: 'networkidle' })
  await page.getByLabel(/e-?mail/i).fill(who.email)
  await page.locator('input[type="password"]').fill(who.password)
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
  await page.waitForSelector('nav', { timeout: 15000 })
  await page.waitForTimeout(800)
}

async function expected(actor, sessionId) {
  const detail = await actor.ok('GET', `/cash/sessions/${sessionId}`)
  return Number(detail.totals.expected)
}

async function run() {
  step = '0 setup'
  const owner = client(await token(OWNER))
  await ensureStaff(owner)
  const service = await owner.ok('POST', '/sales/services', {
    code: `REF-${RUN}`,
    name: `Crown ${RUN}`,
    price: BILL,
    effective_from: '2026-01-01',
  })
  // A clinic of three: the cashier holds `sales.refund.request` and not
  // `.approve`, so without this declaration nobody present could hand back
  // money they had just taken in error (ADR-041).
  const org = await owner.ok('GET', '/organization')
  if (!org.duties_cannot_be_separated) {
    await owner.ok('PUT', '/organization/duty-separation', {
      duties_cannot_be_separated: true,
      reason: 'Three people work here and all of them are family',
    })
  }
  const registers = await owner.ok('GET', '/cash/registers')
  const register =
    registers.find((r) => r.code === 'TILL-E2E') ??
    (await owner.ok('POST', '/cash/registers', {
      code: 'TILL-E2E',
      name: 'Verification till',
      account_code: '5010',
    }))

  // A patient with a bill, built through the API: the journey from
  // registration to an issued invoice is what patient-to-payment.cjs drives,
  // and repeating it here would only make this pass slower.
  const family = `Qaytarov${RUN}`
  const registered = await owner.ok('POST', '/clinic/patients', {
    family_name: family,
    given_name: 'Aziz',
    birth_year: 1990,
  })
  const patient = registered.patient
  const encounter = await owner.ok('POST', '/clinic/encounters', {
    patient_id: patient.id,
    occurred_on: TODAY,
  })
  const items = await owner.ok('POST', `/clinic/encounters/${encounter.id}/items`, {
    items: [{ service_id: service.id, tooth_number: 16 }],
  })
  await owner.ok('POST', `/clinic/treatments/${items[0].id}/complete`, {
    completed_on: TODAY,
  })
  const invoice = await owner.ok('POST', `/clinic/patients/${patient.id}/invoices`, {
    issue_date: TODAY,
  })
  await owner.ok('POST', `/sales/invoices/${invoice.id}/issue`, { tax_treatment: 'NONE' })

  const session = await openSession(owner, register)
  const ledgerAtStart = await ledger(owner)
  const expectedAtStart = await expected(owner, session.id)
  console.log(`patient ${patient.code}, bill ${BILL}, till open`)

  const browser = await chromium.launch({ channel: 'chrome', headless: true })
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  const page = await context.newPage()
  watch(page)

  // ------------------------------------------------- 1-2. take the overpayment
  step = '1 the cashier signs in and opens the till'
  await signIn(page, CASHIER)
  await page.goto(`${WEB}/cash`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(1200)
  await shot(page, '01-cash')

  step = '2 take 800 000 for a 600 000 bill'
  await page.getByRole('button', { name: 'Take payment' }).first().click()
  await page.waitForTimeout(500)
  const option = page.locator('#cash-pay-customer option', { hasText: family }).first()
  await page.locator('#cash-pay-customer').selectOption(await option.getAttribute('value'))
  await page.locator('#cash-pay-amount').fill(HANDED_OVER)
  await page.getByRole('button', { name: /^Take payment$|^Record$|^Save$/ }).last().click()
  await page.waitForTimeout(1800)
  await shot(page, '02-taken')
  if ((await expected(owner, session.id)) !== expectedAtStart + Number(HANDED_OVER)) {
    note('till', 'expected cash did not rise by what was handed over')
  }

  // ------------------------------------------------- 3-5. what may go back
  step = '3 open the refund dialog from the receipt'
  const receiptRow = page.locator('tbody tr', { hasText: 'Payment' }).first()
  if (!(await receiptRow.isVisible().catch(() => false))) {
    note('cash page', 'no receipt row to refund from')
  }
  await receiptRow.getByRole('button', { name: 'Refund' }).click()
  await page.waitForTimeout(1200)
  await shot(page, '03-dialog')

  step = '4 the dialog says what is refundable, and it is not the whole payment'
  const refundable = num(await page.locator('#refund-refundable').textContent())
  if (refundable !== 200000) {
    note('refundable', `expected 200000, the dialog says ${refundable}`)
  }
  const dialogText = await page.locator('dialog[open]').textContent()
  if (!dialogText.includes('600000')) {
    note('refund dialog', 'it does not show how much of the payment is on bills')
  }

  step = '5 more than is refundable is refused before it is sent'
  await page.locator('#refund-amount').fill('250000')
  await page.locator('#refund-reason').fill('More than the patient left')
  await page.waitForTimeout(400)
  if (await page.locator('#refund-submit').isEnabled()) {
    note('over-refund', 'the dialog would submit a refund larger than the refundable amount')
  }
  if (!(await page.getByText(/At most 200000/i).first().isVisible().catch(() => false))) {
    note('over-refund', 'no message saying how much can be given back')
  }
  await shot(page, '04-too-much')

  // ------------------------------------------------- 6-7. part of it back
  step = '6 hand back 120 000'
  await page.locator('#refund-amount').fill('120000')
  await page.locator('#refund-reason').fill('Patient handed over too much cash')
  await page.waitForTimeout(400)
  if (!(await page.locator('#refund-submit').isEnabled())) {
    note('partial refund', 'the dialog refuses a refund within the refundable amount')
  }
  await page.locator('#refund-submit').click()
  await page.waitForTimeout(2200)
  await shot(page, '05-refunded')

  step = '7 the till fell by exactly what went out'
  const afterFirst = await expected(owner, session.id)
  if (afterFirst !== expectedAtStart + Number(HANDED_OVER) - 120000) {
    note('till', `expected cash is ${afterFirst}, not what it should be after 120 000 out`)
  }

  // ------------------------------------------------- 8-10. the rest, and then none
  step = '8 the dialog now shows the refund already given'
  await page.reload({ waitUntil: 'networkidle' })
  await page.waitForTimeout(1500)
  await page.locator('tbody tr', { hasText: 'Payment' }).first()
    .getByRole('button', { name: 'Refund' }).click()
  await page.waitForTimeout(1200)
  const second = num(await page.locator('#refund-refundable').textContent())
  if (second !== 80000) note('refundable', `expected 80000 left, the dialog says ${second}`)
  const history = await page.locator('dialog[open] tbody tr').count()
  if (history < 1) note('refund history', 'the refund already given is not listed')
  await shot(page, '06-history')

  step = '9 an empty box gives back all that is left'
  await page.locator('#refund-reason').fill('The rest, returned')
  await page.waitForTimeout(300)
  await page.locator('#refund-submit').click()
  await page.waitForTimeout(2200)
  const afterSecond = await expected(owner, session.id)
  if (afterSecond !== expectedAtStart + Number(BILL)) {
    note('till', `expected cash is ${afterSecond}; only the bill's money should be left`)
  }
  await shot(page, '07-all-back')

  step = '10 nothing is left to refund, and the dialog says why'
  await page.reload({ waitUntil: 'networkidle' })
  await page.waitForTimeout(1500)
  await page.locator('tbody tr', { hasText: 'Payment' }).first()
    .getByRole('button', { name: 'Refund' }).click()
  await page.waitForTimeout(1200)
  const blocked = await page.locator('dialog[open]').textContent()
  if (!/paying bills|already been given back/i.test(blocked)) {
    note('exhausted', 'the dialog does not say why nothing more can be refunded')
  }
  if (await page.locator('#refund-submit').isEnabled()) {
    note('exhausted', 'the dialog would still submit a refund')
  }
  await shot(page, '08-blocked')
  await page.keyboard.press('Escape')
  await page.waitForTimeout(500)

  // ------------------------------------------------- 11. the bill is still paid
  step = '11 the bill the patient really did pay is still settled'
  const settlement = await owner.ok('GET', `/clinic/invoices/${invoice.id}/settlement`)
  if (Number(settlement.remaining) !== 0) {
    note('invoice', `the bill reopened: ${settlement.remaining} outstanding after a refund`)
  }

  // ------------------------------------------------- 12. the ledger
  step = '12 the ledger agrees with the drawer'
  const ledgerNow = await ledger(owner)
  const cash = moved(ledgerAtStart, ledgerNow, '5010')
  const advances = moved(ledgerAtStart, ledgerNow, '6310')
  const revenue = moved(ledgerAtStart, ledgerNow, '9030')
  // The bill was issued before this baseline, so revenue is already
  // recognised: the point is that a refund does not touch it.
  if (cash !== Number(BILL)) {
    note('ledger', `5010 moved ${cash}, expected ${BILL} — what the bill was paid with`)
  }
  if (advances !== 0) note('ledger', `6310 moved ${advances}, expected 0 — the advance is gone`)
  if (revenue !== 0) {
    note('ledger', `9030 moved ${revenue}; a refund of spare money is not a revenue event`)
  }

  // ------------------------------------------------- 13. no drawer, no cash refund
  step = '13 with the till closed, a cash refund says where to go'
  // A fresh advance with money still in it, so the dialog's reason for
  // refusing is the closed drawer rather than an exhausted payment.
  const spare = await owner.ok('POST', `/cash/sessions/${session.id}/payments`, {
    customer_id: patient.customer_id,
    amount: '100000',
    received_on: TODAY,
    auto_allocate: false,
  })
  await owner.ok('POST', `/cash/sessions/${session.id}/count`, {
    counted_amount: String(await expected(owner, session.id)),
  })
  await owner.ok('POST', `/cash/sessions/${session.id}/close`, {
    duties_cannot_be_separated: true,
  })
  const cardPayment = spare
  if (cardPayment) {
    await page.goto(`${WEB}/sales/payments`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(1500)
    const row = page.locator('tbody tr', { hasText: cardPayment.number }).first()
    if (await row.isVisible().catch(() => false)) {
      await row.getByRole('button', { name: 'Refund' }).click()
      await page.waitForTimeout(1200)
      const message = await page.locator('dialog[open]').textContent()
      const enabled = await page.locator('#refund-submit').isEnabled()
      if (enabled) {
        note('closed till', 'the dialog would submit a cash refund with no open session')
      }
      if (!/open till/i.test(message)) {
        note('closed till', `it does not say to open the till: ${message.slice(0, 200)}`)
      }
      await shot(page, '09-no-drawer')
      await page.keyboard.press('Escape')
    }
  }

  step = '14 phone width'
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto(`${WEB}/cash`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(1200)
  const over = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  )
  if (over > 1) note('horizontal overflow', `cash page at phone width: ${over}px`)
  await shot(page, '10-phone')

  await browser.close()
}

run()
  .then(() => {
    console.log(`\n${problems.length} problem(s)`)
    for (const line of problems) console.log(` - ${line}`)
    process.exit(problems.length === 0 ? 0 : 1)
  })
  .catch((error) => {
    console.error(`\nfailed at step: ${step}`)
    console.error(error)
    process.exit(2)
  })
