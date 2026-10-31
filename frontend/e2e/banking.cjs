/**
 * Drives the Phase 3d banking screens in a real Chrome, the way phases 1 to
 * 3b were verified: click every control, inspect every screen, and fail on
 * any console error, page error, failed request or horizontal overflow.
 */
const path = require('path')
const { chromium } = require('playwright')

const WEB = process.env.E2E_WEB || 'http://localhost:5173'
const SHOTS = process.env.E2E_SHOTS || path.join(__dirname, 'shots')
const STATEMENT = path.join(__dirname, 'statement.csv')

const API = process.env.E2E_API || 'http://localhost:8000/api/v1'
/** The tenant this pass drives; see e2e/README.md for how to create it. */
const TENANT = process.env.E2E_TENANT || 'verify-3d'
const OWNER = { email: `owner@${TENANT}.example.com`, password: 'verify-owner-password-1' }
const ACCOUNTANT = {
  email: `accountant@${TENANT}.example.com`,
  password: 'verify-staff-password-1',
}

/** A code and a statement unique to this run, so the pass is repeatable. */
const RUN = Date.now().toString().slice(-6)
const BANK_CODE = `BANK-${RUN}`

/**
 * Setup, over the API: a card payment for the queue to be matched against.
 * Phase 3 is already browser-verified, so it is not re-driven here; what is
 * under test is every banking screen below.
 */
