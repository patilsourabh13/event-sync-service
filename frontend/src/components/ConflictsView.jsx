import React, { useEffect, useState } from 'react'
import { api } from '../lib/api.js'
import { formatValue } from '../lib/format.js'
import { SeverityBadge } from './Badges.jsx'

const SEVERITIES = ['critical', 'high', 'medium', 'low']

/**
 * Every disagreement between the two sources, in one list, independent of
 * which meeting it belongs to. This is the "where does the data conflict?"
 * view -- the meeting list answers "what is on the calendar", this answers
 * "what should I not trust".
 */
export default function ConflictsView({ onOpenMeeting }) {
  const [data, setData] = useState(null)
  const [severity, setSeverity] = useState('')
  const [field, setField] = useState('')

  useEffect(() => {
    api.conflicts({ severity, field }).then(setData).catch(() => setData(null))
  }, [severity, field])

  if (!data) return <div className="empty">Loading conflicts…</div>

  return (
    <>
      <div className="panel">
        <div className="row">
          <label className="field">
            Severity
            <select value={severity} onChange={(e) => setSeverity(e.target.value)}>
              <option value="">All severities</option>
              {SEVERITIES.map((level) => (
                <option key={level} value={level}>{level}</option>
              ))}
            </select>
          </label>
          <label className="field">
            Field
            <select value={field} onChange={(e) => setField(e.target.value)}>
              <option value="">All fields</option>
              {Object.entries(data.by_field).map(([name, count]) => (
                <option key={name} value={name}>{name} ({count})</option>
              ))}
            </select>
          </label>
          <span className="spacer" />
          <div className="row">
            {SEVERITIES.map((level) => (
              <SeverityBadge key={level} severity={level} count={data.by_severity[level] || 0} />
            ))}
          </div>
        </div>
      </div>

      {data.conflicts.length === 0 && <div className="empty">No conflicts match these filters.</div>}

      {data.conflicts.map((conflict, index) => (
        <div key={`${conflict.meeting_id}-${conflict.field}-${index}`}
             className={`conflict-card ${conflict.severity}`}>
          <div className="row">
            <SeverityBadge severity={conflict.severity} />
            <strong>{conflict.label}</strong>
            <span className="spacer" />
            <button className="plain" onClick={() => onOpenMeeting(conflict.meeting_id)}>
              {conflict.meeting_title} →
            </button>
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
    </>
  )
}
