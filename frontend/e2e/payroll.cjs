/**
 * Paying the staff, in a real Chrome.
 *
 * What this pass exists to show is the four things no unit test can show
 * reaching a person:
 *
 *   1. **A rate nobody approved stops the payroll, and says which rate.**
 *      The run calculates, the screen shows the figures, and approval and
 *      posting are both withheld with the reason named in words — not a
 *      silent disabled button and not a bare 409.
 *   2. **The employer's cost is never taken out of anybody's pay.** The
 *      social tax appears in its own column beside the wage, never in the
 *      withheld column, and net pay is gross minus the withholdings alone.
 *   3. **Whoever calculated cannot approve.** The owner calculates, the
 *      server refuses the owner's own approval, and the director approves.
 *   4. **What posts is the gross plus the employer's contribution**, and the
 *      ledger says so afterwards: it reads the trial balance before and
 *      after, and checks the five accounts the clinic's own posting rules
 *      name. Then it pays, and checks that paying recognises no new expense.
 *
 * **About the social tax rate.** The clinic's current rate is unconfirmed
 * (`OPEN_QUESTIONS.md` P2), which is exactly why payroll is blocked, and the
 * seeder deliberately proposes no rate for it. To drive the rest of the
 * sequence at all, this pass therefore *enters one itself*, the way an
 * accountant would, and labels it in the record as entered for verification
 * rather than confirmed. **That entry is not a statement about Uzbek tax
 * law, and nothing in the application depends on it.** The figures this pass
 * checks are arithmetic over whatever rate was approved, not over that one.
 *
 * It creates its own employee with a unique code, works in the demo tenant's
 * current month, and ends the employment at the finish, so it can be run
 * twice in a row — and is.
 */
const path = require('path')
const { chromium } = require('playwright')

const WEB = process.env.E2E_WEB || 'http://localhost:5173'
const API = process.env.E2E_API || 'http://localhost:8000/api/v1'
const SHOTS = process.env.E2E_SHOTS || path.join(__dirname, 'shots')

const TENANT = process.env.E2E_TENANT || 'demo'
const OWNER = {
  email: `owner@${TENANT}.example.com`,
  password: process.env.E2E_OWNER_PASSWORD || 'demo-owner-password-1',
}
const STAFF_PASSWORD = process.env.E2E_STAFF_PASSWORD || 'demo-staff-password-1'
const TEMP_PASSWORD = 'verify-temp-password-0'
const DIRECTOR = { email: `director@${TENANT}.example.com`, password: STAFF_PASSWORD }
const CASHIER = { email: `cashier@${TENANT}.example.com`, password: STAFF_PASSWORD }

const RUN = Date.now().toString().slice(-6)
const TODAY = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'Asia/Tashkent',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
}).format(new Date())

/**
 * The month to run, and the two days worked in it.
 *
 * Two constraints decide it, and neither can be assumed:
 *
 *   * **Attendance cannot be marked worked in the future**, so the month
 *     needs at least two days already behind it. Early in a month the pass
 *     steps back rather than asserting a day that has not happened.
 *   * **A month may hold only one payroll run**, which is the rule that makes
 *     this pass re-runnable or not. A previous pass settled its month and
 *     that month can never be driven again, so the pass walks backwards to
 *     the most recent month that has no run — staying inside the current
 *     year, because an earlier fiscal year's periods may be closed, and
 *     posting into a closed period is correctly refused.
 */
function latestDrivableMonth() {
  const [year, month, day] = TODAY.split('-').map(Number)
  return day >= 3 ? { year, month } : { year, month: month - 1 }
}

function chooseFreeMonth(runs) {
  const latest = latestDrivableMonth()
  for (let month = latest.month; month >= 1; month -= 1) {
    const taken = runs.some(
      (r) => r.year === latest.year && r.month === month && r.status !== 'CANCELLED',
    )
    if (!taken) return { year: latest.year, month }
  }
  throw new Error(
    `every month of ${latest.year} already holds a payroll run; this pass needs a free one`,
  )
}

/** Set once the free month is known — see `chooseFreeMonth`. */
let PERIOD = null
let PERIOD_CODE = ''
let FIRST_OF_PERIOD = ''
let WORKED_DAYS = []
function usePeriod(period) {
  PERIOD = period
  PERIOD_CODE = `${period.year}-${String(period.month).padStart(2, '0')}`
  FIRST_OF_PERIOD = `${PERIOD_CODE}-01`
  WORKED_DAYS = [`${PERIOD_CODE}-02`, `${PERIOD_CODE}-03`]
}

/** Two of four, so gross is exactly half the salary — the brief's example. */
const WORKING_DAYS = 4
const SALARY = '2000000'

