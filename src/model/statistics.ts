export function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value))
}

export function normalCdf(value: number): number {
  const sign = value < 0 ? -1 : 1
  const absolute = Math.abs(value) / Math.sqrt(2)
  const t = 1 / (1 + 0.3275911 * absolute)
  const polynomial = (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t
  const erf = sign * (1 - polynomial * Math.exp(-absolute * absolute))
  return 0.5 * (1 + erf)
}

function evaluatePolynomial(coefficients: number[], value: number): number {
  return coefficients.reduce((result, coefficient) => result * value + coefficient, 0)
}

export function normalInverse(probability: number): number {
  const p = clamp(probability, 0.00001, 0.99999)
  const lowCoefficients = [-39.6968302866538, 220.946098424521, -275.928510446969, 138.357751867269, -30.6647980661472, 2.50662827745924]
  const highCoefficients = [-54.4760987982241, 161.585836858041, -155.698979859887, 66.8013118877197, -13.2806815528857, 1]
  const low = 0.02425
  if (p < low) {
    const q = Math.sqrt(-2 * Math.log(p))
    return evaluatePolynomial(lowCoefficients, q) / evaluatePolynomial(highCoefficients, q)
  }
  if (p > 1 - low) return -normalInverse(1 - p)
  const q = p - 0.5
  const r = q * q
  return (q * evaluatePolynomial(lowCoefficients, r)) / evaluatePolynomial(highCoefficients, r)
}
