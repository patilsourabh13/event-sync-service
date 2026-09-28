// Display helpers. Timestamps arrive from the API already normalized to the
// firm's timezone with an explicit offset, so we format the wall-clock time as
// given rather than re-projecting it into the viewer's local zone -- a meeting
// at 14:00 in the New York office should read 14:00 everywhere.

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

export function parseStamp(iso) {
  if (!iso) return null
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso)
  if (!match) return null
  const [, year, month, day, hour, minute] = match
  return { year: +year, month: +month, day: +day, hour: +hour, minute: +minute }
}

export function formatDate(iso) {
  const p = parseStamp(iso)
  if (!p) return '—'
  return `${MONTHS[p.month - 1]} ${p.day}, ${p.year}`
}

export function formatTime(iso) {
  const p = parseStamp(iso)
  if (!p) return ''
  const suffix = p.hour >= 12 ? 'PM' : 'AM'
  const hour12 = p.hour % 12 === 0 ? 12 : p.hour % 12
  return `${hour12}:${String(p.minute).padStart(2, '0')} ${suffix}`
}

export function formatDateTime(iso) {
  const p = parseStamp(iso)
  if (!p) return '—'
  return `${formatDate(iso)} at ${formatTime(iso)}`
}

export function formatValue(field, value) {
  if (value === null || value === undefined || value === '') return null
  if (field === 'start' || field === 'end') return formatDateTime(value)
  if (field === 'attendees') {
    if (!Array.isArray(value)) return String(value)
    return value
      .map((a) => a.name || a.raw || a.email)
      .filter(Boolean)
      .join(', ')
  }
  if (field === 'modality') return String(value).replace(/_/g, '-')
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

export const COVERAGE_LABEL = {
  both: 'BOTH SOURCES',
  crm_only: 'CRM ONLY',
  calendar_only: 'CALENDAR ONLY',
}

export const SOURCE_LABEL = { crm: 'CRM', calendar: 'Calendar' }