// Worked out by hand, and the only figures this pass asserts as money:
//   gross            = 2,000,000 x 2 / 4 = 1,000,000
//   income tax @ 12% =                       120,000  (of which 1,000 INPS)
//   net              = 1,000,000 - 120,000 =  880,000
//   social tax @ 12% =                       120,000  (the employer's own)
//   cost to clinic   = 1,000,000 + 120,000 = 1,120,000
const EXPECT = {
  gross: 1000000,
  withheld: 120000,
  inps: 1000,
  incomeTaxToBudget: 119000,
  net: 880000,
  social: 120000,
  cost: 1120000,
  // The entry's debit total is *not* the expense. Withholding moves money
  // between two liabilities, and those legs are debits as well:
  //   1,000,000 wages + 120,000 social tax   (the expense, 1,120,000)
  // +   120,000 income tax out of wages payable
  // +     1,000 INPS out of income tax payable
  // = 1,241,000. A clinic's wage bill is the 1,120,000; this figure only
  // proves the entry was built from the four operations it should be.
  entryTotal: 1241000,
}

const problems = []
let step = 'startup'
let expected = []
const note = (kind, detail) => problems.push(`[${step}] ${kind}: ${detail}`)
const begin = (name, ...statuses) => {
  step = name
  expected = statuses
}

