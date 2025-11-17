document.addEventListener("DOMContentLoaded", function () {
  if (window.ITEM_HISTORY && document.getElementById("stockHistoryChart")) {
    const ctx = document.getElementById("stockHistoryChart").getContext("2d");
    new Chart(ctx, {
      type: "line",
      data: {
        labels: window.ITEM_HISTORY.labels,
        datasets: [{
          label: "Total stock",
          data: window.ITEM_HISTORY.stock,
          fill: false,
          tension: 0.15
        }]
      },
      options: {
        responsive: true,
        scales: {
          x: {
            ticks: {
              maxRotation: 45,
              minRotation: 45
            }
          },
          y: {
            beginAtZero: true
          }
        }
      }
    });
  }
});
