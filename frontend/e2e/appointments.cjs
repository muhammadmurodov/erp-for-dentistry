/**
 * The day, in a real Chrome: booking patients in and working through them.
 *
 * What this pass is for is the two things no unit test reaches — that a time
 * typed at the desk comes back as the same time, in the clinic's own hours,
 * and that a double booking is refused by the server and explained on the
 * screen rather than discovered later by two patients in one chair.
 *
 * It never destroys data: it creates its own patients and cancels or leaves
 * only its own bookings.
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
const DENTIST = { email: `dentist@${TENANT}.example.com`, password: STAFF_PASSWORD }

const RUN = Date.now().toString().slice(-6)
/** Statuses that hold no chair, so clash with nothing. */
const CLOSED = ['CANCELLED', 'NO_SHOW', 'COMPLETED']

/** The clinic's today, not the container's. */
const TODAY = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'Asia/Tashkent',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
}).format(new Date())
const TOMORROW = (() => {
  const [y, m, d] = TODAY.split('-').map(Number)
  return new Date(Date.UTC(y, m - 1, d + 1)).toISOString().slice(0, 10)
})()

const problems = []
let step = 'startup'
/**
 * HTTP statuses the current step means to provoke. A double booking *must*
 * be refused, and Chrome logs every non-2xx response as a console error, so
 * a step that tests a refusal says which one it expects rather than the run
 * reporting the application working correctly as a fault. Same device as
 * `policy.cjs`.
 */
let expected = []
const note = (kind, detail) => problems.push(`[${step}] ${kind}: ${detail}`)

/** Begin a step, declaring the HTTP refusals it intends to provoke. */
const begin = (name, ...statuses) => {
  step = name
  expected = statuses
}

/**
 * Sign in once per account and keep the token.
 *
 * `/auth/login` is rate-limited to ten a minute, and rightly so: that limit
 * is a control, not an obstacle. A pass that signs in afresh for every call
 * spends the clinic's allowance on itself and then reports the limit as a
 * failure — which is how the first run of this script managed to decide the
 * dentist did not exist.
 */
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

/**
 * The three people this pass needs, created once and reused after.
 *
 * Existence is read from the directory the owner may already see, not
 * probed by trying to sign in: three speculative logins a run is most of a
 * minute's allowance, and a probe that is refused for being too frequent is
 * indistinguishable from an account that is not there.
 */
