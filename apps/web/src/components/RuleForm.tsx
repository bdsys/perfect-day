'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type RuleCondition, type RuleConditionLeaf, type RuleOptions, type RulePreview } from '@/lib/api'

// Generates a simple unique ID for tree node keys
function nodeId() {
  return Math.random().toString(36).slice(2, 9)
}

// A node in the internal tree representation (augmented with a client-side id for React keys)
type LeafNode = RuleConditionLeaf & { _id: string }
type GroupNode = { op: 'AND' | 'OR'; children: TreeNode[]; _id: string }
type TreeNode = LeafNode | GroupNode

function isGroup(node: TreeNode): node is GroupNode {
  return node.op === 'AND' || node.op === 'OR'
}

function stripIds(node: TreeNode): RuleCondition {
  if (isGroup(node)) {
    return { op: node.op, children: node.children.map(stripIds) }
  }
  const { _id: _omit, ...rest } = node // eslint-disable-line @typescript-eslint/no-unused-vars
  return rest
}

function defaultLeaf(): LeafNode {
  return { _id: nodeId(), field: 'title', op: 'contains', value: '', case_sensitive: false }
}

function defaultGroup(op: 'AND' | 'OR' = 'AND'): GroupNode {
  return { _id: nodeId(), op, children: [defaultLeaf()] }
}

function fromCondition(c: RuleCondition): TreeNode {
  if ('children' in c) {
    return { _id: nodeId(), op: c.op, children: c.children.map(fromCondition) }
  }
  return { _id: nodeId(), ...c }
}

