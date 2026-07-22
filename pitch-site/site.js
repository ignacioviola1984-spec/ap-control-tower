const formatCurrency = new Intl.NumberFormat('es-ES', {
  style: 'currency',
  currency: 'EUR',
  maximumFractionDigits: 0,
});

const formatDecimal = new Intl.NumberFormat('es-ES', {
  maximumFractionDigits: 1,
});

function numberValue(id) {
  const value = Number.parseFloat(document.getElementById(id)?.value ?? '0');
  return Number.isFinite(value) ? Math.max(0, value) : 0;
}

function updateCalculator() {
  const invoices = numberValue('invoices');
  const minutes = numberValue('minutes');
  const hourlyCost = numberValue('hourly-cost');
  const coverage = Math.min(100, numberValue('coverage')) / 100;
  const investment = numberValue('investment');
  const monthlyCost = numberValue('monthly-cost');

  const hoursSavedMonthly = invoices * minutes / 60 * coverage;
  const grossMonthlyValue = hoursSavedMonthly * hourlyCost;
  const annualGross = grossMonthlyValue * 12;
  const annualNet = annualGross - monthlyCost * 12;
  const monthlyNet = grossMonthlyValue - monthlyCost;
  const payback = monthlyNet > 0 ? investment / monthlyNet : null;
  const roi = investment > 0 ? ((annualNet * 3 - investment) / investment) * 100 : null;

  document.getElementById('hours-saved').textContent = `${formatDecimal.format(hoursSavedMonthly)} h`;
  document.getElementById('annual-gross').textContent = formatCurrency.format(annualGross);
  document.getElementById('annual-net').textContent = formatCurrency.format(annualNet);
  document.getElementById('payback').textContent = payback === null ? 'Sin payback' : `${formatDecimal.format(payback)} meses`;
  document.getElementById('roi-three-year').textContent = roi === null ? '—' : `${formatDecimal.format(roi)}%`;
}

document.querySelectorAll('.calculator-form input').forEach((input) => {
  input.addEventListener('input', updateCalculator);
});
updateCalculator();

const revealObserver = new IntersectionObserver((entries) => {
  entries.forEach((entry) => {
    if (entry.isIntersecting) {
      entry.target.classList.add('is-visible');
      revealObserver.unobserve(entry.target);
    }
  });
}, { threshold: 0.08 });

document.querySelectorAll('.reveal').forEach((element) => revealObserver.observe(element));

document.getElementById('print-page')?.addEventListener('click', () => window.print());
