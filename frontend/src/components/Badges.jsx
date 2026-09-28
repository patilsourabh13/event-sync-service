import React from 'react'
import { COVERAGE_LABEL, SOURCE_LABEL } from '../lib/format.js'

export function CoverageBadge({ coverage }) {
  return <span className={`badge ${coverage}`}>{COVERAGE_LABEL[coverage] || coverage}</span>
}

export function SourceBadge({ source }) {
  return <span className={`badge source-${source}`}>{SOURCE_LABEL[source] || source}</span>
}

export function SeverityBadge({ severity, count }) {
  return (
    <span className={`badge ${severity}`}>
      {count !== undefined ? `${count} ` : ''}{severity}
    </span>
  )
}

/** Compact per-severity conflict chips for a meeting row. */
export function ConflictChips({ summary }) {
  if (!summary || !summary.total) return null
  const order = ['critical', 'high', 'medium', 'low']
  return (
    <>
      {order
        .filter((level) => summary[level] > 0)
        .map((level) => (
          <SeverityBadge key={level} severity={level} count={summary[level]} />
        ))}
    </>
  )
}
