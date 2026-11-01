/**
 * The whole journey, in a real Chrome: a patient walks in, is registered,
 * examined, treated, billed and pays.
 *
 * This is the pass that says whether the clinic can actually be run from the
 * screens, rather than whether the pages render. Every step is driven through
 * the interface as the person who would really do it — reception registers,
 * the dentist records teeth, the cashier takes the money — and the ledger is
 * read at the end to check the books agree with what the screens said.
 *
 * It never destroys data: each run creates its own patient and service, banks
 * what it takes and closes the drawer it opened.
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
const DENTIST = { email: `dentist@${TENANT}.example.com`, password: STAFF_PASSWORD }

const RUN = Date.now().toString().slice(-6)
const TODAY = new Date().toISOString().slice(0, 10)
const PRICE = '600000'

const problems = []
let step = 'startup'
const note = (kind, detail) => problems.push(`[${step}] ${kind}: ${detail}`)
const digits = (text) => (text || '').replace(/\D/g, '')

// --------------------------------------------------------------- the API side

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
    [DENTIST, ['dentist'], 'Verify Dentist'],
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
    console.log(`created ${who.email}`)
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

// ----------------------------------------------------------- the browser side

const shot = (page, name) =>
  page.screenshot({ path: path.join(SHOTS, `p2p-${name}.png`), fullPage: true })

async function assertNoOverflow(page, where) {
  const over = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  )
  if (over > 1) note('horizontal overflow', `${where}: ${over}px wider than the viewport`)
}

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

async function openPatientByCode(page, code) {
  await page.goto(`${WEB}/clinic/patients`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(800)
  await page.locator('main input').first().fill(code)
  await page.waitForTimeout(1200)
  const row = page.locator('tbody tr', { hasText: code }).first()
  if (!(await row.isVisible().catch(() => false))) return false
  await row.getByRole('button').first().click()
  await page.waitForTimeout(1400)
  return true
}

// ------------------------------------------------------------------- the pass

async function run() {
  step = '0 setup'
  const owner = client(await token(OWNER))
  await ensureStaff(owner)
  const service = await owner.ok('POST', '/sales/services', {
    code: `P2P-${RUN}`,
    name: `Canal treatment ${RUN}`,
    price: PRICE,
    effective_from: '2026-01-01',
  })
  const registers = await owner.ok('GET', '/cash/registers')
  const register =
    registers.find((r) => r.code === 'TILL-E2E') ??
    (await owner.ok('POST', '/cash/registers', {
      code: 'TILL-E2E',
      name: 'Verification till',
      account_code: '5010',
    }))
  const ledgerAtStart = await ledger(owner)
  console.log(`service ${service.code} at ${PRICE}`)

  const browser = await chromium.launch({ channel: 'chrome', headless: true })
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  const page = await context.newPage()
  watch(page)

  // ---------------------------------------------- 1-3. sign in and navigate
  step = '1 reception signs in'
  await signIn(page, RECEPTIONIST)
  await shot(page, '01-dashboard')
  await assertNoOverflow(page, 'dashboard')

  step = '2 the dashboard is the clinic, not the module list'
  if (await page.getByText('Organization details').first().isVisible().catch(() => false)) {
    note('dashboard', 'the technical organization card is still on the home screen')
  }

  step = '3 navigate to the clinic'
  await page.goto(`${WEB}/clinic/patients`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(900)
  await assertNoOverflow(page, 'patient list')

  // -------------------------------------------------------- 4-5. the patient
  step = '4 register a patient'
  const family = `Saidov${RUN}`
  await page.getByRole('button', { name: 'Register patient' }).first().click()
  await page.waitForTimeout(400)
  await page.locator('#patient-family').fill(family)
  await page.locator('#patient-given').fill('Akmal')
  await page.locator('#patient-birth-year').fill('1988')
  await page.locator('#patient-phone').fill(`+99893${RUN}`)
  await page.getByRole('button', { name: 'Create', exact: true }).click()
  await page.waitForTimeout(1500)
  await shot(page, '02-registered')

  const reception = client(await token(RECEPTIONIST))
  const found = await reception.ok('GET', `/clinic/patients?query=${encodeURIComponent(family)}`)
  if (found.length !== 1) note('registration', `expected one patient, found ${found.length}`)
  const patient = found[0]
  if (!patient) throw new Error('the patient was not registered')
  console.log(`patient ${patient.code}`)

  step = '5 find the patient again by code'
  if (!(await openPatientByCode(page, patient.code))) {
    note('search', 'the patient just registered could not be found by code')
  }
  await shot(page, '03-patient-account')
  await assertNoOverflow(page, 'patient account')

  step = '6 reception sees no clinical record'
  // The provisional C1 policy: clinical content is behind
  // `clinic.clinical.read`, which reception does not hold.
  if (await page.getByText('Treatment record').first().isVisible().catch(() => false)) {
    note('clinical access', 'reception can see the clinical record panel')
  }
  const receptionClinical = await reception.call(
    'GET',
    `/clinic/patients/${patient.id}/encounters`,
  )
  if (receptionClinical.status !== 403) {
    note('clinical access', `reception read the clinical record (${receptionClinical.status})`)
  }

  // ------------------------------------------------- 7-11. the dentist works
  step = '7 the dentist opens a visit'
  await signIn(page, DENTIST)
  if (!(await openPatientByCode(page, patient.code))) {
    note('navigation', 'the dentist cannot open the patient')
  }
  await shot(page, '04-dentist-patient')
  const panel = page.getByText('Treatment record').first()
  if (!(await panel.isVisible().catch(() => false))) {
    note('clinical access', 'the dentist cannot see the clinical record panel')
  }
  await page.getByRole('button', { name: 'Start a visit' }).first().click()
  await page.waitForTimeout(500)
  await page.locator('#encounter-complaint').fill('Pain in the lower left')
  await page.getByRole('dialog').getByRole('button', { name: 'Start a visit' }).click()
  await page.waitForTimeout(1600)
  await shot(page, '05-visit-open')

  step = '8 record a tooth, in FDI notation'
  // The record dialog opens straight after the visit does.
  const dialog = page.locator('dialog[open]')
  if (!(await dialog.isVisible().catch(() => false))) {
    await page.getByRole('button', { name: 'Record treatment' }).first().click()
    await page.waitForTimeout(600)
  }
  await assertNoOverflow(page, 'record treatment dialog')
  await page.locator('#item-service-1').selectOption({ label: service.name })
  await page.locator('#item-tooth-1').fill('99')
  await page.waitForTimeout(300)
  if (!(await page.getByText('There is no tooth with that number').first().isVisible().catch(() => false))) {
    note('fdi', 'tooth 99 was accepted without complaint')
  }
  await page.locator('#item-tooth-1').fill('36')
  await page.locator('#item-surfaces-1').fill('mo')
  await page.locator('#item-finding-1').fill('Deep caries, distal')
  await page.waitForTimeout(300)
  await shot(page, '06-tooth-recorded')
  await page.getByRole('dialog').getByRole('button', { name: 'Save' }).click()
  await page.waitForTimeout(1800)
  await shot(page, '07-item-listed')

  const dentist = client(await token(DENTIST))
  const items = await dentist.ok('GET', `/clinic/patients/${patient.id}/treatments`)
  if (items.length !== 1) note('clinical', `expected one treatment item, found ${items.length}`)
  const item = items[0]
  if (item && item.tooth_number !== 36) note('fdi', `tooth saved as ${item?.tooth_number}, not 36`)
  if (item && item.tooth_surfaces !== 'MO') {
    note('fdi', `surfaces saved as ${item?.tooth_surfaces}, not MO`)
  }

  step = '9 nothing is billable until the work is finished'
  const earlyBillable = await dentist.ok('GET', `/clinic/patients/${patient.id}/billable`)
  if (earlyBillable.length !== 0) {
    note('billing', 'unfinished work was offered for billing')
  }

  step = '10 mark the work finished'
  await page.getByRole('button', { name: 'Mark finished' }).first().click()
  await page.waitForTimeout(1600)
  await shot(page, '08-item-finished')
  const afterComplete = await dentist.ok('GET', `/clinic/patients/${patient.id}/treatments`)
  if (afterComplete[0]?.status !== 'COMPLETED') {
    note('clinical', `item is ${afterComplete[0]?.status}, not COMPLETED`)
  }

  step = '11 finish the visit'
  await page.getByRole('button', { name: 'Finish the visit' }).first().click()
  await page.waitForTimeout(500)
  await page.locator('#encounter-notes').fill('Canal cleaned; review in two weeks')
  await page.getByRole('dialog').getByRole('button', { name: 'Finish the visit' }).click()
  await page.waitForTimeout(1600)
  await shot(page, '09-visit-finished')
  const encounters = await dentist.ok('GET', `/clinic/patients/${patient.id}/encounters`)
  if (encounters[0]?.status !== 'COMPLETED') {
    note('clinical', `visit is ${encounters[0]?.status}, not COMPLETED`)
  }

  step = '12 the dentist may not raise a bill'
  const dentistBills = await dentist.call('POST', `/clinic/patients/${patient.id}/invoices`, {
    item_ids: null,
    issue_date: null,
  })
  if (dentistBills.status !== 403) {
    note('authority', `the dentist raised a bill (${dentistBills.status})`)
  }

  // ------------------------------------------------- 13-14. billing the work
  step = '13 the cashier bills the finished work'
  await signIn(page, CASHIER)
  if (!(await openPatientByCode(page, patient.code))) {
    note('navigation', 'the cashier cannot open the patient')
  }
  await shot(page, '10-cashier-patient')
  const billButton = page.getByRole('button', { name: /Draft a bill/ }).first()
  if (!(await billButton.isVisible().catch(() => false))) {
    note('billing', 'the cashier is not offered the billing control for finished work')
  } else {
    await billButton.click()
    await page.waitForTimeout(1800)
    await shot(page, '11-billed')
  }

  const cashier = client(await token(CASHIER))
  const detail = await cashier.ok('GET', `/clinic/patients/${patient.id}`)
  if (detail.invoices.length !== 1) {
    note('billing', `expected one bill, found ${detail.invoices.length}`)
  }
  const draft = detail.invoices[0]

  step = '14 the same work cannot be billed twice'
  const stillBillable = await cashier.call('GET', `/clinic/patients/${patient.id}/billable`)
  if (stillBillable.status === 200 && stillBillable.body.length !== 0) {
    note('billing', 'work that has been billed is still offered for billing')
  }

  // ------------------------------------------------------ 15-20. the payment
  step = '15 issue the bill, which recognises the revenue'
  const ledgerBeforeIssue = await ledger(owner)
  await owner.ok('POST', `/sales/invoices/${draft.invoice_id}/issue`, {
    tax_treatment: 'NONE',
  })
  const ledgerAfterIssue = await ledger(owner)
  const revenue = moved(ledgerBeforeIssue, ledgerAfterIssue, '9030')
  if (revenue !== -Number(PRICE)) {
    note('accounting', `issuing moved ${revenue} on 9030, expected a credit of ${PRICE}`)
  }
  const receivable = moved(ledgerBeforeIssue, ledgerAfterIssue, '4010')
  if (receivable !== Number(PRICE)) {
    note('accounting', `issuing moved ${receivable} on 4010, expected a debit of ${PRICE}`)
  }

  step = '16 a drawer must be open to take cash'
  const session = await openSession(cashier, register)

  step = '17 the remaining balance cannot be typed into'
  await openPatientByCode(page, patient.code)
  await page.getByRole('button', { name: 'Take payment' }).first().click()
  await page.waitForTimeout(1600)
  await shot(page, '12-payment-panel')
  await assertNoOverflow(page, 'payment panel')
  const remaining = page.locator('#settle-remaining')
  if (await remaining.isVisible().catch(() => false)) {
    if (!(await remaining.isDisabled().catch(() => false))) {
      note('payment', 'the remaining balance can be edited')
    }
    if (digits(await remaining.inputValue().catch(() => '')) !== digits(PRICE)) {
      note('payment', `the remaining balance shows ${await remaining.inputValue()}, not ${PRICE}`)
    }
  } else {
    note('payment', 'the remaining balance field was not found')
  }

  step = '18 an overpayment is refused by the form'
  await page.locator('#settle-cash').fill('900000')
  await page.waitForTimeout(500)
  await shot(page, '13-overpayment')
  const submit = page.getByRole('button', { name: 'Confirm payment' }).last()
  if (await submit.isEnabled().catch(() => false)) {
    note('payment', 'the form would submit more than is owed')
  }

  step = '19 a mixed cash and card payment settles the bill'
  await page.locator('#settle-cash').fill('200000')
  await page.locator('#settle-card').fill('400000')
  await page.waitForTimeout(600)
  await shot(page, '14-mixed-payment')
  if (!(await submit.isEnabled().catch(() => false))) {
    note('payment', 'a payment that exactly covers the bill cannot be submitted')
  }
  await submit.click()
  await page.waitForTimeout(2500)
  await shot(page, '15-settled')

  const settled = await cashier.ok('GET', `/clinic/invoices/${draft.invoice_id}/settlement`)
  if (Number(settled.remaining) !== 0) {
    note('payment', `the bill still shows ${settled.remaining} owing after a full settlement`)
  }
  if (settled.accepts_payment !== false) {
    note('payment', 'a fully paid bill still accepts payment')
  }

  step = '20 a fully paid bill takes no more money'
  const again = await cashier.call(
    'POST',
    `/clinic/invoices/${draft.invoice_id}/settlement`,
    {
      cash: '1000',
      received_on: TODAY,
      request_key: `p2p-${RUN}-extra`,
      cash_session_id: session.id,
    },
  )
  if (again.status < 400) {
    note('payment', `a settled bill accepted another ${again.status} payment`)
  }

  step = '21 the ledger agrees with the screens'
  const ledgerAtEnd = await ledger(owner)
  const revenueTotal = moved(ledgerAtStart, ledgerAtEnd, '9030')
  if (revenueTotal !== -Number(PRICE)) {
    note('accounting', `revenue moved ${revenueTotal} overall, expected ${PRICE} once`)
  }
  const receivableTotal = moved(ledgerAtStart, ledgerAtEnd, '4010')
  if (receivableTotal !== 0) {
    note('accounting', `the receivable ended at ${receivableTotal}, expected zero once paid`)
  }
  const cashMoved = moved(ledgerAtStart, ledgerAtEnd, '5010')
  if (cashMoved !== 200000) {
    note('accounting', `cash moved ${cashMoved}, expected the 200 000 taken in the drawer`)
  }

  step = '22 languages and phone width'
  for (const locale of ['uz', 'ru', 'en']) {
    await page.evaluate((l) => {
      try {
        localStorage.setItem('erp.locale', l)
      } catch {}
    }, locale)
    await openPatientByCode(page, patient.code)
    await assertNoOverflow(page, `patient account in ${locale}`)
    await shot(page, `16-account-${locale}`)
  }
  await page.setViewportSize({ width: 390, height: 844 })
  await openPatientByCode(page, patient.code)
  await assertNoOverflow(page, 'patient account at phone width')
  await shot(page, '17-account-phone')
  await page.setViewportSize({ width: 1440, height: 900 })

  // ------------------------------------------------------------ leave tidy
  step = '23 bank the takings and close the drawer'
  const totals = (await cashier.ok('GET', `/cash/sessions/${session.id}`)).totals
  if (Number(totals.expected) > 0) {
    await cashier.ok('POST', `/cash/sessions/${session.id}/deposits`, {
      amount: String(Number(totals.expected)),
      route: 'STAFF',
      description: 'e2e banking',
    })
  }
  await cashier.ok('POST', `/cash/sessions/${session.id}/count`, { counted_amount: '0' })
  await cashier.ok('POST', `/cash/sessions/${session.id}/close`)

  await browser.close()
  console.log('\n=== problems ===')
  problems.length ? problems.forEach((p) => console.log(' -', p)) : console.log('none')
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
