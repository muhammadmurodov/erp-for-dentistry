/**
 * A course of treatment, in a real Chrome: the work that spans visits.
 *
 * What this pass is for is the two rules no unit test can show reaching a
 * person — that a plan moves forward only through the states a clinician
 * would recognise, and that a plan cannot be declared finished while a tooth
 * on it is still open. Both are enforced on the server; this checks that the
 * screen says so *before* the button is pressed rather than after, and that
 * the button it would refuse is not offered at all.
 *
 * It also drives the correction screen, which until now had only an API
 * endpoint behind it. A correction is not an edit: the original stays on the
 * record, marked and pointing at its replacement, and this pass fails if it
 * ever disappears.
 *
 * It never destroys data: it creates its own patient and its own plan.
 */
const path = require('path')
const { chromium } = require('playwright')

const WEB = process.env.E2E_WEB || 'http://localhost:5173'
const API = process.env.E2E_API || 'http://localhost:8000/api/v1'
const SHOTS = process.env.E2E_SHOTS || path.join(__dirname, 'shots')

const TENANT = process.env.E2E_TENANT || 'verify-3d'
const OWNER = { email: `owner@${TENANT}.example.com`, password: 'verify-owner-password-1' }
const STAFF_PASSWORD = 'verify-staff-password-1'
const CASHIER = { email: `cashier@${TENANT}.example.com`, password: STAFF_PASSWORD }

const RUN = Date.now().toString().slice(-6)

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
  page.screenshot({ path: path.join(SHOTS, `plan-${name}.png`), fullPage: true })

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

/**
 * Open the patient this run created, from the clinic's patient list.
 *
 * `navLabel` is passed because the last step runs in Uzbek, where the entry
 * reads "Klinika" — a pass that hard-codes the English label cannot check
 * the translated screen at all. `narrow` opens the menu first: below the
 * large breakpoint the sidebar is behind the ☰ button, which is the layout
 * a phone actually gets.
 */
async function openPatient(
  page,
  code,
  { navLabel = 'Clinic', openLabel = 'Edit', narrow = false } = {},
) {
  if (narrow) {
    await page.getByRole('button', { name: '☰' }).click()
    await page.waitForTimeout(300)
  }
  await page.getByRole('link', { name: navLabel }).click()
  await page.waitForLoadState('networkidle')
  await page.locator('input[type="search"], input[placeholder]').first().fill(code)
  await page.waitForTimeout(900)
  // The patient list's control is labelled "Edit" — see the note in
  // docs/MODULE_AUDIT.md; it opens the account rather than editing it.
  await page.getByRole('button', { name: openLabel, exact: true }).first().click()
  await page.waitForTimeout(1200)
}

