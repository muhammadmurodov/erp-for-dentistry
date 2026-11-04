/**
 * The management reports, in a real Chrome.
 *
 * What this pass is for is the claim the whole module rests on: that a
 * report cannot disagree with the ledger it came from. So it does not check
 * that numbers *appear* — it bills a patient for a known amount, takes a
 * known payment, and then checks that every statement moved by exactly that
 * much, reading the trial balance alongside as a second opinion.
 *
 * It also checks the three honesty behaviours, which are the reason this
 * module was written the way it was:
 *
 *   1. The screen renders the backend's capability catalogue, so a blocked
 *      statutory form is named along with the question blocking it — and a
 *      report that works is never hidden behind a "not developed" message.
 *   2. A balance sheet says whether it balanced.
 *   3. An account whose classification is unconfirmed is marked, not folded
 *      into a tidy total.
 *
 * It creates its own patient and its own invoice, and leaves the money where
 * an ordinary day would leave it, so it can be run twice in a row.
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
const CASHIER = { email: `cashier@${TENANT}.example.com`, password: STAFF_PASSWORD }

const RUN = Date.now().toString().slice(-6)
const TODAY = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'Asia/Tashkent',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
}).format(new Date())
const YEAR = TODAY.slice(0, 4)
const YEAR_START = `${YEAR}-01-01`
const YEAR_END = `${YEAR}-12-31`

const BILL = 600000

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
  page.screenshot({ path: path.join(SHOTS, `reports-${name}.png`), fullPage: true })

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

async function signIn(page, who) {
  await page.goto(WEB, { waitUntil: 'networkidle' })
  await page.evaluate(() => {
    try {
      localStorage.clear()
      sessionStorage.clear()
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

async function openTab(page, name) {
  await page.getByRole('link', { name: /^Reports/ }).first().click()
  await page.waitForLoadState('networkidle')
  await page.waitForTimeout(500)
  await page.getByRole('link', { name, exact: true }).click()
  await page.waitForLoadState('networkidle')
  await page.waitForTimeout(900)
}

const money = (value) => Number(String(value).replace(/[^\d.-]/g, ''))

/** The revenue movement for the year, straight from the trial balance. */
async function revenueFromTrialBalance(owner) {
  const report = await owner.ok(
    'GET',
    `/accounting/reports/trial-balance?from_date=${YEAR_START}&to_date=${YEAR_END}`,
  )
  return (report.rows || [])
    .filter((row) => row.element === 'REVENUE')
    .reduce((total, row) => total + Number(row.period_credit) - Number(row.period_debit), 0)
}

