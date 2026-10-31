/**
 * Drives the accounting policy screens in a real Chrome.
 *
 * The policy decides which accounts every business event posts to, so this
 * is the screen with the furthest reach in the application — and until now
 * it had no interface at all.
 */
const path = require('path')
const { chromium } = require('playwright')

const WEB = process.env.E2E_WEB || 'http://localhost:5173'
const SHOTS = process.env.E2E_SHOTS || path.join(__dirname, 'shots')
const TENANT = process.env.E2E_TENANT || 'verify-3d'
// Only the owner may change the policy: the director's role description
// says so outright, and the accountant holds `accounting.policy.read` alone.
// That control is what step 10 checks.
const OWNER = { email: `owner@${TENANT}.example.com`, password: 'verify-owner-password-1' }
const ACCOUNTANT = {
  email: `accountant@${TENANT}.example.com`,
  password: 'verify-staff-password-1',
}

const API = process.env.E2E_API || 'http://localhost:8000/api/v1'

/**
 * The day after whatever is in force now — which is what a person would
 * choose, and what makes this pass re-runnable: each run supersedes the
 * version the previous run activated.
 */
async function dayAfterTheVersionInForce() {
  const token = await fetch(`${API}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(OWNER),
  })
    .then((r) => r.json())
    .then((b) => b.access_token)
  const policies = await fetch(`${API}/accounting/policies`, {
    headers: { Authorization: `Bearer ${token}` },
  }).then((r) => r.json())
  const inForce = policies.find((p) => p.status === 'ACTIVE')
  if (!inForce) throw new Error('no policy is in force; seed one first')
  const next = new Date(`${inForce.effective_from}T00:00:00Z`)
  next.setUTCDate(next.getUTCDate() + 1)
  return next.toISOString().slice(0, 10)
}

const problems = []
let step = 'startup'
/**
 * HTTP statuses this step means to provoke. A refusal is a feature here —
 * seeding a second policy over one already in force must be refused — and
 * Chrome logs every non-2xx response as a console error, so a step that
 * tests a refusal says which one it expects rather than the run reporting
 * the application working correctly as a fault.
 */
let expected = []
const note = (kind, detail) => problems.push(`[${step}] ${kind}: ${detail}`)
const shot = (page, name) =>
  page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: true })

async function assertNoOverflow(page, where) {
  const over = await page.evaluate(() => {
    const d = document.documentElement
    return d.scrollWidth - d.clientWidth
  })
  if (over > 1) note('horizontal overflow', `${where}: ${over}px wider than the viewport`)
}

async function signIn(page, who) {
  await page.goto(WEB, { waitUntil: 'networkidle' })
  await page.evaluate(() => {
    try {
      window.localStorage.setItem('erp.locale', 'en')
    } catch {}
  })
  await page.reload({ waitUntil: 'networkidle' })
  await page.locator('form input').first().fill(who.email)
  await page.locator('input[type="password"]').fill(who.password)
  await page.locator('button[type="submit"]').click()
  await page.waitForTimeout(2500)
}

async function run() {
  const effective = await dayAfterTheVersionInForce()
  console.log(`the next version will start on ${effective}`)

  const browser = await chromium.launch({ channel: 'chrome', headless: true })
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  const page = await context.newPage()

  page.on('console', (m) => {
    if (m.type() !== 'error') return
    if (expected.some((status) => m.text().includes(`status of ${status}`))) return
    note('console error', m.text())
  })
  page.on('pageerror', (e) => note('page error', e.message))
  page.on('requestfailed', (r) => {
    if (!/favicon/.test(r.url())) note('request failed', `${r.method()} ${r.url()}`)
  })
  page.on('response', (r) => {
    if (r.status() >= 500) note('server error', `${r.status()} ${r.url()}`)
  })

  /** Begin a step, declaring the HTTP refusals it intends to provoke. */
  const begin = (name, ...statuses) => {
    step = name
    expected = statuses
  }

  begin('1 open the policy tab')
  await signIn(page, OWNER)
  await page.goto(`${WEB}/accounting/policy`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(1500)
  await shot(page, 'p01-policies')
  await assertNoOverflow(page, 'policy list')

  const body = await page.locator('main').innerText()
  if (!/In force/.test(body)) note('no active version', 'the in-force version is not marked')
  const ruleRows = await page.locator('tbody tr').count()
  if (ruleRows === 0) note('no versions', 'the policy list is empty')

  begin('2 the in-force version shows its rules')
  const rulesShown = await page.locator('main').innerText()
  // A rule is an event and the two accounts it posts to; all three must be
  // on the screen or the editor shows nothing an accountant can review.
  if (!/[a-z_]+_[a-z_]+/.test(rulesShown)) {
    note('rules not shown', 'no business event codes are listed')
  }
  if (!/\b\d{4}(\.\d{2})?\b/.test(rulesShown)) {
    note('no accounts', 'rule accounts are not shown')
  }
  // A version in force must not be editable; that is the whole point of
  // versioning, and ADR-011 says a policy change never rewrites history.
  if (await page.getByRole('button', { name: 'Change' }).first().isVisible().catch(() => false)) {
    note('active version is editable', 'an in-force policy offers a Change button')
  }

  begin('3 building from the workbook again is refused, with the reason', 409)
  await page.getByRole('button', { name: 'Build from the workbook' }).click()
  await page.waitForTimeout(600)
  await shot(page, 'p02-seed-dialog')
  await page.locator('#seed-from').fill(effective)
  await page.locator('dialog').getByRole('button', { name: 'Create' }).click()
  await page.waitForTimeout(1500)
  await shot(page, 'p03-seed-refused')
  const refusal = await page.locator('main').innerText()
  if (!/already in force/i.test(refusal)) {
    note('unhelpful refusal', `expected the in-force conflict, got: ${refusal.slice(0, 300)}`)
  }

  // A draft blocks every new version, and its effective date cannot be
  // edited, so one left over from an earlier run is discarded rather than
  // continued. Until the discard existed this was a dead end.
  begin('4a discard any draft left over from a previous run')
  const leftover = page.locator('tbody tr', { hasText: 'Draft' }).first()
  if (await leftover.isVisible().catch(() => false)) {
    console.log('discarding a draft left over from an earlier run')
    await leftover.click()
    await page.waitForTimeout(1200)
    await page.getByRole('button', { name: 'Discard draft' }).first().click()
    await page.waitForTimeout(600)
    await shot(page, 'p03b-discard-confirm')
    await page.locator('dialog').getByRole('button', { name: 'Discard draft' }).click()
    await page.waitForTimeout(1800)
    const gone = await page.locator('main').innerText()
    if (!/Draft discarded/.test(gone)) note('discard not confirmed', gone.slice(0, 250))
    if (await page.locator('tbody tr', { hasText: 'Draft' }).first().isVisible().catch(() => false)) {
      note('draft survived', 'the discarded draft is still listed')
    }
  }

  begin('4 start a new version')
  await page.getByRole('button', { name: 'New version' }).click()
  await page.waitForTimeout(600)
  await page.locator('#policy-name').fill('Reviewed policy')
  await page.locator('#policy-from').fill(effective)
  await page.locator('dialog').getByRole('button', { name: 'Create' }).click()
  await page.waitForTimeout(1800)
  await shot(page, 'p04-draft-created')
  await assertNoOverflow(page, 'draft version')
  const draft = await page.locator('main').innerText()
  if (!/Draft/.test(draft)) note('no draft', 'the new version is not shown as a draft')
  if (!/Check every rule with an accountant/.test(draft)) {
    note('no review warning', 'a draft does not warn that rules must be reviewed')
  }

  begin('5 add a rule to the draft')
  await page.getByRole('button', { name: 'Add a rule' }).click()
  await page.waitForTimeout(700)
  await shot(page, 'p05-rule-dialog')
  await assertNoOverflow(page, 'rule dialog')
  await page.locator('#rule-operation').fill('bank_charge_on_deposit')
  await page.locator('#rule-description').fill('Bank charge deducted from a deposit')
  await page.selectOption('#rule-debit', '9420')
  await page.selectOption('#rule-credit', '5110.01')
  await shot(page, 'p06-rule-filled')
  await page.locator('dialog').getByRole('button', { name: 'Save' }).click()
  await page.waitForTimeout(1800)
  await shot(page, 'p07-rule-saved')
  const saved = await page.locator('main').innerText()
  if (!/Rule saved/.test(saved)) note('rule not saved', saved.slice(0, 300))
  if (!/bank_charge_on_deposit/.test(saved)) note('rule missing', 'the new rule is not listed')

  begin('6 an existing rule can be changed on a draft')
  await page.getByRole('button', { name: 'Change' }).first().click()
  await page.waitForTimeout(700)
  const locked = await page.locator('#rule-operation').isDisabled()
  if (!locked) {
    note('operation code editable', 'changing the code would create a second rule, not edit this one')
  }
  await shot(page, 'p08-rule-edit')
  await page.locator('dialog').getByRole('button', { name: 'Cancel' }).click()
  await page.waitForTimeout(500)

  begin('7 put the draft into force')
  await page.getByRole('button', { name: 'Put into force' }).click()
  await page.waitForTimeout(2200)
  await shot(page, 'p09-activated')
  const active = await page.locator('main').innerText()
  if (!/Policy is now in force/.test(active)) note('not activated', active.slice(0, 300))
  // The previous version must now say it was superseded rather than vanish:
  // it still answers for its own dates (ADR-023).
  if (!/Superseded/.test(active)) {
    note('previous version lost', 'the superseded version is not shown')
  }

  begin('8 the other languages')
  for (const locale of ['uz', 'ru']) {
    await page.selectOption('#locale-switcher', locale)
    await page.waitForTimeout(1000)
    await shot(page, `p10-${locale}`)
    await assertNoOverflow(page, `policy screen in ${locale}`)
  }
  await page.selectOption('#locale-switcher', 'en')

  begin('9 phone width')
  await page.setViewportSize({ width: 390, height: 844 })
  await page.waitForTimeout(900)
  await shot(page, 'p11-narrow')
  await assertNoOverflow(page, 'policy screen at phone width')
  await page.setViewportSize({ width: 1440, height: 900 })

  begin('10 an accountant reads the policy but cannot change it')
  await context.clearCookies()
  const readerPage = await context.newPage()
  readerPage.on('pageerror', (e) => note('page error (reader)', e.message))
  await signIn(readerPage, ACCOUNTANT)
  await readerPage.goto(`${WEB}/accounting/policy`, { waitUntil: 'networkidle' })
  await readerPage.waitForTimeout(1500)
  await readerPage.screenshot({ path: path.join(SHOTS, 'p12-reader.png'), fullPage: true })
  if ((await readerPage.locator('tbody tr').count()) === 0) {
    note('reader sees nothing', 'the accountant cannot read the policy')
  }
  const forbiddenControls = [
    'New version',
    'Build from the workbook',
    'Put into force',
    'Add a rule',
    'Discard draft',
  ]
  for (const forbidden of forbiddenControls) {
    if (await readerPage.getByRole('button', { name: forbidden }).isVisible().catch(() => false)) {
      note('reader over-privileged', `the accountant is offered "${forbidden}"`)
    }
  }

  await browser.close()
  console.log('\n=== problems ===')
  problems.length ? problems.forEach((p) => console.log(' -', p)) : console.log('none')
  console.log(`\n${problems.length} problem(s)`)
}

run().catch((error) => {
  console.log(`\nFAILED at step: ${step}`)
  console.log(error.message)
  console.log('\n=== problems so far ===')
  problems.forEach((p) => console.log(' -', p))
  process.exit(1)
})