// Recursive condition node renderer
function ConditionNode({
  node,
  depth,
  onChange,
  onRemove,
}: {
  node: TreeNode
  depth: number
  onChange: (updated: TreeNode) => void
  onRemove: (() => void) | null
}) {
  if (isGroup(node)) {
    return (
      <div style={{ border: '1px solid #ddd', borderRadius: 6, padding: '0.75rem', marginBottom: '0.5rem', background: depth % 2 === 0 ? '#fafafa' : '#fff' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.5rem' }}>
          <select
            value={node.op}
            onChange={(e) => onChange({ ...node, op: e.target.value as 'AND' | 'OR' })}
            style={{ fontWeight: 600, fontSize: '0.85rem' }}
          >
            <option value="AND">AND — all must match</option>
            <option value="OR">OR — any must match</option>
          </select>
          {onRemove && (
            <button className="btn btn-danger" style={{ fontSize: '0.75rem', padding: '0.2rem 0.5rem' }} onClick={onRemove}>
              Remove group
            </button>
          )}
        </div>
        {node.children.map((child, i) => (
          <ConditionNode
            key={child._id}
            node={child}
            depth={depth + 1}
            onChange={(updated) => {
              const children = [...node.children]
              children[i] = updated
              onChange({ ...node, children })
            }}
            onRemove={node.children.length > 1 ? () => {
              const children = node.children.filter((_, j) => j !== i)
              onChange({ ...node, children })
            } : null}
          />
        ))}
        <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.5rem' }}>
          <button
            className="btn btn-secondary"
            style={{ fontSize: '0.75rem', padding: '0.25rem 0.5rem' }}
            onClick={() => onChange({ ...node, children: [...node.children, defaultLeaf()] })}
          >
            + Add condition
          </button>
          {depth < 3 && (
            <button
              className="btn btn-secondary"
              style={{ fontSize: '0.75rem', padding: '0.25rem 0.5rem' }}
              onClick={() => onChange({ ...node, children: [...node.children, defaultGroup()] })}
            >
              + Add group
            </button>
          )}
        </div>
      </div>
    )
  }

  // Leaf
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.4rem', flexWrap: 'wrap' }}>
      <select
        value={node.field}
        onChange={(e) => onChange({ ...node, field: e.target.value as RuleConditionLeaf['field'] })}
        style={{ fontSize: '0.85rem' }}
      >
        <option value="title">Title</option>
        <option value="description">Description</option>
        <option value="location">Location</option>
        <option value="attendee_email">Attendee email</option>
      </select>
      <select
        value={node.op}
        onChange={(e) => onChange({ ...node, op: e.target.value as RuleConditionLeaf['op'] })}
        style={{ fontSize: '0.85rem' }}
      >
        <option value="contains">contains</option>
        <option value="equals">equals</option>
        <option value="not_contains">does not contain</option>
      </select>
      <input
        type="text"
        value={node.value}
        onChange={(e) => onChange({ ...node, value: e.target.value })}
        placeholder="value"
        style={{ fontSize: '0.85rem', flex: '1 1 120px', minWidth: 80 }}
      />
      {onRemove && (
        <button className="btn btn-danger" style={{ fontSize: '0.75rem', padding: '0.2rem 0.4rem' }} onClick={onRemove}>
          ×
        </button>
      )}
    </div>
  )
}

interface RuleFormProps {
  diaryId: string
  initialName?: string
  initialCondition?: RuleCondition
  initialOptions?: RuleOptions
  saving: boolean
  onSave: (name: string, condition: RuleCondition, options: RuleOptions) => void
}

export function RuleForm({ diaryId, initialName = '', initialCondition, initialOptions, saving, onSave }: RuleFormProps) {
  const [name, setName] = useState(initialName)
  const [tree, setTree] = useState<TreeNode>(() =>
    initialCondition ? fromCondition(initialCondition) : defaultGroup()
  )
  const [options, setOptions] = useState<RuleOptions>(initialOptions ?? { recurring: 'per_instance', multi_day: 'per_day' })
  const [preview, setPreview] = useState<RulePreview | null>(null)
  const [previewing, setPreviewing] = useState(false)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const condition = stripIds(tree)

  const runPreview = useCallback(async (cond: RuleCondition, opts: RuleOptions) => {
    // Only run if condition has at least one leaf with a non-empty value
    const hasValue = JSON.stringify(cond).includes('"value":"') && !JSON.stringify(cond).includes('"value":""')
    if (!hasValue) {
      setPreview(null)
      return
    }
    setPreviewing(true)
    try {
      const result = await api.rules.preview(diaryId, { condition: cond, options: opts })
      setPreview(result)
    } catch {
      // Preview failures are non-fatal; silently clear
      setPreview(null)
    } finally {
      setPreviewing(false)
    }
  }, [diaryId])

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => runPreview(condition, options), 500)
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current) }
  }, [condition, options, runPreview])

  function handleSave() {
    if (!name.trim()) return
    onSave(name.trim(), condition, options)
  }

  return (
    <div>
      <div className="form-field" style={{ marginBottom: '1rem' }}>
        <label className="form-label" htmlFor="rule-name">Rule name</label>
        <input
          id="rule-name"
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. Soccer practices"
          style={{ maxWidth: 400 }}
        />
      </div>

      <div style={{ marginBottom: '1rem' }}>
        <div className="form-label" style={{ marginBottom: '0.5rem' }}>Conditions</div>
        <ConditionNode
          node={tree}
          depth={0}
          onChange={setTree}
          onRemove={null}
        />
      </div>

      <div style={{ display: 'flex', gap: '1rem', marginBottom: '1.5rem', flexWrap: 'wrap' }}>
        <div className="form-field">
          <label className="form-label" htmlFor="recurring-opt">Recurring events</label>
          <select
            id="recurring-opt"
            value={options.recurring ?? 'per_instance'}
            onChange={(e) => setOptions(o => ({ ...o, recurring: e.target.value as 'per_instance' | 'per_series' }))}
          >
            <option value="per_instance">One entry per instance</option>
            <option value="per_series">One entry per series</option>
          </select>
        </div>
        <div className="form-field">
          <label className="form-label" htmlFor="multiday-opt">Multi-day events</label>
          <select
            id="multiday-opt"
            value={options.multi_day ?? 'per_day'}
            onChange={(e) => setOptions(o => ({ ...o, multi_day: e.target.value as 'per_day' | 'spanning' }))}
          >
            <option value="per_day">One entry per day</option>
            <option value="spanning">Single spanning entry</option>
          </select>
        </div>
      </div>

      {/* Live preview pane */}
      <div className="card" style={{ marginBottom: '1.5rem', background: '#f9fafb', minHeight: 60 }}>
        <div style={{ fontSize: '0.85rem', fontWeight: 600, marginBottom: '0.5rem', color: '#555' }}>
          Preview against last 90 days
        </div>
        {previewing ? (
          <div style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>Checking…</div>
        ) : preview ? (
          <>
            {preview.threshold_exceeded && (
              <div style={{
                background: '#fffbeb', border: '1px solid #f59e0b', borderRadius: 4,
                padding: '0.5rem 0.75rem', marginBottom: '0.5rem', fontSize: '0.85rem', color: '#92400e',
              }}>
                ⚠ This rule would match ~{preview.matched_count} events. Make sure the condition is specific enough.
              </div>
            )}
            <div style={{ fontSize: '0.85rem', color: '#555' }}>
              {preview.matched_count} of {preview.total_evaluated} recent events match
            </div>
            {preview.sample.length > 0 && (
              <ul style={{ margin: '0.4rem 0 0', padding: '0 0 0 1.2rem', fontSize: '0.8rem', color: '#666' }}>
                {preview.sample.slice(0, 3).map((s, i) => (
                  <li key={i}>{s.summary || '(no title)'}{s.location ? ` · ${s.location}` : ''}</li>
                ))}
              </ul>
            )}
          </>
        ) : (
          <div style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>
            Fill in at least one condition value to see a preview.
          </div>
        )}
      </div>

      <button className="btn btn-primary" onClick={handleSave} disabled={saving || !name.trim()}>
        {saving ? 'Saving…' : 'Save Rule'}
      </button>
    </div>
  )
}