async function ensureStaff(owner) {
  const directory = await owner.ok('GET', '/users')
  for (const [who, roles, name] of [
    [RECEPTIONIST, ['receptionist'], 'Verify Receptionist'],
    [CASHIER, ['cashier'], 'Verify Cashier'],
    [DENTIST, ['dentist'], 'Verify Dentist'],
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

const shot = (page, name) =>
  page.screenshot({ path: path.join(SHOTS, `appt-${name}.png`), fullPage: true })

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

const hhmm = (minutes) =>
  `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`

/**
 * An hour this dentist has free today, from 15:00 onwards.
 *
 * Not a fixed 15:00: the pass has to be re-runnable, and a run that booked
 * 15:00 this morning would make the next one fail against its own leftovers
 * — reporting the clash rule working as a fault. It needs a whole free hour,
 * because it books the start of it, provokes a clash a quarter past, and
 * then moves to the half hour, which must be free for the right reason.
 */
async function freeHour(owner, dentistId) {
  const booked = (await owner.ok('GET', `/clinic/appointments?on=${TODAY}`))
    .filter((a) => a.dentist_user_id === dentistId && !CLOSED.includes(a.status))
    .filter((a) => a.scheduled_at)
    .map((a) => {
      const [h, m] = a.scheduled_at.split(':').map(Number)
      return [h * 60 + m, h * 60 + m + (a.duration_minutes || 30)]
    })
  for (let start = 15 * 60; start <= 21 * 60; start += 60) {
    if (!booked.some(([from, to]) => start < to && from < start + 60)) return start
  }
  throw new Error('the verification dentist has no free hour left today')
}

async function run() {
  begin('0 setup')
  const owner = client(await token(OWNER))
  await ensureStaff(owner)
  const staff = await owner.ok('GET', '/users')
  const dentist = staff.find((u) => u.email === DENTIST.email)
  if (!dentist) throw new Error('the verification dentist is missing')

  const hour = await freeHour(owner, dentist.id)
  const AT = hhmm(hour)
  const CLASHES = hhmm(hour + 15)
  const FREE = hhmm(hour + 30)
  console.log(`booking into the free hour at ${AT}`)

  const first = await owner.ok('POST', '/clinic/patients', {
    family_name: `Navbatov${RUN}`,
    given_name: 'Bir',
    birth_year: 1991,
  })
  const second = await owner.ok('POST', '/clinic/patients', {
    family_name: `Navbatov${RUN}`,
    given_name: 'Ikki',
    birth_year: 1992,
    allow_duplicate: true,
  })
  console.log(`patients ${first.patient.code} and ${second.patient.code}`)

  const browser = await chromium.launch({ channel: 'chrome', headless: true })
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  const page = await context.newPage()
  watch(page)

  begin('1 reception signs in and opens the day')
  await signIn(page, RECEPTIONIST)
  await page.goto(`${WEB}/clinic/appointments`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(1200)
  await shot(page, '01-day')

  begin('2 the day on screen is the clinic s day')
  const shown = await page.locator('#appointments-day').inputValue()
  if (shown !== TODAY) {
    note('today', `the screen opened on ${shown}, the clinic's day is ${TODAY}`)
  }

  begin(`3 book the first patient at ${AT}`)
  await page.getByRole('button', { name: 'Book an appointment' }).first().click()
  await page.waitForTimeout(600)
  await page.locator('#appointment-search').fill(`Navbatov${RUN}`)
  await page.waitForTimeout(1200)
  const firstOption = page
    .locator('#appointment-patient option', { hasText: first.patient.code })
    .first()
  await page.locator('#appointment-patient').selectOption(
    await firstOption.getAttribute('value'),
  )
  const dentistOption = page
    .locator('#appointment-dentist option', { hasText: 'Verify Dentist' })
    .first()
  await page.locator('#appointment-dentist').selectOption(
    await dentistOption.getAttribute('value'),
  )
  await page.locator('#appointment-time').fill(AT)
  await page.locator('#appointment-minutes').fill('30')
  await page.locator('#appointment-reason').fill('Pain in the lower left')
  await page.locator('#appointment-submit').click()
  await page.waitForTimeout(1800)
  await shot(page, '02-booked')

  begin('4 the hour on the screen is the hour that was typed')
  const row = page.locator('tbody tr', { hasText: first.patient.code }).first()
  if (!(await row.isVisible().catch(() => false))) {
    note('booking', 'the appointment does not appear in the day')
  } else {
    const text = await row.textContent()
    if (!text.includes(AT)) note('time', `the row reads ${text.slice(0, 60)}`)
    if (!text.includes('Pain in the lower left')) note('reason', 'the reason is not shown')
    if (!text.includes('Verify Dentist')) note('dentist', 'the dentist is not shown')
  }

  begin('5 a clashing booking is warned about before it is sent')
  await page.getByRole('button', { name: 'Book an appointment' }).first().click()
  await page.waitForTimeout(600)
  await page.locator('#appointment-search').fill(`Navbatov${RUN}`)
  await page.waitForTimeout(1200)
  const secondOption = page
    .locator('#appointment-patient option', { hasText: second.patient.code })
    .first()
  await page.locator('#appointment-patient').selectOption(
    await secondOption.getAttribute('value'),
  )
  await page.locator('#appointment-dentist').selectOption(
    await dentistOption.getAttribute('value'),
  )
  await page.locator('#appointment-time').fill(CLASHES)
  await page.locator('#appointment-minutes').fill('30')
  await page.waitForTimeout(600)
  if (!(await page.getByText(/already booked then/i).first().isVisible().catch(() => false))) {
    note('clash', 'the dialog does not warn that the dentist is already booked')
  }
  await shot(page, '03-clash')

  begin('6 and the server refuses it if it is sent anyway', 409)
  await page.locator('#appointment-submit').click()
  await page.waitForTimeout(1600)
  const stillOpen = await page.locator('dialog[open] #appointment-time').isVisible().catch(() => false)
  if (!stillOpen) note('clash', 'the clashing booking was accepted')
  const dialogText = await page.locator('dialog[open]').textContent()
  if (!/already booked/i.test(dialogText)) {
    note('clash', 'the refusal does not say the dentist is booked')
  }

  begin('7 moved to a free hour, it books')
  await page.locator('#appointment-time').fill(FREE)
  await page.waitForTimeout(600)
  await page.locator('#appointment-submit').click()
  await page.waitForTimeout(1800)
  const secondRow = page.locator('tbody tr', { hasText: second.patient.code }).first()
  if (!(await secondRow.isVisible().catch(() => false))) {
    note('booking', 'the back-to-back appointment was not accepted')
  }
  await shot(page, '04-two-bookings')

  begin('8 confirm, then mark the patient arrived')
  await row.getByRole('button', { name: 'Confirm' }).click()
  await page.waitForTimeout(1500)
  await page.locator('tbody tr', { hasText: first.patient.code }).first()
    .getByRole('button', { name: 'Arrived' }).click()
  await page.waitForTimeout(1500)
  const arrived = await page.locator('tbody tr', { hasText: first.patient.code }).first().textContent()
  if (!/Arrived/.test(arrived)) note('status', `the row reads ${arrived.slice(0, 80)}`)
  await shot(page, '05-arrived')

  begin('9 a patient who has arrived is not offered a no-show')
  const arrivedRow = page.locator('tbody tr', { hasText: first.patient.code }).first()
  if (await arrivedRow.getByRole('button', { name: 'Did not come' }).isVisible().catch(() => false)) {
    note('status', 'a patient who is here can still be marked absent')
  }

  begin('10 move the second booking to tomorrow')
  await page.locator('tbody tr', { hasText: second.patient.code }).first()
    .getByRole('button', { name: 'Move' }).click()
  await page.waitForTimeout(800)
  await page.locator('#appointment-date').fill(TOMORROW)
  await page.locator('#appointment-time').fill('11:00')
  await page.locator('#appointment-submit').click()
  await page.waitForTimeout(2000)
  const dayNow = await page.locator('#appointments-day').inputValue()
  if (dayNow !== TOMORROW) {
    note('move', `after moving the booking the screen shows ${dayNow}, not ${TOMORROW}`)
  }
  const movedRow = page.locator('tbody tr', { hasText: second.patient.code }).first()
  if (!(await movedRow.isVisible().catch(() => false))) {
    note('move', 'the moved booking is not on the new day')
  } else {
    const text = await movedRow.textContent()
    if (!text.includes('11:00')) note('move', `the moved row reads ${text.slice(0, 60)}`)
  }
  await shot(page, '06-moved')

  begin('11 cancelling needs a reason and keeps the booking')
  await movedRow.getByRole('button', { name: 'Cancel', exact: true }).click()
  await page.waitForTimeout(800)
  if (await page.locator('#appointment-cancel-submit').isEnabled()) {
    note('cancel', 'the dialog would cancel without a reason')
  }
  await page.locator('#appointment-cancel-reason').fill('The patient rang to call it off')
  await page.waitForTimeout(400)
  await page.locator('#appointment-cancel-submit').click()
  await page.waitForTimeout(1800)
  const cancelled = await page.locator('tbody tr', { hasText: second.patient.code }).first().textContent()
  if (!/Cancelled/.test(cancelled)) {
    note('cancel', `the booking does not read as cancelled: ${cancelled.slice(0, 80)}`)
  }
  if (!/call it off/.test(cancelled)) note('cancel', 'the reason is not kept on the screen')
  await shot(page, '07-cancelled')

  begin('12 the dashboard counts the real bookings')
  await page.goto(`${WEB}/`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(1500)
  const today = await owner.ok(`GET`, `/clinic/today`)
  const tile = await page.locator('main').textContent()
  if (today.appointments && today.appointments.total > 0) {
    if (!tile.includes(String(today.appointments.total))) {
      note('dashboard', `the day has ${today.appointments.total} bookings and the tile does not say so`)
    }
  }
  await shot(page, '08-dashboard')

  // The cashier reads the day and may not change it: `clinic.appointment.read`
  // without `.manage`. That is deliberate — the person taking the money is
  // asked all day who is still expected — so what this step checks is the
  // boundary, not a refusal. Reception owns the book.
  begin('13 the cashier reads the day and is offered no control over it')
  await signIn(page, CASHIER)
  await page.goto(`${WEB}/clinic/appointments`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(1200)
  if (!(await page.locator('#appointments-day').isVisible().catch(() => false))) {
    note('permissions', 'the cashier cannot see the day they are asked about all morning')
  }
  for (const control of ['Book an appointment', 'Confirm', 'Arrived', 'Move', 'Did not come']) {
    if (await page.getByRole('button', { name: control }).first().isVisible().catch(() => false)) {
      note('permissions', `the cashier is offered "${control}", which needs appointment.manage`)
    }
  }
  const cashierBooking = await page.request.post(`${API}/clinic/appointments`, {
    headers: { Authorization: `Bearer ${await token(CASHIER)}` },
    data: { patient_id: first.patient.id, scheduled_on: TODAY, scheduled_at: '09:00' },
  })
  if (cashierBooking.status() !== 403) {
    note('permissions', `the API let the cashier book: ${cashierBooking.status()}`)
  }
  await shot(page, '09-cashier')

  begin('14 phone width, and in Uzbek')
  await signIn(page, RECEPTIONIST)
  await page.evaluate(() => {
    try {
      localStorage.setItem('erp.locale', 'uz')
    } catch {}
  })
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto(`${WEB}/clinic/appointments`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(1500)
  const over = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  )
  if (over > 1) note('horizontal overflow', `appointments at phone width: ${over}px`)
  const uz = await page.locator('main').textContent()
  if (/Appointments|Book an appointment/.test(uz)) {
    note('language', 'English is showing with the interface set to Uzbek')
  }
  await shot(page, '10-phone-uz')

  // Leave the day as it was found. The patients stay — a clinic does not
  // delete people, and the duplicate work needs them — but the chairs this
  // run took are given back, so the next run finds its hour where it
  // expects to and the clash it provokes is its own.
  begin('15 the run gives back the hours it took')
  for (const patient of [first.patient, second.patient]) {
    const theirs = await owner.ok('GET', `/clinic/appointments?patient_id=${patient.id}`)
    for (const appointment of theirs) {
      if (CLOSED.includes(appointment.status)) continue
      const result = await owner.call('POST', `/clinic/appointments/${appointment.id}/status`, {
        status: 'CANCELLED',
        reason: 'Verification pass tidying up after itself',
      })
      if (result.status >= 400) {
        note('cleanup', `could not release ${appointment.scheduled_at}: ${result.status}`)
      }
    }
  }

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