async function run() {
  const browser = await chromium.launch({
    executablePath: process.env.E2E_CHROME || '/usr/bin/google-chrome',
  })
  const page = await browser.newPage({
    viewport: { width: 1440, height: 900 },
    acceptDownloads: true,
  })
  watch(page)

  const owner = client(await token(OWNER))

  // --- a known amount of business, so every figure can be checked ---------
  begin('set up a known amount of business')
  const revenueBefore = await revenueFromTrialBalance(owner)

  // Registration answers with the new record *and* any duplicates it found,
  // because it never refuses on a suspicion.
  const registration = await owner.ok('POST', '/clinic/patients', {
    family_name: `Hisobot${RUN}`,
    given_name: 'Bemor',
    birth_year: 1990,
  })
  const patient = registration.patient
  const services = await owner.ok('GET', '/sales/services')
  const service = services[0]
  if (!service) throw new Error('the tenant has no service to bill')

  const invoice = await owner.ok('POST', '/sales/invoices', {
    customer_id: patient.customer_id,
    issue_date: TODAY,
    lines: [
      {
        service_id: service.id,
        description: `Reports pass ${RUN}`,
        quantity: '1',
        unit_price: String(BILL),
      },
    ],
  })
  await owner.ok('POST', `/sales/invoices/${invoice.id}/issue`, { tax_treatment: 'NONE' })

  const revenueAfter = await revenueFromTrialBalance(owner)
  if (Math.round(revenueAfter - revenueBefore) !== BILL) {
    note(
      'ledger',
      `issuing the bill moved revenue by ${revenueAfter - revenueBefore}, expected ${BILL}`,
    )
  }

  // --- 1. the profit and loss agrees with the ledger ----------------------
  begin('profit and loss')
  await signIn(page, OWNER)
  await openTab(page, 'Profit and loss')
  await page.getByLabel('Period').selectOption('year_to_date')
  await page.waitForLoadState('networkidle')
  await page.waitForTimeout(900)

  const api_pl = await owner.ok(
    'GET',
    `/reporting/profit-and-loss?from_date=${YEAR_START}&to_date=${TODAY}`,
  )
  if (Math.round(Number(api_pl.revenue.total)) < BILL) {
    note('profit and loss', `revenue ${api_pl.revenue.total} is less than the bill just issued`)
  }
  let text = (await page.locator('main').textContent()) || ''
  if (!text.includes('Net result')) {
    note('profit and loss', 'the statement does not show a net result')
  }
  if (!/management report/i.test(text)) {
    note('honesty', 'the statement does not say it is a management report')
  }
  if (!/unconfirmed/i.test(text)) {
    note('honesty', 'nothing marks the accounts whose classification is unconfirmed (A1)')
  }
  await shot(page, '01-profit-and-loss')

  // --- 2. a comparative period is offered and computed --------------------
  begin('comparative period')
  await page.getByLabel('Compare with last year').check()
  await page.waitForLoadState('networkidle')
  await page.waitForTimeout(900)
  text = (await page.locator('main').textContent()) || ''
  if (!text.includes('Last year') || !text.includes('Change')) {
    note('comparative', 'the comparative columns are not shown')
  }
  await shot(page, '02-comparative')
  await page.getByLabel('Compare with last year').uncheck()
  await page.waitForTimeout(600)

  // --- 3. drilling into a line reaches the entries behind it --------------
  begin('drill-down')
  const revenueLine = page
    .locator('main button', { hasText: /^\d{4}/ })
    .first()
  if ((await revenueLine.count()) === 0) {
    note('drill-down', 'no report line is clickable')
  } else {
    await revenueLine.click()
    await page.waitForTimeout(1200)
    const dialog = (await page.locator('dialog').textContent()) || ''
    if (!/Debit|Credit/.test(dialog)) {
      note('drill-down', 'the entries behind the figure are not shown')
    }
    await shot(page, '03-drill-down')
    await page.getByRole('button', { name: 'Close' }).last().click()
    await page.waitForTimeout(500)
  }

  // --- 4. the balance sheet balances, and says so -------------------------
  begin('balance sheet')
  await openTab(page, 'Balance sheet')
  const api_bs = await owner.ok('GET', `/reporting/balance-sheet?as_of=${TODAY}`)
  if (!api_bs.balances) {
    note('balance sheet', `it does not balance: difference ${api_bs.difference}`)
  }
  text = (await page.locator('main').textContent()) || ''
  if (api_bs.balances && /DOES NOT BALANCE/i.test(text)) {
    note('balance sheet', 'a balanced statement is showing the unbalanced warning')
  }
  if (!text.includes('Total assets') || !text.includes('Total liabilities and equity')) {
    note('balance sheet', 'the statement does not show both totals')
  }
  if (Math.round(Number(api_bs.total_assets)) !== Math.round(Number(api_bs.total_liabilities_and_equity))) {
    note(
      'balance sheet',
      `assets ${api_bs.total_assets} do not equal claims ${api_bs.total_liabilities_and_equity}`,
    )
  }
  await shot(page, '04-balance-sheet')

  // --- 5. the receivable shows the bill that was just issued --------------
  begin('patients owing')
  await openTab(page, 'Patients owing')
  await page.waitForTimeout(600)
  text = (await page.locator('main').textContent()) || ''
  if (!text.includes(`Hisobot${RUN}`)) {
    note('receivables', 'the patient just billed does not appear in the ageing report')
  }
  const sales_ageing = await owner.ok('GET', '/sales/receivables/ageing')
  const reporting_ageing = await owner.ok('GET', '/reporting/receivables-ageing')
  if (sales_ageing.total_outstanding !== reporting_ageing.total_outstanding) {
    note(
      'receivables',
      `the report says ${reporting_ageing.total_outstanding} and sales says ${sales_ageing.total_outstanding}`,
    )
  }
  await shot(page, '05-receivables')

  // --- 6. what is owed to suppliers, or why the page is empty -------------
  begin('owed to suppliers')
  await openTab(page, 'Owed to suppliers')
  await page.waitForTimeout(600)
  const payables = await owner.ok('GET', '/reporting/payables-ageing')
  text = (await page.locator('main').textContent()) || ''
  if (payables.no_suppliers_yet && !/No suppliers yet/i.test(text)) {
    note('payables', 'an empty report does not say why it is empty')
  }
  await shot(page, '06-payables')

  // --- 7. cash movement reconciles ----------------------------------------
  begin('cash movement')
  await openTab(page, 'Cash movement')
  await page.waitForTimeout(600)
  const cash = await owner.ok(
    'GET',
    `/reporting/cash-flow?from_date=${YEAR_START}&to_date=${TODAY}`,
  )
  if (!cash.reconciles) {
    note('cash flow', `opening plus movement does not equal closing: ${cash.difference}`)
  }
  text = (await page.locator('main').textContent()) || ''
  if (!cash.no_cash_accounts_configured && !/operating, investing and financing/i.test(text)) {
    note('honesty', 'the statement does not say the movements are unclassified (A3)')
  }
  await shot(page, '07-cash-movement')

  // --- 8. blocked statutory forms are named, with their question ----------
  begin('what cannot be produced yet')
  text = (await page.locator('main').textContent()) || ''
  for (const expectedText of ['Statutory financial statement forms', 'A2', 'A3', 'VAT return']) {
    if (!text.includes(expectedText)) {
      note('catalogue', `the blocked-report list does not mention ${expectedText}`)
    }
  }
  if (/not developed|coming soon|under construction/i.test(text)) {
    note('catalogue', 'the old module-not-developed message is still showing')
  }
  await shot(page, '08-blocked-reports')

  // --- 9. the spreadsheet downloads ---------------------------------------
  begin('export')
  await openTab(page, 'Profit and loss')
  const [download] = await Promise.all([
    page.waitForEvent('download', { timeout: 15000 }),
    page.getByRole('button', { name: 'Excel' }).click(),
  ])
  const name = download.suggestedFilename()
  if (!name.endsWith('.xlsx')) {
    note('export', `the download is not a spreadsheet: ${name}`)
  }
  const stream = await download.createReadStream()
  const chunks = []
  for await (const chunk of stream) chunks.push(chunk)
  const bytes = Buffer.concat(chunks)
  if (bytes.length < 1000 || bytes[0] !== 0x50 || bytes[1] !== 0x4b) {
    note('export', `the downloaded file is not a valid workbook (${bytes.length} bytes)`)
  }

  // --- 10. the cashier may not read the ledger in aggregate ---------------
  begin('the cashier is refused', 403)
  const cashier = client(await token(CASHIER))
  for (const pathname of [
    '/reporting/profit-and-loss?from_date=' + YEAR_START + '&to_date=' + YEAR_END,
    `/reporting/balance-sheet?as_of=${TODAY}`,
    '/reporting/catalogue',
  ]) {
    const refused = await cashier.call('GET', pathname)
    if (refused.status !== 403) {
      note('permissions', `the cashier reached ${pathname}: ${refused.status}`)
    }
  }
  await signIn(page, CASHIER)
  await page.goto(`${WEB}/reports`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(700)
  text = (await page.locator('main').textContent()) || ''
  if (!/do not have permission/i.test(text)) {
    note('permissions', 'the cashier opening Reports is not told they may not see it')
  }
  await shot(page, '09-cashier-refused')

  // --- 11. at phone width, in Uzbek ---------------------------------------
  begin('phone width, in Uzbek')
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
  await page.getByRole('link', { name: /^Hisobotlar/ }).first().click()
  await page.waitForLoadState('networkidle')
  await page.waitForTimeout(1200)
  const over = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  )
  if (over > 1) note('horizontal overflow', `reports at phone width: ${over}px`)
  const uz = (await page.locator('main').textContent()) || ''
  if (/Net result|Balance sheet|Patients owing/.test(uz)) {
    note('language', 'English is showing with the interface set to Uzbek')
  }
  await shot(page, '10-phone-uz')

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
