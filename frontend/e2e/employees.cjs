/**
 * Who works here, and what each day was — in a real Chrome.
 *
 * What this pass is for is the boundary the module is built around: **an
 * employee is not a user.** The screen shows the login and the roles it
 * holds, and must never be a second way to create one or grant one. It also
 * drives the timesheet, where the rules that matter are the ones that stop a
 * cell being filled: before somebody started, after they left, or a future
 * day marked as worked.
 *
 * It never destroys data: it creates its own position and its own employee,
 * and the employee it ends is one it hired.
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

/** The clinic's today and this month, not the container's. */
const TODAY = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'Asia/Tashkent',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
}).format(new Date())
const [YEAR, MONTH, DAY] = TODAY.split('-').map(Number)

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
  page.screenshot({ path: path.join(SHOTS, `emp-${name}.png`), fullPage: true })

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

/**
 * Sign in, always through the English login screen.
 *
 * The locale is switched *after* the shell is up, not before: the sign-in
 * form is translated too, so setting Uzbek first means looking for a field
 * labelled "Elektron pochta" — which is how the first version of this pass
 * managed to fail at the login box while testing the timesheet.
 */
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

/** Switch the interface language from the sidebar, as a person would. */
async function setLocale(page, locale) {
  await page.locator('aside select').first().selectOption(locale)
  await page.waitForTimeout(600)
}

