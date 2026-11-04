/**
 * The store cupboard, in a real Chrome.
 *
 * What this pass exists to show is the three rules no unit test can show
 * reaching a person:
 *
 *   1. **Stock arrives through the delivery, not by hand.** The screen
 *      offers no way to type stock into existence; it appears on the shelf
 *      because a goods receipt in Purchases named the material.
 *   2. **A shelf cannot go short.** Taking more than is there is refused,
 *      and the refusal says what is actually on the shelf.
 *   3. **Counting and approving are two people.** The approve button is not
 *      offered to whoever did the counting, and the server refuses it too.
 *
 * It also checks the one thing the whole module is honest about: while the
 * valuation method is undeclared, the quantities are real and the value is
 * stated as unknown rather than invented.
 *
 * It reads the trial balance around the delivery and the consumption,
 * because the central claim of this module is that moving a quantity posts
 * nothing at all.
 *
 * It creates its own material with a unique code and leaves the shelf as it
 * found it, so it can be run twice in a row.
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
  page.screenshot({ path: path.join(SHOTS, `stock-${name}.png`), fullPage: true })

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
  // The sidebar link's accessible name carries the module's status —
  // "Inventory Partly working" — so this matches on the name, not the label.
  await page.getByRole('link', { name: /^Inventory/ }).first().click()
  await page.waitForLoadState('networkidle')
  await page.waitForTimeout(400)
  await page.getByRole('link', { name, exact: true }).click()
  await page.waitForLoadState('networkidle')
  await page.waitForTimeout(900)
}

/** Closing balances by account code, to prove nothing posted. */
async function balances(owner) {
  const year = TODAY.slice(0, 4)
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

function same(before, after, code) {
  const a = before[code] || { debit: 0, credit: 0 }
  const b = after[code] || { debit: 0, credit: 0 }
  return a.debit === b.debit && a.credit === b.credit
}

async function warningBanner(page, startsWith) {
  const banners = await page.getByRole('status').allTextContents()
  return banners.find((line) => line.trim().startsWith(startsWith)) || ''
}

async function run() {
  const browser = await chromium.launch({
    executablePath: process.env.E2E_CHROME || '/usr/bin/google-chrome',
  })
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
  watch(page)

  const owner = client(await token(OWNER))

  // --- setup, through the API, as the clinic would have done once ---------
  begin('setup')
  const locations = await owner.ok('GET', '/inventory/locations')
  let shelf = locations.find((row) => row.is_default) || locations[0]
  if (!shelf) {
    shelf = await owner.ok('POST', '/inventory/locations', {
      code: 'MAIN',
      name: 'Store cupboard',
      is_default: true,
    })
  }

  const material = await owner.ok('POST', '/inventory/items', {
    code: `E2E-${RUN}`,
    name: `Composite A2 ${RUN}`,
    unit: 'box',
    reorder_level: '4',
  })

  // --- 1. the material is low before anything has been bought -------------
  begin('low stock before buying')
  await signIn(page, OWNER)
  await openTab(page, 'Stock')
  let text = (await page.locator('main').textContent()) || ''
  let lowBanner = await warningBanner(page, 'Running low')
  if (!lowBanner) {
    note('low stock', 'a material with a reorder level and no stock is not reported as low')
  } else if (!lowBanner.includes(material.name)) {
    note('low stock', `${material.name} missing from the low-stock warning`)
  }
  await shot(page, '01-low-before-buying')

  // --- 2. there is no way to put stock on the shelf from this screen ------
  begin('no hand-written stock')
  const buttons = await page.getByRole('button').allTextContents()
  if (buttons.some((label) => /^(receive|add stock|receipt)$/i.test(label.trim()))) {
    note('the wrong door', 'the stock screen offers a way to create stock by hand')
  }

  // --- 3. a delivery puts it on the shelf, and posts nothing --------------
  begin('delivery')
  const before = await balances(owner)
  const supplier =
    (await owner.ok('GET', '/purchasing/suppliers'))[0] ||
    (await owner.ok('POST', '/purchasing/suppliers', { name: `Depot ${RUN}` }))
  const receipt = await owner.ok('POST', '/purchasing/receipts', {
    supplier_id: supplier.id,
    received_on: TODAY,
    lines: [
      {
        description: `Composite A2 ${RUN}`,
        quantity: '10',
        unit: 'box',
        item_id: material.id,
        location_id: shelf.id,
      },
    ],
  })
  const after = await balances(owner)
  for (const code of ['1010', '6010', '5010']) {
    if (!same(before, after, code)) {
      note('posting', `account ${code} moved when a delivery was recorded, and should not`)
    }
  }

  // The API first, so a failure here is told apart from one in the screen.
  const shelved = await owner.ok('GET', `/inventory/stock?item_id=${material.id}`)
  if (Number(shelved[0]?.quantity) !== 10) {
    note('shelf', `the delivery did not reach the shelf: ${JSON.stringify(shelved)}`)
  }

  await page.reload({ waitUntil: 'networkidle' })
  await openTab(page, 'Stock')
  text = (await page.locator('main').textContent()) || ''
  if (!text.includes('10 box')) {
    note('shelf', `the screen does not show 10 box; it shows: ${
      (text.match(/[\d.,]+ box/g) || ['nothing']).join(' / ')
    }`)
  }
  lowBanner = await warningBanner(page, 'Running low')
  if (lowBanner.includes(material.name)) {
    note('low stock', 'still reported as low after ten boxes arrived')
  }
  await shot(page, '02-on-the-shelf')

  // --- 4. the movement names the delivery that caused it ------------------
  begin('the ledger')
  await openTab(page, 'Movements')
  text = (await page.locator('main').textContent()) || ''
  if (!text.includes('Received') || !text.includes('Delivery')) {
    note('movements', 'the receipt is not shown as Received, from a Delivery')
  }
  await shot(page, '03-movements')

  // --- 5. taking more than is there is refused, and says what is there ----
  begin('a shelf cannot go short', 409)
  const short = await owner.call('POST', '/inventory/consumption', {
    item_id: material.id,
    quantity: '11',
    location_id: shelf.id,
  })
  if (short.status !== 409) {
    note('negative stock', `taking 11 of 10 returned ${short.status}, expected 409`)
  } else if (!String(short.body?.message || '').includes('10')) {
    note('negative stock', 'the refusal does not say what is actually on the shelf')
  }

  // --- 6. a treatment uses two, and still posts nothing -------------------
  begin('consumption posts nothing')
  const consumedBefore = await balances(owner)
  await owner.ok('POST', '/inventory/consumption', {
    item_id: material.id,
    quantity: '2',
    location_id: shelf.id,
  })
  const consumedAfter = await balances(owner)
  for (const code of ['1010', '9420', '6010']) {
    if (!same(consumedBefore, consumedAfter, code)) {
      note('posting', `account ${code} moved when a material was used, and should not`)
    }
  }

  // --- 7. value is refused honestly while the method is undeclared --------
  begin('counted, not costed')
  await openTab(page, 'Stock')
  text = (await page.locator('main').textContent()) || ''
  if (!/No valuation method has been chosen/i.test(text)) {
    note('valuation', 'the screen does not say that no valuation method has been chosen')
  }
  const valuation = await owner.ok('GET', '/inventory/valuation')
  if (valuation.method !== 'UNDECLARED' || valuation.total_value !== null) {
    note('valuation', `expected an undeclared method and no total, got ${JSON.stringify(valuation)}`)
  }
  if (!valuation.rows.some((row) => row.item_id === material.id && row.quantity === '8.0000')) {
    note('valuation', 'the quantity is missing from a valuation that cannot be costed')
  }
  await shot(page, '04-valuation-undeclared')

  // --- 8. a count moves nothing until somebody else approves it ----------
  begin('the count')
  await openTab(page, 'Counts')
  await page.getByRole('button', { name: 'New count' }).click()
  await page.waitForTimeout(500)
  await page.getByLabel('Location').selectOption(shelf.id)
  await page.waitForTimeout(800)
  await page.getByLabel('Reason').fill(`Monthly count ${RUN}`)
  await page.getByLabel(`Composite A2 ${RUN} Counted`).fill('7')
  await page.waitForTimeout(300)
  text = (await page.locator('[role="dialog"], dialog').textContent()) || ''
  if (!text.includes('-1')) {
    note('count', 'the sheet does not show the difference as it is typed')
  }
  if (!/Saving moves nothing/i.test(text)) {
    note('count', 'the sheet does not say that saving moves nothing')
  }
  await shot(page, '05-count-sheet')
  await page.getByRole('button', { name: 'Save' }).click()
  await page.waitForLoadState('networkidle')
  await page.waitForTimeout(900)

  const stillTen = await owner.ok('GET', `/inventory/stock?item_id=${material.id}`)
  if (Number(stillTen[0]?.quantity) !== 8) {
    note('count', 'saving a count changed the shelf before anybody approved it')
  }

  // --- 9. whoever counted is not offered the approve button --------------
  begin('counting and approving are two people')
  await page.getByRole('button', { name: 'Open' }).first().click()
  await page.waitForLoadState('networkidle')
  await page.waitForTimeout(600)
  await page.getByRole('button', { name: 'Send for approval' }).click()
  await page.waitForLoadState('networkidle')
  await page.waitForTimeout(900)
  text = (await page.locator('main').textContent()) || ''
  if (!/cannot approve their own count/i.test(text)) {
    note('separation', 'the owner who counted is not told why they cannot approve it')
  }
  const approveOffered = await page
    .getByRole('button', { name: 'Approve', exact: true })
    .count()
  if (approveOffered > 0) {
    note('separation', 'the approve button is offered to the person who counted')
  }
  await shot(page, '06-cannot-approve-own')

  // The server refuses it too, not only the screen.
  const counts = await owner.ok('GET', '/inventory/counts?status=PROPOSED')
  const mine = counts.find((row) => row.reason === `Monthly count ${RUN}`)
  const selfApproval = await owner.call('POST', `/inventory/counts/${mine.id}/approve`, {})
  if (selfApproval.status !== 409) {
    note('separation', `the server allowed a self-approval: ${selfApproval.status}`)
  }

  // --- 10. the cashier is offered the module nowhere ---------------------
  begin('the cashier holds no inventory permission', 403)
  const cashier = client(await token(CASHIER))
  const refused = await cashier.call('GET', '/inventory/items')
  if (refused.status !== 403) {
    note('permissions', `the cashier reached the material list: ${refused.status}`)
  }
  await signIn(page, CASHIER)
  const links = await page.getByRole('link').allTextContents()
  if (links.some((label) => label.trim().startsWith('Inventory'))) {
    // The sidebar lists every module with a status dot, so the link may be
    // present; what matters is that opening it is refused rather than
    // showing a cashier the cupboard.
    await page.goto(`${WEB}/inventory`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(700)
    const cashierText = (await page.locator('main').textContent()) || ''
    if (!/do not have permission/i.test(cashierText)) {
      note('permissions', 'the cashier opening Inventory is not told they may not see it')
    }
  }
  await shot(page, '07-cashier-refused')

  // --- 11. the whole thing at phone width, in Uzbek ----------------------
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
  await page.getByRole('link', { name: /^Ombor/ }).first().click()
  await page.waitForLoadState('networkidle')
  await page.waitForTimeout(1000)
  const over = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  )
  if (over > 1) note('horizontal overflow', `inventory at phone width: ${over}px`)
  const uz = (await page.locator('main').textContent()) || ''
  if (/Add a material|On the shelves|New count/.test(uz)) {
    note('language', 'English is showing with the interface set to Uzbek')
  }
  await shot(page, '08-phone-uz')

  // --- tidy up: leave the shelf as it was found --------------------------
  begin('tidy up')
  await owner.ok('POST', '/inventory/write-offs', {
    item_id: material.id,
    quantity: '8',
    reason: `End of browser pass ${RUN}`,
    location_id: shelf.id,
  })
  await owner.ok('PATCH', `/inventory/items/${material.id}`, { is_active: false })
  void receipt

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
