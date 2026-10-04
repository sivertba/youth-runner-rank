export function parseDate(value: string): Date {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value)
  if (!match) throw new Error('Use a valid date in YYYY-MM-DD format.')
  const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])))
  if (date.getUTCFullYear() !== Number(match[1]) || date.getUTCMonth() !== Number(match[2]) - 1 || date.getUTCDate() !== Number(match[3])) {
    throw new Error('Use a real calendar date.')
  }
  return date
}

export function ageInDays(birthDate: string, raceDate: string): number {
  const birth = parseDate(birthDate)
  const race = parseDate(raceDate)
  const days = Math.floor((race.getTime() - birth.getTime()) / 86_400_000)
  if (days < 0) throw new Error('Race date must be after the date of birth.')
  return days
}

export function formatAge(days: number): string {
  let remaining = days
  const years = Math.floor(remaining / 365.2425)
  remaining -= Math.round(years * 365.2425)
  const months = Math.floor(remaining / 30.4375)
  remaining -= Math.round(months * 30.4375)
  return `${years} years, ${months} months, ${Math.max(0, remaining)} days`
}
