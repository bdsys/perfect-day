import fs from 'fs'
import path from 'path'

// Tracker row 64: a rainbow favicon shaped like Lucide's `rainbow` icon (option A,
// chosen by Andrew 2026-11-18), each arc its own colour, red outside to violet inside.
const APP = path.join(__dirname, '..')

// Lucide 1.75.0 `rainbow`, outermost arc first.
const LUCIDE_ARCS = ['M22 17a10 10 0 0 0-20 0', 'M6 17a6 6 0 0 1 12 0', 'M10 17a2 2 0 0 1 4 0']
const COLOURS = ['#e5484d', '#2fa36b', '#9d5ce6'] // red, green, violet

describe('icon.svg (browser tab icon)', () => {
  const svg = fs.readFileSync(path.join(APP, 'icon.svg'), 'utf8')
  const paths = [...svg.matchAll(/<path\b[^>]*>/g)].map((m) => m[0])

  it("keeps Lucide's 24-unit grid", () => {
    expect(svg).toMatch(/viewBox="0 0 24 24"/)
  })

  it("draws Lucide's three arcs, outermost first, red to violet", () => {
    expect(paths).toHaveLength(3)
    paths.forEach((p, i) => {
      expect(p).toContain(`d="${LUCIDE_ARCS[i]}"`)
      expect(p).toContain(`stroke="${COLOURS[i]}"`)
    })
  })

  it("uses Lucide's line weight and round ends", () => {
    expect(svg).toMatch(/stroke-width="2"/)
    expect(svg).toMatch(/stroke-linecap="round"/)
  })

  it('has a transparent background', () => {
    expect(svg).not.toMatch(/<rect\b/)
    expect(svg).toMatch(/fill="none"/)
  })
})

describe('apple-icon.png (iPhone home screen)', () => {
  const png = fs.readFileSync(path.join(APP, 'apple-icon.png'))

  it('is a 180×180 PNG', () => {
    expect(png.subarray(1, 4).toString('ascii')).toBe('PNG')
    expect(png.readUInt32BE(16)).toBe(180)
    expect(png.readUInt32BE(20)).toBe(180)
  })

  it('has no transparency, because iOS shows clear pixels as black', () => {
    const colourType = png[25]
    expect(colourType).toBe(2) // truecolour, no alpha channel
  })
})
