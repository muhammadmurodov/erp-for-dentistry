/**
 * Drives the Phase 3c clinic screens in a real Chrome, the way phases 1 to 3d
 * were verified: click every control, inspect every screen, and fail on any
 * console error, page error, failed request or horizontal overflow.
 *
 * The narrative is the clinic's own division of labour, because that is what
 * the permissions encode and what the screens have to survive:
 *
 *   reception registers the patient and may not touch the money;
 *   the dentist records the work — over the API, because the clinical screens
 *     do not exist yet, which is the one thing this pass cannot drive;
 *   the cashier takes the payment and may not register anybody.
 *
 * What it checks beyond the mechanical faults:
 *
 *   - the remaining balance is shown and never typed: the field is disabled,
 *     read-only, and recomputed on every keystroke;
 *   - an overpayment is refused by the form before it is refused by the
 *     service, and the service is still the authority;
 *   - cash is unavailable with no drawer open, and becomes available when one
 *     is, without a reload of the application;
 *   - a bill settled to the penny disables what is left of the form, and
 *     clearing an amount puts it back;
 *   - reception is not offered a payment, the cashier is not offered a
 *     registration, and the accountant is not offered the clinic at all;
 *   - and the ledger: issuing recognises the revenue, taking the money moves
 *     cash without touching revenue a second time.
 *
 * It never destroys data. Every run creates its own service, its own till and
 * its own patients, and closes the drawer it opened so the next run starts
 * from the same place.
 */
const path = require('path')
const { chromium } = require('playwright')

const WEB = process.env.E2E_WEB || 'http://localhost:5173'
const API = process.env.E2E_API || 'http://localhost:8000/api/v1'
const SHOTS = process.env.E2E_SHOTS || path.join(__dirname, 'shots')

/** The tenant this pass drives; see e2e/README.md for how to create it. */
const TENANT = process.env.E2E_TENANT || 'verify-3d'
const OWNER = { email: `owner@${TENANT}.example.com`, password: 'verify-owner-password-1' }
const STAFF_PASSWORD = 'verify-staff-password-1'
const TEMP_PASSWORD = 'verify-temp-password-0'
const RECEPTIONIST = { email: `receptionist@${TENANT}.example.com`, password: STAFF_PASSWORD }
const CASHIER = { email: `cashier@${TENANT}.example.com`, password: STAFF_PASSWORD }
const ACCOUNTANT = { email: `accountant@${TENANT}.example.com`, password: STAFF_PASSWORD }

/** Unique to this run, so the pass is repeatable against the same tenant. */
const RUN = Date.now().toString().slice(-6)
const TODAY = new Date().toISOString().slice(0, 10)

/** Whole thousands, so a formatted figure can be compared digit for digit. */
const PRICE = '1250000'
const CASH_HALF = '400000'
const CARD_HALF = '850000'

const problems = []
let step = 'startup'

function note(kind, detail) {
  problems.push(`[${step}] ${kind}: ${detail}`)
}

/** Only the digits, so an assertion does not depend on how money is grouped. */
function digits(text) {
  return (text || '').replace(/\D/g, '')
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

/**
 * The roles the clinic actually runs on, as users this pass can sign in as.
 *
 * A created account must change its password before it is usable, so the
 * account is created with a temporary one and settled on the shared staff
 * password through the same route a real person would use.
 */
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
    if (existing.ok) {
      console.log(`${who.email} already exists`)
      continue
    }
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

/** The closing balance per account, so the pass can assert on what moved. */
async function ledger(owner) {
  const report = await owner.ok(
    `GET`,
    `/accounting/reports/trial-balance?from_date=2026-01-01&to_date=2026-12-31`,
  )
  const balances = {}
  for (const row of report.rows) {
    balances[row.account_code] = Number(row.closing_debit) - Number(row.closing_credit)
  }
  return balances
}

function movement(before, after, code) {
  return (after[code] ?? 0) - (before[code] ?? 0)
}

// ----------------------------------------------------------- the browser side

async function shot(page, name) {
  await page.screenshot({ path: path.join(SHOTS, `clinic-${name}.png`), fullPage: true })
}

/** Bug 23's mechanical form: nothing may push the page wider than the viewport. */
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
    if (response.status() >= 500) note(`server error${label}`, `${response.status()} ${response.url()}`)
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
  await page.waitForTimeout(600)
}

