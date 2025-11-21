document.addEventListener("DOMContentLoaded", function () {
  const canvas = document.getElementById("stockHistoryChart");
  if (!canvas || !window.ITEM_HISTORY) return;

  const ctx = canvas.getContext("2d");

  const labels = window.ITEM_HISTORY.labels || [];
  let datasets = window.ITEM_HISTORY.datasets || [];

  // Backward compatibility
  if ((!datasets || datasets.length === 0) && window.ITEM_HISTORY.stock) {
    datasets = [{
      label: "Total stock",
      data: window.ITEM_HISTORY.stock
    }];
  }

  // Nice palette for multiple lines (Chart.js defaults would all look identical)
  const palette = [
    "#0d6efd", "#20c997", "#fd7e14", "#6f42c1",
    "#198754", "#dc3545", "#0dcaf0", "#adb5bd"
  ];

  datasets = datasets.map((ds, i) => ({
    ...ds,
    borderColor: palette[i % palette.length],
    backgroundColor: palette[i % palette.length],
    fill: false,
    tension: 0.15,
    pointRadius: 2,
    pointHoverRadius: 4,
  }));

  new Chart(ctx, {
    type: "line",
    data: { labels, datasets },
    options: {
      responsive: true,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { position: "bottom" },
        tooltip: { enabled: true }
      },
      scales: {
        x: {
          ticks: { maxRotation: 45, minRotation: 45 }
        },
        y: {
          beginAtZero: true
        }
      }
    }
  });
});
