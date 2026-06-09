'use client'

import { useState } from 'react'

export interface DiaryFormValues {
  name: string
  subject_relation: string | null
  subject_name: string | null
  tone_hint: string
  voice_override: string | null
  scan_interval_minutes: number
  timezone: string
}

interface DiaryFormProps {
  mode: 'create' | 'edit'
  initialValues?: Partial<DiaryFormValues>
  saving: boolean
  onSave: (values: DiaryFormValues) => void
}

const RELATION_OPTIONS = [
  { label: 'Myself', value: 'self' },
  { label: 'My child', value: 'child' },
  { label: 'My family', value: 'family' },
  { label: 'Someone else', value: 'other_person' },
]

const SCAN_OPTIONS = [
  { label: 'Every 30 minutes', value: 30 },
  { label: 'Every 60 minutes', value: 60 },
  { label: 'Every 2 hours', value: 120 },
  { label: 'Every 6 hours', value: 360 },
]

function defaultTimezone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone
  } catch {
    return 'UTC'
  }
}

export function DiaryForm({ mode, initialValues, saving, onSave }: DiaryFormProps) {
  const [name, setName] = useState(initialValues?.name ?? '')
  const [subjectRelation, setSubjectRelation] = useState<string | null>(
    initialValues?.subject_relation ?? null
  )
  const [subjectName, setSubjectName] = useState(initialValues?.subject_name ?? '')
  const [toneHint, setToneHint] = useState(initialValues?.tone_hint ?? 'warm, narrative')
  const [voiceOverride, setVoiceOverride] = useState<string>(
    initialValues?.voice_override ?? ''
  )
  const [scanInterval, setScanInterval] = useState(
    initialValues?.scan_interval_minutes ?? 60
  )
  const [timezone, setTimezone] = useState(
    initialValues?.timezone ?? defaultTimezone()
  )
  const [showAdvanced, setShowAdvanced] = useState(false)

  function handleSave() {
    if (!name.trim()) return
    onSave({
      name: name.trim(),
      subject_relation: subjectRelation,
      subject_name: subjectName.trim() || null,
      tone_hint: toneHint,
      voice_override: voiceOverride || null,
      scan_interval_minutes: scanInterval,
      timezone,
    })
  }

  const submitLabel = saving
    ? 'Saving…'
    : mode === 'edit'
    ? 'Save changes'
    : 'Create diary'

  return (
    <div>
      {/* Name */}
      <div className="form-field">
        <label className="form-label" htmlFor="diary-name">Name</label>
        <input
          id="diary-name"
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="My diary"
        />
      </div>

      {/* Relationship */}
      <div className="form-field">
        <div className="form-label">Who is this diary about?</div>
        <div style={{ display: 'flex', gap: '0.4rem', flexWrap: 'wrap', marginTop: '0.25rem' }}>
          {RELATION_OPTIONS.map(({ label, value }) => (
            <button
              key={value}
              type="button"
              className={`btn ${subjectRelation === value ? 'btn-primary' : 'btn-secondary'}`}
              onClick={() => setSubjectRelation(value)}
            >
              {label}
            </button>
          ))}
        </div>
        <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '0.3rem' }}>
          Sets the narrative voice — &ldquo;My child&rdquo; writes the diary <em>to</em> them (&ldquo;you&rdquo;).
        </p>
      </div>

      {/* Their name */}
      <div className="form-field">
        <label className="form-label" htmlFor="diary-subject-name">
          Their name <span style={{ fontWeight: 400, color: 'var(--text-muted)' }}>(optional)</span>
        </label>
        <input
          id="diary-subject-name"
          type="text"
          value={subjectName}
          onChange={(e) => setSubjectName(e.target.value)}
          placeholder="e.g. Emma"
        />
      </div>

      {/* Tone */}
      <div className="form-field">
        <label className="form-label" htmlFor="diary-tone">Tone</label>
        <input
          id="diary-tone"
          type="text"
          value={toneHint}
          onChange={(e) => setToneHint(e.target.value)}
          placeholder="warm, narrative"
        />
      </div>

      {/* Advanced disclosure */}
      <div style={{ marginTop: '0.5rem' }}>
        <button
          type="button"
          className="btn btn-secondary"
          style={{ fontSize: '0.85rem' }}
          onClick={() => setShowAdvanced((v) => !v)}
        >
          {showAdvanced ? '▴ Advanced' : '▾ Advanced'}
        </button>

        {showAdvanced && (
          <div style={{ marginTop: '0.75rem', display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
            {/* Voice override */}
            <div className="form-field">
              <label className="form-label" htmlFor="diary-voice">Narrative voice</label>
              <select
                id="diary-voice"
                value={voiceOverride}
                onChange={(e) => setVoiceOverride(e.target.value)}
              >
                <option value="">Auto (from relationship)</option>
                <option value="first_singular">I / me</option>
                <option value="first_plural">we / us</option>
                <option value="second">you</option>
                <option value="third">their name / they</option>
              </select>
            </div>

            {/* Scan frequency */}
            <div className="form-field">
              <label className="form-label" htmlFor="diary-scan-interval">Scan frequency</label>
              <select
                id="diary-scan-interval"
                value={scanInterval}
                onChange={(e) => setScanInterval(Number(e.target.value))}
              >
                {SCAN_OPTIONS.map(({ label, value }) => (
                  <option key={value} value={value}>{label}</option>
                ))}
              </select>
            </div>

            {/* Timezone */}
            <div className="form-field">
              <label className="form-label" htmlFor="diary-timezone">Timezone</label>
              <input
                id="diary-timezone"
                type="text"
                value={timezone}
                onChange={(e) => setTimezone(e.target.value)}
                placeholder="America/Los_Angeles"
              />
            </div>
          </div>
        )}
      </div>

      <div style={{ marginTop: '1.25rem' }}>
        <button
          type="button"
          className="btn btn-primary"
          onClick={handleSave}
          disabled={saving || !name.trim()}
        >
          {submitLabel}
        </button>
      </div>
    </div>
  )
}
