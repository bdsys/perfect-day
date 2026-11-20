import fs from 'fs'
import path from 'path'

// One Node major everywhere: the production image, CI, local dev (.nvmrc) and the
// type definitions. @types/node ahead of the runtime would let code use Node APIs
// that production doesn't have. Node 26 is the Active LTS (2026-10-28 to 2029-04-30,
// https://github.com/nodejs/Release/blob/main/schedule.json).
const ROOT = path.join(__dirname, '..', '..', '..', '..')
const read = (p: string) => fs.readFileSync(path.join(ROOT, p), 'utf8')
const NODE_MAJOR = '26'

it('the web image builds and runs on Node 26 in every stage', () => {
  const stages = [...read('apps/web/Dockerfile').matchAll(/^FROM node:(\d+)-alpine/gm)].map((m) => m[1])
  expect(stages).toHaveLength(3)
  expect(new Set(stages)).toEqual(new Set([NODE_MAJOR]))
})

it.each(['.github/workflows/ci.yml', '.github/workflows/e2e.yml'])('%s sets up Node 26', (wf) => {
  const versions = [...read(wf).matchAll(/node-version:\s*"?(\d+)"?/g)].map((m) => m[1])
  expect(versions.length).toBeGreaterThan(0)
  expect(new Set(versions)).toEqual(new Set([NODE_MAJOR]))
})

it('local dev pins Node 26 in .nvmrc', () => {
  expect(read('.nvmrc').trim()).toBe(NODE_MAJOR)
})

it('@types/node matches the Node major', () => {
  const pkg = JSON.parse(read('apps/web/package.json'))
  expect(pkg.devDependencies['@types/node']).toMatch(new RegExp(`^\\^${NODE_MAJOR}\\.`))
})

it('Dependabot never moves @types/node to a new major on its own', () => {
  const cfg = read('.github/dependabot.yml')
  expect(cfg).toMatch(
    /- dependency-name: "@types\/node"\s*\n\s*update-types: \["version-update:semver-major"\]/,
  )
})