async function seedPayment() {
  const token = await fetch(`${API}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(OWNER),
  })
    .then((r) => r.json())
    .then((b) => b.access_token)
  const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }
  const customer = await fetch(`${API}/sales/customers`, { headers }).then((r) => r.json())
  const payment = await fetch(`${API}/sales/payments`, {
    method: 'POST',
    headers,
    body: JSON.stringify({
      customer_id: customer[0].id,
      amount: '500000',
      method: 'CARD',
      received_on: '2026-10-01',
    }),
  }).then((r) => r.json())
  if (!payment.number) throw new Error(`could not seed a card payment: ${JSON.stringify(payment)}`)
  return payment.number
}
const AUDITOR = { email: `auditor@${TENANT}.example.com`, password: 'verify-staff-password-1' }

const problems = []
let step = 'startup'

function note(kind, detail) {
  problems.push(`[${step}] ${kind}: ${detail}`)
}

/** Select the option whose text starts with `prefix`. */
async function selectByPrefix(page, selector, prefix) {
  const value = await page.$eval(
    selector,
    (select, wanted) => {
      const option = [...select.options].find((o) => o.textContent.trim().startsWith(wanted))
      if (!option) throw new Error(`no option starting with ${wanted}`)
      return option.value
    },
    prefix,
  )
  await page.selectOption(selector, value)
}

async function shot(page, name) {
  await page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: true })
}

/** Bug 23's mechanical form: nothing may push the page wider than the viewport. */
async function assertNoOverflow(page, where) {
  const over = await page.evaluate(() => {
    const d = document.documentElement
    return d.scrollWidth - d.clientWidth
  })
  if (over > 1) note('horizontal overflow', `${where}: page is ${over}px wider than the viewport`)
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
  await page.getByRole('button', { name: /sign in/i }).click()
  await page.waitForSelector('nav', { timeout: 15000 })
  await page.waitForTimeout(500)
}

async function run() {
  const browser = await chromium.launch({ channel: 'chrome', headless: true })
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  const page = await context.newPage()

  page.on('console', (message) => {
    if (message.type() === 'error') note('console error', message.text())
    if (message.type() === 'warning' && !/React Router|Download the React/i.test(message.text())) {
      note('console warning', message.text())
    }
  })
  page.on('pageerror', (error) => note('page error', error.message))
  page.on('requestfailed', (request) => {
    if (!/favicon/.test(request.url())) {
      note('request failed', `${request.method()} ${request.url()} — ${request.failure()?.errorText}`)
    }
  })
  page.on('response', (response) => {
    if (response.status() >= 500) note('server error', `${response.status()} ${response.url()}`)
  })

  // ---------------------------------------------------------- 1. the queue
  step = '0 seed a card payment for the queue to match against'
  const paymentNumber = await seedPayment()
  console.log(`seeded ${paymentNumber}; this run's bank account is ${BANK_CODE}`)

  step = '1 sign in as the accountant'
  await signIn(page, ACCOUNTANT)
  await shot(page, '00-dashboard')

  step = '2 open Banking from the navigation'
  await page.locator('nav a[href="/banking"]').click()
  await page.waitForURL(/\/banking\/reconcile/, { timeout: 10000 })
  await page.waitForTimeout(800)
  await shot(page, '01-reconcile-no-accounts')
  await assertNoOverflow(page, 'reconciliation, no accounts')
  const firstRun = await page.getByText('No bank accounts').first().isVisible().catch(() => false)
  console.log(firstRun ? 'tenant has no bank accounts yet' : 'tenant already holds bank accounts')

  // ------------------------------------------------------- 2. bank account
  step = '3 bank accounts tab'
  await page.locator('main nav a[href$="accounts"]').click()
  await page.waitForTimeout(600)
  await shot(page, '02-accounts-empty')

  step = '4 add a bank account'
  await page.getByRole('button', { name: 'Add bank account' }).first().click()
  await page.waitForTimeout(400)
  await shot(page, '03-account-dialog')
  await assertNoOverflow(page, 'add bank account dialog')
  await page.locator('#bank-code').fill(BANK_CODE)
  await page.locator('#bank-name').fill('Hisob-kitob hisobraqami')
  await page.locator('#bank-ledger').selectOption('5110.01')
  await page.locator('#bank-bank').fill('AKB Demo Bank')
  await page.locator('#bank-mfo').fill('00414')
  await page.locator('#bank-number').fill('20208000900123456789')
  await page.getByRole('button', { name: 'Save' }).click()
  await page.waitForTimeout(1200)
  await shot(page, '04-accounts-created')
  await assertNoOverflow(page, 'bank accounts list')

  step = '5 the account number must never reach the screen'
  const html = await page.content()
  if (html.includes('20208000900123456789')) {
    note('credential leak', 'the full account number is present in the rendered page')
  }
  if (!html.includes('****************6789')) {
    note('missing mask', 'the masked account number is not shown')
  }

  step = '6 column mapping dialog'
  await page.getByRole('button', { name: 'Column mapping' }).first().click()
  await page.waitForTimeout(700)
  await shot(page, '05-column-mapping')
  await assertNoOverflow(page, 'column mapping dialog')
  for (const field of ['value_date', 'credit', 'debit', 'description']) {
    if (!(await page.locator(`#map-${field}`).isVisible())) {
      note('missing mapping field', field)
    }
  }
  await page.getByRole('button', { name: 'Cancel' }).click()
  await page.waitForTimeout(400)

  // ---------------------------------------------------------- 3. the import
  step = '7 statements tab'
  await page.locator('main nav a[href$="statements"]').click()
  await page.waitForTimeout(700)
  await shot(page, '06-statements-empty')
  await assertNoOverflow(page, 'statements, empty')

  step = '8 import the statement'
  await page.getByRole('button', { name: 'Import statement' }).first().click()
  await page.waitForTimeout(400)
  await shot(page, '07-upload-dialog')
  await selectByPrefix(page, '#import-account', BANK_CODE)
  await page.locator('#import-file').setInputFiles(STATEMENT)
  await page
    .locator('dialog')
    .getByRole('button', { name: 'Import statement' })
    .click()
  await page.waitForTimeout(1800)
  await shot(page, '08-import-result')
  await assertNoOverflow(page, 'import result')
  const resultText = await page.locator('main [role="status"], main').first().innerText()
  if (!/Imported:\s*2/.test(resultText)) {
    note('wrong import count', `expected 2 imported, screen says: ${resultText.slice(0, 400)}`)
  }
  if (/Rejected:\s*[1-9]/.test(resultText)) {
    note('unexpected rejection', 'the totals footer or a data row was rejected')
  }

  step = '9 re-import the same file'
  await page.getByRole('button', { name: 'Import statement' }).first().click()
  await page.waitForTimeout(400)
  await selectByPrefix(page, '#import-account', BANK_CODE)
  await page.locator('#import-file').setInputFiles(STATEMENT)
  await page.locator('dialog').getByRole('button', { name: 'Import statement' }).click()
  await page.waitForTimeout(1800)
  await shot(page, '09-reimport-all-duplicates')
  const again = await page.locator('body').innerText()
  if (!/Already held:\s*2/.test(again)) {
    note('duplicates not suppressed', `re-import did not report 2 already held: ${again.slice(0, 400)}`)
  }

  // ------------------------------------------------------------- 4. match
  step = '10 the queue now holds the imported lines'
  await page.locator('main nav a[href$="reconcile"]').click()
  await page.waitForTimeout(1000)
  await selectByPrefix(page, 'select[aria-label="Bank account"]', BANK_CODE)
  await page.waitForTimeout(900)
  await shot(page, '10-queue')
  await assertNoOverflow(page, 'reconciliation queue')
  const rows = await page.locator('tbody tr').count()
  if (rows < 2) note('queue is short', `expected at least 2 statement lines, found ${rows}`)

  step = '11 select the settlement line'
  await page.locator('tbody tr', { hasText: '495' }).first().click()
  await page.waitForTimeout(1000)
  await shot(page, '11-line-selected')
  await assertNoOverflow(page, 'line detail')

  step = '12 open the match dialog'
  await page.getByRole('button', { name: 'Match', exact: true }).first().click()
  await page.waitForTimeout(1200)
  await shot(page, '12-match-dialog')
  await assertNoOverflow(page, 'match dialog')

  step = '13 choose the card payment and declare the fee'
  const candidate = page.locator('dialog tbody tr').first()
  if ((await page.locator('dialog tbody tr').count()) === 0) {
    note('no candidates', 'the card payment was not offered as a match candidate')
  } else {
    const offered = await page.locator('dialog tbody tr').first().innerText()
    if (!offered.includes(paymentNumber)) {
      note('wrong candidate', `expected ${paymentNumber}, the dialog offers: ${offered.replace(/\n/g, ' | ')}`)
    }
    await candidate.locator('input[type="checkbox"]').check()
    await page.waitForTimeout(300)
    await page.locator('#match-commission').fill('5000')
    await page.locator('#match-note').fill('Oktyabr ekvayring hisob-kitobi')
    await page.waitForTimeout(400)
    await shot(page, '13-match-filled')
    await page.locator('dialog').getByRole('button', { name: 'Match', exact: true }).click()
    await page.waitForTimeout(2000)
    await shot(page, '14-matched')
    await assertNoOverflow(page, 'after matching')
    const after = await page.locator('body').innerText()
    if (!/Line reconciled/i.test(after)) {
      note('match not confirmed', `no success notice after matching: ${after.slice(0, 400)}`)
    }
  }

  // --------------------------------------------------------- 5. set aside
  step = '14 set the bank charge aside'
  await page.locator('main nav a[href$="reconcile"]').click()
  await page.waitForTimeout(1000)
  await selectByPrefix(page, 'select[aria-label="Bank account"]', BANK_CODE)
  await page.waitForTimeout(900)
  const outRow = page.locator('tbody tr', { hasText: 'Out' }).first()
  if ((await page.locator('tbody tr').count()) > 0) {
    await outRow.click()
    await page.waitForTimeout(900)
    const setAside = page.getByRole('button', { name: 'Set aside' })
    if (await setAside.isVisible()) {
      await setAside.click()
      await page.waitForTimeout(400)
      await shot(page, '15-set-aside-dialog')
      await page.locator('#ignore-reason').fill("Bankning o'z komissiyasi; alohida provodka qilinadi")
      await page.locator('dialog').getByRole('button', { name: 'Confirm' }).click()
      await page.waitForTimeout(1600)
      await shot(page, '16-set-aside')
      const aside = await page.locator('body').innerText()
      if (!/Line set aside/i.test(aside)) note('set aside not confirmed', aside.slice(0, 300))
    } else {
      note('no set aside button', 'the accountant cannot set a line aside')
    }
  }

  step = '15 bring it back'
  await page.selectOption('select[aria-label="Status"]', 'IGNORED')
  await page.waitForTimeout(1000)
  await page.locator('tbody tr').first().click()
  await page.waitForTimeout(900)
  await shot(page, '17-ignored-line')
  const bringBack = page.getByRole('button', { name: 'Bring back' })
  if (await bringBack.isVisible()) {
    await bringBack.click()
    await page.waitForTimeout(1600)
    await shot(page, '18-brought-back')
  } else {
    note('no bring back button', 'a set-aside line cannot be returned to the queue')
  }

  // ------------------------------------------------- 6. the other locales
  for (const [locale, label] of [
    ['uz', "O‘zbekcha"],
    ['ru', 'Русский'],
  ]) {
    step = `16 the banking screens in ${locale}`
    await page.selectOption('#locale-switcher', locale)
    await page.waitForTimeout(900)
    await shot(page, `19-${locale}-reconcile`)
    await assertNoOverflow(page, `reconciliation in ${locale}`)
    // Navigate the tabs by position: their labels are translated.
    const tabs = page.locator('main nav ul li a')
    const count = await tabs.count()
    for (let i = 0; i < count; i += 1) {
      await tabs.nth(i).click()
      await page.waitForTimeout(800)
      await shot(page, `19-${locale}-tab${i}`)
      await assertNoOverflow(page, `${locale} tab ${i}`)
    }
    if (label) void label
  }

  step = '17 narrow viewport'
  await page.selectOption('#locale-switcher', 'uz')
  await page.waitForTimeout(600)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.waitForTimeout(800)
  await shot(page, '20-uz-narrow')
  await assertNoOverflow(page, 'reconciliation at phone width')
  await page.setViewportSize({ width: 1440, height: 900 })

  // ------------------------------------------------------- 7. the auditor
  step = '18 an auditor may read but not reconcile'
  await context.clearCookies()
  const auditorPage = await context.newPage()
  auditorPage.on('pageerror', (error) => note('page error (auditor)', error.message))
  auditorPage.on('console', (m) => {
    if (m.type() === 'error') note('console error (auditor)', m.text())
  })
  await signIn(auditorPage, AUDITOR)
  await auditorPage.goto(`${WEB}/banking/reconcile`, { waitUntil: 'networkidle' })
  await auditorPage.waitForTimeout(1500)
  await auditorPage.screenshot({ path: path.join(SHOTS, '21-auditor-queue.png'), fullPage: true })
  const auditorRows = await auditorPage.locator('tbody tr').count()
  if (auditorRows === 0) note('auditor sees nothing', 'the auditor cannot read the statement queue')
  await auditorPage.locator('tbody tr').first().click()
  await auditorPage.waitForTimeout(1200)
  await auditorPage.screenshot({ path: path.join(SHOTS, '22-auditor-detail.png'), fullPage: true })
  for (const forbidden of ['Match', 'Set aside', 'Bring back']) {
    if (await auditorPage.getByRole('button', { name: forbidden, exact: true }).isVisible().catch(() => false)) {
      note('auditor over-privileged', `the auditor is offered "${forbidden}"`)
    }
  }
  await auditorPage.goto(`${WEB}/banking/accounts`, { waitUntil: 'networkidle' })
  await auditorPage.waitForTimeout(1200)
  await auditorPage.screenshot({ path: path.join(SHOTS, '23-auditor-accounts.png'), fullPage: true })
  if (await auditorPage.getByRole('button', { name: 'Add bank account' }).isVisible().catch(() => false)) {
    note('auditor over-privileged', 'the auditor is offered "Add bank account"')
  }

  await browser.close()

  console.log('\n=== problems ===')
  if (problems.length === 0) console.log('none')
  else problems.forEach((p) => console.log(' -', p))
  console.log(`\n${problems.length} problem(s)`)
}

run().catch((error) => {
  console.log(`\nFAILED at step: ${step}`)
  console.log(error.message)
  console.log('\n=== problems so far ===')
  problems.forEach((p) => console.log(' -', p))
  process.exit(1)
})
