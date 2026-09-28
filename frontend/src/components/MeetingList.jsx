import React from 'react'
import { formatDate, formatTime, formatValue } from '../lib/format.js'
import { CoverageBadge, ConflictChips } from './Badges.jsx'

function MeetingRow({ meeting, onOpen }) {
  const summary = meeting.conflict_summary || {}
  const classes = ['meeting']
  if (summary.critical) classes.push('has-critical')
  else if (summary.high) classes.push('has-high')
  if (meeting.status.value === 'cancelled') classes.push('cancelled')

  const client = meeting.client_name.value || meeting.client_company.value
  const meta = [
    meeting.location.value,
    client,
    meeting.owner.value && `owner: ${meeting.owner.value}`,
  ].filter(Boolean)

  return (
    <div className={classes.join(' ')} onClick={() => onOpen(meeting.id)}>
      <div className="row">
        <span className="title">{meeting.title.value || '(untitled)'}</span>
        <CoverageBadge coverage={meeting.coverage} />
        <ConflictChips summary={summary} />
        {meeting.status.value === 'cancelled' && <span className="badge critical">cancelled</span>}
        {meeting.is_recurring && <span className="tag">recurring</span>}
        <span className="spacer" />
        <span className="tag">
          {formatDate(meeting.start.value)}
          {formatTime(meeting.start.value) && ` · ${formatTime(meeting.start.value)}`}
        </span>
      </div>
      <div className="meta">
        {meta.join(' · ') || 'no location or client recorded'}
        {' — '}
        <span className="mono">
          {meeting.sources.map((s) => s.record_id).join(' + ')}
        </span>
      </div>
    </div>
  )
}

export default function MeetingList({ meetings, onOpen }) {
  if (!meetings.length) {
    return <div className="empty">No meetings match these filters.</div>
  }
  return (
    <div>
      {meetings.map((meeting) => (
        <MeetingRow key={meeting.id} meeting={meeting} onOpen={onOpen} />
      ))}
    </div>
  )
}
