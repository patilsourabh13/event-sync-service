import React, { useCallback, useEffect, useState } from 'react'
import { api } from './lib/api.js'
import StatsBar from './components/StatsBar.jsx'
import MeetingList from './components/MeetingList.jsx'
import MeetingDetail from './components/MeetingDetail.jsx'
import ConflictsView from './components/ConflictsView.jsx'
import ReviewQueueView from './components/ReviewQueueView.jsx'
import DataQualityView from './components/DataQualityView.jsx'

const EMPTY_FILTERS = {
  q: '',
  coverage: '',
  min_severity: '',
  status: '',
  date_from: '',
  date_to: '',
  sort: 'start',
}

function FilterBar({ filters, onChange, onReset, resultCount }) {
  const set = (key) => (event) => onChange({ ...filters, [key]: event.target.value })

  return (
    <div className="panel">
      <div className="row">
        <input
          type="text"
          placeholder="Search title, client, owner, location, notes, record id…"
          value={filters.q}
          onChange={set('q')}
        />
        <label className="field">
          Source coverage
          <select value={filters.coverage} onChange={set('coverage')}>
            <option value="">All sources</option>
            <option value="both">In both sources</option>
            <option value="crm_only">CRM only</option>
            <option value="calendar_only">Calendar only</option>
          </select>
        </label>
        <label className="field">
          Conflicts
          <select value={filters.min_severity} onChange={set('min_severity')}>
            <option value="">Any</option>
            <option value="critical">Critical only</option>
            <option value="high">High and above</option>
            <option value="medium">Medium and above</option>
            <option value="low">Has any conflict</option>
          </select>
        </label>
        <label className="field">
          Status
          <select value={filters.status} onChange={set('status')}>
            <option value="">Any status</option>
            {['confirmed', 'scheduled', 'tentative', 'completed', 'cancelled'].map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </label>
        <label className="field">
          Sort
          <select value={filters.sort} onChange={set('sort')}>
            <option value="start">By date</option>
            <option value="conflicts">Most conflicted first</option>
          </select>
        </label>
        <span className="spacer" />
        <button className="plain" onClick={onReset}>Reset</button>
      </div>
      <div className="explain" style={{ marginTop: 8, color: '#6b7280', fontSize: 12 }}>
        Showing {resultCount} meeting{resultCount === 1 ? '' : 's'}. Click any row to see
        the field-by-field comparison between the two sources.
      </div>
    </div>
  )
}

export default function App() {
  const [tab, setTab] = useState('meetings')
  const [stats, setStats] = useState(null)
  const [filters, setFilters] = useState(EMPTY_FILTERS)
  const [meetings, setMeetings] = useState([])
  const [selected, setSelected] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  const loadStats = useCallback(() => {
    api.stats().then(setStats).catch((err) => setError(err.message))
  }, [])

  useEffect(() => { loadStats() }, [loadStats])

  useEffect(() => {
    let active = true
    api.meetings(filters)
      .then((data) => { if (active) { setMeetings(data.meetings); setError(null) } })
      .catch((err) => { if (active) setError(err.message) })
    return () => { active = false }
  }, [filters])

  const resync = async () => {
    setBusy(true)
    try {
      await api.resync()
      loadStats()
      setFilters({ ...filters })
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const tabs = [
    ['meetings', 'Meetings', stats?.unified_meetings],
    ['conflicts', 'Conflicts', stats?.total_conflicts],
    ['review', 'Review queue', stats?.needs_review],
    ['quality', 'Data quality', stats?.records_with_warnings],
  ]

  return (
    <div className="app">
      <header className="masthead">
        <div className="row">
          <div>
            <h1>Event Sync Service</h1>
            <p>
              One meeting list reconciled from a CRM feed and a calendar feed, with every
              source disagreement kept visible.
            </p>
          </div>
          <span className="spacer" />
          <button className="plain" onClick={resync} disabled={busy}>
            {busy ? 'Re-syncing…' : 'Re-sync sources'}
          </button>
        </div>
      </header>

      <StatsBar stats={stats} />

      {error && (
        <div className="panel" style={{ borderColor: '#fecaca', background: '#fef2f2' }}>
          <strong>Could not reach the API.</strong> {error}
          <div className="explain">Is the backend running on port 8000?</div>
        </div>
      )}

      <div className="tabs">
        {tabs.map(([key, label, count]) => (
          <button
            key={key}
            className={tab === key ? 'active' : ''}
            onClick={() => setTab(key)}
          >
            {label}
            {count !== undefined && <span className="count">{count}</span>}
          </button>
        ))}
      </div>

      {tab === 'meetings' && (
        <>
          <FilterBar
            filters={filters}
            onChange={setFilters}
            onReset={() => setFilters(EMPTY_FILTERS)}
            resultCount={meetings.length}
          />
          <MeetingList meetings={meetings} onOpen={setSelected} />
        </>
      )}

      {tab === 'conflicts' && <ConflictsView onOpenMeeting={setSelected} />}
      {tab === 'review' && <ReviewQueueView />}
      {tab === 'quality' && <DataQualityView />}

      {selected && (
        <MeetingDetail meetingId={selected} onClose={() => setSelected(null)} />
      )}
    </div>
  )
}