const tokens = new Map()
async function token(who) {
  const held = tokens.get(who.email)
  if (held) return held
  const r = await fetch(`${API}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(who),
  })
  if (r.status === 429) {
    throw new Error(`rate-limited signing in as ${who.email}; wait a minute and re-run`)
  }
  if (!r.ok) throw new Error(`login failed for ${who.email}: ${r.status}`)
  const access = (await r.json()).access_token
  tokens.set(who.email, access)
  return access
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

const shot = (page, name) =>
  page.screenshot({ path: path.join(SHOTS, `payroll-${name}.png`), fullPage: true })

function watch(page) {
  page.on('console', (m) => {
    if (m.type() !== 'error') return
    if (expected.some((status) => m.text().includes(`status of ${status}`))) return
    note('console error', m.text())
  })
  page.on('pageerror', (e) => note('page error', e.message))
  page.on('response', (r) => {
    if (r.status() >= 500) note('server error', `${r.status()} ${r.url()}`)
  })
}

async function signIn(page, who, locale = 'en') {
  await page.goto(WEB, { waitUntil: 'networkidle' })
  await page.evaluate((chosen) => {
    try {
      localStorage.clear()
      sessionStorage.clear()
      localStorage.setItem('erp.locale', chosen)
    } catch {}
  }, locale)
  await page.reload({ waitUntil: 'networkidle' })
  await page.getByLabel(/e-?mail/i).fill(who.email)
  await page.locator('input[type="password"]').fill(who.password)
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
  await page.waitForSelector('nav', { timeout: 15000 })
  await page.waitForTimeout(800)
}

async function openPayroll(page, tab, linkPattern = /^Payroll/) {
  await page.getByRole('link', { name: linkPattern }).first().click()
  await page.waitForLoadState('networkidle')
  await page.waitForTimeout(500)
  if (tab) {
    await page.getByRole('link', { name: tab, exact: true }).click()
    await page.waitForLoadState('networkidle')
    await page.waitForTimeout(900)
  }
}

/**
 * Click a button and wait for the request it is supposed to make.
 *
 * Written after the first run of this pass clicked "Calculate again" and
 * nothing reached the server: React had re-rendered and the click landed on
 * a detached node. The run was then approved and posted with figures from
 * before the rates were approved, which is the defect this pass found — but
 * it found it by the figures being wrong, two steps later. A click that
 * sends nothing should fail here, where the cause is still visible.
 */
async function clickAndExpect(page, name, pathFragment) {
  const button = page.getByRole('button', { name, exact: true })
  await button.waitFor({ state: 'visible' })
  const [response] = await Promise.all([
    page.waitForResponse(
      (r) =>
        r.url().includes(pathFragment) && r.request().method() === 'POST',
      { timeout: 20000 },
    ),
    button.click(),
  ])
  await page.waitForLoadState('networkidle')
  await page.waitForTimeout(900)
  return response
}

/** The people this pass needs, created once and reused after. */
async function ensureStaff(owner) {
  const directory = await owner.ok('GET', '/users')
  for (const [who, roles, name] of [
    [DIRECTOR, ['director'], 'Verify Director'],
    [CASHIER, ['cashier'], 'Verify Cashier'],
  ]) {
    if (directory.some((u) => u.email === who.email)) continue
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
    tokens.delete(who.email)
    console.log(`created ${who.email} as ${roles.join(', ')}`)
  }
}

/**
 * A tenant that can hold a ledger at all: a chart, a fiscal year with its
 * monthly periods, and a policy in force.
 *
 * `seed-demo` creates the organization and its accounts; it does not do the
 * accounting setup, which the earlier phases did by hand against the
 * long-lived development tenant. A pass meant to run against a disposable
 * database has to do it itself, and doing it here is also the only place
 * anything checks that the bootstrap endpoints still work in sequence.
 */
async function ensureAccountingSetup(owner) {
  const accounts = await owner.ok('GET', '/accounting/accounts')
  if (accounts.length === 0) {
    const summary = await owner.ok('POST', '/accounting/accounts/seed-statutory-chart', {
      confirm: true,
    })
    console.log(`seeded the statutory chart: ${JSON.stringify(summary).slice(0, 120)}`)
  }

  const years = await owner.ok('GET', '/accounting/fiscal-years')
  const year = PERIOD.year
  if (!years.some((y) => y.starts_on <= FIRST_OF_PERIOD && y.ends_on >= FIRST_OF_PERIOD)) {
    await owner.ok('POST', '/accounting/fiscal-years', {
      code: String(year),
      starts_on: `${year}-01-01`,
      ends_on: `${year}-12-31`,
      create_monthly_periods: true,
    })
    console.log(`opened the ${year} fiscal year and its months`)
  }

  const policies = await owner.ok('GET', '/accounting/policies')
  if (!policies.some((p) => p.status !== 'DRAFT' && p.effective_from <= TODAY)) {
    // Dated to the first of the run's month so it governs the period end.
    const draft = await owner.ok(
      'POST',
      `/accounting/policies/seed-default?effective_from=${FIRST_OF_PERIOD}`,
      {},
    )
    await owner.ok(`POST`, `/accounting/policies/${draft.id}/activate`, {})
    console.log('seeded the workbook policy and put it in force')
  }
}

/** The clinic's own payroll posting rules, from `default_policy.py`. */
const PAYROLL_RULES = [
  ['payroll_wages_accrued', 'Wages accrued for the period', '9420', '6710'],
  ['payroll_income_tax_withheld', 'Income tax withheld from wages', '6710', '6411'],
  ['payroll_inps_withheld', 'The INPS component moved to the pension account', '6411', '6520.02'],
  ['payroll_social_tax_accrued', 'Social tax on the payroll', '9420', '6520.01'],
  [
    'payroll_employee_pension_withheld',
    'Employee pension contribution withheld',
    '6710',
    '6520.03',
  ],
  ['payroll_paid_bank', 'Wages paid by bank transfer', '6710', '5110.01'],
  ['payroll_paid_cash', 'Wages paid from the till', '6710', '5010'],
]

/**
 * A policy version carrying the payroll rules.
 *
 * Needed for the same reason `purchasing.cjs` needs one: a tenant whose
 * policy was activated before payroll existed has no rule for a wage, and
 * the refusal to post without one is correct. The new version must be in
 * force on the run's period end, so it is dated to the first of that month.
 */
async function ensurePayrollRules(owner) {
  const policies = await owner.ok('GET', '/accounting/policies')
  const inForce = policies
    .filter((p) => p.status !== 'DRAFT' && p.effective_from <= TODAY)
    .sort((a, b) => (a.effective_from < b.effective_from ? 1 : -1))[0]
  if (!inForce) throw new Error('the tenant has no accounting policy in force today')

  const current = await owner.ok('GET', `/accounting/policies/${inForce.id}/rules`)
  const held = new Set(current.map((r) => r.operation_code))
  const missing = PAYROLL_RULES.filter(([code]) => !held.has(code))
  if (missing.length === 0) return

  console.log(`adding ${missing.length} payroll rules in a new policy version`)
  for (const policy of policies.filter((p) => p.status === 'DRAFT')) {
    await owner.call('DELETE', `/accounting/policies/${policy.id}`)
  }
  // A version must start after the one it replaces, and must be in force on
  // the period end. The first of the run's month satisfies both, unless the
  // version in force started later — in which case the day after it does.
  const effectiveFrom =
    inForce.effective_from < FIRST_OF_PERIOD
      ? FIRST_OF_PERIOD
      : new Date(new Date(inForce.effective_from).getTime() + 86400000)
          .toISOString()
          .slice(0, 10)
  const draft = await owner.ok('POST', '/accounting/policies', {
    name: `Policy with payroll rules (${RUN})`,
    effective_from: effectiveFrom,
    code: 'default',
  })
  for (const rule of [
    ...current.map((r) => ({
      operation_code: r.operation_code,
      description: r.description,
      debit_account_code: r.debit_account_code,
      credit_account_code: r.credit_account_code,
    })),
    ...missing.map(([operation_code, description, debit, credit]) => ({
      operation_code,
      description,
      debit_account_code: debit,
      credit_account_code: credit,
    })),
  ]) {
    await owner.ok('PUT', `/accounting/policies/${draft.id}/rules`, rule)
  }
  await owner.ok('POST', `/accounting/policies/${draft.id}/activate`, {})
}

/** Closing balances by account code, so the ledger can be read as evidence. */
async function balances(owner) {
  const year = PERIOD.year
  const report = await owner.ok(
    'GET',
    `/accounting/reports/trial-balance?from_date=${year}-01-01&to_date=${year}-12-31`,
  )
  const byCode = {}
  for (const row of report.rows || []) {
    byCode[row.account_code] = {
      debit: Number(row.closing_debit),
      credit: Number(row.closing_credit),
    }
  }
  return byCode
}

/** How much the debit side of an account moved between two readings. */
function movedDebit(before, after, code) {
  const a = before[code] || { debit: 0, credit: 0 }
  const b = after[code] || { debit: 0, credit: 0 }
  return b.debit - a.debit - (b.credit - a.credit)
}

/** How much the credit side moved. Positive means the account was credited. */
function movedCredit(before, after, code) {
  return -movedDebit(before, after, code)
}

async function run() {
  begin('0 setup')
  const owner = client(await token(OWNER))
  await ensureStaff(owner)
  usePeriod(chooseFreeMonth(await owner.ok('GET', '/payroll/runs')))
  console.log(`driving payroll for ${PERIOD_CODE}`)

  // A rule may not be approved with a start date on or before an approved
  // rule of the same kind — rates are only ever added going forward. So a
  // tenant that has already been driven for a later month cannot be driven
  // for an earlier one: the social tax rate this pass needs could only be
  // dated before the one already approved, and that is refused. Said here,
  // where it is actionable, rather than eight steps in.
  const already = (await owner.ok('GET', '/payroll/rules')).filter(
    (r) => r.kind === 'SOCIAL_TAX' && r.status === 'APPROVED',
  )
  const governsPeriod = already.some(
    (r) =>
      r.effective_from <= `${PERIOD_CODE}-28` &&
      (r.effective_to === null || r.effective_to >= `${PERIOD_CODE}-01`),
  )
  if (already.length > 0 && !governsPeriod) {
    throw new Error(
      `this tenant has an approved social tax rate starting ${already[0].effective_from}, ` +
        `which is after ${PERIOD_CODE}; a rate cannot be back-dated behind an approved one, ` +
        'so this pass needs a fresh database — see "Against a disposable database" in the README',
    )
  }
  await ensureAccountingSetup(owner)
  await ensurePayrollRules(owner)

  // --- an employee of our own, with a salary and two days worked ----------
  const employee = await owner.ok('POST', '/hr/employees', {
    family_name: 'Tekshiruv',
    given_name: `Xodim ${RUN}`,
    hired_on: FIRST_OF_PERIOD,
  })
  await owner.ok('POST', `/payroll/employees/${employee.id}/compensation`, {
    amount: SALARY,
    effective_from: FIRST_OF_PERIOD,
    note: `Browser verification pass ${RUN}`,
  })
  for (const day of WORKED_DAYS) {
    await owner.ok('POST', '/hr/attendance', {
      employee_id: employee.id,
      on_date: day,
      code: 'WORKED',
      hours: '8',
    })
  }

  const browser = await chromium.launch({ channel: 'chrome', headless: true })
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  const page = await context.newPage()
  watch(page)

  // --- 1. the rules screen says what is missing ---------------------------
  begin('1 the rules screen names what is unapproved')
  await signIn(page, OWNER)
  await openPayroll(page, 'Rules')
  await owner.ok('POST', '/payroll/rules/seed', {})
  await page.reload({ waitUntil: 'networkidle' })
  await page.waitForTimeout(900)
  let text = (await page.locator('main').textContent()) || ''
  if (!/Some rates are not approved/.test(text)) {
    note('rules', 'the screen does not say that some rates are unapproved')
  }
  if (!/Social tax/.test(text)) {
    note('rules', 'the unapproved social tax is not named')
  }
  if (!/could not be confirmed/.test(text)) {
    note('rules', 'the screen does not explain why the social tax rate is missing (P2)')
  }
  if (!/A draft calculates nothing/.test(text)) {
    note('rules', 'a draft rate is not marked as calculating nothing')
  }
  // The seeded drafts must arrive as drafts, with their sources.
  const seeded = await owner.ok('GET', '/payroll/rules')
  if (seeded.some((r) => r.kind === 'SOCIAL_TAX')) {
    note('seeding', 'the seeder proposed a social tax rate, whose rate is unconfirmed')
  }
  if (seeded.some((r) => r.status === 'APPROVED' && r.approved_by_user_id === null)) {
    note('seeding', 'a rule is approved with nobody recorded as having approved it')
  }
  if (seeded.filter((r) => r.status === 'DRAFT').length === 0) {
    note('seeding', 'no draft rule was proposed')
  }
  if (seeded.some((r) => r.status === 'DRAFT' && !r.legal_reference)) {
    note('seeding', 'a draft was proposed with no legal reference')
  }
  await shot(page, '01-rules-blocked')

  // --- 2. the run calculates, and is blocked with the reason named --------
  begin('2 the run is blocked, and says why', 409)
  await openPayroll(page, 'Runs')
  // The month may already exist from an earlier pass; reuse it if so.
  const existing = (await owner.ok('GET', '/payroll/runs')).find(
    (r) => r.year === PERIOD.year && r.month === PERIOD.month && r.status !== 'CANCELLED',
  )
  const theRun =
    existing ||
    (await owner.ok('POST', '/payroll/runs', {
      year: PERIOD.year,
      month: PERIOD.month,
      working_days: WORKING_DAYS,
    }))
  if (existing && existing.working_days !== WORKING_DAYS) {
    note(
      'setup',
      `the month was already opened with ${existing.working_days} working days; expected ${WORKING_DAYS}`,
    )
  }
  await page.reload({ waitUntil: 'networkidle' })
  await page.waitForTimeout(900)
  // The panel opens on the newest run, which need not be this one — a
  // settled month from a previous pass sorts above it. Choose it the way a
  // person would, from the list on the left.
  await page.getByRole('button', { name: new RegExp(`^${PERIOD_CODE}`) }).first().click()
  await page.waitForLoadState('networkidle')
  await page.waitForTimeout(700)
  await clickAndExpect(page, 'Calculate', '/calculate')
  text = (await page.locator('main').textContent()) || ''
  if (!/This run cannot go further/.test(text)) {
    note('blocking', 'a run blocked on an unapproved rate does not say so on screen')
  }
  if (!/control working/.test(text)) {
    note('blocking', 'the screen does not say that blocking is the control rather than a fault')
  }
  const blockedRun = await owner.ok('GET', `/payroll/runs/${theRun.id}`)
  if (blockedRun.blocked_reasons.length === 0) {
    note('blocking', 'the run reports no blocking reason while a required rate is unapproved')
  }
  if (!blockedRun.blocked_reasons.join(' ').toLowerCase().includes('social')) {
    note(
      'blocking',
      `the blocking reason does not name the social tax: ${JSON.stringify(blockedRun.blocked_reasons)}`,
    )
  }
  // Approving must be withheld on the screen, and refused by the server.
  const approveButton = page.getByRole('button', { name: 'Approve', exact: true })
  if ((await approveButton.count()) > 0 && (await approveButton.first().isEnabled())) {
    note('blocking', 'the screen offers to approve a blocked run')
  }
  // Asked as the director, not the owner: the owner calculated this run and
  // would be refused for that reason first — which is correct, and would
  // hide whether the blocking refusal names the rate.
  const asDirector = client(await token(DIRECTOR))
  const refusedApprove = await asDirector.call('POST', `/payroll/runs/${theRun.id}/approve`, {})
  if (refusedApprove.status !== 409) {
    note('blocking', `approving a blocked run was not refused: ${refusedApprove.status}`)
  } else if (!JSON.stringify(refusedApprove.body).toLowerCase().includes('social')) {
    note('blocking', 'the refusal does not name the rule that caused it')
  }
  const refusedPost = await owner.call('POST', `/payroll/runs/${theRun.id}/post`, {})
  if (refusedPost.status !== 409) {
    note('blocking', `posting a blocked run was not refused: ${refusedPost.status}`)
  }
  await shot(page, '02-run-blocked')

  // --- 3. the figures are there even while it is blocked ------------------
  begin('3 the payslip is shown, and the employer cost is not a deduction')
  let slips = await owner.ok('GET', `/payroll/runs/${theRun.id}/payslips`)
  const mine = slips.find((s) => s.employee_id === employee.id)
  if (!mine) {
    note('payslip', 'the employee with an agreed salary got no payslip')
  } else if (Number(mine.gross) !== EXPECT.gross) {
    note('payslip', `gross is ${mine.gross}, expected ${EXPECT.gross} (2,000,000 x 2/4)`)
  }
  await shot(page, '03-payslips-blocked')

  // --- 4. approving the income tax leaves only the social tax -------------
  begin('4 approving one rate narrows the blockage')
  const incomeTax = seeded.find((r) => r.kind === 'INCOME_TAX' && r.status === 'DRAFT')
  if (incomeTax) {
    await owner.ok('POST', `/payroll/rules/${incomeTax.id}/approve`, {})
  }
  const inps = (await owner.ok('GET', '/payroll/rules')).find(
    (r) => r.kind === 'INPS' && r.status === 'DRAFT',
  )
  if (inps) await owner.ok('POST', `/payroll/rules/${inps.id}/approve`, {})
  const narrowed = await owner.ok('GET', `/payroll/runs/${theRun.id}`)
  if (narrowed.blocked_reasons.length !== 1) {
    note(
      'blocking',
      `after approving the income tax, ${narrowed.blocked_reasons.length} reasons remain; expected only the social tax`,
    )
  }

  // --- 5. the accountant enters the social tax rate themselves ------------
  //
  // Not a claim about the law: see the note at the top of this file. The
  // point being verified is that payroll proceeds once a rate has been
  // approved by somebody, and not before.
  begin('5 a rate entered and approved unblocks the run')
  const rulesNow = await owner.ok('GET', '/payroll/rules')
  let social = rulesNow.find(
    (r) =>
      r.kind === 'SOCIAL_TAX' &&
      r.status === 'APPROVED' &&
      r.effective_from <= blockedRun.period_end &&
      (r.effective_to === null || r.effective_to >= blockedRun.period_end),
  )
  if (!social) {
    const draft = await owner.ok('POST', '/payroll/rules', {
      kind: 'SOCIAL_TAX',
      method: 'PERCENT_OF_GROSS',
      rate: '12',
      effective_from: FIRST_OF_PERIOD,
      legal_reference:
        'ENTERED BY THE BROWSER VERIFICATION PASS, NOT CONFIRMED. The current ' +
        'social tax rate is an open question (OPEN_QUESTIONS P2). This record ' +
        'exists so the payroll sequence can be driven end to end.',
    })
    social = await owner.ok('POST', `/payroll/rules/${draft.id}/approve`, {})
  }

  // The defect this pass found, now a step of its own: every rate is
  // approved, so nothing is *missing* — but these payslips were worked out
  // before the rates existed and carry no tax at all. Approving them posted
  // a month of wages with nothing withheld. It must be refused until the
  // figures are produced again under the rules now in force.
  const stale = await owner.ok(`GET`, `/payroll/runs/${theRun.id}/payslips`)
  if (Number(stale.find((x) => x.employee_id === employee.id)?.employee_deductions) !== 0) {
    note('setup', 'the figures were expected to be stale at this point and are not')
  }
  const director0 = client(await token(DIRECTOR))
  const staleApproval = await director0.call('POST', `/payroll/runs/${theRun.id}/approve`, {})
  if (staleApproval.status !== 409) {
    note(
      'stale figures',
      `a run calculated before the rates were approved was approved anyway: ${staleApproval.status}`,
    )
  } else if (!JSON.stringify(staleApproval.body).includes('calculated')) {
    note('stale figures', 'the refusal does not say that the run must be calculated again')
  }
  const stalePost = await owner.call('POST', `/payroll/runs/${theRun.id}/post`, {})
  if (stalePost.status !== 409) {
    note('stale figures', `stale figures were posted to the ledger: ${stalePost.status}`)
  }

  await page.reload({ waitUntil: 'networkidle' })
  await page.waitForTimeout(900)
  const recalculated = await clickAndExpect(page, 'Calculate again', '/calculate')
  if (recalculated.status() >= 400) {
    note('recalculation', `recalculating was refused: ${recalculated.status()}`)
  }
  text = (await page.locator('main').textContent()) || ''
  if (/This run cannot go further/.test(text)) {
    note('blocking', 'the run is still reported as blocked after every rate was approved')
  }
  const unblocked = await owner.ok('GET', `/payroll/runs/${theRun.id}`)
  if (unblocked.blocked_reasons.length > 0) {
    note('blocking', `still blocked: ${JSON.stringify(unblocked.blocked_reasons)}`)
  }
  await shot(page, '04-run-unblocked')

  // --- 6. the figures, against the ones worked out by hand ----------------
  begin('6 the payslip matches the figures worked out by hand')
  slips = await owner.ok('GET', `/payroll/runs/${theRun.id}/payslips`)
  const slip = slips.find((s) => s.employee_id === employee.id)
  if (!slip) {
    note('payslip', 'the payslip vanished after recalculation')
  } else {
    const got = {
      gross: Number(slip.gross),
      withheld: Number(slip.employee_deductions),
      net: Number(slip.net),
      social: Number(slip.employer_contributions),
    }
    if (got.gross !== EXPECT.gross) note('payslip', `gross ${got.gross} ≠ ${EXPECT.gross}`)
    if (got.withheld !== EXPECT.withheld) {
      note('payslip', `withheld ${got.withheld} ≠ ${EXPECT.withheld}`)
    }
    if (got.net !== EXPECT.net) note('payslip', `net ${got.net} ≠ ${EXPECT.net}`)
    if (got.social !== EXPECT.social) {
      note('payslip', `employer contribution ${got.social} ≠ ${EXPECT.social}`)
    }
    // The arithmetic that must hold whatever the rates are.
    if (got.gross - got.withheld !== got.net) {
      note('payslip', 'net pay is not gross minus what was withheld')
    }
    const lines = slip.lines || []
    const socialLine = lines.find((l) => l.code === 'SOCIAL_TAX')
    if (!socialLine) {
      note('payslip', 'no social tax line on the payslip')
    } else if (socialLine.category !== 'EMPLOYER_CONTRIBUTION') {
      note('payslip', `the social tax is filed as ${socialLine.category}, not a contribution`)
    }
    const inpsLine = lines.find((l) => l.code === 'INPS')
    if (inpsLine && Number(inpsLine.amount) !== EXPECT.inps) {
      note('payslip', `INPS ${inpsLine.amount} ≠ ${EXPECT.inps}`)
    }
    if (lines.some((l) => l.code !== 'SALARY' && !l.statutory_rule_id)) {
      note('payslip', 'a statutory line does not name the rule version behind it')
    }
  }
  // And on the screen: the withheld column must not carry the employer cost.
  const screenText = (await page.locator('main').textContent()) || ''
  if (!/never reduces what somebody is paid/.test(screenText)) {
    note('payslip', 'the screen does not say that an employer contribution is not a deduction')
  }
  await shot(page, '05-payslip-figures')

  // --- 7. whoever calculated cannot approve -------------------------------
  begin('7 the person who calculated cannot approve', 409)
  const ownSelfApproval = await owner.call('POST', `/payroll/runs/${theRun.id}/approve`, {})
  if (ownSelfApproval.status !== 409) {
    note(
      'separation of duties',
      `the owner approved a run they calculated themselves: ${ownSelfApproval.status}`,
    )
  }
  const director = client(await token(DIRECTOR))
  const approved = await director.call('POST', `/payroll/runs/${theRun.id}/approve`, {})
  if (approved.status >= 400) {
    note('approval', `the director could not approve: ${approved.status} ${JSON.stringify(approved.body)}`)
  }

  // --- 8. posting moves the ledger by exactly the right amounts ----------
  begin('8 posting debits the gross plus the employer cost')
  const before = await balances(owner)
  const posted = await owner.call('POST', `/payroll/runs/${theRun.id}/post`, {})
  if (posted.status >= 400) {
    note('posting', `posting failed: ${posted.status} ${JSON.stringify(posted.body)}`)
  }
  const after = await balances(owner)
  const checks = [
    ['9420', movedDebit(before, after, '9420'), EXPECT.cost, 'the wage expense'],
    ['6710', movedCredit(before, after, '6710'), EXPECT.net, 'what is owed to staff'],
    ['6411', movedCredit(before, after, '6411'), EXPECT.incomeTaxToBudget, 'income tax payable'],
    ['6520.02', movedCredit(before, after, '6520.02'), EXPECT.inps, 'the INPS account'],
    ['6520.01', movedCredit(before, after, '6520.01'), EXPECT.social, 'social tax payable'],
  ]
  for (const [code, got, want, what] of checks) {
    if (Math.abs(got - want) > 0.005) {
      note('ledger', `${what} (${code}) moved ${got}, expected ${want}`)
    }
  }
  // One entry for the whole run, and it balances.
  const run2 = await owner.ok('GET', `/payroll/runs/${theRun.id}`)
  if (!run2.journal_entry_id) {
    note('posting', 'a posted run carries no journal entry')
  } else {
    const entry = await owner.ok('GET', `/accounting/journal/${run2.journal_entry_id}`)
    const debit = (entry.lines || []).reduce((sum, l) => sum + Number(l.debit || 0), 0)
    const credit = (entry.lines || []).reduce((sum, l) => sum + Number(l.credit || 0), 0)
    if (Math.abs(debit - credit) > 0.005) {
      note('posting', `the payroll entry does not balance: ${debit} vs ${credit}`)
    }
    if (Math.abs(debit - EXPECT.entryTotal) > 0.005) {
      note('posting', `the entry totals ${debit}, expected ${EXPECT.entryTotal}`)
    }
  }
  // Posting twice must be refused rather than doubling the cost.
  const twice = await owner.call('POST', `/payroll/runs/${theRun.id}/post`, {})
  if (twice.status !== 409) {
    note('posting', `a run was posted twice: ${twice.status}`)
  }
  await page.reload({ waitUntil: 'networkidle' })
  await page.waitForTimeout(1000)
  await shot(page, '06-posted')

  // --- 9. the reports agree with it --------------------------------------
  begin('9 the statements reconcile with the payroll entry')
  const balanceSheet = await owner.ok(
    'GET',
    `/reporting/balance-sheet?as_of=${unblocked.period_end}`,
  )
  if (balanceSheet.balances === false) {
    note('reporting', 'the balance sheet does not balance after payroll was posted')
  }
  const pnl = await owner.ok(
    'GET',
    `/reporting/profit-and-loss?from_date=${FIRST_OF_PERIOD}&to_date=${unblocked.period_end}`,
  )
  const pnlText = JSON.stringify(pnl)
  if (!pnlText.includes('9420')) {
    note('reporting', 'the wage expense account does not appear in the profit and loss')
  }

  // --- 10. paying settles the debt and recognises no new expense ---------
  begin('10 paying recognises no further expense')
  const beforePay = await balances(owner)
  const paid = await owner.call('POST', `/payroll/runs/${theRun.id}/pay`, {
    paid_on: unblocked.period_end,
  })
  if (paid.status >= 400) {
    note('paying', `paying failed: ${paid.status} ${JSON.stringify(paid.body)}`)
  }
  const afterPay = await balances(owner)
  if (Math.abs(movedDebit(beforePay, afterPay, '9420')) > 0.005) {
    note('paying', 'paying the staff moved the wage expense account')
  }
  if (Math.abs(movedDebit(beforePay, afterPay, '6710') - EXPECT.net) > 0.005) {
    note('paying', `paying cleared ${movedDebit(beforePay, afterPay, '6710')} of the staff debt, expected ${EXPECT.net}`)
  }

  // --- 11. an adjustment cannot be recorded without a reason -------------
  begin('11 an adjustment needs a reason', 422)
  const noReason = await owner.call('POST', `/payroll/runs/${theRun.id}/adjustments`, {
    employee_id: employee.id,
    kind: 'BONUS',
    amount: '50000',
    reason: '',
  })
  if (noReason.status < 400) {
    note('adjustments', 'an adjustment was accepted with no reason')
  }

  // --- 12. a settled run cannot be recalculated --------------------------
  begin('12 a settled run is history', 409)
  const recalc = await owner.call('POST', `/payroll/runs/${theRun.id}/calculate`, {})
  if (recalc.status !== 409) {
    note('immutability', `a paid run was recalculated: ${recalc.status}`)
  }

  // --- 13. the cashier is refused the module -----------------------------
  begin('13 the cashier may not see anybody’s pay')
  const cashier = client(await token(CASHIER))
  for (const pathname of ['/payroll/runs', '/payroll/rules']) {
    const refused = await cashier.call('GET', pathname)
    if (refused.status !== 403) {
      note('permissions', `the cashier reached ${pathname}: ${refused.status}`)
    }
  }
  await signIn(page, CASHIER)
  await page.goto(`${WEB}/payroll`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(800)
  const cashierText = (await page.locator('main').textContent()) || ''
  if (!/do not have permission/i.test(cashierText)) {
    note('permissions', 'the cashier opening Payroll is not told they may not see it')
  }
  await shot(page, '07-cashier-refused')

  // --- 14. phone width, in Uzbek -----------------------------------------
  begin('14 phone width, in Uzbek')
  // Signed in in English first: the sign-in page itself is translated, so
  // asking for the e-mail field by its English label after switching the
  // locale finds nothing.
  await signIn(page, OWNER)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.evaluate(() => {
    try {
      localStorage.setItem('erp.locale', 'uz')
    } catch {}
  })
  await page.reload({ waitUntil: 'networkidle' })
  await page.getByRole('button', { name: '☰' }).click()
  await page.waitForTimeout(300)
  await openPayroll(page, null, /^Ish haqi/)
  await page.waitForTimeout(1000)
  const over = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  )
  if (over > 1) note('horizontal overflow', `payroll at phone width: ${over}px`)
  const uz = (await page.locator('main').textContent()) || ''
  if (/Open a month|Calculate|Payslips|Net pay/.test(uz)) {
    note('language', 'English is showing with the interface set to Uzbek')
  }
  await shot(page, '08-phone-uz')

  // --- tidy up: end the employment this pass created ---------------------
  begin('tidy up')
  await owner.ok('POST', `/hr/employees/${employee.id}/end`, {
    ended_on: TODAY,
    reason: `End of browser pass ${RUN}`,
  })

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
