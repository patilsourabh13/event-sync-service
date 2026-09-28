import React, { useEffect, useState } from 'react'
import { api } from '../lib/api.js'

/**
 * Pairs the matcher scored between the review and auto-match thresholds --
 * the cases it refuses to decide alone.
 *
 * An empty queue is a real result, not a broken view, so it is stated
 * explicitly along with the thresholds that produced it.
 */
export default function ReviewQueueView() {
  const [data, setData] = useState(null)

  useEffect(() => { api.reviewQueue().then(setData).catch(() => setData(null)) }, [])

  if (!data) return <div className="empty">Loading review queue…</div>

  return (
    <>
      <div className="panel">
        <h3>How pairs are classified</h3>
        <div className="explain">
          Every same-day CRM/calendar pair is scored from 0 to 1 across five weighted
          signals. Pairs at or above <strong>{data.auto_match_threshold}</strong> are
          merged automatically. Pairs below <strong>{data.review_threshold}</strong> are
          treated as unrelated. Anything between the two lands here for a human to
          decide, rather than the service guessing.
        </div>
      </div>

      {data.count === 0 ? (
        <div className="empty">
          Nothing is awaiting review. At the current thresholds every pair scored either
          confidently matched or confidently unrelated — there were no borderline cases
          in this dataset.
        </div>
      ) : (
        data.pairs.map((pair) => (
          <div key={`${pair.crm_record_id}-${pair.calendar_record_id}`} className="conflict-card medium">
            <div className="row">
              <span className="mono">{pair.crm_record_id}</span>
              <span>↔</span>
              <span className="mono">{pair.calendar_record_id}</span>
              <span className="spacer" />
              <strong>score {pair.score.toFixed(3)}</strong>
            </div>
            <table className="signals">
              <tbody>
                {pair.signals.map((signal) => (
                  <tr key={signal.signal}>
                    <td style={{ width: 96, fontWeight: 600 }}>{signal.signal}</td>
                    <td className={signal.score === null ? 'na' : ''}>{signal.explanation}</td>
                    <td className="num">{signal.score === null ? 'n/a' : signal.score.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))
      )}
    </>
  )
}
