import React, { useEffect, useState } from 'react'
import { api } from '../lib/api.js'
import { SourceBadge } from './Badges.jsx'

/**
 * Everything the pipeline repaired, inferred, collapsed, or refused to read.
 *
 * This tab exists because the alternative -- quietly fixing malformed records
 * and showing only the clean result -- hides exactly the information an
 * operator needs to go fix the upstream system.
 */
export default function DataQualityView() {
  const [data, setData] = useState(null)
  const [code, setCode] = useState('')

  useEffect(() => { api.dataQuality().then(setData).catch(() => setData(null)) }, [])

  if (!data) return <div className="empty">Loading data quality report…</div>

  const warnings = code ? data.warnings.filter((w) => w.code === code) : data.warnings

  return (
    <>
      <div className="panel">
        <div className="row">
          <div><strong>{data.summary.records_read}</strong> records read</div>
          <span>·</span>
          <div><strong>{data.summary.records_with_warnings}</strong> needed repair or inference</div>
          <span>·</span>
          <div><strong>{data.summary.intra_source_duplicates}</strong> duplicates collapsed</div>
          <span>·</span>
          <div><strong>{data.summary.quarantined}</strong> quarantined</div>
        </div>
      </div>

      {data.duplicates.length > 0 && (
        <div className="panel">
          <h3>Duplicates collapsed within a single source</h3>
          {data.duplicates.map((dup) => (
            <div key={dup.duplicate_record_id} className="conflict-card medium">
              <div className="row">
                <SourceBadge source={dup.source} />
                <span className="mono">{dup.duplicate_record_id}</span>
                <span>folded into</span>
                <span className="mono"><strong>{dup.kept_record_id}</strong></span>
              </div>
              <div className="explain">{dup.reason}</div>
              {dup.differences.length > 0 && (
                <ul className="explain" style={{ margin: '5px 0 0 16px' }}>
                  {dup.differences.map((diff, i) => <li key={i}>{diff}</li>)}
                </ul>
              )}
            </div>
          ))}
        </div>
      )}

      {data.quarantined.length > 0 && (
        <div className="panel">
          <h3>Quarantined — could not be placed on a timeline</h3>
          {data.quarantined.map((record, index) => (
            <div key={index} className="conflict-card critical">
              <div className="row">
                <SourceBadge source={record.source} />
                <span className="mono">{record.record_id || '(no id)'}</span>
              </div>
              <div className="explain">{record.reason}</div>
              <details>
                <summary>Raw record</summary>
                <pre className="raw">{JSON.stringify(record.raw, null, 2)}</pre>
              </details>
            </div>
          ))}
        </div>
      )}

      <div className="panel">
        <div className="row" style={{ marginBottom: 10 }}>
          <h3 style={{ margin: 0 }}>Field-level warnings</h3>
          <span className="spacer" />
          <label className="field">
            Type
            <select value={code} onChange={(e) => setCode(e.target.value)}>
              <option value="">All types ({data.warnings.length})</option>
              {Object.entries(data.warnings_by_code).map(([name, count]) => (
                <option key={name} value={name}>{name} ({count})</option>
              ))}
            </select>
          </label>
        </div>

        <table className="diff">
          <thead>
            <tr>
              <th>Source</th>
              <th>Record</th>
              <th>Type</th>
              <th>What we did</th>
            </tr>
          </thead>
          <tbody>
            {warnings.map((warning, index) => (
              <tr key={index}>
                <td><SourceBadge source={warning.source} /></td>
                <td className="mono">{warning.record_id}</td>
                <td><span className="tag">{warning.code}</span></td>
                <td>
                  {warning.message}
                  {warning.field && (
                    <span className="why">field: {warning.field}</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}
