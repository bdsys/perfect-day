// Runs once, before the test workers start, so they inherit the zone. Node fixes its
// time zone at startup: setting process.env.TZ inside a test does not reliably change it,
// and CI runs in UTC while development machines run in local time. Pacific is a zone
// behind UTC, where reading "2026-12-01" as local midnight would show Nov 30.
module.exports = async () => {
  process.env.TZ = 'America/Los_Angeles'
}
