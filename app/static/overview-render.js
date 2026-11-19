export function metricCard(label, value, foot, accent = false) {
  return `<div class="metric-card${accent ? " metric-accent" : ""}">` +
    `<div class="metric-label"><span>${label}</span><span>↗</span></div>` +
    `<div class="metric-value">${value}</div>` +
    `<div class="metric-foot">${foot}</div>` +
    `</div>`;
}
