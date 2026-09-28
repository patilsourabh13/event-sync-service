import React from 'react'

/** Headline sync numbers: what went in, what came out, and what is unresolved. */
export default function StatsBar({ stats }) {
  if (!stats) return null
  const severity = stats.conflicts_by_severity || {}

  const tiles = [
    {
      label: 'Records ingested',
      value: stats.total_records_read,
      sub: `${stats.crm_records_read} CRM · ${stats.calendar_records_read} calendar`,
    },
    {
      label: 'Unified meetings',
      value: stats.unified_meetings,
      sub: `${stats.matched_both_sources} matched across both sources`,
    },
    {
      label: 'Single-source',
      value: stats.crm_only + stats.calendar_only,
      sub: `${stats.crm_only} CRM only · ${stats.calendar_only} calendar only`,
    },
    {
      label: 'Conflicts',
      value: stats.total_conflicts,
      sub: `across ${stats.meetings_with_conflicts} meetings`,
    },
    {
      label: 'Needs attention',
      value: (severity.critical || 0) + (severity.high || 0),
      sub: `${severity.critical || 0} critical · ${severity.high || 0} high`,
    },
    {
      label: 'Data quality',
      value: stats.records_with_warnings,
      sub: `${stats.intra_source_duplicates} duplicate · ${stats.quarantined} quarantined`,
    },
  ]

  return (
    <div className="stats">
      {tiles.map((tile) => (
        <div className="stat" key={tile.label}>
          <div className="value">{tile.value}</div>
          <div className="label">{tile.label}</div>
          <div className="sub">{tile.sub}</div>
        </div>
      ))}
    </div>
  )
}