async function run() {
  begin('0 setup')
  const owner = client(await token(OWNER))

  // The module needs a position to offer; create one of this run's own.
  const position = await owner.ok('POST', '/hr/positions', {
    code: `VERIFY${RUN}`,
    name: `Verification role ${RUN}`,
  })

  const browser = await chromium.launch({ channel: 'chrome', headless: true })
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  const page = await context.newPage()
  watch(page)

  begin('1 the owner opens the employees module')
  await signIn(page, OWNER)
  await page.getByRole('link', { name: 'Employees' }).click()
  await page.waitForLoadState('networkidle')
  await page.waitForTimeout(800)
  if ((await page.getByRole('link', { name: 'Timesheet' }).count()) === 0) {
    note('missing tab', 'the timesheet tab is not offered')
  }
  await shot(page, '01-people')

  begin('2 hire somebody')
  await page.getByRole('button', { name: 'Add an employee' }).click()
  await page.waitForTimeout(500)
  let dialog = page.locator('dialog[open]')
  await dialog.locator('#new-family').fill(`Xodimov${RUN}`)
  await dialog.locator('#new-given').fill('Dilshod')
  await dialog.locator('#new-phone').fill('+998901234567')
  await dialog.locator('#new-position').selectOption(position.id)
  // Start a month ago, so the timesheet has days that may be filled.
  const hiredOn = `${YEAR}-${String(MONTH).padStart(2, '0')}-01`
  await dialog.locator('#new-hired').fill(hiredOn)
  await dialog.getByRole('button', { name: 'Save' }).click()
  await page.waitForTimeout(1500)
  if ((await page.getByText(`Xodimov${RUN}`).count()) === 0) {
    note('not opened', 'hiring somebody did not open their record')
  }

  begin('3 somebody with no login holds no roles')
  // The point of the module: the employee record is not the directory.
  if ((await page.getByText('None — this person cannot sign in').count()) === 0) {
    note('roles invented', 'an employee with no login was shown roles')
  }
  await shot(page, '02-employee')

  begin('4 the screen offers no way to create a login')
  const text = (await page.locator('main').textContent()) || ''
  for (const forbidden of ['Invite', 'New user', 'Create user', 'Password']) {
    if (text.includes(forbidden)) {
      note('authority leak', `the employee screen offers "${forbidden}"`)
    }
  }

  begin('5 set the ordinary week')
  await page.getByRole('button', { name: 'Edit' }).last().click()
  await page.waitForTimeout(500)
  await page.locator('#rota-2').fill('7.5')
  await page.locator('#rota-4').fill('7.5')
  await page.getByRole('button', { name: 'Save' }).last().click()
  await page.waitForTimeout(1200)
  const rota = (await page.locator('main').textContent()) || ''
  if (!/Tuesday/.test(rota) || !/Thursday/.test(rota)) {
    note('rota lost', 'the saved rota does not show Tuesday and Thursday')
  }
  await shot(page, '03-rota')

  begin('6 the timesheet shows the month, and this person on it')
  await page.getByRole('link', { name: 'Timesheet' }).click()
  await page.waitForLoadState('networkidle')
  await page.waitForTimeout(1200)
  if ((await page.getByText(`Xodimov${RUN}`).count()) === 0) {
    note('missing row', 'the new employee has no row on the timesheet')
  }
  const shown = (await page.locator('main').textContent()) || ''
  if (!shown.includes(`${String(MONTH).padStart(2, '0')}.${YEAR}`)) {
    note('wrong month', `the timesheet opened on something other than ${MONTH}.${YEAR}`)
  }

  begin('7 record a day worked')
  const cell = page.getByRole('button', { name: new RegExp(`Xodimov${RUN}.*${YEAR}-${String(MONTH).padStart(2, '0')}-01`) })
  if ((await cell.count()) === 0) {
    note('no cell', 'the first of the month has no cell for this person')
  } else {
    await cell.first().click()
    await page.waitForTimeout(600)
    dialog = page.locator('dialog[open]')
    await dialog.locator('#day-code').selectOption('WORKED')
    await dialog.locator('#day-hours').fill('7.5')
    await dialog.getByRole('button', { name: 'Save' }).click()
    await page.waitForTimeout(1300)
  }
  await shot(page, '04-timesheet')

  begin('8 hours belong only to a day that was worked')
  const sickCell = page.getByRole('button', {
    name: new RegExp(`Xodimov${RUN}.*${YEAR}-${String(MONTH).padStart(2, '0')}-02`),
  })
  if ((await sickCell.count()) > 0) {
    await sickCell.first().click()
    await page.waitForTimeout(600)
    dialog = page.locator('dialog[open]')
    await dialog.locator('#day-code').selectOption('SICK')
    await page.waitForTimeout(300)
    if ((await dialog.locator('#day-hours').count()) !== 0) {
      note('contradiction offered', 'a sick day offered an hours field')
    }
    await dialog.getByRole('button', { name: 'Save' }).click()
    await page.waitForTimeout(1300)
  }

  begin('9 a day before somebody started cannot be recorded')
  // They started on the first, so there is nothing before it this month.
  // Step back a month: every cell in it must be refused.
  await page.getByRole('button', { name: 'Previous month' }).click()
  await page.waitForTimeout(1200)
  const previous = await page
    .getByRole('button', { name: new RegExp(`Xodimov${RUN}`) })
    .all()
  if (previous.length === 0) {
    // No row at all is also correct: they were not employed that month.
    begin('9 a day before somebody started cannot be recorded')
  } else {
    const enabled = []
    for (const button of previous) {
      if (!(await button.isDisabled())) enabled.push(await button.getAttribute('aria-label'))
    }
    if (enabled.length > 0) {
      note('offered anyway', `${enabled.length} cells before this person started were clickable`)
    }
  }
  await page.getByRole('button', { name: 'Next month' }).click()
  await page.waitForTimeout(1000)

  begin('10 the statutory timesheet is not claimed')
  const legend = (await page.locator('main').textContent()) || ''
  if (!/statutory timesheet/i.test(legend)) {
    note('silent', 'the timesheet does not say its codes are not the statutory ones')
  }

  begin('11 end the employment, with a reason', 409, 422)
  await page.getByRole('link', { name: 'People' }).click()
  await page.waitForLoadState('networkidle')
  await page.waitForTimeout(900)
  // Search for this run's own employee. Opening whichever row is first
  // would, on a second run, open the one the previous run ended.
  await page.locator('#employee-search').fill(`Xodimov${RUN}`)
  await page.waitForTimeout(1000)
  await page.getByRole('button', { name: 'Open', exact: true }).first().click()
  await page.waitForTimeout(1000)
  await page.getByRole('button', { name: 'Record that they left' }).click()
  await page.waitForTimeout(500)
  dialog = page.locator('dialog[open]')
  const submit = dialog.getByRole('button', { name: 'Record that they left' })
  if (!(await submit.isDisabled())) {
    note('no reason required', 'an employment could be ended with no reason')
  }
  await dialog.locator('#end-reason').fill('Verification pass tidying up after itself')
  await submit.click()
  await page.waitForTimeout(1500)

  begin('12 an ended record is kept, and says so')
  const after = (await page.locator('main').textContent()) || ''
  if (!/This employment ended on/.test(after)) {
    note('silent', 'an ended employment does not say so')
  }
  for (const gone of ['Suspend', 'Mark as working', 'Record that they left']) {
    if ((await page.getByRole('button', { name: gone }).count()) !== 0) {
      note('illegal move offered', `an ended employment still offered "${gone}"`)
    }
  }
  await shot(page, '05-ended')

  begin('13 the cashier reaches none of it', 403)
  await signIn(page, CASHIER)
  if ((await page.getByRole('link', { name: 'Employees' }).count()) !== 0) {
    // The navigation hides a module the caller may not read at all.
    note('nav leak', 'the cashier was offered the employees module')
  }
  await page.goto(`${WEB}/employees`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(1000)
  const cashierSees = (await page.locator('main').textContent()) || ''
  if (cashierSees.includes(`Xodimov${RUN}`)) {
    note('data leak', 'the cashier was shown a staff record')
  }
  if (!/do not have permission/i.test(cashierSees)) {
    note('unexplained', 'the cashier was shown neither the module nor a reason')
  }
  await shot(page, '06-cashier')

  begin('14 it fits a phone, in Uzbek')
  await signIn(page, OWNER)
  await setLocale(page, 'uz')
  await page.setViewportSize({ width: 390, height: 844 })
  await page.reload({ waitUntil: 'networkidle' })
  await page.getByRole('button', { name: '☰' }).click()
  await page.waitForTimeout(300)
  await page.getByRole('link', { name: 'Xodimlar' }).click()
  await page.waitForLoadState('networkidle')
  await page.waitForTimeout(1000)
  const over = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  )
  if (over > 1) note('horizontal overflow', `the people list at phone width: ${over}px`)
  const uz = (await page.locator('main').textContent()) || ''
  if (/Add an employee|Timesheet/.test(uz)) {
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
