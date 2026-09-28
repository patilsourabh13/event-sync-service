import React, { useEffect, useState } from 'react'
import { api } from '../lib/api.js'
import { formatValue, formatDate } from '../lib/format.js'
import { CoverageBadge, ConflictChips, SeverityBadge } from './Badges.jsx'

// Order chosen so the fields that can actually hurt someone (is it cancelled?
// when is it? where is it?) sit at the top of the comparison.
const FIELD_ORDER = [
  ['status', 'Status'],
  ['start', 'Start time'],
  ['end', 'End time'],
  ['location', 'Location'],
  ['modality', 'Format'],
  ['title', 'Title'],
  ['client_name', 'Client'],
  ['client_company', 'Company'],
  ['owner', 'Relationship owner'],
  ['attendees', 'Attendees'],
  ['notes', 'Notes'],
]

function candidateFor(field, source) {
  return (field.candidates || []).find((c) => c.source === source)
}

/** One source's cell in the comparison table. */
function DiffCell({ fieldName, field, source }) {
  const candidate = candidateFor(field, source)

  if (!candidate) {
    return <td className="absent">not provided</td>
  }

  const isWinner = field.chosen_from === source
  const className = field.conflict ? (isWinner ? 'winner' : 'loser') : (isWinner ? 'winner' : '')

  return (
    <td className={className}>
      {formatValue(fieldName, candidate.value)}
      {isWinner && field.candidates.length > 1 && <span className="wins">✓ USED</span>}
      <span className="mono" style={{ display: 'block', color: '#9ca3af', marginTop: 3 }}>
        {candidate.record_id}
      </span>
    </td>
  )
}

/** The per-signal breakdown behind a match decision. */
function MatchExplanation({ decision, heading }) {
  if (!decision) return null
  return (
    <div className="panel">
      <h3>{heading}</h3>
      <div className="row" style={{ marginBottom: 6 }}>
        <span className="mono">{decision.crm_record_id}</span>
        <span>↔</span>
        <span className="mono">{decision.calendar_record_id}</span>
        <span className="spacer" />
        <strong>score {decision.score.toFixed(3)}</strong>
        <span className="tag">{decision.decision.replace(/_/g, ' ')}</span>
      </div>
      <table className="signals">
        <tbody>
          {decision.signals.map((signal) => (
            <tr key={signal.signal}>
              <td style={{ width: 96, fontWeight: 600 }}>{signal.signal}</td>
              <td className={signal.score === null ? 'na' : ''}>{signal.explanation}</td>
              <td className="num">
                {signal.score === null ? 'n/a' : signal.score.toFixed(2)}
              </td>
              <td className="num" style={{ color: '#9ca3af' }}>
                ×{signal.weight}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function MeetingDetail({ meetingId, onClose }) {
  const [payload, setPayload] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let active = true
    setPayload(null)
    setError(null)
    api.meeting(meetingId)
      .then((data) => { if (active) setPayload(data) })
      .catch((err) => { if (active) setError(err.message) })
    return () => { active = false }
  }, [meetingId])

  useEffect(() => {
    const onKey = (event) => { if (event.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const meeting = payload?.meeting

  return (
    <div className="overlay" onClick={onClose}>
      <div className="drawer" onClick={(event) => event.stopPropagation()}>
        <button className="plain close" onClick={onClose}>Close ✕</button>

        {error && <div className="empty">Could not load this meeting: {error}</div>}
        {!payload && !error && <div className="empty">Loading…</div>}

        {meeting && (
          <>
            <h2>{meeting.title.value || '(untitled)'}</h2>
            <div className="row" style={{ marginBottom: 14 }}>
              <CoverageBadge coverage={meeting.coverage} />
              <ConflictChips summary={meeting.conflict_summary} />
              <span className="tag">{formatDate(meeting.start.value)}</span>
              {meeting.is_recurring && <span className="tag">recurring</span>}
              {meeting.is_internal && <span className="tag">internal</span>}
              <span className="tag mono">{meeting.id}</span>
            </div>

            {meeting.conflicts.length > 0 && (
              <div className="panel">
                <h3>Conflicts on this meeting</h3>
                {meeting.conflicts.map((conflict) => (
                  <div key={conflict.field} className={`conflict-card ${conflict.severity}`}>
                    <div className="row">
                      <SeverityBadge severity={conflict.severity} />
                      <strong>{conflict.label}</strong>
                    </div>
                    <div className="explain">{conflict.explanation}</div>
                    <div className="side-by-side">
                      <div className="side crm">
                        <div className="who">CRM · {conflict.crm_record_id}</div>
                        <div className="val">{formatValue(conflict.field, conflict.crm_value) ?? '—'}</div>
                      </div>
                      <div className="side calendar">
                        <div className="who">Calendar · {conflict.calendar_record_id}</div>
                        <div className="val">{formatValue(conflict.field, conflict.calendar_value) ?? '—'}</div>
                      </div>
                    </div>
                    <div className="explain">
                      Serving <strong>{formatValue(conflict.field, conflict.resolved_value) ?? '—'}</strong>
                      {conflict.resolved_from && <> from the <strong>{conflict.resolved_from}</strong></>}
                    </div>
                  </div>
                ))}
              </div>
            )}

            <div className="panel">
              <h3>Field-by-field comparison</h3>
              <table className="diff">
                <thead>
                  <tr>
                    <th>Field</th>
                    <th className="col-crm">CRM</th>
                    <th className="col-calendar">Calendar</th>
                    <th>Served value</th>
                  </tr>
                </thead>
                <tbody>
                  {FIELD_ORDER.map(([name, label]) => {
                    const field = meeting[name]
                    if (!field || !field.candidates?.length) return null
                    const rowClass = field.conflict ? `conflict ${field.severity || ''}` : ''
                    return (
                      <tr key={name} className={rowClass}>
                        <td className="label">{label}</td>
                        <DiffCell fieldName={name} field={field} source="crm" />
                        <DiffCell fieldName={name} field={field} source="calendar" />
                        <td>
                          {formatValue(name, field.value) ?? '—'}
                          {field.reason && <span className="why">{field.reason}</span>}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>

            <MatchExplanation decision={meeting.match} heading="Why these records were matched" />
            <MatchExplanation
              decision={meeting.closest_candidate}
              heading="Closest candidate considered and rejected"
            />

            {meeting.warnings.length > 0 && (
              <div className="panel">
                <h3>Data quality notes</h3>
                {meeting.warnings.map((warning, index) => (
                  <div key={index} className="explain" style={{ marginBottom: 5 }}>
                    <span className="mono">{warning.record_id}</span>{' '}
                    <span className="tag">{warning.code}</span> {warning.message}
                  </div>
                ))}
              </div>
            )}

            <div className="panel">
              <h3>Source records</h3>
              {Object.entries(payload.raw_records).map(([source, raw]) => (
                <details key={source}>
                  <summary>Raw {source} record</summary>
                  <pre className="raw">{JSON.stringify(raw, null, 2)}</pre>
                </details>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
