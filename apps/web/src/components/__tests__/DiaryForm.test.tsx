import { render, screen, fireEvent } from '@testing-library/react'
import { DiaryForm, DiaryFormValues } from '../DiaryForm'

const noop = () => {}

const defaultSave = jest.fn()

beforeEach(() => {
  defaultSave.mockClear()
})

describe('DiaryForm — basics', () => {
  it('renders the Name field', () => {
    render(<DiaryForm mode="create" saving={false} onSave={noop} />)
    expect(screen.getByLabelText('Name')).toBeInTheDocument()
  })

  it('renders "Who is this diary about?" relation buttons', () => {
    render(<DiaryForm mode="create" saving={false} onSave={noop} />)
    expect(screen.getByRole('button', { name: /myself/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /my child/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /my family/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /someone else/i })).toBeInTheDocument()
  })

  it('renders the Their name field', () => {
    render(<DiaryForm mode="create" saving={false} onSave={noop} />)
    expect(screen.getByLabelText(/their name/i)).toBeInTheDocument()
  })

  it('renders the Tone field with default value', () => {
    render(<DiaryForm mode="create" saving={false} onSave={noop} />)
    const tone = screen.getByLabelText(/tone/i)
    expect(tone).toBeInTheDocument()
    expect((tone as HTMLInputElement).value).toBe('warm, narrative')
  })
})

describe('DiaryForm — relation button selection', () => {
  it('marks a relation button as selected when clicked', () => {
    render(<DiaryForm mode="create" saving={false} onSave={noop} />)
    const child = screen.getByRole('button', { name: /my child/i })
    fireEvent.click(child)
    expect(child.className).toMatch(/btn-primary/)
  })

  it('deselects the previously selected relation when another is clicked', () => {
    render(<DiaryForm mode="create" saving={false} onSave={noop} />)
    const myself = screen.getByRole('button', { name: /myself/i })
    const child = screen.getByRole('button', { name: /my child/i })
    fireEvent.click(myself)
    fireEvent.click(child)
    expect(myself.className).not.toMatch(/btn-primary/)
    expect(child.className).toMatch(/btn-primary/)
  })
})

describe('DiaryForm — Advanced section', () => {
  it('advanced section is collapsed by default', () => {
    render(<DiaryForm mode="create" saving={false} onSave={noop} />)
    expect(screen.queryByLabelText(/narrative voice/i)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/scan frequency/i)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/timezone/i)).not.toBeInTheDocument()
  })

  it('reveals voice, scan frequency and timezone when Advanced is toggled', () => {
    render(<DiaryForm mode="create" saving={false} onSave={noop} />)
    fireEvent.click(screen.getByText(/advanced/i))
    expect(screen.getByLabelText(/narrative voice/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/scan frequency/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/timezone/i)).toBeInTheDocument()
  })
})

describe('DiaryForm — onSave callback', () => {
  it('calls onSave with the entered values when submitted', () => {
    render(<DiaryForm mode="create" saving={false} onSave={defaultSave} />)

    fireEvent.change(screen.getByLabelText('Name'), { target: { value: "Emma's diary" } })
    fireEvent.click(screen.getByRole('button', { name: /my child/i }))
    fireEvent.change(screen.getByLabelText(/their name/i), { target: { value: 'Emma' } })

    fireEvent.click(screen.getByRole('button', { name: /create diary/i }))

    expect(defaultSave).toHaveBeenCalledTimes(1)
    const values: DiaryFormValues = defaultSave.mock.calls[0][0]
    expect(values.name).toBe("Emma's diary")
    expect(values.subject_relation).toBe('child')
    expect(values.subject_name).toBe('Emma')
    expect(values.tone_hint).toBe('warm, narrative')
    expect(values.voice_override).toBeNull()
  })

  it('does not call onSave when name is empty', () => {
    render(<DiaryForm mode="create" saving={false} onSave={defaultSave} />)
    fireEvent.click(screen.getByRole('button', { name: /create diary/i }))
    expect(defaultSave).not.toHaveBeenCalled()
  })

  it('disables the save button while saving', () => {
    render(<DiaryForm mode="create" saving={true} onSave={noop} />)
    expect(screen.getByRole('button', { name: /saving/i })).toBeDisabled()
  })
})

describe('DiaryForm — edit mode pre-fill', () => {
  it('pre-fills all fields from initialValues in edit mode', () => {
    const initial: Partial<DiaryFormValues> = {
      name: 'Test diary',
      subject_relation: 'child',
      subject_name: 'Leo',
      tone_hint: 'playful',
      voice_override: 'second',
      scan_interval_minutes: 120,
      timezone: 'America/New_York',
    }
    render(<DiaryForm mode="edit" initialValues={initial} saving={false} onSave={noop} />)

    expect((screen.getByLabelText('Name') as HTMLInputElement).value).toBe('Test diary')
    expect((screen.getByLabelText(/their name/i) as HTMLInputElement).value).toBe('Leo')
    expect((screen.getByLabelText(/tone/i) as HTMLInputElement).value).toBe('playful')
    // My Child button should be highlighted
    expect(screen.getByRole('button', { name: /my child/i }).className).toMatch(/btn-primary/)

    // Advanced section should show pre-filled values
    fireEvent.click(screen.getByText(/advanced/i))
    expect((screen.getByLabelText(/narrative voice/i) as HTMLSelectElement).value).toBe('second')
    expect((screen.getByLabelText(/scan frequency/i) as HTMLSelectElement).value).toBe('120')
    expect((screen.getByLabelText(/timezone/i) as HTMLInputElement).value).toBe('America/New_York')
  })

  it('shows "Save changes" button text in edit mode', () => {
    render(<DiaryForm mode="edit" saving={false} onSave={noop} initialValues={{ name: 'Test' }} />)
    expect(screen.getByRole('button', { name: /save changes/i })).toBeInTheDocument()
  })

  it('shows "Create diary" button text in create mode', () => {
    render(<DiaryForm mode="create" saving={false} onSave={noop} />)
    expect(screen.getByRole('button', { name: /create diary/i })).toBeInTheDocument()
  })
})
