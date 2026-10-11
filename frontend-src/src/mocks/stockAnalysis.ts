/** Fixture prose deliberately stays in its original language, just like saved model output. */
export function stockAnalysisFixture(symbol: string, price: number, iv: number, scores: { trend: number; momentum: number; volume: number; volatility: number }, bias: number, label: string): string {
  const ivTone = iv >= 60 ? '偏贵' : iv <= 40 ? '相对便宜' : '中性';
  return `${symbol} 模型分析完成：趋势偏向分 ${bias}（${label}），` +
    `分项读数 趋势 ${scores.trend} / 动量 ${scores.momentum} / 量能 ${scores.volume} / 波动 ${scores.volatility}。` +
    `现价 ${price.toFixed(2)} 美元，IV 百分位 ${iv}%，期权定价${ivTone}。` +
    '近端观察 MA20 附近的量能配合与突破延续性；若量价背离放大，偏向读数将快速回落。';
}