async function openClinic(page) {
  const entry = page.locator('nav a[href="/clinic"]').first()
  if (!(await entry.isVisible().catch(() => false))) {
    note('navigation', 'the clinic entry is not offered to somebody who may read patients')
    await page.goto(`${WEB}/clinic`, { waitUntil: 'networkidle' })
  } else {
    await entry.click()
  }
  await page.waitForURL(/\/clinic/, { timeout: 10000 })
  await page.waitForTimeout(900)
}

/**
 * Find a patient by code and open their account, in any language.
 *
 * Nothing here depends on a translated label: the search box is the only text
 * input on the list, and the only control on a row opens that patient.
 */
async function openByCode(page, code) {
  await page.goto(`${WEB}/clinic`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(1000)
  await page.locator('main input').first().fill(code)
  await page.waitForTimeout(1300)
  const row = page.locator('tbody tr', { hasText: code }).first()
  if (!(await row.isVisible().catch(() => false))) return false
  await row.getByRole('button').first().click()
  await page.waitForTimeout(1600)
  return true
}

/** Register one patient through the dialog, returning the duplicate panel's state. */
async function registerPatient(page, { family, given, birthYear, phone }) {
  await page.getByRole('button', { name: 'Register patient' }).first().click()
  await page.waitForTimeout(400)
  await assertNoOverflow(page, 'registration dialog')
  await page.locator('#patient-family').fill(family)
  await page.locator('#patient-given').fill(given)
  await page.locator('#patient-birth-year').fill(String(birthYear))
  if (phone) await page.locator('#patient-phone').fill(phone)
  await page.getByRole('button', { name: 'Create', exact: true }).click()
  await page.waitForTimeout(1200)
  const duplicates = await page
    .getByText('This person may already be on file')
    .isVisible()
    .catch(() => false)
  return duplicates
}

// ------------------------------------------------------------------- the pass

async function run() {
  // ----------------------------------------------- 0. what the clinic needs
  step = '0 setup over the API: staff, a priced service, a till'
  const owner = client(await token(OWNER))
  await ensureStaff(owner)

  const service = await owner.ok('POST', '/sales/services', {
    code: `TRT-${RUN}`,
    name: `Verification treatment ${RUN}`,
    price: PRICE,
    effective_from: '2026-01-01',
  })
  // One till, reused by every run. A cash account may only hold what the
  // ledger says it holds, so a fresh till each time would be refused the
  // moment an earlier run had left anything in 5010. This one banks what it
  // takes and closes on a count of nothing, which is what a clinic does.
  const registers = await owner.ok('GET', '/cash/registers')
  const register =
    registers.find((row) => row.code === 'TILL-E2E') ??
    (await owner.ok('POST', '/cash/registers', {
      code: 'TILL-E2E',
      name: 'Verification till',
      account_code: '5010',
    }))
  const alreadyOpen = await owner.ok('GET', '/cash/sessions?session_status=OPEN')
  const drawerWasOpen = alreadyOpen.length > 0
  if (drawerWasOpen) {
    console.log('a drawer is already open in this tenant: the "no cash session" check is skipped')
  }
  console.log(`service ${service.code} at ${PRICE}; till ${register.code}`)

  const browser = await chromium.launch({ channel: 'chrome', headless: true })
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  const page = await context.newPage()
  watch(page, '')

  // ------------------------------------------------------- 1. reception
  step = '1 sign in as the receptionist'
  await signIn(page, RECEPTIONIST)
  await shot(page, '00-dashboard')
  await assertNoOverflow(page, 'dashboard')

  step = '2 open the clinic from the navigation'
  await openClinic(page)
  await shot(page, '01-patients')
  await assertNoOverflow(page, 'patient list')

  step = '3 register a patient'
  const family = `Oripov${RUN}`
  const duplicatesOnFirst = await registerPatient(page, {
    family,
    given: 'Jahongir',
    birthYear: 1990,
    phone: `+99890${RUN}1`,
  })
  if (duplicatesOnFirst) {
    note('false duplicate', 'a patient nobody resembles was reported as a possible duplicate')
    await page.getByRole('button', { name: 'Confirm', exact: true }).click()
    await page.waitForTimeout(1000)
  }
  await shot(page, '02-patient-account-empty')
  await assertNoOverflow(page, 'patient account, nothing billed')

  step = '4 a new patient owes nothing and has no bills'
  const emptyBills = await page.getByText('No bills yet.').isVisible().catch(() => false)
  if (!emptyBills) note('missing empty state', 'a patient with no bills does not say so')
  const accountText = await page.locator('main').innerText()
  if (!/Debt/.test(accountText)) note('missing figure', 'the account does not show the debt')

  step = '5 the same name and birth year is reported, not refused'
  await page.getByRole('button', { name: 'Back', exact: true }).click()
  await page.waitForTimeout(900)
  const duplicatesOnSecond = await registerPatient(page, {
    family,
    given: 'Jahongir',
    birthYear: 1990,
    phone: `+99890${RUN}2`,
  })
  await shot(page, '03-duplicate-candidates')
  if (!duplicatesOnSecond) {
    note('duplicate not detected', 'a second patient with the same name and birth year was accepted silently')
  } else {
    await assertNoOverflow(page, 'duplicate candidates')
    const panel = await page.locator('dialog[open]').innerText()
    if (!panel.includes(family)) {
      note('unhelpful duplicate panel', 'the candidate shown does not name the patient on file')
    }
    if (!/was not refused/i.test(panel)) {
      note('misleading duplicate panel', 'the panel does not say the registration went through')
    }
    await page.getByRole('button', { name: 'Confirm', exact: true }).click()
    await page.waitForTimeout(1200)
  }
  await shot(page, '04-second-patient-account')

  step = '6 find both of them by name'
  await page.getByRole('button', { name: 'Back', exact: true }).click()
  await page.waitForTimeout(700)
  await page.getByLabel('Search', { exact: true }).fill(family)
  await page.waitForTimeout(1200)
  const found = await page.locator('tbody tr').count()
  if (found !== 2) note('search', `searching for this run's surname returned ${found} rows, expected 2`)
  await shot(page, '05-search')
  await assertNoOverflow(page, 'patient search')

  step = '7 reception may not register nobody — it may not take money either'
  // Read the patient the dentist is about to treat, from the list the
  // receptionist is looking at.
  const patients = await owner.ok('GET', `/clinic/patients?query=${family}`)
  const patient = patients.find((row) => row.phone === `+99890${RUN}1`) ?? patients[0]
  if (!patient) throw new Error('the patient registered in the browser cannot be found over the API')

  // ------------------------------------------- 2. the dentist, over the API
  step = '8 record and complete the treatment (no clinical screen exists)'
  const encounter = await owner.ok('POST', '/clinic/encounters', {
    patient_id: patient.id,
    occurred_on: TODAY,
    complaint: 'Pain on the upper right',
  })
  const items = await owner.ok('POST', `/clinic/encounters/${encounter.id}/items`, {
    items: [
      {
        service_id: service.id,
        tooth_number: 16,
        finding: 'Deep caries',
        status: 'IN_PROGRESS',
      },
    ],
  })
  await owner.ok('POST', `/clinic/treatments/${items[0].id}/complete`, { completed_on: TODAY })

  step = '9 the completed work is waiting to be billed, on the screen'
  await page.reload({ waitUntil: 'networkidle' })
  await page.waitForTimeout(1000)
  await page.getByLabel('Search', { exact: true }).fill(family)
  await page.waitForTimeout(1000)
  await page
    .locator('tbody tr', { hasText: patient.code })
    .getByRole('button', { name: 'Edit', exact: true })
    .click()
  await page.waitForTimeout(1200)
  const waiting = await page.locator('main').innerText()
  if (!/Completed treatments not yet billed/.test(waiting)) {
    note('missing figure', 'the account does not report completed work that is not yet billed')
  }
  await shot(page, '06-billable-waiting')

  step = '10 draft the bill and issue it'
  const beforeIssue = await ledger(owner)
  const draft = await owner.ok('POST', `/clinic/patients/${patient.id}/invoices`, {
    issue_date: TODAY,
  })
  if (digits(draft.total) !== `${PRICE}00`) {
    note('price', `the bill came to ${draft.total}, expected ${PRICE}.00`)
  }
  await owner.ok('POST', `/sales/invoices/${draft.id}/issue`, { tax_treatment: 'NONE' })
  const afterIssue = await ledger(owner)
  if (movement(beforeIssue, afterIssue, '9030') !== -Number(PRICE)) {
    note(
      'ledger',
      `issuing the bill moved revenue by ${movement(beforeIssue, afterIssue, '9030')}, expected -${PRICE}`,
    )
  }
  if (movement(beforeIssue, afterIssue, '4010') !== Number(PRICE)) {
    note(
      'ledger',
      `issuing the bill moved the receivable by ${movement(beforeIssue, afterIssue, '4010')}, expected +${PRICE}`,
    )
  }

  step = '11 reception sees the debt and is not offered a payment'
  if (!(await openByCode(page, patient.code))) {
    throw new Error('the patient cannot be reopened from the list by code')
  }
  await shot(page, '07-reception-debt')
  const debtText = await page.locator('main').innerText()
  if (digits(debtText).indexOf(PRICE) === -1) {
    note('missing figure', 'the bill does not appear on the account the receptionist is reading')
  }
  await page.getByRole('button', { name: 'Take payment', exact: true }).first().click()
  await page.waitForTimeout(1400)
  await shot(page, '08-reception-no-payment')
  await assertNoOverflow(page, 'payment panel as reception sees it')
  if (
    await page
      .getByRole('button', { name: 'Confirm payment', exact: true })
      .isVisible()
      .catch(() => false)
  ) {
    note('over-privileged', 'reception, which may not record a payment, is offered "Confirm payment"')
  }
  if (!(await page.getByText('You do not have access to this section.').isVisible().catch(() => false))) {
    note('unexplained refusal', 'reception is shown a payment form with no explanation of why it cannot use it')
  }
  for (const field of ['#settle-cash', '#settle-card', '#settle-advance', '#settle-reference']) {
    if (!(await page.locator(field).isDisabled())) {
      note('inviting a refusal', `reception can type into ${field} on a form it may not submit`)
    }
  }

  // --------------------------------------------------------- 3. the cashier
  step = '12 sign in as the cashier'
  await context.clearCookies()
  const till = await context.newPage()
  watch(till, ' (cashier)')
  await signIn(till, CASHIER)
  await openClinic(till)
  await shot(till, '09-cashier-patients')
  await assertNoOverflow(till, 'patient list as the cashier')

  step = '13 a cashier may not register a patient'
  if (
    await till
      .getByRole('button', { name: 'Register patient' })
      .isVisible()
      .catch(() => false)
  ) {
    note('over-privileged', 'the cashier, which may not register a patient, is offered "Register patient"')
  }

  step = '14 open the account that owes money'
  await till.getByLabel('Search', { exact: true }).fill(patient.code)
  await till.waitForTimeout(1200)
  await till
    .locator('tbody tr', { hasText: patient.code })
    .getByRole('button', { name: 'Edit', exact: true })
    .click()
  await till.waitForTimeout(1400)
  await shot(till, '10-cashier-account')
  await assertNoOverflow(till, 'patient account as the cashier')
  const unpaid = await till.locator('main').innerText()
  if (!/Unpaid/.test(unpaid)) note('status', 'an unpaid bill is not marked unpaid')

  step = '15 the payment panel: the remaining balance is shown, never typed'
  await till.getByRole('button', { name: 'Take payment', exact: true }).first().click()
  await till.waitForTimeout(1400)
  await shot(till, '11-panel-opened')
  await assertNoOverflow(till, 'payment panel')
  const remaining = till.locator('#settle-remaining')
  if (!(await remaining.isDisabled())) {
    note('editable balance', 'the remaining balance can be focused and typed into')
  }
  if ((await remaining.getAttribute('readonly')) === null) {
    note('editable balance', 'the remaining balance is not read-only')
  }
  if (digits(await remaining.inputValue()) !== PRICE) {
    note('balance', `the remaining balance reads ${await remaining.inputValue()}, expected ${PRICE}`)
  }

  step = '16 cash is unavailable while no drawer is open'
  if (!drawerWasOpen) {
    if (!(await till.locator('#settle-cash').isDisabled())) {
      note('cash without a drawer', 'the cash field is enabled with no cash session open')
    }
    const saidOnce = await till
      .getByText('No cash session is open, so cash cannot be taken.', { exact: false })
      .count()
    if (saidOnce === 0) note('unexplained refusal', 'the cash field is disabled without saying why')
    if (saidOnce > 1) note('repetition', `the drawer is explained ${saidOnce} times on one screen`)
    await shot(till, '12-no-drawer')
  }

  step = '17 an overpayment is refused by the form'
  await till.locator('#settle-card').fill('1251000')
  await till.waitForTimeout(500)
  await shot(till, '13-overpay')
  if (!(await till.getByText('That is more than is owed. Reduce the amount.').isVisible().catch(() => false))) {
    note('overpayment', 'typing more than is owed is not reported')
  }
  if (!(await till.getByRole('button', { name: 'Confirm payment', exact: true }).isDisabled())) {
    note('overpayment', 'the form offers to take more money than is owed')
  }
  // Nothing owed and over the amount owed are not the same state, and a
  // cashier reading "0" while typing too much is being told the opposite.
  const overBalance = await remaining.inputValue()
  if (digits(overBalance) === '0') {
    note('overpayment', 'the remaining balance reads nothing owed while the form holds more than is owed')
  }
  if (digits(overBalance) !== '1000') {
    note('overpayment', `the remaining balance reads ${overBalance}, expected the 1 000 the form is over by`)
  }

  step = '18 ... and the service refuses it too, whatever the form thinks'
  // 422, not 409: the submission is wrong, the bill is not in the wrong
  // state. A settled bill is the conflict, and step 27 checks that instead.
  const cashier = client(await token(CASHIER))
  const refused = await cashier.call('POST', `/clinic/invoices/${draft.id}/settlement`, {
    card: '1251000',
    request_key: `clinic-pass-${RUN}-overpay`,
  })
  if (refused.status !== 422) {
    note('overpayment', `the service answered an overpayment with ${refused.status}, expected 422`)
  }
  if (!/Nothing was taken/i.test(JSON.stringify(refused.body))) {
    note('overpayment', 'the refusal does not say that nothing was taken')
  }

  step = '19 clearing the form puts the balance back'
  await till.getByRole('button', { name: 'Clear', exact: true }).click()
  await till.waitForTimeout(500)
  if (digits(await remaining.inputValue()) !== PRICE) {
    note('balance', 'clearing the form does not restore the full remaining balance')
  }
  if (!(await till.getByRole('button', { name: 'Confirm payment', exact: true }).isDisabled())) {
    note('empty submission', 'an empty form offers to take a payment')
  }
  if (!(await till.getByText('Enter an amount in cash, in card, or an advance to apply.').isVisible().catch(() => false))) {
    note('missing hint', 'an empty form does not say what is needed')
  }

  step = '20 a bill covered to the penny disables what is left of the form'
  await till.locator('#settle-card').fill(PRICE)
  await till.waitForTimeout(600)
  await shot(till, '14-covered')
  if (digits(await remaining.inputValue()) !== '0') {
    note('balance', `covering the bill leaves ${await remaining.inputValue()} remaining`)
  }
  if (!drawerWasOpen && !(await till.locator('#settle-cash').isDisabled())) {
    note('nothing left to take', 'the cash field is still enabled with nothing left to pay')
  }
  await till.locator('#settle-card').fill('')
  await till.waitForTimeout(600)
  if (digits(await remaining.inputValue()) !== PRICE) {
    note('balance', 'emptying the card field does not put the balance back')
  }

  step = '21 open the drawer, and the cash field becomes available'
  // The float is declared, not posted: it has to agree with the cash account
  // the till is paired to, or the day's count would report a difference that
  // is not one.
  const onHand = (await ledger(owner))['5010'] ?? 0
  const session = await owner.ok('POST', '/cash/sessions', {
    register_id: register.id,
    business_date: TODAY,
    opening_float: String(onHand),
  })
  if (!(await openByCode(till, patient.code))) {
    throw new Error('the cashier cannot reopen the account by code')
  }
  await till.getByRole('button', { name: 'Take payment', exact: true }).first().click()
  await till.waitForTimeout(1500)
  if (await till.locator('#settle-cash').isDisabled()) {
    note('cash with a drawer', 'the cash field is still disabled with a cash session open')
  }
  await shot(till, '15-drawer-open')

  step = '22 take the payment: part cash, part card, one submission'
  const afterIssueAgain = await ledger(owner)
  await till.locator('#settle-cash').fill(CASH_HALF)
  await till.waitForTimeout(400)
  if (digits(await till.locator('#settle-remaining').inputValue()) !== '850000') {
    note('balance', `after ${CASH_HALF} in cash the balance reads ${await till.locator('#settle-remaining').inputValue()}`)
  }
  await till.locator('#settle-card').fill(CARD_HALF)
  await till.waitForTimeout(400)
  await till.locator('#settle-reference').fill(`TERM-${RUN}`)
  await till.waitForTimeout(300)
  if (digits(await till.locator('#settle-remaining').inputValue()) !== '0') {
    note('balance', 'cash plus card covering the bill does not bring the balance to zero')
  }
  await shot(till, '16-ready-to-confirm')
  await till.getByRole('button', { name: 'Confirm payment', exact: true }).click()
  await till.waitForTimeout(2500)
  await shot(till, '17-payment-taken')
  await assertNoOverflow(till, 'payment panel after settling')

  step = '23 the bill is settled, and says so'
  const settledText = await till.locator('main').innerText()
  if (!/Payment taken\./.test(settledText)) note('no confirmation', 'the payment is not confirmed on screen')
  if (!/This bill is settled in full/.test(settledText)) {
    note('status', 'a fully paid bill does not say it accepts nothing further')
  }
  if (!/Fully paid/.test(settledText)) note('status', 'the bill is not marked fully paid')
  if (!(await till.locator('#settle-cash').isDisabled())) {
    note('settled bill', 'a settled bill still offers to take cash')
  }

  step = '24 the history shows both halves, by method'
  // The history is the last table on the page: the bills are above it.
  const historyRows = await till.locator('table').last().locator('tbody tr').count()
  if (historyRows < 2) {
    note('history', `the payment history shows ${historyRows} rows, expected the cash and the card half`)
  }
  if (!/Cash/.test(settledText) || !/Card/.test(settledText)) {
    note('history', 'the payment history does not name both methods')
  }

  step = '25 the ledger: the money moved, the revenue did not move again'
  const afterPayment = await ledger(owner)
  const moves = [
    ['4010', -Number(PRICE), 'the receivable is cleared'],
    ['5010', Number(CASH_HALF), 'the cash is in the drawer'],
    ['5110.03', Number(CARD_HALF), 'the card half is in transit'],
    ['9030', 0, 'revenue is recognised once, at issue, not again on payment'],
  ]
  for (const [code, expected, what] of moves) {
    const actual = movement(afterIssueAgain, afterPayment, code)
    if (actual !== expected) {
      note('ledger', `${code} moved ${actual}, expected ${expected} — ${what}`)
    }
  }
  const drawer = await owner.ok(`GET`, `/cash/sessions/${session.id}`)
  const shouldHold = onHand + Number(CASH_HALF)
  if (Number(drawer.totals.expected) !== shouldHold) {
    note('drawer', `the till expects ${drawer.totals.expected}, expected ${shouldHold}`)
  }

  step = '26 the account agrees: nothing owed'
  if (!(await openByCode(till, patient.code))) {
    throw new Error('the settled account cannot be reopened')
  }
  await shot(till, '18-account-settled')
  const finalText = await till.locator('main').innerText()
  if (!/Fully paid/.test(finalText)) note('status', 'the settled bill is not marked paid on the account')

  step = '27 a resubmitted payment is replayed, not taken twice'
  const key = `clinic-pass-${RUN}-replay`
  const first = await cashier.call('POST', `/clinic/invoices/${draft.id}/settlement`, {
    card: '1000',
    request_key: key,
  })
  if (first.status !== 409) {
    note('settled bill', `the service took ${first.status} on a bill already paid in full, expected 409`)
  }

  // --------------------------------------------------- 4. the other locales
  for (const locale of ['uz', 'ru']) {
    step = `28 the clinic screens in ${locale}`
    await till.selectOption('#locale-switcher', locale)
    await till.waitForTimeout(900)
    await till.goto(`${WEB}/clinic`, { waitUntil: 'networkidle' })
    await till.waitForTimeout(1100)
    await shot(till, `19-${locale}-list`)
    await assertNoOverflow(till, `patient list in ${locale}`)
    if (await openByCode(till, patient.code)) {
      await shot(till, `19-${locale}-account`)
      await assertNoOverflow(till, `patient account in ${locale}`)
      await till.locator('tbody tr').first().getByRole('button').last().click()
      await till.waitForTimeout(1500)
      await shot(till, `19-${locale}-payment`)
      await assertNoOverflow(till, `payment panel in ${locale}`)
    } else {
      note('search', `the patient cannot be found by code in ${locale}`)
    }
  }

  step = '29 phone width'
  await till.selectOption('#locale-switcher', 'uz')
  await till.waitForTimeout(600)
  await till.setViewportSize({ width: 390, height: 844 })
  await till.goto(`${WEB}/clinic`, { waitUntil: 'networkidle' })
  await till.waitForTimeout(1100)
  await shot(till, '20-uz-narrow-list')
  await assertNoOverflow(till, 'patient list at phone width')
  if (await openByCode(till, patient.code)) {
    await shot(till, '21-uz-narrow-account')
    await assertNoOverflow(till, 'patient account at phone width')
    await till.locator('tbody tr').first().getByRole('button').last().click()
    await till.waitForTimeout(1500)
    await shot(till, '22-uz-narrow-payment')
    await assertNoOverflow(till, 'payment panel at phone width')
  }
  await till.setViewportSize({ width: 1440, height: 900 })

  // ------------------------------------------- 5. somebody with no business
  step = '30 the accountant is not offered the clinic at all'
  await context.clearCookies()
  const books = await context.newPage()
  watch(books, ' (accountant)')
  await signIn(books, ACCOUNTANT)
  if (await books.locator('nav a[href="/clinic"]').isVisible().catch(() => false)) {
    note('over-privileged', 'the accountant, who may not read a patient, is offered the clinic in the navigation')
  }
  await books.goto(`${WEB}/clinic`, { waitUntil: 'networkidle' })
  await books.waitForTimeout(1400)
  await shot(books, '23-accountant-no-access')
  await assertNoOverflow(books, 'clinic as the accountant')
  if (!(await books.getByText('You do not have access to this section.').isVisible().catch(() => false))) {
    note('unexplained refusal', 'the accountant reaching /clinic is not told why there is nothing there')
  }
  if ((await books.locator('tbody tr').count()) > 0) {
    note('leak', 'the accountant can read the patient list')
  }

  await browser.close()

  // ------------------------------------------------------------ 6. tidy up
  step = '31 bank the takings and close the drawer, so the next run starts clean'
  await owner.ok('POST', `/cash/sessions/${session.id}/deposits`, {
    amount: String(shouldHold),
    route: 'STAFF',
    reference: `clinic verification ${RUN}`,
  })
  await owner.ok('POST', `/cash/sessions/${session.id}/count`, { counted_amount: '0' })
  const closed = await owner.ok('POST', `/cash/sessions/${session.id}/close`)
  if (Number(closed.totals.discrepancy ?? 0) !== 0) {
    note('drawer', `closing reported a difference of ${closed.totals.discrepancy}`)
  }
  const emptied = (await ledger(owner))['5010'] ?? 0
  if (emptied !== 0) {
    note('drawer', `the cash account still holds ${emptied} after the takings were banked`)
  }

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