async function run() {
  begin('0 setup')
  const owner = client(await token(OWNER))

  const services = await owner.ok('GET', '/sales/services')
  const service = services.find((s) => s.is_active) || services[0]
  if (!service) throw new Error('the tenant has no service to record against')

  const registered = await owner.ok('POST', '/clinic/patients', {
    family_name: `Rejaev${RUN}`,
    given_name: 'Nodira',
    birth_year: 1986,
  })
  const patient = registered.patient
  console.log(`patient ${patient.code}`)

  const browser = await chromium.launch({ channel: 'chrome', headless: true })
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  const page = await context.newPage()
  watch(page)

  // --- the plan's life cycle, as the owner ---------------------------------
  begin('1 the owner opens the patient')
  await signIn(page, OWNER)
  await openPatient(page, patient.code)
  if ((await page.getByText('Courses of treatment').count()) === 0) {
    note('missing panel', 'the courses-of-treatment panel is not on the patient page')
  }

  begin('2 create a course of treatment')
  await page.getByRole('button', { name: 'New course of treatment' }).first().click()
  await page.waitForTimeout(400)
  let dialog = page.locator('dialog[open]')
  await dialog.locator('#plan-title').fill('Upper left bridge')
  await dialog.locator('#plan-notes').fill('Impression first visit, fitting later')
  await dialog.getByRole('button', { name: 'Save' }).click()
  await page.waitForTimeout(1200)
  if ((await page.getByText('Upper left bridge').count()) === 0) {
    note('not opened', 'creating a plan did not open it')
  }
  await shot(page, '01-draft')

  begin('3 a draft offers no jump to finished')
  // The rule stated as a button that is simply not there. A screen that
  // offered it and then showed a 409 would be telling the user the system is
  // broken, when the system is correct and the offer was wrong.
  if ((await page.getByRole('button', { name: 'Finished', exact: true }).count()) !== 0) {
    note('illegal move offered', 'a draft plan offered "Finished"')
  }
  for (const offered of ['Propose to the patient', 'Abandon']) {
    if ((await page.getByRole('button', { name: offered }).count()) === 0) {
      note('missing move', `a draft plan did not offer "${offered}"`)
    }
  }

  begin('4 walk it forward: proposed, agreed, started')
  for (const [button, badge] of [
    ['Propose to the patient', 'Proposed'],
    ['Patient agreed', 'Agreed'],
    ['Start the work', 'In progress'],
  ]) {
    await page.getByRole('button', { name: button }).click()
    await page.waitForTimeout(900)
    if ((await page.getByText(badge, { exact: true }).count()) === 0) {
      note('state not shown', `after "${button}" the plan does not read as ${badge}`)
    }
  }
  await shot(page, '02-in-progress')

  begin('5 record a tooth against the plan')
  await page.getByRole('button', { name: /Back to the list/ }).click()
  await page.waitForTimeout(700)
  await page.getByRole('button', { name: 'Start a visit' }).first().click()
  await page.waitForTimeout(500)
  dialog = page.locator('dialog[open]')
  await dialog.locator('#encounter-complaint').fill('Pain, upper left')
  await dialog.getByRole('button', { name: 'Start a visit' }).click()
  await page.waitForTimeout(1500)

  dialog = page.locator('dialog[open]')
  if ((await dialog.locator('#record-plan').count()) === 0) {
    note('no plan selector', 'recording work did not offer the open course of treatment')
  } else {
    const offered = await dialog.locator('#record-plan option').allInnerTexts()
    if (!offered.some((o) => o.includes('Upper left bridge'))) {
      note('plan not offered', `the selector held: ${offered.join(' | ')}`)
    }
    await dialog.locator('#record-plan').selectOption({ index: 1 })
  }
  await dialog.locator('select[id^=item-service]').first().selectOption(service.id)
  await dialog.locator('input[id^=item-tooth]').first().fill('26')
  await dialog.locator('input[id^=item-surfaces]').first().fill('MO')
  await dialog.locator('input[id^=item-finding]').first().fill('Caries, distal')
  await dialog.getByRole('button', { name: 'Save' }).click()
  await page.waitForTimeout(1500)
  if ((await page.getByText('Caries, distal').count()) === 0) {
    note('not recorded', 'the tooth does not appear on the clinical record')
  }

  begin('6 the plan carries the work, and counts it')
  await page.getByRole('button', { name: 'Open', exact: true }).first().click()
  await page.waitForTimeout(1200)
  const progress = await page.getByText(/of 1 finished/).first().textContent()
  if (!/0 of 1/.test(progress || '')) {
    note('miscounted', `progress read "${progress}" with one unfinished tooth`)
  }

  begin('7 an unfinished plan refuses to be finished')
  if ((await page.getByText(/still has unfinished work/).count()) === 0) {
    note('unexplained', 'no warning that the plan still has work outstanding')
  }
  const finish = page.getByRole('button', { name: 'Finished', exact: true })
  if ((await finish.count()) === 0) {
    note('missing move', 'a plan in progress did not offer "Finished" at all')
  } else if (!(await finish.isDisabled())) {
    note('offered anyway', '"Finished" was clickable with a tooth still open')
  }
  await shot(page, '03-completion-blocked')

  begin('8 finish the tooth, then the plan')
  await page.getByRole('button', { name: /Back to the list/ }).click()
  await page.waitForTimeout(800)
  await page.getByRole('button', { name: 'Mark finished' }).first().click()
  await page.waitForTimeout(1300)
  await page.getByRole('button', { name: 'Open', exact: true }).first().click()
  await page.waitForTimeout(1300)
  const finishNow = page.getByRole('button', { name: 'Finished', exact: true })
  if (await finishNow.isDisabled()) {
    note('still refused', 'the plan would not finish with all its work done')
  } else {
    await finishNow.click()
    await page.waitForTimeout(1300)
  }
  if ((await page.getByText('This plan has finished and no longer changes.').count()) === 0) {
    note('not final', 'a finished plan does not say that it no longer changes')
  }
  for (const gone of ['Abandon', 'Start the work']) {
    if ((await page.getByRole('button', { name: gone }).count()) !== 0) {
      note('illegal move offered', `a finished plan still offered "${gone}"`)
    }
  }
  await shot(page, '04-finished')

  // --- correcting a recorded treatment -------------------------------------
  begin('9 correct a recorded treatment')
  await page.getByRole('button', { name: /Back to the list/ }).click()
  await page.waitForTimeout(800)
  await page.getByRole('button', { name: 'Correct', exact: true }).first().click()
  await page.waitForTimeout(600)
  dialog = page.locator('dialog[open]')
  // A correction without a reason is not a correction, it is an edit.
  const submit = dialog.getByRole('button', { name: 'Correct', exact: true })
  if (!(await submit.isDisabled())) {
    note('no reason required', 'a correction could be submitted with no reason')
  }
  await dialog.locator('#correct-tooth').fill('36')
  await dialog.locator('#correct-finding').fill('Caries, distal (tooth 36)')
  await dialog.locator('#correct-reason').fill('Tooth 36 was treated, 26 was written down')
  await submit.click()
  await page.waitForTimeout(1600)

  begin('10 the original is kept, marked, and still readable')
  if ((await page.getByText('Corrected', { exact: true }).count()) === 0) {
    note('history lost', 'the superseded treatment is not on the record')
  }
  if ((await page.getByText('Caries, distal (tooth 36)').count()) === 0) {
    note('replacement missing', 'the corrected treatment is not on the record')
  }
  const items = await owner.ok(`GET`, `/clinic/patients/${patient.id}/treatments`)
  const superseded = items.find((i) => i.status === 'CORRECTED')
  if (!superseded) {
    note('history lost', 'no CORRECTED item in the patient record')
  } else if (!superseded.corrected_by_item_id) {
    note('dangling', 'the superseded item does not point at its replacement')
  } else if (superseded.tooth_number !== 26) {
    note('rewritten', 'the superseded item was changed rather than kept')
  }
  await shot(page, '05-corrected')

  // --- who may see any of it ----------------------------------------------
  begin('11 the cashier sees no clinical record at all', 403)
  await signIn(page, CASHIER)
  await openPatient(page, patient.code)
  for (const forbidden of ['Courses of treatment', 'Treatment record']) {
    if ((await page.getByText(forbidden).count()) !== 0) {
      note('clinical leak', `the cashier was shown "${forbidden}"`)
    }
  }
  if ((await page.getByText('Caries, distal').count()) !== 0) {
    note('clinical leak', 'the cashier was shown a finding')
  }
  // They must still be able to do their own job on the same screen.
  if ((await page.getByText('Account').count()) === 0) {
    note('over-restricted', 'the cashier lost the patient account along with the chart')
  }
  await shot(page, '06-cashier')

  // --- the shape of it ------------------------------------------------------
  begin('12 the plan screen fits a phone, in Uzbek')
  await signIn(page, OWNER)
  await page.evaluate(() => {
    try {
      localStorage.setItem('erp.locale', 'uz')
    } catch {}
  })
  await page.setViewportSize({ width: 390, height: 844 })
  await page.reload({ waitUntil: 'networkidle' })
  await openPatient(page, patient.code, {
    navLabel: 'Klinika',
    openLabel: 'Tahrirlash',
    narrow: true,
  })
  await page.waitForTimeout(800)
  const over = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  )
  if (over > 1) note('horizontal overflow', `the patient page at phone width: ${over}px`)
  const text = (await page.locator('main').textContent()) || ''
  if (/Courses of treatment|New course of treatment/.test(text)) {
    note('language', 'English is showing with the interface set to Uzbek')
  }
  await shot(page, '07-phone-uz')

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
